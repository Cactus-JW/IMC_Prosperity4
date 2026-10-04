---
name: VELVETFRUIT_EXTRACT Round 3 profile
description: Microstructure and statistics summary for the VELVETFRUIT_EXTRACT underlying in Round 3 — slow OU around ~5250 with a clear two-tier MM bot footprint and a strong imbalance signal
type: project
---

Round 3 VELVETFRUIT_EXTRACT underlying, 30k snapshots × 3 days.

**Price regime:** anchored around 5250, stdev 15.6, range [5198, 5300]. AR(1) φ = 0.99724 → OU half-life ~251 ticks. Per-day end drifts −6/+20.5/+28; low R² so weak trend.

**Returns:** stdev 1.13, |ret| p90=2, p99=3, max=5.5; 24.7% zero-ticks. AC(1) = −0.16, AC(≥2) ≈ 0. Prev-tick +1 → next mean −0.144; prev −1 → +0.179 (strong 1-tick reversion).

**Book structure:** top spread = 5 in 74% of ticks, 6 in 18%; outer spread = 6 in 75%, 7 in 25%. Dominant 2-level pattern (spread=5, bid-gap=1, ask-gap=1) with bv1 ≈ 19, bv2 ≈ 40 (symmetric on asks). Tier is "thin inner ~20 lots, thick outer ~40 lots". Spread-2/3 snapshots are asymmetric (35 vs 10 lots) and predict drift toward the thin side.

**Signal:** order-book imbalance → next-tick return corr = +0.281, decays to +0.11 at lag 10, +0.08 at lag 20. Half-life of signal ~10 ticks.

**Trades:** 1372 trades / 8269 lots across 3 days, mean qty 6.0, sizes uniformly spread over 3–8 with tail to 15.

**Why:** Suggests MM-inside-5-spread strategy with microprice skew + inventory-aware skew rather than simple symmetric quoting.
**How to apply:** Fair = microprice blended with slow EMA (span ~500 ticks). Primary quotes at fair ± 2, size ~15/side, position skew 0.03 · pos. Skip asymmetric spread≤3 regimes. Position limit 200.
