"""Review Firehose v0.12 — track the reviews Apple surfaces on App Store pages.

Data source: the localized App Store webpage's server-rendered JSON
(`<script id="serialized-server-data">` on apps.apple.com).
(Correction 2026-09-16: Apple's RSS review feed was re-verified working
from Apify's network; the earlier claim that it was deprecated and
returned zero entries was wrong and is removed. The engine's data
source is under review.)

What the product is: Apple curates a "most helpful" review set per
storefront and platform. This actor fetches three review pages per
storefront (all / iPhone / Mac — up to ~30 unique reviews each) and, in
delta mode, returns only reviews not seen in previous runs. It tracks
*changes in the reviews Apple chooses to show*, not a complete
chronological firehose of every review ever written.

Modes:
  full  - every review Apple currently surfaces (up to maxReviews)
  delta - only reviews not seen in previous runs (state in the KVS)

Pricing: pay-per-event. Each review pushed to the dataset charges the
"review" event (price configured in Apify Console, not here). Runs that
surface no new reviews bill nothing.
"""
import asyncio
import json
import os
import random
import re
from datetime import datetime, timezone

import httpx
from apify import Actor

# Overridable for local testing only. Production always uses apps.apple.com.
BASE_URL = os.environ.get("REVIEW_FIREHOSE_BASE_URL", "https://apps.apple.com")

