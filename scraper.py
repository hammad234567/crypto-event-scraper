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
    "LBank": ["https://www.lbank.com", "https://lbank.com"],
}

# Pehle se maloom promotion / events pages (khul jayein to kaam aate hain, warna chhor diye jate hain)
EXTRA_PAGES = {
    "WEEX": ["https://www.weex.com/events",
             "https://wlakixx.online/rewards?type=activities&languageType=0&appTheme=standard&qrType=activity"],
    "KCEX": ["https://www.kcex.com/en-US/promotions", "https://www.kcex.com/futures/reward_center"],
    "Bitunix": ["https://www.bitunix.com/promo"],
    "Toobit": ["https://www.toobit.com/en-US/promotions"],
    "XT": ["https://www.xt.com/en/promotions"],
    "BloFin": ["https://www.blofin.com/promotions"],
    "MEXC": ["https://www.mexc.com/events"],
    "LBank": ["https://www.lbank.com/new-popular-events", "https://www.lbank.com/bonus/center"],
    "Zoomex": ["https://m.zoomex.com/en/task-center/rewards_hub", "https://www.zoomex.com/en/promotions"],
}

# Welcome bonus pages (homepage ke ilawa ye bhi dekhe jayenge)
WELCOME_PATHS = ["/welcome", "/newbie", "/newcomer", "/rewards", "/en/welcome", "/en/newbie"]
WELCOME_RE = (r"(welcome|new user|new member|newbie|newcomer|sign.?up|register|first deposit)[^\n]{0,90}"
              r"(bonus|reward|gift|voucher|usdt|gift)|up to \$?[\d,]+\s?(usdt|usd|\$)")


# Kuch na mile to aam promotion pages ke raaste azmaye jayenge
PROBE_PATHS = [
    "/promotions", "/promo", "/events", "/activity", "/activities",
    "/campaign", "/rewards", "/welcome", "/bonus", "/en/promotions", "/en/events",
]

MAX_PAGES_PER_EXCHANGE = 8    # ek exchange ke itne pages khulenge
MAX_HUBS_PER_PAGE = 5         # ek page se aage itne promo-hub links follow honge
MAX_DEPTH = 2
MAX_DETAIL_PAGES = 25         # jin events ki photo nahi mili unke apne pages
MAX_EVENTS_PER_EXCHANGE = 60
MIN_CARDS = 3                 # itne repeated cards hon tabhi ye "promotions ki list" maani jayegi
CONCURRENCY = 3

KW = re.compile(
    r"event|promo|activit|campaign|reward|bonus|airdrop|contest|competition|carnival|festival|"
    r"welcome|newbie|newcomer|giveaway|lottery|trial|gift", re.I)
STRONG_KW = re.compile(r"event|promo|activit|campaign|welcome|newbie|newcomer", re.I)
WEAK_KW = re.compile(r"reward|bonus|airdrop|contest|competition|carnival|festival|giveaway|gift", re.I)
HUB_SEG = re.compile(
    r"^(events?|promos?|promotions?|activit(y|ies)|campaigns?|rewards?|bonus(es)?|welcome|newbie|"
    r"en|en-us|en_us|zh|zh-cn|ja|ko|index|home|list|all|center|hub|new)$", re.I)
SKIP_LINK = re.compile(
    r"(^|/)(login|log-in|signin|sign-in|register|signup|sign-up)(/|\?|$)|"
    r"\.(pdf|jpg|jpeg|png|gif|svg|zip)(\?|$)|^mailto:|^javascript:|^tel:",
    re.I,
)
# Ye sab "promotion" nahi: nav, legal, announcements, listing/maintenance notices wagera
JUNK = re.compile(
    r"cookie|privacy|terms|copyright|all rights reserved|sign up|log in|login|register now|download|"
    r"about us|contact us|careers|support|help center|view more|learn more|read more|"
    r"customer service|disclaimer|risk warning|language|subscribe|follow us|my events|"
    r"academy|affiliate program|faq|rules|how to |tutorial|announcement|notice|maintenance|"
    r"delist|will list|will launch|new listing|trading pairs?|system upgrade|wallet",
    re.I,
)

