---
name: Static MU/SIGMA z-score breaks under per-day drift
description: Strategies that use a static fitted MU and SIGMA for z-score-driven mean reversion will fade legitimate regime drift, accumulating losing inventory at the band edge
type: feedback
---

VELVETFRUIT_EXTRACT data-explorer reports per-day end drifts of -6, +20.5, +28 against MU=5250.098 SIGMA=15.630. A +28 drift is z = +1.79 — already deep into the MR sell zone (alpha=0.89, MR ask volume = 27 lots placed at or near best_bid). If day 4+ continues drifting, z exceeds 2 and the strategy clamps alpha=1.0 forever, posting maximum-size aggressive sells at the bid into a market that legitimately moved up. Inventory pins at -200 with no mechanism to recover (z stays >2 → MR keeps selling, MM ask is full size, MM bid skewed to 0).

OU half-life is 251 ticks but the data only spans 30k snapshots; a single-day session of ~10k ticks is only ~40 half-lives, and the per-day drift evidence shows the "mean" is not stationary on the relevant horizon.

**Why:** Static fits assume the calibration window equals the deployment window. When deployment runs longer or the regime shifts, the strategy fades the trend until it's max-short or max-long, then rides the loss.

**How to apply:** Any z-score-based MR strategy with hardcoded MU/SIGMA should be flagged unless (a) MU is rolling/EMA-updated, (b) there is a stop-out at extreme z (e.g. z>3 → flatten, not pile in), or (c) the historical drift evidence is bounded well below the SIGMA scale. None of these are present in VelvetMM-MR.py.
