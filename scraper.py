import asyncio
import hashlib
import os
import re
import urllib.parse

from playwright.async_api import async_playwright
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Har exchange: naam -> homepage ke candidate addresses (pehla jo khul jaye wohi use hoga)
EXCHANGES = {
    "KCEX": ["https://www.kcex.com", "https://kcex.com"],
    "BTCC": ["https://www.btcc.com", "https://btcc.com"],
    "Bitunix": ["https://www.bitunix.com", "https://bitunix.com"],
    "XT": ["https://www.xt.com", "https://xt.com"],
    "Toobit": ["https://www.toobit.com", "https://toobit.com"],
    "Phemex": ["https://phemex.com", "https://www.phemex.com"],
    "BloFin": ["https://www.blofin.com", "https://blofin.com"],
    "EasiCoin": ["https://www.easicoin.io", "https://easicoin.io"],
    "Bitbase": ["https://www.bitbase.com", "https://bitbase.com"],
    "AlphaX": ["https://www.alphax.com", "https://alphax.com"],
    "Yubit": ["https://www.yubit.com", "https://yubit.com"],
    "WEEX": ["https://www.weex.com", "https://weex.com"],
    "MEXC": ["https://www.mexc.com", "https://mexc.com"],
    "Zoomex": ["https://www.zoomex.com", "https://zoomex.com"],
    "Tapbit": ["https://www.tapbit.com", "https://tapbit.com"],
    "Hibit": ["https://www.hibt.com", "https://hibt.com"],
}

# Pehle se maloom events pages (agar khul jaye to kaam aate hain, warna chhor diye jate hain)
EXTRA_PAGES = {
    "WEEX": ["https://www.weex.com/events"],
    "KCEX": ["https://www.kcex.com/en-US/promotions"],
    "Bitunix": ["https://www.bitunix.com/promo"],
    "Toobit": ["https://www.toobit.com/en-US/promotions"],
    "XT": ["https://www.xt.com/en/promotions"],
    "BloFin": ["https://www.blofin.com/promotions"],
    "MEXC": ["https://www.mexc.com/events"],
}

MAX_PAGES_PER_EXCHANGE = 5
MAX_DETAIL_PAGES = 25      # ek run mein har exchange ke itne event pages khulenge
CONCURRENCY = 3

STRONG_KW = re.compile(r"event|promo|activit|campaign", re.I)
WEAK_KW = re.compile(r"reward|bonus|airdrop|contest|competition|carnival|festival", re.I)
SKIP_LINK = re.compile(
    r"login|log-in|signin|sign-in|register|signup|sign-up|\.(pdf|jpg|jpeg|png|gif|svg|zip)(\?|$)|^mailto:|^javascript:|^tel:",
    re.I,
)
JUNK = re.compile(
    r"cookie|privacy|terms|copyright|all rights reserved|sign up|log in|login|register|download|"
    r"about us|contact us|careers|support|help center|view more|learn more|read more|"
    r"customer service|disclaimer|risk warning|language|subscribe|follow us|my events|"
    r"buy crypto|markets|futures|spot|academy|affiliate",
    re.I,
)

TITLE_SELECTOR = (
    "h1, h2, h3, h4, [class*='title'], [class*='Title'], "
    "[class*='event'], [class*='Event'], [class*='promo'], [class*='Promo'], "
    "[class*='activity'], [class*='Activity']"
)

