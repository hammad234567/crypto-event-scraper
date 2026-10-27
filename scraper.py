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
PAGE_JS = """
(sel) => {
  const out = [];
  const abs = (h) => { try { return new URL(h, location.href).href; } catch (e) { return null; } };
  const kw = /event|promo|activit|campaign|reward|bonus|airdrop|contest|competition|carnival|festival/i;
  document.querySelectorAll(sel).forEach((el) => {
    const t = (el.innerText || '').trim();
    const a = el.closest('a');
    out.push({ t: t, h: a ? abs(a.getAttribute('href')) : null, k: 'title' });
  });
  document.querySelectorAll('a[href]').forEach((a) => {
    const h = abs(a.getAttribute('href'));
    if (!h) return;
    const img = a.querySelector('img');
    let t = (a.innerText || '').trim();
    if (!t && img) t = (img.alt || img.title || '').trim();
    if (!t) t = (a.getAttribute('aria-label') || a.title || '').trim();
    out.push({ t: t, h: h, k: kw.test(h) ? 'kwlink' : 'link' });
  });
  return out;
}
"""

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


def titles_from_items(items, page_url, strict):
    """items se (title, link) ki list banata hai."""
    found = {}
    for it in items:
        kind = it.get("k")
        if kind == "link":
            continue
        if strict and kind != "kwlink":
            continue
        title = clean_title(it.get("t"), allow_multiline=(kind == "kwlink"))
        if not title or title in found:
            continue
        link = it.get("h") or page_url
        if SKIP_LINK.search(link or ""):
            link = page_url
        found[title] = link
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


def save_event(name, title, url):
    """True return karta hai agar event bilkul naya tha."""
    row = {
        "exchange_name": name,
        "event_name": title,
        "event_url": url,
        "event_hash": generate_hash(f"{name}_{title}"),
    }
    try:
        r = supabase.table("events").upsert(
            row, on_conflict="event_hash", ignore_duplicates=True
        ).execute()
        return bool(r.data)
    except Exception as e:
        print(f"  [!] DB error ({name}): {str(e)[:100]}")
        return False


async def scrape_exchange(context, name, candidates, sem):
    async with sem:
        log = {"pages_opened": 0, "found": 0, "new": 0, "errors": [], "home": None}
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
            for url in pages:
                for t, link in (await extract_titles(page, url, log)).items():
                    all_titles.setdefault(t, link)

            # Kuch na mile to homepage ke banners/links (strict mode) aazmayen
            if not all_titles and home:
                for t, link in (await extract_titles(page, home, log, strict=True)).items():
                    all_titles.setdefault(t, link)

            log["found"] = len(all_titles)
            for title, link in all_titles.items():
                if save_event(name, title, link):
                    log["new"] += 1
                    print(f"  [+] {name} NEW: {title}")
        except Exception as e:
            log["errors"].append(str(e)[:80])
        finally:
            await page.close()

        print(
            f"[{name}] home={log['home']} pages={log['pages_opened']} "
            f"events={log['found']} new={log['new']}"
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
            f"events={log['found']} new={log['new']}"
        )


if __name__ == "__main__":
    asyncio.run(main())
