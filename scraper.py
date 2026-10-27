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

# Har exchange ka homepage + (optional) pehle se maloom events pages
EXCHANGES = {
    "WEEX": {"home": "https://www.weex.com", "extra": ["https://www.weex.com/events"]},
    "KCEX": {"home": "https://www.kcex.com", "extra": ["https://www.kcex.com/en-US/promotions"]},
    "Bitunix": {"home": "https://www.bitunix.com", "extra": ["https://www.bitunix.com/promo"]},
    "Toobit": {"home": "https://www.toobit.com", "extra": ["https://www.toobit.com/en-US/promotions"]},
    "LBank": {"home": "https://www.lbank.com", "extra": ["https://www.lbank.com/new-popular-events"]},
    "XT": {"home": "https://www.xt.com", "extra": ["https://www.xt.com/en/promotions"]},
    "Ourbit": {"home": "https://www.ourbit.com", "extra": ["https://www.ourbit.com/promotions"]},
    "Phemex": {"home": "https://phemex.com", "extra": ["https://phemex.com/promo"]},
    "BloFin": {"home": "https://www.blofin.com", "extra": ["https://www.blofin.com/promotions"]},
}

MAX_PAGES_PER_EXCHANGE = 6

# Link dhoondne ke liye keywords
STRONG_KW = re.compile(r"event|promo|activit|campaign", re.I)
WEAK_KW = re.compile(r"reward|bonus|airdrop|contest|competition|carnival|festival", re.I)
SKIP_LINK = re.compile(
    r"login|log-in|signin|sign-in|register|signup|sign-up|\.(pdf|jpg|jpeg|png|gif|svg|zip)(\?|$)|^mailto:|^javascript:|^tel:",
    re.I,
)

# Titles nikalne ke selectors
TITLE_SELECTOR = (
    "h1, h2, h3, h4, [class*='title'], [class*='Title'], "
    "[class*='event'], [class*='Event'], [class*='promo'], [class*='Promo'], "
    "[class*='activity'], [class*='Activity']"
)

# Faltu text jo event nahi hota
JUNK = re.compile(
    r"cookie|privacy|terms|copyright|all rights reserved|sign up|log in|login|register|download|"
    r"about us|contact us|careers|support|help center|view more|learn more|read more|"
    r"customer service|disclaimer|risk warning|language|subscribe|follow us|my events|"
    r"buy crypto|markets|futures|spot|academy|affiliate",
    re.I,
)


def generate_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def base_domain(url):
    host = urllib.parse.urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def same_site(url, base):
    host = urllib.parse.urlparse(url).netloc.lower()
    return host == base or host.endswith("." + base)


def clean_title(raw):
    raw = (raw or "").strip()
    if "\n" in raw:  # bade containers chhod do, sirf single-line titles
        return None
    text = re.sub(r"\s+", " ", raw)
    if len(text) < 10 or len(text) > 150:
        return None
    if not re.search(r"[A-Za-z]", text):
        return None
    if JUNK.search(text):
        return None
    return text


async def discover_links(page, home, log):
    """Homepage khol kar event/promo wale links dhoondta hai."""
    base = base_domain(home)
    try:
        await page.goto(home, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        anchors = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => [e.href, (e.innerText || '').trim()])",
        )
    except Exception as e:
        log["errors"].append(f"homepage: {str(e)[:80]}")
        return []

    scored = {}
    for href, text in anchors:
        href = href.split("#")[0]
        if not href.startswith("http") or SKIP_LINK.search(href):
            continue
        if not same_site(href, base):
            continue
        score = 0
        if STRONG_KW.search(href):
            score += 2
        if STRONG_KW.search(text):
            score += 1
        if WEAK_KW.search(href) or WEAK_KW.search(text):
            score += 1
        if score > 0:
            scored[href] = max(score, scored.get(href, 0))

    ranked = sorted(scored.items(), key=lambda x: -x[1])
    return [u for u, _ in ranked]


async def extract_titles(page, url, log):
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        raws = await page.eval_on_selector_all(
            TITLE_SELECTOR, "els => els.map(e => (e.innerText || '').trim())"
        )
        log["pages_opened"] += 1
    except Exception as e:
        log["errors"].append(f"{url[:60]}: {str(e)[:60]}")
        return []

    titles, seen = [], set()
    for raw in raws:
        t = clean_title(raw)
        if t and t not in seen:
            seen.add(t)
            titles.append(t)
    return titles


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
        print(f"  [!] DB error: {str(e)[:100]}")
        return False


async def scrape_exchange(context, name, cfg):
    print(f"\n=== {name} ===")
    log = {"pages_opened": 0, "found": 0, "new": 0, "errors": []}
    page = await context.new_page()

    discovered = await discover_links(page, cfg["home"], log)
    candidates = list(cfg.get("extra", []))
    for u in discovered:
        if u not in candidates:
            candidates.append(u)
    candidates = candidates[:MAX_PAGES_PER_EXCHANGE]
    print(f"  Links mile: {len(discovered)} | Pages try honge: {len(candidates)}")

    all_titles = {}
    for url in candidates:
        for t in await extract_titles(page, url, log):
            all_titles.setdefault(t, url)

    log["found"] = len(all_titles)
    for title, url in all_titles.items():
        if save_event(name, title, url):
            log["new"] += 1
            print(f"  [+] NEW: {title}")

    await page.close()
    print(
        f"  Result: pages khuli={log['pages_opened']}, events mile={log['found']}, naye={log['new']}"
    )
    for err in log["errors"][:3]:
        print(f"  [warn] {err}")
    return log


async def main():
    summary = {}
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
        )
        for name, cfg in EXCHANGES.items():
            try:
                summary[name] = await scrape_exchange(context, name, cfg)
            except Exception as e:
                print(f"Error scraping {name}: {e}")
                summary[name] = {"pages_opened": 0, "found": 0, "new": 0, "errors": [str(e)]}
        await browser.close()

    print("\n========== SUMMARY ==========")
    for name, log in summary.items():
        status = "OK " if log["found"] > 0 else "FAIL"
        print(
            f"[{status}] {name:8} pages={log['pages_opened']} "
            f"events={log['found']} new={log['new']}"
        )


if __name__ == "__main__":
    asyncio.run(main())