# Page ke andar chalne wala JS: titles + links nikalta hai
PAGE_JS = r"""
(sel) => {
  const out = [];
  const abs = (h) => { try { return new URL(h, location.href).href; } catch (e) { return null; } };
  const kw = /event|promo|activit|campaign|reward|bonus|airdrop|contest|competition|carnival|festival/i;
  const BAD = /logo|icon|avatar|sprite|flag|arrow|qrcode|\.svg(\?|$)/i;

  // root ke andar sab se bari asli photo dhoondta hai
  const imgOf = (root) => {
    if (!root) return null;
    let best = null, bestScore = 0;
    const list = (root.tagName === 'IMG') ? [root] : Array.from(root.querySelectorAll('img'));
    list.forEach((im) => {
      let src = im.currentSrc || im.src || im.getAttribute('data-src') || im.getAttribute('data-lazy-src') || im.getAttribute('data-original') || '';
      if (!src && im.getAttribute('srcset')) src = im.getAttribute('srcset').split(',')[0].trim().split(' ')[0];
      if (!src || src.startsWith('data:') || BAD.test(src)) return;
      const w = im.naturalWidth || im.width || 0, h = im.naturalHeight || im.height || 0;
      const score = w * h;
      if (score >= 15000 && score > bestScore) { best = src; bestScore = score; }
    });
    if (best) return abs(best);
    const els = [root].concat(Array.from(root.querySelectorAll('*')).slice(0, 60));
    for (const e of els) {
      let bg = '';
      try { bg = getComputedStyle(e).backgroundImage; } catch (x) {}
      const m = bg && bg.match(/url\(["']?(.*?)["']?\)/);
      if (m && m[1] && !m[1].startsWith('data:') && !BAD.test(m[1])) return abs(m[1]);
    }
    return null;
  };

  // kya is node mein is element jaise aur bhi cards hain? (to ye poori list hai, ek card nahi)
  const multi = (node, el) => {
    if (el.tagName === 'A') {
      return Array.from(node.querySelectorAll('a[href]')).filter((x) =>
        x !== el && kw.test(x.getAttribute('href') || '') && x.href !== el.href).length > 0;
    }
    let q = el.tagName.toLowerCase();
    const c = (typeof el.className === 'string') ? el.className.trim().split(/\s+/)[0] : '';
    if (c) q += '.' + CSS.escape(c);
    return node.querySelectorAll(q).length > 1;
  };

  // element se upar chadh kar sab se chhota card dhoondta hai jisme photo ho
  const cardInfo = (el, title) => {
    let node = el, image = null, box = null;
    for (let i = 0; i < 6 && node; i++) {
      if ((node.innerText || '').length > 700) break;
      if (node !== el && multi(node, el)) break;
      const im = imgOf(node);
      if (im) { image = im; box = node; break; }
      node = node.parentElement;
    }
    let src = box;
    if (!src && el.parentElement && !multi(el.parentElement, el)) src = el.parentElement;
    let desc = '';
    if (src && (src.innerText || '').length <= 700) {
      const lines = (src.innerText || '').split('\n').map((x) => x.trim())
        .filter((x) => x.length >= 15 && x.length <= 220 && x !== title);
      desc = lines.slice(0, 2).join(' ');
    }
    return { i: image, d: desc };
  };

  let budget = 250;
  document.querySelectorAll(sel).forEach((el) => {
    const t = (el.innerText || '').trim();
    const a = el.closest('a');
    let info = { i: null, d: '' };
    if (t.length >= 10 && t.length <= 150 && t.indexOf('\n') === -1 && budget > 0) { budget--; info = cardInfo(el, t); }
    out.push({ t: t, h: a ? abs(a.getAttribute('href')) : null, k: 'title', i: info.i, d: info.d });
  });
  document.querySelectorAll('a[href]').forEach((a) => {
    const h = abs(a.getAttribute('href'));
    if (!h) return;
    const img = a.querySelector('img');
    let t = (a.innerText || '').trim();
    if (!t && img) t = (img.alt || img.title || '').trim();
    if (!t) t = (a.getAttribute('aria-label') || a.title || '').trim();
    const k = kw.test(h) ? 'kwlink' : 'link';
    let info = { i: null, d: '' };
    if (k === 'kwlink' && t.length >= 10 && budget > 0) {
      budget--;
      info = cardInfo(a, t.split('\n')[0].trim());
      if (!info.i) info.i = imgOf(a);
    }
    out.push({ t: t, h: h, k: k, i: info.i, d: info.d });
  });
  return out;
}
"""

