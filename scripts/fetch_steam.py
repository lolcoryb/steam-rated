#!/usr/bin/env python3
"""
Crawl recent Steam releases and write docs/data.json for the web app.

Steam's store cannot sort new releases by review tier. This pulls the review
summary Steam already computes for every game out of its search endpoint and
writes a single JSON file the static site reads.

Run locally:
    python scripts/fetch_steam.py --days 180

In CI this is driven by .github/workflows/refresh.yml on a daily schedule.
Standard library only, Python 3.8+.
"""

import argparse
import gzip
import html
import io
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# Steam's review tiers, best to worst. Rank is used for sorting in the app.
TIERS = [
    "Overwhelmingly Positive",
    "Very Positive",
    "Positive",
    "Mostly Positive",
    "Mixed",
    "Mostly Negative",
    "Negative",
    "Very Negative",
    "Overwhelmingly Negative",
]
TIER_RANK = {name: len(TIERS) - i for i, name in enumerate(TIERS)}

SEARCH_URL = "https://store.steampowered.com/search/results/"
APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
TAGS_URL = "https://store.steampowered.com/tagdata/populartags/english"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0 Safari/537.36")

# Non-game items that leak through Steam's "Games" category filter.
JUNK_PATTERNS = [
    r"\bdemo\b", r"\bsoundtrack\b", r"\bost\b", r"\bartbook\b", r"\bart book\b",
    r"supporter pack", r"\bdlc\b", r"season pass", r"\bplaytest\b",
    r"expansion pass", r"\bwallpaper\b", r"digital deluxe upgrade",
    r"\bbonus content\b",
]
JUNK_RE = re.compile("|".join(JUNK_PATTERNS), re.I)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def fetch(url, params=None, tries=4, timeout=30):
    """GET a URL past Steam's age gate. Returns text, or None on failure."""
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    for attempt in range(tries):
        req = urllib.request.Request(url, headers={
            "User-Agent": UA,
            "Accept": "application/json, text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip",
            "Cookie": "birthtime=283993201; lastagecheckage=1-0-1979; "
                      "wants_mature_content=1; Steam_Language=english",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                return raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 15 * (attempt + 1)
                print(f"    rate limited, waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            if e.code in (403, 500, 502, 503):
                time.sleep(2 * (attempt + 1))
                continue
            return None
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return None


# --------------------------------------------------------------------------
# Parsing Steam's search result rows
# --------------------------------------------------------------------------

ROW_RE = re.compile(r'<a href="(?P<href>[^"]*?/app/\d+[^"]*)"(?P<attrs>.*?)</a>', re.S)
APPID_RE = re.compile(r'data-ds-appid="(\d+)"')
BUNDLE_RE = re.compile(r'data-ds-bundleid="')
TITLE_RE = re.compile(r'<span class="title">(.*?)</span>', re.S)
RELEASED_RE = re.compile(r'<div class="search_released[^"]*">\s*(.*?)\s*</div>', re.S)
TOOLTIP_RE = re.compile(r'data-tooltip-html="(.*?)"', re.S)
TAGIDS_RE = re.compile(r'data-ds-tagids="\[([\d,\s]*)\]"')
PRICE_FINAL_RE = re.compile(r'data-price-final="(\d+)"')
DISCOUNT_RE = re.compile(r'<div class="col search_discount[^"]*">\s*<span>-(\d+)%</span>', re.S)

# "Very Positive<br>92% of the 1,234 user reviews for this game are positive."
SUMMARY_RE = re.compile(
    r"^\s*(?P<tier>[A-Za-z ]+?)\s*<br>\s*(?P<pct>\d+)%\s+of\s+the\s+"
    r"(?P<count>[\d,]+)\s+user\s+reviews", re.I)

DATE_FORMATS = ["%b %d, %Y", "%d %b, %Y", "%b %Y", "%d %b %Y", "%B %d, %Y"]


def parse_date(text):
    """Steam prints dates several ways. None means it isn't a real date."""
    text = text.strip()
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None                     # "Coming soon", "Q4 2026", "To be announced"


def parse_rows(results_html):
    """Yield one dict per game found in a page of search results."""
    for m in ROW_RE.finditer(results_html):
        blob = m.group("attrs")
        href = m.group("href")
        if BUNDLE_RE.search(blob):
            continue
        appid_m = APPID_RE.search(blob)
        if not appid_m:
            continue

        title_m = TITLE_RE.search(blob)
        title = html.unescape(title_m.group(1)).strip() if title_m else ""
        if not title:
            continue

        released_m = RELEASED_RE.search(blob)
        released_text = html.unescape(released_m.group(1)).strip() if released_m else ""

        tier = pct = count = None
        tip_m = TOOLTIP_RE.search(blob)
        if tip_m:
            tip = html.unescape(html.unescape(tip_m.group(1)))
            s = SUMMARY_RE.match(tip)
            if s:
                tier = s.group("tier").strip()
                pct = int(s.group("pct"))
                count = int(s.group("count").replace(",", ""))
            # else "Need more user reviews to generate a score" -> stays None

        tags = []
        tag_m = TAGIDS_RE.search(blob)
        if tag_m and tag_m.group(1).strip():
            tags = [int(t) for t in tag_m.group(1).split(",") if t.strip()]

        price_cents = None
        p = PRICE_FINAL_RE.search(blob)
        if p:
            price_cents = int(p.group(1))
        discount = 0
        d = DISCOUNT_RE.search(blob)
        if d:
            discount = int(d.group(1))

        yield {
            "appid": int(appid_m.group(1)),
            "title": title,
            "released_text": released_text,
            "released": None,
            "tier": tier,
            "pct": pct,
            "reviews": count,
            "tag_ids": tags,
            "price_cents": price_cents,
            "discount": discount,
            "url": href.split("?")[0],
        }


# --------------------------------------------------------------------------
# Crawl
# --------------------------------------------------------------------------

def crawl(days, max_pages, page_size, delay, cc):
    """Page Steam's search sorted by release date until we pass the cutoff."""
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days)
    games, seen = [], set()
    start = 0
    stale_pages = 0

    print(f"Crawling Steam releases back to {cutoff.isoformat()}")

    for page in range(max_pages):
        text = fetch(SEARCH_URL, {
            "query": "",
            "start": start,
            "count": page_size,
            "sort_by": "Released_DESC",
            "category1": 998,          # Games
            "infinite": 1,
            "cc": cc,
            "l": "english",
            "ignore_preferences": 1,
        })
        if not text:
            print(f"  page {page + 1}: request failed, stopping", file=sys.stderr)
            break
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            print(f"  page {page + 1}: unexpected response, stopping", file=sys.stderr)
            break

        rows = list(parse_rows(payload.get("results_html", "")))
        if not rows:
            print(f"  page {page + 1}: no rows, stopping")
            break

        oldest = None
        new_here = 0
        for row in rows:
            if row["appid"] in seen:
                continue
            seen.add(row["appid"])
            new_here += 1
            d = parse_date(row["released_text"])
            row["released"] = d.isoformat() if d else None
            if d:
                if oldest is None or d < oldest:
                    oldest = d
                if d >= cutoff:
                    games.append(row)

        start += page_size
        if (page + 1) % 10 == 0 or page == 0:
            print(f"  page {page + 1}: oldest {oldest or '?'}, {len(games)} in window")

        stale_pages = stale_pages + 1 if new_here == 0 else 0
        if stale_pages >= 2:
            print("  results repeating, stopping")
            break
        if oldest and oldest < cutoff:
            print(f"  passed cutoff at page {page + 1}")
            break

        time.sleep(delay + random.uniform(0, 0.3))

    return games


def load_tag_names():
    text = fetch(TAGS_URL)
    if not text:
        print("  tag list unavailable, continuing without tag names", file=sys.stderr)
        return {}
    try:
        return {int(t["tagid"]): t["name"] for t in json.loads(text)}
    except Exception:
        return {}


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


def enrich(games, cache_path, budget, delay):
    """Attach developer/genre/type from Steam's app API, within a request budget.

    Cached between runs and committed, so a daily run only looks up games it has
    not seen. A first run fills what the budget allows; later runs finish the
    rest. Games with no cached details still appear, just without a developer.
    """
    cache = load_json(cache_path, {})
    looked_up = 0
    kept, dropped = [], 0

    for g in games:
        key = str(g["appid"])
        info = cache.get(key)

        if info is None and looked_up < budget:
            text = fetch(APPDETAILS_URL, {
                "appids": g["appid"], "cc": "us", "l": "english",
                "filters": "basic,genres",
            })
            info = {}
            if text:
                try:
                    node = json.loads(text).get(key, {})
                    if node.get("success") and node.get("data"):
                        d = node["data"]
                        info = {
                            "type": d.get("type"),
                            "genres": [x["description"] for x in d.get("genres", [])][:3],
                            "dev": (d.get("developers") or [None])[0],
                            "desc": (d.get("short_description") or "")[:220],
                        }
                except Exception:
                    pass
            cache[key] = info
            looked_up += 1
            time.sleep(delay + random.uniform(0, 0.4))
            if looked_up % 50 == 0:
                print(f"  looked up {looked_up}/{budget}")
                save_json(cache_path, cache)

        if info and info.get("type") and info["type"] != "game":
            dropped += 1
            continue
        if info:
            for k in ("genres", "dev", "desc"):
                if info.get(k):
                    g[k] = info[k]
        kept.append(g)

    # Keep the cache from growing forever: drop apps outside the current window.
    live = {str(g["appid"]) for g in games}
    cache = {k: v for k, v in cache.items() if k in live}
    save_json(cache_path, cache)

    remaining = sum(1 for g in kept if "dev" not in g and "desc" not in g)
    print(f"  looked up {looked_up} this run, {dropped} non-games dropped, "
          f"{remaining} still awaiting details")
    return kept


def save_json(path, obj, indent=None):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"), indent=indent)


def main():
    ap = argparse.ArgumentParser(description="Crawl Steam releases into data.json")
    ap.add_argument("--days", type=int, default=180,
                    help="how far back to crawl (default 180; the app slices "
                         "shorter windows out of this client-side)")
    ap.add_argument("--min-reviews", type=int, default=10,
                    help="drop games below this review count (default 10)")
    ap.add_argument("--keep-junk", action="store_true",
                    help="don't filter demos/soundtracks/DLC out by name")
    ap.add_argument("--verify-budget", type=int, default=400,
                    help="max app-detail lookups per run (default 400, 0 disables)")
    ap.add_argument("--max-pages", type=int, default=400)
    ap.add_argument("--page-size", type=int, default=100)
    ap.add_argument("--delay", type=float, default=0.7)
    ap.add_argument("--verify-delay", type=float, default=1.6,
                    help="seconds between app-detail lookups; Steam allows "
                         "roughly 200 per 5 minutes (default 1.6)")
    ap.add_argument("--cc", default="us", help="store region for prices")
    ap.add_argument("--out", default="docs/data.json")
    ap.add_argument("--cache", default="scripts/details_cache.json")
    args = ap.parse_args()

    games = crawl(args.days, args.max_pages, args.page_size, args.delay, args.cc)
    print(f"\n{len(games)} releases in the last {args.days} days")

    if not args.keep_junk:
        before = len(games)
        games = [g for g in games if not JUNK_RE.search(g["title"])]
        print(f"  {before - len(games)} dropped by name (demos, soundtracks, DLC)")

    games = [g for g in games
             if g["tier"] in TIER_RANK and (g["reviews"] or 0) >= args.min_reviews]
    print(f"  {len(games)} rated with {args.min_reviews}+ reviews")

    if not games:
        sys.exit("No games matched — refusing to overwrite data.json with nothing.")

    if args.verify_budget > 0:
        print(f"\nFilling in details (budget {args.verify_budget})...")
        games = enrich(games, args.cache, args.verify_budget, args.verify_delay)

    tag_names = load_tag_names()
    for g in games:
        g["tags"] = [tag_names[t] for t in g.pop("tag_ids", []) if t in tag_names][:5]
        g["rank"] = TIER_RANK[g["tier"]]
        g.pop("released_text", None)
        g["url"] = g["url"].replace("https://store.steampowered.com", "")

    games.sort(key=lambda g: (-g["rank"], -(g["reviews"] or 0)))

    payload = {
        "meta": {
            "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
            "days": args.days,
            "min_reviews": args.min_reviews,
            "count": len(games),
            "region": args.cc.upper(),
            "tiers": TIERS,
        },
        "games": games,
    }
    save_json(args.out, payload)
    size = os.path.getsize(args.out) / 1024
    print(f"\nWrote {args.out}: {len(games)} games, {size:.0f} KB")


if __name__ == "__main__":
    main()
