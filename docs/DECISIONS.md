# Decisions and deviations

This log records any implementation deviation from [`SPEC.md`](SPEC.md), along
with its reason and consequences. There were no deviations in M0.

## M1-001 — Partition long STAC searches into 90-day intervals

- **Decision:** Split each requested AOI period into contiguous, non-overlapping
  intervals of at most 90 days, paginate every interval with the specified
  `limit=500`, merge by `scene_id`, and then apply the specified deduplication.
- **Spec alternative:** §6.2 describes one search call over the entire requested
  period for each AOI.
- **Reason:** On 2026-08-21, Earth Search returned HTTP 502 for the Berlin 2024
  query after all five retries, both with `+00:00` and `Z` timestamps. A bounded
  one-day Berlin request returned HTTP 200 and the expected fields/assets.
- **Consequence:** The logical query and outputs are unchanged, but initial
  discovery makes more bounded metadata requests. Watermark-overlap reruns
  normally cover one interval per AOI.