DETAIL_JS = r"""
() => {
  const abs = (h) => { try { return new URL(h, location.href).href; } catch (e) { return null; } };
  const meta = (n) => {
    const m = document.querySelector('meta[property="' + n + '"],meta[name="' + n + '"]');
    return m ? (m.getAttribute('content') || '').trim() : '';
  };
  const BAD = /logo|icon|avatar|sprite|flag|arrow|qrcode|\.svg(\?|$)/i;
  const imgs = Array.from(document.querySelectorAll('img')).map((im) => {
    let src = im.currentSrc || im.src || im.getAttribute('data-src') || '';
    return { s: src, w: im.naturalWidth || 0, h: im.naturalHeight || 0, a: im.alt || '' };
  }).filter((x) => x.s && !x.s.startsWith('data:') && !BAD.test(x.s));
  const og = meta('og:image') || meta('twitter:image') || '';
  return {
    og: og ? abs(og) : null,
    desc: meta('og:description') || meta('description'),
    imgs: imgs.slice(0, 40).map((x) => ({ s: abs(x.s), w: x.w, h: x.h, a: x.a }))
  };
}
"""

GENERIC_IMG = re.compile(r"open_graph|og[-_]default|default[-_]og|share[-_]default|placeholder|default[-_]share", re.I)


def choose_image(data, title):
    """Event page ki sab se munasib bari photo (logo/icon nahi)."""
    best, best_score = None, 0
    words = [w for w in re.findall(r"[a-z0-9]+", (title or "").lower()) if len(w) > 3]
    for im in data.get("imgs") or []:
        src = im.get("s")
        area = (im.get("w") or 0) * (im.get("h") or 0)
        if not src or area < 20000:
            continue
        alt = (im.get("a") or "").lower()
        score = area + (800000 if any(w in alt for w in words) else 0) + (300000 if re.search(r"banner|cover|hero|poster", src, re.I) else 0)
        if score > best_score:
            best, best_score = src, score
    return best


STEALTH_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"


def generate_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def base_domain(url):
    host = urllib.parse.urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def same_site(url, base):
    host = urllib.parse.urlparse(url).netloc.lower()
    return host == base or host.endswith("." + base)


def clean_title(raw, allow_multiline=False):
    raw = (raw or "").strip()
    if "\n" in raw:
        if not allow_multiline:
            return None
        lines = [l.strip() for l in raw.split("\n") if len(l.strip()) >= 10]
        raw = lines[0] if lines else ""
    text = re.sub(r"\s+", " ", raw)
    if len(text) < 10 or len(text) > 150:
        return None
    if not re.search(r"[A-Za-z]", text):
        return None
    if re.fullmatch(r"[\d\sDdHhMmSs:.\-/]+", text):   # countdown jaise 00D00H00M00S
        return None
    if JUNK.search(text):
        return None
    return text


async def settle(page):
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    for _ in range(3):
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            break
        await page.wait_for_timeout(1200)


async def open_page(page, url):
    """True return karta hai agar page theek khula."""
    resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    if resp is not None and resp.status >= 400:
        return False
    await page.wait_for_timeout(2500)
    return True


async def discover_links(page, candidates, log):
    """Homepage kholta hai aur event/promo wale links dhoondta hai."""
    for home in candidates:
        try:
            if not await open_page(page, home):
                log["errors"].append(f"{home}: HTTP error")
                continue
            await settle(page)
            items = await page.evaluate(PAGE_JS, TITLE_SELECTOR)
        except Exception as e:
            log["errors"].append(f"{home}: {str(e)[:70]}")
            continue

        base = base_domain(page.url or home)
        scored = {}
        for it in items:
            href = (it.get("h") or "").split("#")[0]
            if not href.startswith("http") or SKIP_LINK.search(href):
                continue
            if not same_site(href, base):
                continue
            text = it.get("t") or ""
            score = 0
            if STRONG_KW.search(href):
                score += 2
            if STRONG_KW.search(text):
                score += 1
            if WEAK_KW.search(href) or WEAK_KW.search(text):
                score += 1
            if score > 0:
                scored[href] = max(score, scored.get(href, 0))
        ranked = [u for u, _ in sorted(scored.items(), key=lambda x: -x[1])]
        return home, ranked
    return None, []


