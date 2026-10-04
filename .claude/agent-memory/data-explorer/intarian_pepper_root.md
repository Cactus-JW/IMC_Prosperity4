---
name: INTARIAN_PEPPER_ROOT Round 1 profile
description: Deterministic linear-drift product with wide-bot MM around a closed-form fair value (Round 1, position limit 80)
type: project
---

Pure linear-trend product: `fair(ts) = 10000 + 1000·(day_index + 2) + 0.001·ts`. Regression R² = 0.9999+ on all 3 days; slope exactly 0.001/ts (= +1000 per full day, overnight jumps ≤ 3 seashells — continuous).

**Why:** On days -2/-1/0, intercepts were 9999.98 / 10999.97 / 11999.95. Residual std grows 2.01 → 2.22 → 2.36 per day; max absolute residual only ±10.7 even on day 0; d_mid AR(1) = −0.500 (pure bid-ask bounce) with higher-lag autocorr = 0. Residual autocorr after detrending ≈ 0 at lag 1/10/100, so residuals are white noise around the trend. Linear fair beats microprice and raw mid as predictor: RMSE to next mid = 1.13 (linear) vs 1.51 (micro) vs 1.58 (raw), measured on day -2.

**How to apply:** Do NOT use EMA/rolling-median/microprice as primary anchor — the closed-form time-based fair is strictly better. Day index is inferable from the first observed mid (snap to nearest 1000). Market-make inside the wide bot: buy at `round(fair)−2` and sell at `round(fair)+2` for 4-tick round-trip edge (wide bot sits at fair ± 5/6/7 for days -2/-1/0 respectively). Taker snipe only when `best_ask < fair − 1` or `best_bid > fair + 1` — these happen ~1.7% of ticks. Skew inventory *toward long* (+0.3·position on ask side) because +0.001/ts drift gives positive carry to longs and negative to shorts.

Key stats:
- Spread mode: 11–12 / 12–13 / 13–14 (day -2/-1/0); widens ~1/day
- L1 depth mean/median 11.5/10 on both sides; L2 present 65%, L3 1.5%
- Empty-book ~4% each side, both-empty ~0.2% (16–21 ticks/day with mid=0 null)
- Trades: ~335–344/day, qty mean 5.1, max 8, ~1730 units/day total; 52/48 split below/above fair (weak flow bias)
- OFI corr with d_mid 0.56 (mechanical, not predictive once detrended)
- All bid/ask prices are integer-tick; mids are X.0 or X.5
- Position limit 80; inventory drift is the main risk (persistent short costs 1 seashell/tick/unit)
- Regime-break guard: `|mid − fair| > 15` for 5+ ticks should trigger flatten