# Page ke andar chalne wala JS. Sirf teen cheezein deta hai:
#   ev   = promo/event ke slug wale links (card ke saath photo + detail)
#   card = bina link ke bhi image+title wale repeated cards (promotion list)
#   hub  = aage follow karne layak promotions/events/welcome pages ke links
PAGE_JS = r"""
() => {
  const out = [];
  const abs = (h) => { try { return new URL(h, location.href).href; } catch (e) { return null; } };
  const KW = /(event|promo|activit|campaign|reward|bonus|airdrop|contest|competition|carnival|festival|welcome|newbie|newcomer|giveaway|lottery|trial|gift)/i;
  const HUB = /^(events?|promos?|promotions?|activit(y|ies)|campaigns?|rewards?|bonus(es)?|welcome|newbie|en|en-us|en_us|zh|zh-cn|ja|ko|index|home|list|all|center|hub|new)$/i;
  const BAD = /logo|icon|avatar|sprite|flag|arrow|qrcode|\.svg(\?|$)/i;

  const evHref = (h) => {
    let u; try { u = new URL(h); } catch (e) { return false; }
    const segs = u.pathname.split('/').filter(Boolean);
    if (!segs.length) return false;
    if (!KW.test(u.pathname + u.search)) return false;
    if (!HUB.test(segs[segs.length - 1])) return true;
    return /[?&](id|activityid|eventid|aid|code)=/i.test(u.search);
  };
  const srcOf = (im) => {
    let s = im.currentSrc || im.src || im.getAttribute('data-src') || im.getAttribute('data-lazy-src') || im.getAttribute('data-original') || '';
    if (!s && im.getAttribute('srcset')) s = im.getAttribute('srcset').split(',')[0].trim().split(' ')[0];
    return s;
  };

  // root ke andar sab se bari asli photo
  const imgOf = (root) => {
    if (!root) return null;
    let best = null, bestScore = 0;
    const list = (root.tagName === 'IMG') ? [root] : Array.from(root.querySelectorAll('img'));
    list.forEach((im) => {
      const src = srcOf(im);
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

  // kya is node mein is jaise aur bhi event-cards hain? (to ye poori list hai, ek card nahi)
  const multi = (node, el) => {
    return Array.from(node.querySelectorAll('a[href]')).some((x) => x !== el && x.href !== el.href && evHref(x.href));
  };
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

  // 1) links: ev (event-like) ya hub (promotions page ka rasta)
  let budget = 300;
  document.querySelectorAll('a[href]').forEach((a) => {
    const h = abs(a.getAttribute('href'));
    if (!h) return;
    const ev = evHref(h);
    const img = a.querySelector('img');
    let t = (a.innerText || '').trim();
    if (!t && img) t = (img.alt || img.title || '').trim();
    if (!t) t = (a.getAttribute('aria-label') || a.title || '').trim();
    let info = { i: null, d: '' };
    if (ev && budget > 0) {
      budget--;
      info = cardInfo(a, t.split('\n')[0].trim());
      if (!info.i) info.i = imgOf(a);
    }
    let k = 'link';
    if (ev) k = 'ev';
    else if (KW.test(h) || (KW.test(t) && t.length <= 40)) k = 'hub';
    out.push({ t: t, h: h, k: k, i: info.i, d: info.d });
  });

  // 2) bina link ke cards: bari image + title wale repeated blocks
  const big = (im) => { const r = im.getBoundingClientRect(); return r.width >= 120 && r.width * r.height >= 20000; };
  const bigImgs = Array.from(document.images).filter((im) => {
    const s = srcOf(im);
    return big(im) && s && !s.startsWith('data:') && !BAD.test(s);
  });
  const countBig = (n) => bigImgs.filter((im) => n.contains(im)).length;
  const firstClass = (n) => (n && typeof n.className === 'string') ? n.className.trim().split(/\s+/)[0] : '';
  const groups = {};
  bigImgs.forEach((im) => {
    if (im.closest('header, footer, nav')) return;
    let node = im, box = null;
    for (let i = 0; i < 6; i++) {
      const p = node.parentElement;
      if (!p || countBig(p) > 1) break;
      node = p;
      const len = (node.innerText || '').trim().length;
      if (len > 500) break;
      if (len >= 10) box = node;
    }
    if (!box) return;
    const sig = box.tagName + '.' + (firstClass(box) || firstClass(box.parentElement));
    (groups[sig] = groups[sig] || []).push({ box: box, im: im });
  });
  Object.keys(groups).forEach((sig) => {
    const g = groups[sig];
    if (g.length < 3) return;
    g.slice(0, 60).forEach((m) => {
      const a = m.box.closest('a[href]') || m.box.querySelector('a[href]');
      out.push({ t: (m.box.innerText || '').trim(), h: a ? abs(a.getAttribute('href')) : null, k: 'card', i: abs(srcOf(m.im)), d: '' });
    });
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
  const h1 = document.querySelector('h1');
  return {
    title: meta('og:title') || (h1 ? (h1.innerText || '').trim() : '') || document.title || '',
    og: og ? abs(og) : null,
    desc: meta('og:description') || meta('description'),
    imgs: imgs.slice(0, 40).map((x) => ({ s: abs(x.s), w: x.w, h: x.h, a: x.a }))
  };
}
"""