# Three page variants per storefront. The base product page's review shelf
# is a subset of `?see-all=reviews`; ipad/watch duplicate iphone, so the
# three below cover the full surfaced set with no redundant fetches.
PAGE_URLS = [
    BASE_URL + "/{country}/app/id{app_id}?see-all=reviews",
    BASE_URL + "/{country}/app/id{app_id}?see-all=reviews&platform=iphone",
    BASE_URL + "/{country}/app/id{app_id}?see-all=reviews&platform=mac",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4 Safari/605.1.15"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}
SPA_DATA_RE = re.compile(
    r'<script[^>]*id="serialized-server-data"[^>]*>(.+?)</script>',
    re.DOTALL,
)
COUNTRY_RE = re.compile(r"^[a-z]{2}$")
MAX_STOREFRONTS = 20
STATE_KEEP = 10_000
CONCURRENCY = 3
RETRY_DELAY_S = 1.5
CHARGE_EVENT = "review"


def find_kind(state, kind: str):
    """Depth-first search for the first dict with $kind == kind."""
    if isinstance(state, dict):
        if state.get("$kind") == kind:
            return state
        for v in state.values():
            found = find_kind(v, kind)
            if found is not None:
                return found
    elif isinstance(state, list):
        for v in state:
            found = find_kind(v, kind)
            if found is not None:
                return found
    return None


def parse_spa_json(html: str):
    """Parse the server-rendered JSON blob; return None when absent/broken."""
    m = SPA_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def extract_reviews(html: str) -> list[dict]:
    """Pull every {$kind: Review} block from the server-rendered JSON."""
    state = parse_spa_json(html)
    if state is None:
        return []
    reviews: list[dict] = []

    def walk(obj) -> None:
        if isinstance(obj, dict):
            if obj.get("$kind") == "Review":
                reviews.append(obj)
                return
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(state)
    return reviews


def extract_ratings(html: str) -> dict | None:
    """Pull the storefront Ratings block (average, totals, histogram)."""
    state = parse_spa_json(html)
    if state is None:
        return None
    node = find_kind(state, "Ratings")
    if node is None:
        return None
    return {
        "ratingAverage": node.get("ratingAverage"),
        "totalNumberOfRatings": node.get("totalNumberOfRatings"),
        "ratingCounts": node.get("ratingCounts"),
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
    }


def safe_rating(value) -> int:
    """Coerce Apple's rating to int 1-5. Returns 0 (unknown) on bad data.

    Never raises: a single malformed record must not abort a run.
    """
    try:
        r = int(float(str(value).strip()))
    except (TypeError, ValueError, AttributeError):
        Actor.log.warning("non-numeric rating %r; storing 0", value)
        return 0
    if r == 0:
        return 0
    if r < 1 or r > 5:
        Actor.log.warning("rating %r outside 1-5; clamping", value)
        return max(1, min(5, r))
    return r


def normalize(review: dict, app_id: str, country: str) -> dict:
    return {
        "reviewId": str(review.get("id", "") or ""),
        "appId": app_id,
        "country": country,
        "author": str(review.get("reviewerName", "") or ""),
        "rating": safe_rating(review.get("rating")),
        "title": str(review.get("title", "") or ""),
        "text": str(review.get("contents", "") or ""),
        # Not exposed by Apple's page JSON; kept for schema compatibility.
        "version": "",
        "voteCount": 0,
        # Apple's `date` is the posted date, not a last-updated timestamp.
        "updated": str(review.get("date", "") or ""),
    }


def parse_countries(inp: dict) -> list[str]:
    """Validate the `countries` input: list of two-letter codes, max 20."""
    raw = inp.get("countries") or ["us"]
    if isinstance(raw, str):
        raw = [raw]
    countries: list[str] = []
    for c in raw:
        code = str(c).strip().lower()
        if COUNTRY_RE.match(code) and code not in countries:
            countries.append(code)
    if not countries:
        raise ValueError(
            "countries must contain at least one valid two-letter country code."
        )
    if len(countries) > MAX_STOREFRONTS:
        Actor.log.warning(
            "got %d countries; capping at %d per run", len(countries), MAX_STOREFRONTS
        )
    return countries[:MAX_STOREFRONTS]


def parse_max_reviews(inp: dict) -> int:
    try:
        n = int(inp.get("maxReviews") or 200)
    except (TypeError, ValueError):
        Actor.log.warning("invalid maxReviews %r; using 200", inp.get("maxReviews"))
        n = 200
    return max(1, min(500, n))


def parse_mode(inp: dict) -> str:
    mode = str(inp.get("mode", "delta")).strip().lower()
    if mode not in ("full", "delta"):
        raise ValueError(f'invalid mode {inp.get("mode")!r}: must be "full" or "delta".')
    return mode


def sort_ids(ids) -> list[str]:
    """Numeric sort for numeric-string IDs (lexicographic mis-sorts them)."""
    return sorted(
        (str(i) for i in ids),
        key=lambda x: (0, int(x)) if x.isdigit() else (1, x),
    )


async def fetch_one(client: httpx.AsyncClient, url: str):
    """GET one page with one retry. Returns (status, html_or_None, outcome).

    404 means "app not in this storefront" — not an error, no retry.
    429 / 5xx / network errors get one retry after a short delay.
    """
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            resp = await client.get(url, timeout=30)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt == 1:
                Actor.log.warning("retrying %s after network error: %s", url, exc)
                await asyncio.sleep(RETRY_DELAY_S)
            continue
        if resp.status_code == 404:
            return 404, None, "skipped: app not in this storefront (404)"
        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            last_exc = RuntimeError(f"HTTP {resp.status_code}")
            if attempt == 1:
                Actor.log.warning("retrying %s after HTTP %s", url, resp.status_code)
                await asyncio.sleep(RETRY_DELAY_S)
            continue
        if resp.status_code != 200:
            return resp.status_code, None, f"HTTP {resp.status_code} (no retry)"
        return 200, resp.text, "ok"
    return 0, None, f"failed after retry: {last_exc}"


async def scrape_country(
    client: httpx.AsyncClient,
    app_id: str,
    country: str,
    sem: asyncio.Semaphore,
):
    """Fetch the three review pages for one storefront.

    Returns (country, reviews, ratings_or_None, outcome_summary).
    """
    async with sem:
        await asyncio.sleep(random.uniform(0.2, 0.8))  # jitter
        by_id: dict[str, dict] = {}
        ratings: dict | None = None
        outcomes: list[str] = []
        empty_ids = 0
        for template in PAGE_URLS:
            url = template.format(country=country, app_id=app_id)
            variant = url.split("?", 1)[1]
            status, html, outcome = await fetch_one(client, url)
            outcomes.append(f"{variant}: {outcome}")
            if status != 200 or not html:
                continue
            page_reviews = extract_reviews(html)
            if not page_reviews:
                Actor.log.warning(
                    "country %s variant %s: HTTP 200 but no reviews extracted "
                    "(body %d bytes) — page format may have changed",
                    country, variant, len(html),
                )
            if ratings is None:
                ratings = extract_ratings(html)
            for raw in page_reviews:
                rec = normalize(raw, app_id, country)
                if not rec["reviewId"]:
                    empty_ids += 1
                    continue
                by_id.setdefault(rec["reviewId"], rec)
        if empty_ids:
            Actor.log.warning(
                "country %s: skipped %d review blocks with empty id", country, empty_ids
            )
        Actor.log.info(
            "country %s: %d unique reviews (%s)", country, len(by_id), "; ".join(outcomes)
        )
        return country, list(by_id.values()), ratings, "; ".join(outcomes)


async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}
        app_id = str(inp.get("appId", "")).strip()
        if not app_id.isdigit():
            raise ValueError("appId must be a numeric Apple App Store app ID.")
        countries = parse_countries(inp)
        max_reviews = parse_max_reviews(inp)
        mode = parse_mode(inp)
        state_key = str(inp.get("stateKey") or "").strip() or f"review-firehose-state-{app_id}"

        store = await Actor.open_key_value_store(name="review-firehose")
        saved = await store.get_value(state_key) or {}
        if isinstance(saved, list):
            # pre-0.12 state format (bare ID list)
            seen: set[str] = set(saved)
            saved_ratings: dict = {}
        else:
            seen = set(saved.get("seen") or saved.get("seenIds") or [])
            saved_ratings = saved.get("ratings") or {}

        # Only build a proxy configuration when the buyer actually supplied
        # one. create_proxy_configuration() with no input still constructs a
        # default Apify Proxy config on the platform, which would route (and
        # bill) traffic the buyer never asked to proxy.
        proxy_input = inp.get("proxyConfiguration")
        proxy_url = None
        if proxy_input:
            proxy_cfg = await Actor.create_proxy_configuration(
                actor_proxy_input=proxy_input
            )
            proxy_url = await proxy_cfg.new_url() if proxy_cfg else None
        if proxy_url:
            Actor.log.info("using proxy from proxyConfiguration input")

        sem = asyncio.Semaphore(CONCURRENCY)
        async with httpx.AsyncClient(
            proxy=proxy_url, follow_redirects=True, headers=HEADERS
        ) as client:
            results = await asyncio.gather(
                *[scrape_country(client, app_id, c, sem) for c in countries]
            )

        # Cross-storefront dedup: review IDs are global.
        surfaced: dict[str, dict] = {}
        for _country, revs, _ratings, _outcome in results:
            for r in revs:
                surfaced.setdefault(r["reviewId"], r)
        ratings_now = {
            country: ratings for country, _revs, ratings, _o in results if ratings
        }
        outcomes = {country: outcome for country, _r, _ra, outcome in results}

        surfaced_list = list(surfaced.values())
        if not surfaced_list:
            detail = "; ".join(f"{c}: {o}" for c, o in outcomes.items())
            raise RuntimeError(
                f"no reviews surfaced for app {app_id}. "
                f"per-storefront outcomes: {detail}"
            )

        new_reviews = (
            [r for r in surfaced_list if r["reviewId"] not in seen]
            if mode == "delta"
            else surfaced_list
        )
        to_push = new_reviews[:max_reviews]

        # Charge the explicit "review" event (price configured in Console).
        # The SDK writes only data[:push_limit] when a buyer's charge limit
        # applies (it accounts for the combined explicit+synthetic event
        # price, which ChargeResult.event_charge_limit_reached alone does not
        # reflect). Compute the same limit push_data uses, so only
        # actually-written IDs are marked seen. Undelivered reviews stay
        # unmarked and retry on the next run instead of being lost.
        charging = Actor.get_charging_manager()
        push_limit = charging.compute_push_data_limit(
            len(to_push), CHARGE_EVENT, is_default_dataset=True
        )
        result = await Actor.push_data(to_push, charged_event_name=CHARGE_EVENT)
        delivered = to_push[:push_limit]
        if len(delivered) < len(to_push):
            Actor.log.warning(
                "charge limit reached: delivered %d of %d reviews; "
                "undelivered reviews stay unmarked and will retry next run",
                len(delivered), len(to_push),
            )
        for r in delivered:
            seen.add(r["reviewId"])

        await store.set_value(
            state_key,
            {
                "seen": sort_ids(seen)[-STATE_KEEP:],
                "ratings": {**saved_ratings, **ratings_now},
            },
        )

        msg = (
            f"pushed {len(delivered)} new / {len(surfaced_list)} surfaced / "
            f"{len(countries)} storefronts (mode={mode})"
        )
        Actor.log.info(msg)
        await Actor.set_status_message(msg)


if __name__ == "__main__":
    asyncio.run(main())
