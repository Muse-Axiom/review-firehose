# Changelog

## v0.12 (2026-09-16)
- Source upgrade: three review pages per storefront (`?see-all=reviews`,
  `&platform=iphone`, `&platform=mac`) — up to ~30 unique reviews per
  storefront instead of ~8. Base page is a subset of see-all; ipad/watch
  duplicate iphone and are skipped.
- Removed the iTunes Lookup slug resolution entirely. Apple ignores the URL
  slug, so the actor fetches `/app/id{appId}` directly — one less dependency
  and one less failure mode.
- Billing fix: `push_data(..., charged_event_name="review")` charges the
  explicit `review` event. Previously the code relied on the synthetic
  `apify-default-dataset-item` event while the event file named it `review`,
  which would have billed $0.
- Data-loss fix: only review IDs actually delivered (per the SDK
  `ChargeResult`) are marked seen. Reviews dropped by a buyer's charge limit
  now retry on the next run instead of being silently skipped forever.
- Wired the `proxyConfiguration` input (default off). Buyers can flip on
  Apify Proxy — or a custom proxy URL — with no redeploy if direct access
  ever gets blocked.
- Dataset schema rewritten in Apify's dataset-schema format and wired via
  `storages.dataset` in actor.json (table view + field metadata).
- Hardening: `safe_rating` (non-numeric ratings log and store 0, never crash
  the run), null-safe `maxReviews`, `mode` validated, 404 treated as
  "not in storefront" (info + skip, no retry), retry with delay on
  429/5xx/network, per-storefront outcome reporting.
- State: numeric ID sort (lexicographic sort corrupted the 10k cap),
  per-app default state key (`review-firehose-state-{appId}`), backward
  compatible with older state formats, plus a per-storefront ratings
  snapshot (average / total / histogram) stored each run.
- Honest copy: "reviews Apple surfaces" throughout; no "new reviews"
  promises. `updated` documented as the posted date.
- Performance: storefronts fetched concurrently (3-wide with jitter);
  reviews pushed in one batch instead of one request per review.
- Input simplified to a single `countries` field (default `["us"]`).

## Roadmap (unplanned)
- Google Play reviews
- Sentiment scoring + rating-delta alerts (ratings snapshots are already stored)
- MCP tool wrapper for agentic buyers (prototype exists at Muse-Axiom/review-firehose-mcp)