WELCOME_JS = r"""
(re) => {
  const rx = new RegExp(re, 'i');
  const abs = (h) => { try { return new URL(h, location.href).href; } catch (e) { return null; } };
  const out = [], seen = new Set();
  document.querySelectorAll('h1,h2,h3,h4,h5,p,span,div,a,li').forEach((el) => {
    if (el.children.length > 4) return;
    const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (t.length < 12 || t.length > 120 || !rx.test(t) || seen.has(t)) return;
    seen.add(t);
    const a = el.closest('a');
    let box = el, img = null;
    for (let i = 0; i < 4 && box && !img; i++) { img = box.querySelector && box.querySelector('img'); box = box.parentElement; }
    const src = img ? (img.currentSrc || img.src || '') : '';
    out.push({ t: t, h: a ? abs(a.getAttribute('href')) : null,
               i: (src && !src.startsWith('data:') && !/logo|icon|\.svg/i.test(src)) ? abs(src) : '' });
  });
  return out.slice(0, 6);
}
"""

GENERIC_IMG = re.compile(r"open_graph|og[-_]default|default[-_]og|share[-_]default|placeholder|default[-_]share", re.I)
JUNK_WELCOME = re.compile(r"sign up now|register now|log in|login|download|cookie|terms|privacy", re.I)
STEALTH_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"


# ---------------------------------------------------------------- helpers
def generate_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def base_domain(url):
    host = urllib.parse.urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def same_site(url, base):
    host = urllib.parse.urlparse(url).netloc.lower()
    return host == base or host.endswith("." + base)


def norm_url(url):
    u = urllib.parse.urlparse((url or "").split("#")[0])
    return f"{u.scheme}://{u.netloc.lower()}{u.path.rstrip('/')}" + (f"?{u.query}" if u.query else "")


def short(url, n=70):
    return (url or "")[:n]


def is_event_url(url):
    """Kya ye link ek khaas promo/event ka hai (slug wala), na ke sirf 'events' hub page."""
    try:
        u = urllib.parse.urlparse(url)
    except Exception:
        return False
    segs = [s for s in u.path.split("/") if s]
    if not segs:
        return False
    if not KW.search(u.path + ("?" + u.query if u.query else "")):
        return False
    if not HUB_SEG.match(segs[-1]):
        return True
    return bool(re.search(r"(^|&)(id|activityid|eventid|aid|code)=", u.query, re.I))


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


def clean_desc(raw, title):
    text = re.sub(r"\s+", " ", (raw or "")).strip()
    if len(text) < 20:
        return None
    if title and (text == title or text in title):
        return None
    if JUNK.search(text):
        return None
    return text[:220]


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


def event_from_detail_page(data, page_url):
    """Ek event ke page se sirf us event ka apna title/photo/description."""
    raw = (data.get("title") or "").strip()
    parts = re.split(r"\s+[|–—]\s+|\s+-\s+", raw)
    title = clean_title(parts[0]) or clean_title(raw)
    if not title:
        return {}
    og = data.get("og")
    img = og if (og and not GENERIC_IMG.search(og)) else choose_image(data, title)
    desc = clean_desc(data.get("desc"), title)
    return {title: {"link": page_url, "image": img or "", "desc": desc, "specific": True, "detail": True}}


