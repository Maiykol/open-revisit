# Metric definitions

Let `U` be the sorted `observed_at` of usable, complete observations for an AOI; `A` the same for all complete observations. Period `[start, end]`, horizon `H` = 60 days (config).

- **Nominal gaps**: differences between consecutive elements of `A`. **Effective gaps**: consecutive elements of `U`. Report median, P90, max, count of gaps > 30 days. Both go to `aoi_gaps`.
- **Wait time**: for every calendar day `t0` from `start` to `end − H`: `wait = min{u ∈ U : u ≥ t0} − t0` in fractional days; if none within `H`, `wait = H` and `censored = true`. Evaluating only `t0 ≤ end − H` removes end-of-period bias.
- **Survival**: `S(n) = P(wait > n)` for integer `n` in `0..H` over all evaluated `t0`.
- **P(within N)**: `1 − S(N)` for N ∈ {3, 5, 7, 14, 30}.
- **Service-level success rate** for "one usable observation every W days": defined as `P(wait < W)` over all start days. Note in the README that this equals the fraction of rolling W-day windows that contain a usable observation.
- **Monthly reliability**: `P(wait ≤ N | month(t0) = m)` for N ∈ {5, 7, 14}.
- **Catalog-filter evaluation**: over observations, for thresholds `t` in 0, 5, …, 100: predicted usable iff `catalog_cloud_cover ≤ t`; actual = `usable`. TP/FP/FN/TN, precision, recall, F1, `kept_unusable_rate = FP / (TP + FP)`, `discarded_usable_rate = FN / (TP + FN)`. Computed per AOI and pooled (`aoi_id = 'ALL'`). The README headline uses t = 20.
