# Review Firehose

Track Apple App Store reviews over time — not just a one-shot scrape.

**Two modes:**
- `full` — scrape recent reviews (up to `maxReviews`, Apple caps at ~500)
- `delta` — return only reviews **not seen in previous runs** (state tracked in the key-value store). Run it on a schedule and you get a firehose of new reviews.

**Input:** `appId` (numeric Apple App Store ID), `country` (default `us`), `maxReviews` (default 200), `mode` (`delta`), `stateKey`.

**Output:** one dataset item per review — reviewId, author, rating, title, text, version, voteCount, updated.

**Pricing:** pay per review (`PAY_PER_EVENT`).

## Why it beats a plain scraper
One-shot scrapers make you diff the results yourself. Review Firehose remembers what it already sent you. Point a schedule at it and every run hands you only what's new — that's the product.

## Roadmap
- v0.2: Google Play reviews
- v0.3: sentiment scoring + rating-delta alerts
- v0.4: MCP tool wrapper for agentic buyers
