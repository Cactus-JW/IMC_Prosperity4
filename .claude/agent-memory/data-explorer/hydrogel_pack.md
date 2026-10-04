---
name: HYDROGEL_PACK Round 3 profile
description: HYDROGEL_PACK microstructure - two-tier symmetric MM bots, 16-spread regime, slow asymmetric mean reversion to ~10000
type: project
---

HYDROGEL_PACK trades in a two-tier symmetric-bot market around an anchor of ~10000, with meaningful reversion only on the upside.

**Why:** Baseline knowledge for any Round 3 HYDROGEL_PACK strategy work; saves re-running the full analysis.

**How to apply:** When writing/reviewing strategies for HYDROGEL_PACK, assume these parameters unless fresh data contradicts:

- Mid anchor ≈ 10000 (nominally); std 32 over 30k ticks; range [9891, 10079] across d0/d1/d2.
- OU half-life ≈ 300 ticks (phi=0.99769); level AC(1)=0.9977.
- L1 spread = 16 for 92.7% of ticks (b1 = mid−8, a1 = mid+8). L1 sizes mean 12.4, range [4,15].
- L2 quotes at mid±10 (47.5%) or mid±11 (45.3%); L2 spread=21 for 91.5% of ticks; sizes ~25, range [10,30]; present 100% of ticks.
- 1-tick return std ≈ 2.17; AC(1) ≈ −0.129 (one-tick reversion only; AC(2..10) ≈ 0).
- All bot trades happen at exactly L1 (mid±8). ~337 trades/day, 3.3% of ticks active. Trade size mean 4.0, range [2,6]. Flow balanced (hit 48% / lift 52%).
- L1 imbalance corr with next-return: N=1 +0.30, N=5 +0.14, N=20 +0.07. Strong 1-tick signal.
- Deviation-from-10000 200-tick forward return: dev∈[+25,+35] → −17; dev∈[+15,+25] → −10; dev∈[−35,−25] → +1.1; dev∈[−25,−15] → +0.9.
- **Reversion is strongly asymmetric: upside only**. Do not fade downside deviations.
- Best simple fair value: L1+L2 volume-weighted mid (RMS 2.036 vs next-mid). L1 microprice 2.113. Plain mid 2.170. EMA strictly worse. Blending with 10000 anchor doesn't help at 1-tick horizon.
- Zero correlation with other Round 3 products (VELVETFRUIT_EXTRACT and all VEV vouchers: level |corr|≤0.10, return corr ≤0.01). Isolated instrument.
- Position limit 200. No ConversionObservation relevance.
- Preferred strategy: quote at fair ± 7 (one tick inside bot L1) with L1+L2 VWAP fair; inventory skew coef ~0.04; optional imbalance lean coef ~3; upside-only taker overlay at dev ≥ +25.
