"""Unit tests for Review Firehose v0.12 pure functions (offline)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ["REVIEW_FIREHOSE_BASE_URL"] = "http://127.0.0.1:9"  # never hit

import main as M  # noqa: E402

FIX = os.path.join(os.path.dirname(__file__), "fixtures")
passed = failed = 0


def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ok: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name}")


print("parse_countries")
check("list", M.parse_countries({"countries": ["us", "gb", "de"]}) == ["us", "gb", "de"])
check("string", M.parse_countries({"countries": "us"}) == ["us"])
check("empty->us", M.parse_countries({"countries": []}) == ["us"])
check("missing->us", M.parse_countries({}) == ["us"])
check("dedup+lower", M.parse_countries({"countries": ["US", "us", "gb"]}) == ["us", "gb"])
try:
    M.parse_countries({"countries": ["xx1", ""]})
    check("invalid raises", False)
except ValueError:
    check("invalid raises", True)
codes25 = [chr(97 + i // 26) + chr(97 + i % 26) for i in range(25)]
check("cap 20", M.parse_countries({"countries": codes25}) == codes25[:20])

print("safe_rating")
check("int 5", M.safe_rating(5) == 5)
check("str '4'", M.safe_rating("4") == 4)
check("str '4.5'->4", M.safe_rating("4.5") == 4)
check("str 'five'->0", M.safe_rating("five") == 0)
check("None->0", M.safe_rating(None) == 0)
check("list->0", M.safe_rating([1]) == 0)
check("7 clamps to 5", M.safe_rating(7) == 5)
check("-2 clamps to 1", M.safe_rating(-2) == 1)
check("0 stays 0", M.safe_rating(0) == 0)

print("parse_mode")
check("delta", M.parse_mode({"mode": "delta"}) == "delta")
check("'Delta ' strips", M.parse_mode({"mode": "Delta "}) == "delta")
try:
    M.parse_mode({"mode": "incremental"})
    check("bad mode raises", False)
except ValueError:
    check("bad mode raises", True)

print("parse_max_reviews")
check("default 200", M.parse_max_reviews({}) == 200)
check("null->200", M.parse_max_reviews({"maxReviews": None}) == 200)
check("str '100'", M.parse_max_reviews({"maxReviews": "100"}) == 100)
check("garbage->200", M.parse_max_reviews({"maxReviews": "abc"}) == 200)

print("sort_ids")
check(
    "numeric order",
    M.sort_ids(["9999999999", "13657831289", "123"]) == ["123", "9999999999", "13657831289"],
)

print("extract_reviews on fixtures (30 unique across 3 pages)")
all_ids = set()
per_page = {}
for name in ("seeall", "iphone", "mac"):
    html = open(os.path.join(FIX, f"whatsapp-us-{name}.html"), encoding="utf-8").read()
    revs = M.extract_reviews(html)
    per_page[name] = revs
    check(f"{name} has 10 reviews", len(revs) == 10)
    all_ids.update(str(r["id"]) for r in revs)
check("union is 30", len(all_ids) == 30)
check("pages mutually disjoint",
      len(set(str(r["id"]) for r in per_page["seeall"]) & set(str(r["id"]) for r in per_page["iphone"])) == 0
      and len(set(str(r["id"]) for r in per_page["seeall"]) & set(str(r["id"]) for r in per_page["mac"])) == 0
      and len(set(str(r["id"]) for r in per_page["iphone"]) & set(str(r["id"]) for r in per_page["mac"])) == 0)

print("extract_ratings")
html = open(os.path.join(FIX, "whatsapp-us-seeall.html"), encoding="utf-8").read()
rt = M.extract_ratings(html)
check("ratings found", rt is not None and rt["ratingAverage"] == 4.7
      and rt["totalNumberOfRatings"] == 18602647 and len(rt["ratingCounts"]) == 5)

print("normalize")
rec = M.normalize(per_page["seeall"][0], "310633997", "us")
check("required fields", all(rec.get(k) for k in ("reviewId", "appId", "text", "updated"))
      and isinstance(rec["rating"], int) and rec["country"] == "us")
check("version/voteCount defaults", rec["version"] == "" and rec["voteCount"] == 0)
check("all 30 normalize with unique ids",
      len({M.normalize(r, "310633997", "us")["reviewId"]
           for revs in per_page.values() for r in revs}) == 30)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