# ---------------------------------------------------------------- page parsing
def parse_page(items, page_url):
    """PAGE_JS ke items se: ev (link wale events), cards (bina link ke cards), hubs (aage ke pages)."""
    base = base_domain(page_url)
    here = norm_url(page_url)
    ev, cards, hubs = {}, {}, {}

    def add(store, title, link, specific, image, desc):
        cur = store.get(title)
        if cur is None:
            store[title] = {"link": link, "image": image or None, "desc": desc, "specific": specific}
            return
        if specific and not cur["specific"]:
            cur["link"], cur["specific"] = link, True
        if not cur["image"] and image:
            cur["image"] = image
        if not cur["desc"] and desc:
            cur["desc"] = desc

    for it in items:
        k = it.get("k")
        href = (it.get("h") or "").split("#")[0]
        if k == "ev":
            if not href.startswith("http") or SKIP_LINK.search(href) or norm_url(href) == here:
                continue
            title = clean_title(it.get("t"), allow_multiline=True)
            if not title:
                continue
            add(ev, title, href, True, it.get("i"), clean_desc(it.get("d"), title))
        elif k == "card":
            lines = [l.strip() for l in (it.get("t") or "").split("\n") if l.strip()]
            title, rest = None, []
            for idx, l in enumerate(lines):
                c = clean_title(l)
                if c:
                    title, rest = c, lines[idx + 1:]
                    break
            if not title:
                continue
            desc = clean_desc(" ".join(x for x in rest if len(x) >= 15)[:300], title)
            specific = (href.startswith("http") and not SKIP_LINK.search(href)
                        and same_site(href, base) and norm_url(href) != here)
            add(cards, title, href if specific else page_url, specific, it.get("i"), desc)
        elif k == "hub":
            if not href.startswith("http") or SKIP_LINK.search(href) or not same_site(href, base):
                continue
            if norm_url(href) == here or is_event_url(href):
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
                hubs[href] = max(score, hubs.get(href, 0))
    return {"ev": ev, "cards": cards, "hubs": hubs}


def pick_events(parsed):
    ev, cards = parsed["ev"], parsed["cards"]
    if len(ev) >= MIN_CARDS:
        return dict(ev), "links"
    if len(cards) >= MIN_CARDS:
        out = dict(cards)
        for t, info in ev.items():
            out.setdefault(t, info)
        return out, "cards"
    if ev:
        return dict(ev), "few-links"
    return {}, "none"


# ---------------------------------------------------------------- browser steps
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
    """(theek khula?, HTTP status)"""
    resp = await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    status = resp.status if resp is not None else 0
    if resp is not None and status >= 400:
        return False, status
    await page.wait_for_timeout(2500)
    return True, status


async def process_page(page, url, log):
    """Ek page kholta hai. (events, hubs, final_url, khula?) return karta hai."""
    try:
        ok, status = await open_page(page, url)
    except Exception as e:
        log["trace"].append(f"ERR  {short(url)}  {str(e)[:50]}")
        return {}, {}, url, False
    if not ok:
        log["trace"].append(f"HTTP {status}  {short(url)}")
        return {}, {}, url, False
    try:
        await settle(page)
        items = await page.evaluate(PAGE_JS)
        final = (page.url or url).split("#")[0]
        parsed = parse_page(items, final)
        events, kind = pick_events(parsed)
        if not events and is_event_url(final):
            data = await page.evaluate(DETAIL_JS)
            events = event_from_detail_page(data, final)
            kind = "single" if events else "none"
    except Exception as e:
        log["trace"].append(f"ERR  {short(url)}  {str(e)[:50]}")
        return {}, {}, url, False
    log["pages_opened"] += 1
    log["trace"].append(
        f"{status}  {short(final)}  links={len(parsed['ev'])} cards={len(parsed['cards'])} -> {len(events)} ({kind})"
    )
    return events, parsed["hubs"], final, True


# ---------------------------------------------------------------- database
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


async def enrich_with_detail_pages(page, name, found, existing, visited, log):
    """Jin events ki photo listing se nahi mili, unka apna page khol kar photo/description nikalta hai."""
    targets = []
    for title, info in found.items():
        if info["image"] or info.get("detail"):
            continue
        cur = existing.get(generate_hash(f"{name}_{title}"))
        if cur is not None and cur.get("image_url") is not None:
            continue  # pehle try ho chuka ya photo mil chuki
        link = info["link"]
        if not info.get("specific") or not link.startswith("http") or norm_url(link) in visited:
            continue
        targets.append((title, link))
    targets = targets[:MAX_DETAIL_PAGES]

    results = {}
    for title, link in targets:
        try:
            ok, _ = await open_page(page, link)
            if not ok:
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

    og_count, desc_count = {}, {}
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


