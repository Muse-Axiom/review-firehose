# Review Firehose

Know the moment Apple changes which reviews it shows on your App Store page — in every storefront you sell in.

**What it tracks:** Apple curates a "most helpful" review set per storefront and platform (up to ~30 unique reviews per storefront). These are the reviews every visitor to the product page sees — the ones that drive install conversion. This actor fetches them and, in delta mode, returns only reviews that newly appeared since the last run.

**Two modes:**
- `full` — every review Apple currently surfaces (up to `maxReviews`)
- `delta` — only reviews not seen in previous runs (state tracked in the key-value store). Run it on a schedule and each run hands you newly surfaced reviews.

**Input:** `appId` (numeric Apple App Store ID), `countries` (default `["us"]`, max 20 — aggregates storefronts in one run, deduped by review ID), `maxReviews` (default 200, total cap), `mode` (`delta`), `stateKey` (leave empty for one key per app), `proxyConfiguration` (optional, off by default).

**Output:** one dataset item per review — reviewId, appId, country, author, rating, title, text, version, voteCount, updated. The key-value store also keeps a per-storefront ratings snapshot (average, total count, histogram) from each run.

**Data source:** Apple's App Store webpage server-rendered JSON (`apps.apple.com`). Apple deprecated the old RSS customerreviews endpoint (it silently returns zero entries as of 2026). `version` and `voteCount` are not exposed by Apple and are returned empty/0 for schema compatibility. `updated` is the review's posted date — Apple exposes no last-updated time.

**Honest limits:** this is not a complete chronological firehose of every review. It tracks *changes in the reviews Apple chooses to show*. How often Apple rotates that set varies; verify cadence for your app before promising alerts on a schedule.

**Pricing:** pay per review delivered (event `review`, configured in Apify Console). Runs that surface no new reviews bill nothing.

## Changelog
See [CHANGELOG.md](CHANGELOG.md).
