"""Review Firehose v0.1 — Apple App Store review tracking.

Modes:
  full  - scrape recent reviews (up to maxReviews)
  delta - return only reviews not seen in previous runs (state in KVS)

Pricing: one event per review pushed to the dataset (pay-per-event).
"""
import asyncio
import logging

import httpx
from apify import Actor

log = logging.getLogger(__name__)

ITUNES_RSS = (
    "https://itunes.apple.com/rss/customerreviews/page={page}/id={app_id}"
    "/sortby=mostrecent/json?cc={country}"
)
HEADERS = {"User-Agent": "ReviewFirehose/0.1 (+https://getaxiomworks.com)"}


async def fetch_page(client: httpx.AsyncClient, app_id: str, country: str, page: int) -> list[dict]:
    url = ITUNES_RSS.format(page=page, app_id=app_id, country=country)
    resp = await client.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    feed = resp.json().get("feed", {})
    return feed.get("entry", [])


def normalize(entry: dict, app_id: str, country: str) -> dict:
    def txt(node: dict | str | None, key: str = "label") -> str:
        if isinstance(node, dict):
            return str(node.get(key, ""))
        return str(node or "")

    return {
        "reviewId": txt(entry.get("id")),
        "appId": app_id,
        "country": country,
        "author": txt(entry.get("author", {}).get("name")),
        "rating": int(txt(entry.get("im:rating")) or 0),
        "title": txt(entry.get("title")),
        "text": txt(entry.get("content")),
        "version": txt(entry.get("im:version")),
        "voteCount": txt(entry.get("im:voteCount")),
        "updated": txt(entry.get("updated")),
    }


async def main() -> None:
    async with Actor:
        inp = await Actor.get_input() or {}
        app_id = str(inp.get("appId", "")).strip()
        if not app_id.isdigit():
            raise ValueError("appId must be a numeric Apple App Store app ID.")
        country = str(inp.get("country", "us")).strip().lower() or "us"
        max_reviews = max(1, min(500, int(inp.get("maxReviews", 200))))
        mode = str(inp.get("mode", "delta")).strip().lower()
        state_key = str(inp.get("stateKey", "review-firehose-state")).strip()

        seen: set[str] = set()
        if mode == "delta":
            store = await Actor.open_key_value_store()
            saved = await store.get_value(state_key)
            if isinstance(saved, dict):
                seen = set(saved.get("seenIds", []))

        reviews: list[dict] = []
        async with httpx.AsyncClient(follow_redirects=True) as client:
            for page in range(1, 11):  # Apple exposes ~10 pages x 50
                try:
                    entries = await fetch_page(client, app_id, country, page)
                except httpx.HTTPStatusError as exc:
                    log.warning("page %d failed: %s", page, exc)
                    break
                if not entries:
                    break
                for e in entries:
                    r = normalize(e, app_id, country)
                    if not r["reviewId"]:
                        continue
                    if mode == "delta" and r["reviewId"] in seen:
                        continue
                    reviews.append(r)
                    seen.add(r["reviewId"])
                    if len(reviews) >= max_reviews:
                        break
                if len(reviews) >= max_reviews:
                    break
                await asyncio.sleep(0.3)

        for r in reviews:
            await Actor.push_data(r)
            await Actor.charge({"eventName": "review"})

        if mode == "delta":
            store = await Actor.open_key_value_store()
            # keep the window bounded so state stays small
            await store.set_value(state_key, {"seenIds": sorted(seen)[-5000:]})

        log.info("pushed=%d mode=%s app=%s country=%s", len(reviews), mode, app_id, country)


if __name__ == "__main__":
    asyncio.run(main())