def clean_desc(raw, title):
    text = re.sub(r"\s+", " ", (raw or "")).strip()
    if len(text) < 20:
        return None
    if title and (text == title or text in title):
        return None
    if JUNK.search(text):
        return None
    return text[:220]


def titles_from_items(items, page_url, strict):
    """items se {title: {link, image, desc}} banata hai."""
    found = {}
    for it in items:
        kind = it.get("k")
        if kind == "link":
            continue
        if strict and kind != "kwlink":
            continue
        title = clean_title(it.get("t"), allow_multiline=(kind == "kwlink"))
        if not title:
            continue
        link = it.get("h") or page_url
        specific = bool(it.get("h")) and not SKIP_LINK.search(link or "")
        if not specific:
            link = page_url
        img = it.get("i") or None
        desc = clean_desc(it.get("d"), title)
        cur = found.get(title)
        if cur is None:
            found[title] = {"link": link, "image": img, "desc": desc, "specific": specific}
        else:
            if specific and not cur["specific"]:
                cur["link"], cur["specific"] = link, True
            if not cur["image"] and img:
                cur["image"] = img
            if not cur["desc"] and desc:
                cur["desc"] = desc
    return found


async def extract_titles(page, url, log, strict=False):
    try:
        if not await open_page(page, url):
            log["errors"].append(f"{url[:60]}: HTTP error")
            return {}
        await settle(page)
        items = await page.evaluate(PAGE_JS, TITLE_SELECTOR)
        log["pages_opened"] += 1
    except Exception as e:
        log["errors"].append(f"{url[:60]}: {str(e)[:60]}")
        return {}
    return titles_from_items(items, page.url or url, strict)


def load_existing(name):
    try:
        res = (
            supabase.table("events")
            .select("event_hash,image_url,description")
            .eq("exchange_name", name)
            .execute()
        )
        return {r["event_hash"]: r for r in (res.data or [])}
    except Exception as e:
        print(f"  [!] select error ({name}): {str(e)[:100]}")
        return {}


def sync_events(name, found, existing):
    """Naye events insert karta hai, purane mein photo/description bharta hai. Naye ki ginti return karta hai.
    image_url == '' ka matlab: event page dekha ja chuka, photo nahi mili (dobara koshish nahi hogi)."""
    new = 0
    for title, info in found.items():
        h = generate_hash(f"{name}_{title}")
        base = {
            "exchange_name": name,
            "event_name": title,
            "event_url": info["link"],
            "event_hash": h,
        }
        full = dict(base, image_url=info["image"], description=info["desc"])
        cur = existing.get(h)
        try:
            if cur is None:
                try:
                    r = supabase.table("events").upsert(
                        full, on_conflict="event_hash", ignore_duplicates=True
                    ).execute()
                except Exception:
                    # SQL (image_url/description columns) abhi nahi chali to bina photo ke save karo
                    r = supabase.table("events").upsert(
                        base, on_conflict="event_hash", ignore_duplicates=True
                    ).execute()
                if r.data:
                    new += 1
                    print(f"  [+] {name} NEW: {title}")
            else:
                upd = {}
                cur_img = cur.get("image_url")
                if info["image"] and not cur_img:
                    upd["image_url"] = info["image"]
                elif info["image"] == "" and cur_img is None:
                    upd["image_url"] = ""
                if info["desc"] and not cur.get("description"):
                    upd["description"] = info["desc"]
                if upd:
                    supabase.table("events").update(upd).eq("event_hash", h).execute()
        except Exception as e:
            print(f"  [!] DB error ({name}): {str(e)[:100]}")
    return new