# ---------------------------------------------------------------- one exchange
async def scrape_exchange(context, name, candidates, sem):
    async with sem:
        log = {"pages_opened": 0, "found": 0, "new": 0, "errors": [], "home": None,
               "with_image": 0, "detail_pages": 0, "trace": []}
        page = await context.new_page()
        try:
            all_titles, by_link, keys = {}, {}, set()

            def merge(found):
                for t, info in found.items():
                    key = re.sub(r"\W+", " ", t.lower()).strip()
                    cur = all_titles.get(t)
                    if cur is None and info.get("specific"):
                        t0 = by_link.get(info["link"])
                        if t0:
                            cur = all_titles[t0]   # wohi event, doosre title ke saath
                    if cur is None:
                        if key in keys or len(all_titles) >= MAX_EVENTS_PER_EXCHANGE:
                            continue
                        keys.add(key)
                        all_titles[t] = dict(info)
                        if info.get("specific"):
                            by_link[info["link"]] = t
                        continue
                    if info.get("specific") and not cur.get("specific"):
                        cur["link"], cur["specific"] = info["link"], True
                        by_link[info["link"]] = t
                    if info.get("detail"):
                        cur["detail"] = True
                    if not cur["image"] and info["image"]:
                        cur["image"] = info["image"]
                    if not cur["desc"] and info["desc"]:
                        cur["desc"] = info["desc"]

            visited = set()
            queue = []          # (url, depth, score)

            def enqueue(url, depth, score):
                n = norm_url(url)
                if n in visited or any(norm_url(q[0]) == n for q in queue):
                    return
                queue.append((url, depth, score))

            # 1) homepage (www / bina www jo khule)
            for cand in candidates:
                events, hubs, final, ok = await process_page(page, cand, log)
                visited.add(norm_url(cand))
                visited.add(norm_url(final))
                if ok:
                    log["home"] = final
                    merge(events)
                    for u, sc in sorted(hubs.items(), key=lambda x: -x[1])[:MAX_HUBS_PER_PAGE]:
                        enqueue(u, 1, sc)
                    break
            home = log["home"]

            # 2) maloom promotion pages pehle, phir homepage se mile hub links
            for u in EXTRA_PAGES.get(name, []):
                enqueue(u, 0, 99)

            while queue and log["pages_opened"] < MAX_PAGES_PER_EXCHANGE:
                queue.sort(key=lambda q: (-q[2], q[1]))
                url, depth, _ = queue.pop(0)
                if norm_url(url) in visited:
                    continue
                visited.add(norm_url(url))
                events, hubs, final, ok = await process_page(page, url, log)
                visited.add(norm_url(final))
                if not ok:
                    continue
                merge(events)
                if depth < MAX_DEPTH:
                    for u, sc in sorted(hubs.items(), key=lambda x: -x[1])[:MAX_HUBS_PER_PAGE]:
                        enqueue(u, depth + 1, sc)

            # 3) kuch bhi na mila: aam promotion raaste azmao
            if not all_titles and home:
                o = urllib.parse.urlparse(home)
                origin = f"{o.scheme}://{o.netloc}"
                for p in PROBE_PATHS:
                    if log["pages_opened"] >= MAX_PAGES_PER_EXCHANGE + 4:
                        break
                    u = origin + p
                    if norm_url(u) in visited:
                        continue
                    visited.add(norm_url(u))
                    events, _, final, ok = await process_page(page, u, log)
                    visited.add(norm_url(final))
                    if ok and events:
                        merge(events)
                        break

            # 4) welcome bonus: homepage + welcome pages. Amount/title badle to naya event ban jata hai
            try:
                wpages = [home] if home else []
                if home:
                    o = urllib.parse.urlparse(home)
                    wpages += [f"{o.scheme}://{o.netloc}{p}" for p in WELCOME_PATHS[:4]]
                got = 0
                for wu in wpages:
                    if got >= 2:
                        break
                    try:
                        resp = await page.goto(wu, wait_until="domcontentloaded", timeout=30000)
                        if resp is not None and resp.status >= 400:
                            continue
                        await page.wait_for_timeout(2500)
                        items = await page.evaluate(WELCOME_JS, WELCOME_RE)
                    except Exception:
                        continue
                    for it in items:
                        title = re.sub(r"\s+", " ", it.get("t") or "").strip()
                        if len(title) < 12 or JUNK_WELCOME.search(title) or got >= 2:
                            continue
                        merge({title: {"link": it.get("h") or page.url, "image": it.get("i") or "",
                                       "desc": "", "specific": bool(it.get("h"))}})
                        log["trace"].append(f"WELCOME {short(wu)} -> {title[:60]}")
                        got += 1
            except Exception as e:
                log["errors"].append("welcome: " + str(e)[:60])

            existing = load_existing(name)
            await enrich_with_detail_pages(page, name, all_titles, existing, visited, log)

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
        for line in log["trace"][:10]:
            print(f"   {line}")
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
    fails = [(n, l) for (n, _), l in zip(EXCHANGES.items(), results) if l["found"] == 0]
    if fails:
        print("\n---------- FAIL wale exchanges (wajah) ----------")
        for n, l in fails:
            print(f"{n}:")
            for line in l["trace"][:6] or ["(koi page nahi khula)"]:
                print(f"   {line}")


if __name__ == "__main__":
    asyncio.run(main())
