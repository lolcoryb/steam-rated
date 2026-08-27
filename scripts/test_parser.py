#!/usr/bin/env python3
"""Parser tests. The first row is real Steam markup captured from the search
endpoint; the rest are hand-built variants that exercise the edge cases
(no review score, "need more reviews", alternate date formats, free, discount).
This is a test fixture, not a dataset."""

import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_steam import parse_rows, parse_date, TIER_RANK

REAL_ROW = r'''
<a href="https://store.steampowered.com/app/4020490/Smack_Talk/?snr=1_7_7_230_150_1"
 data-ds-appid="4020490" data-ds-itemkey="App_4020490" data-ds-tagids="[7178,4136,3859,1719,597,1743,10397]" data-ds-crtrids="[46271321]" class="search_result_row ds_collapse_flag "
 data-search-page="1" data-gpnav="item">
<div class="search_capsule"><img src="https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/4020490/capsule_231x87.jpg"></div>
<div class="responsive_search_name_combined">
<div class="search_name ellipsis"><span class="title">Smack Talk</span></div>
<div class="search_released responsive_secondrow">Aug 27, 2026</div>
<div class="search_reviewscore responsive_secondrow">
<span class="search_review_summary positive" data-tooltip-html="Overwhelmingly Positive&lt;br&gt;96% of the 793 user reviews for this game are positive."></span>
</div>
<div class="search_price_discount_combined" data-price-final="389">
<div class="col search_discount responsive_secondrow"></div>
<div class="col search_price responsive_secondrow">$3.89</div>
</div>
</div>
</a>
'''

VARIANTS = r'''
<a href="https://store.steampowered.com/app/111111/No_Score_Yet/?snr=1"
 data-ds-appid="111111" data-ds-tagids="[492]" class="search_result_row">
<div class="search_name ellipsis"><span class="title">No Score Yet</span></div>
<div class="search_released responsive_secondrow">26 Aug, 2026</div>
<div class="search_reviewscore responsive_secondrow"></div>
<div class="search_price_discount_combined" data-price-final="0">
<div class="col search_price responsive_secondrow">Free</div></div>
</a>
<a href="https://store.steampowered.com/app/222222/Needs_More/?snr=1"
 data-ds-appid="222222" data-ds-tagids="[]" class="search_result_row">
<div class="search_name ellipsis"><span class="title">Needs More &amp; More</span></div>
<div class="search_released responsive_secondrow">Aug 20, 2026</div>
<div class="search_reviewscore responsive_secondrow">
<span class="search_review_summary" data-tooltip-html="Need more user reviews to generate a score"></span></div>
</a>
<a href="https://store.steampowered.com/app/333333/Discounted/?snr=1"
 data-ds-appid="333333" data-ds-tagids="[19,492]" class="search_result_row">
<div class="search_name ellipsis"><span class="title">Discounted Mixed Thing</span></div>
<div class="search_released responsive_secondrow">Jul 2, 2026</div>
<div class="search_reviewscore responsive_secondrow">
<span class="search_review_summary mixed" data-tooltip-html="Mixed&lt;br&gt;58% of the 12,431 user reviews for this game are positive."></span></div>
<div class="search_price_discount_combined" data-price-final="1499">
<div class="col search_discount responsive_secondrow"><span>-40%</span></div>
<div class="col search_price responsive_secondrow">$14.99</div></div>
</a>
<a href="https://store.steampowered.com/app/444444/Unreleased/?snr=1"
 data-ds-appid="444444" data-ds-tagids="[492]" class="search_result_row">
<div class="search_name ellipsis"><span class="title">Coming Later</span></div>
<div class="search_released responsive_secondrow">Q4 2026</div>
<div class="search_reviewscore responsive_secondrow"></div>
</a>
<a href="https://store.steampowered.com/bundle/55555/Some_Bundle/?snr=1"
 data-ds-bundleid="55555" class="search_result_row">
<div class="search_name ellipsis"><span class="title">A Bundle</span></div>
</a>
'''

rows = list(parse_rows(REAL_ROW + VARIANTS))
by_id = {r["appid"]: r for r in rows}

def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        check.failed = True
check.failed = False

print("parsed", len(rows), "rows")

g = by_id.get(4020490)
check(g is not None, "real row parsed")
check(g["title"] == "Smack Talk", f"title -> {g['title']!r}")
check(g["tier"] == "Overwhelmingly Positive", f"tier -> {g['tier']!r}")
check(g["pct"] == 96, f"pct -> {g['pct']}")
check(g["reviews"] == 793, f"reviews -> {g['reviews']}")
check(g["price_cents"] == 389, f"price -> {g['price_cents']}")
check(len(g["tag_ids"]) == 7, f"tag ids -> {g['tag_ids']}")
check(parse_date(g["released_text"]).isoformat() == "2026-08-27", "date 'Aug 27, 2026'")
check(g["url"].endswith("/app/4020490/Smack_Talk/"), f"url -> {g['url']}")

g = by_id.get(111111)
check(g["tier"] is None and g["reviews"] is None, "no review block -> unrated")
check(g["price_cents"] == 0, "free -> 0 cents")
check(parse_date(g["released_text"]).isoformat() == "2026-08-26", "date '26 Aug, 2026'")

g = by_id.get(222222)
check(g["tier"] is None, "'need more user reviews' -> unrated")
check(g["title"] == "Needs More & More", f"entity unescaped -> {g['title']!r}")
check(g["price_cents"] is None, "missing price -> None")

g = by_id.get(333333)
check(g["tier"] == "Mixed" and g["pct"] == 58, "mixed tier")
check(g["reviews"] == 12431, f"comma-separated count -> {g['reviews']}")
check(g["discount"] == 40, f"discount -> {g['discount']}")

g = by_id.get(444444)
check(parse_date(g["released_text"]) is None, "'Q4 2026' -> not a real date")

check(55555 not in by_id and len(rows) == 5, "bundle row skipped")
check(all(t in TIER_RANK for t in ["Overwhelmingly Positive", "Mixed"]), "tier ranks present")
check(TIER_RANK["Overwhelmingly Positive"] > TIER_RANK["Very Positive"] > TIER_RANK["Mixed"],
      "tier ordering")

print("\nFAILURES" if check.failed else "\nall passed")
sys.exit(1 if check.failed else 0)
