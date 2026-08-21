# Metric definitions

This document is the line-by-line contract for `src/open_revisit/metrics.py`.
All timestamps are UTC. Acquisition timestamps and derived gaps and waits retain
fractional days; an acquisition is never rounded to its calendar date.

## Input timelines

For one AOI and one `config_hash`, let `A` be the sorted `observed_at` timestamps
of all complete observations and `U` the sorted timestamps of observations that
are both complete and usable. Incomplete observations are excluded everywhere,
including catalog-filter evaluation. Nominal metrics use `A`; effective metrics
use `U`.

The configured period is `[start, end]` and the horizon is `H` days. Daily start
times `t0` are UTC midnights from `start` through `end - H`, inclusive. Thus the
number of evaluated start days is `(end - H - start) + 1`. Only the month of
`t0`, never the month of the future observation, conditions monthly metrics.

## Gaps — `gap_table`

Nominal gaps are the timestamp differences between consecutive elements of `A`;
effective gaps are the differences between consecutive elements of `U`. Units
are fractional days and the denominator/population is the set of adjacent pairs.
No artificial gaps from a period boundary to the first or last observation are
added. The median is the ordinary median, P90 uses pandas' linear quantile,
the maximum effective gap is the longest outage, and outages over 30 days use
the strict test `gap_days > 30`.

## Daily waits — `wait_daily`

For every evaluated start day `t0`, find the earliest `u` in `U` with `u >= t0`.
If `(u - t0) <= H`, `wait_days` is that fractional-day difference and the row is
not censored. If there is no such observation within the inclusive horizon,
`wait_days = H` and `censored = true`. The denominator is all evaluated start
days. In particular, a real observation exactly `H` days after `t0` is not
censored.

## Survival and within-N — `survival_curve`, `within_probability`

For every integer `n` from 0 through `H`, inclusive:

`S(n) = P(wait_days > n)`

The unit is days; the denominator is all evaluated start days. Within-N is
`P(wait_days <= N) = 1 - S(N)` for `N` in `{3, 5, 7, 14, 30}`. The strict
survival comparison `>` is intentional.

## Service-level success — `service_level_success`

For a requirement of one usable observation every `W` days, success is
`P(wait_days < W)`. The result is unitless and its denominator is all selected
start days (or start days whose `t0` is in a selected month). The strict `< W`
is intentional and differs from within-N's `<= N`. It is the fraction of rolling
W-day windows that contain a usable observation.

## Monthly reliability — `monthly_reliability`

For each month `m` from 1 through 12, report
`P(wait_days <= N | month(t0) = m)` for `N` in `{5, 7, 14}`. Values are unitless;
the denominator `n_days` is the count of evaluated start days whose `t0` is in
that month. All 12 months are emitted.

## Catalog-filter evaluation — `catalog_filter_evaluation`

For thresholds `t = 0, 5, ..., 100`, predicted usable means
`catalog_cloud_cover <= t`, while actual usable is the pixel-derived `usable`
flag. A missing catalog cloud-cover value compares false and is predicted
unusable. Counts and rates are calculated per AOI and over the pooled complete
observations under `aoi_id = "ALL"`:

- `precision = TP / (TP + FP)`
- `recall = TP / (TP + FN)`
- `F1 = 2 TP / (2 TP + FP + FN)`
- `kept_unusable_rate = FP / (TP + FP)`
- `discarded_usable_rate = FN / (TP + FN)`

Counts have units of complete observations. Rates are unitless and use the
denominators shown above. The headline threshold is 20 percent.

## Summary — `summary_metrics`

`n_observations` counts complete observations and `n_usable` counts complete,
usable observations. `usable_rate` uses `n_observations` as denominator. Gap
headline fields use the nominal/effective adjacent-gap populations described
above. Within-N fields use all evaluated start days. Best and worst month compare
`p_within_7d` only among months with `n_days > 0`; ties select the earliest month.
Catalog precision and recall select the AOI's threshold-20 row.

## Zero-denominator behavior

Every ratio with a zero denominator is `0.0`, never NaN or infinity. Therefore an
empty month has `n_days = 0` and all three monthly probabilities equal `0.0`; an
empty predicted-positive set has precision and kept-unusable rate `0.0`; and an
empty actual-usable set has recall and discarded-usable rate `0.0`. A missing gap
population yields summary gap statistics of `0.0`. If no month has start days,
summary best/worst month is `0` and both probabilities are `0.0`.

## Hand-computed example timeline

Take a 60-day period from day 0 through day 60, horizon `H = 20`, and usable
observations at day offsets 0, 5, and 40. Start days are offsets 0 through 40,
so there are 41 wait rows. The exact waits are:

- offset 0: 0 days;
- offsets 1–4: `5 - t0`, hence 4, 3, 2, 1 days;
- offset 5: 0 days;
- offsets 6–19: 20 days and censored (14 rows);
- offsets 20–40: `40 - t0`, hence 20, 19, ..., 0 days, all uncensored.

Consequently `S(7) = 27/41`, `P(wait <= 7) = 14/41`, and SLA(7), which uses
`wait < 7`, is `13/41`. There are 14 censored rows. The effective gaps are 5 and
35 days, so the longest outage is exactly 35 days.

The following block is parsed by `tests/test_metrics.py`; it is the same example,
expressed without rounded decimal expectations.

<!-- metric-example
start: '2024-01-01'
end: '2024-03-01'
horizon_days: 20
usable_day_offsets: [0, 5, 40]
expected_wait_days: [0, 4, 3, 2, 1, 0, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 20, 19, 18, 17, 16, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 0]
expected_censored_day_offsets: [6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]
expected_survival_7: 0.6585365853658537
expected_p_within_7: 0.34146341463414637
expected_sla_7: 0.3170731707317073
expected_n_censored: 14
expected_longest_outage_days: 35
metric-example -->