async def enrich_with_detail_pages(page, name, found, existing, listing_urls, log):
    """Jin events ki photo listing se nahi mili, unka apna page khol kar photo/description nikalta hai."""
    targets = []
    for title, info in found.items():
        if info["image"]:
            continue
        cur = existing.get(generate_hash(f"{name}_{title}"))
        if cur is not None and cur.get("image_url") is not None:
            continue  # pehle try ho chuka ya photo mil chuki
        link = info["link"]
        if not info.get("specific") or not link.startswith("http") or link in listing_urls:
            continue
        targets.append((title, link))
    targets = targets[:MAX_DETAIL_PAGES]

    results = {}
    for title, link in targets:
        try:
            if not await open_page(page, link):
                results[title] = {}  # HTTP error: photo nahi, dobara try nahi
                continue
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            results[title] = await page.evaluate(DETAIL_JS)
        except Exception as e:
            log["errors"].append(f"detail {link[:50]}: {str(e)[:50]}")
            # exception par marker nahi lagta, agli run mein dobara koshish hogi

    og_count = {}
    desc_count = {}
    for data in results.values():
        if data.get("og"):
            og_count[data["og"]] = og_count.get(data["og"], 0) + 1
        if data.get("desc"):
            desc_count[data["desc"]] = desc_count.get(data["desc"], 0) + 1

    for title, data in results.items():
        info = found[title]
        img = None
        og = data.get("og")
        if og and og_count.get(og) == 1 and not GENERIC_IMG.search(og):
            img = og
        if not img:
            img = choose_image(data, title)
        info["image"] = img or ""
        d = data.get("desc")
        if d and desc_count.get(d) == 1 and not info["desc"]:
            info["desc"] = clean_desc(d, title)
    log["detail_pages"] = len(results)


async def scrape_exchange(context, name, candidates, sem):
    async with sem:
        log = {"pages_opened": 0, "found": 0, "new": 0, "errors": [], "home": None, "with_image": 0, "detail_pages": 0}
        page = await context.new_page()
        try:
            home, discovered = await discover_links(page, candidates, log)
            log["home"] = home

            pages = []
            for u in EXTRA_PAGES.get(name, []) + discovered:
                if u not in pages:
                    pages.append(u)
            pages = pages[:MAX_PAGES_PER_EXCHANGE]

            all_titles = {}

            def merge(found):
                for t, info in found.items():
                    cur = all_titles.get(t)
                    if cur is None:
                        all_titles[t] = dict(info)
                    else:
                        if info.get("specific") and not cur.get("specific"):
                            cur["link"], cur["specific"] = info["link"], True
                        if not cur["image"] and info["image"]:
                            cur["image"] = info["image"]
                        if not cur["desc"] and info["desc"]:
                            cur["desc"] = info["desc"]

            for url in pages:
                merge(await extract_titles(page, url, log))

            # Kuch na mile to homepage ke banners/links (strict mode) aazmayen
            if not all_titles and home:
                merge(await extract_titles(page, home, log, strict=True))

            existing = load_existing(name)
            listing_urls = set(EXTRA_PAGES.get(name, [])) | ({home} if home else set())
            await enrich_with_detail_pages(page, name, all_titles, existing, listing_urls, log)

            log["found"] = len(all_titles)
            log["with_image"] = sum(1 for v in all_titles.values() if v["image"])
            log["new"] = sync_events(name, all_titles, existing)
        except Exception as e:
            log["errors"].append(str(e)[:80])
        finally:
            await page.close()

        print(
            f"[{name}] home={log['home']} pages={log['pages_opened']} detail={log['detail_pages']} "
            f"events={log['found']} photos={log['with_image']} new={log['new']}"
        )
        for err in log["errors"][:3]:
            print(f"   [warn] {err}")
        return log


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        await context.add_init_script(STEALTH_JS)

        sem = asyncio.Semaphore(CONCURRENCY)
        results = await asyncio.gather(
            *[scrape_exchange(context, n, c, sem) for n, c in EXCHANGES.items()]
        )
        await browser.close()

    print("\n========== SUMMARY ==========")
    for (name, _), log in zip(EXCHANGES.items(), results):
        status = "OK  " if log["found"] > 0 else "FAIL"
        print(
            f"[{status}] {name:9} pages={log['pages_opened']} "
            f"events={log['found']} photos={log['with_image']} new={log['new']}"
        )


if __name__ == "__main__":
    asyncio.run(main())
