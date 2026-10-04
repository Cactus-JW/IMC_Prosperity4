# Analysis: HYDROGEL_PACK — 2026-04-24

Data: 30,000 price rows (3 days × 10,000 ticks) and 1,010 bot-to-bot trades from Round 3 days 0-2.

## Key Statistics

### Price level
- **Mid min/max/mean/std**: 9891.0 / 10079.0 / 9990.81 / 31.94 (global).
- Daily means all within 9989-9992; price drifts but always reverts — no clear day-over-day trend.
  - day0: mean 9990.96 / std 25.33 / range [9928, 10071], open 10000 → close 9958.
  - day1: mean 9992.06 / std 37.61 / range [9908.5, 10079], open 9958 → close 10015.
  - day2: mean 9989.40 / std 31.62 / range [9891, 10051], open 10011 → close 10010.
- Deviation from 10000: mean −9.2, std 31.9, range [−109, +79]. Anchor ≈ **10000** (but drift is large).
- Mid sits on integer grid 94.4% of ticks; remaining 5.6% on .5 grid.

### Spread / book shape
- **L1 spread is 16 in 92.7% of ticks** (27,820/30,000). Distribution: {7:275, 8:490, 9:225, 15:798, **16:27,820**, 17:392}.
- **L1 ask = mid+8, L1 bid = mid−8** in the 16-spread regime. Book is perfectly symmetric around mid.
- **L2 quote sits at mid±10 (47.5%) or mid±11 (45.3%)**; L2 spread = 21 in 91.5% of ticks. L2 present 100% of ticks; L3 only 1.6%.
- Tight-spread events (≠16): 2,180 rows (7.3%). Symmetric pairs: {(7.5,7.5): 798, (4,4): 490, (3.5,3.5): 275, (4.5,4.5): 225}.

### Volume / liquidity
- **L1 size**: mean 12.4, median 12, range [4, 15] — symmetric bid/ask.
- **L2 size**: mean 24.8, median 25, range [10, 30] — symmetric.
- Book is thin for aggressive taking: only ~12 units at L1 before a 2-unit gap up to L2.

### Time-series / autocorrelation
- **1-tick return std ≈ 2.17** (d0: 2.19, d1: 2.15, d2: 2.17). Stationary across days.
- **Return AC(1) = −0.129** → one-tick bid-ask bounce / reversion.
- AC(2)..AC(10) all ≈ 0. Reversion is one-tick only.
- **OU fit on (mid−10000)**: phi = 0.99769 → **half-life ≈ 300 ticks** (slow anchor reversion).
- Level AC(1) = 0.9977. Sign-changes of (mid−10000) = 333 over 30k ticks (~1 crossing per 90 ticks).
- Return percentiles: p1=−5.5, p5=−3.5, p50=0, p95=+3.5, p99=+5.0.

### Trades (bot-to-bot; buyer/seller fields both NaN)
- 1,010 trades → **~337/day**, only **3.3% of ticks** have any trade.
- Sizes: p50=4, mean 4.0, std 1.4, range [2, 6]. Total volume 4,078 over 3 days.
- **Trade price = exactly mid±8 in 93%**: 480 at mid+8 (lift ask), 459 at mid−8 (hit bid). Flow balanced (lift 51.9% / hit 48.1%).
- No mid-trades, no L2 trades. All flow goes through L1 bot quotes.

### Predictive signals
- **L1 imbalance** (bv1−av1)/(bv1+av1) vs next N-tick return: N=1: **+0.299**, N=5: +0.139, N=20: +0.069, N=50: +0.041. Microprice ≈ equivalent.
- **Deviation from 10000** vs forward return: N=1: −0.034, N=5: −0.068, N=20: −0.130, N=50: **−0.201**. Reversion is slow (shows only at N≥50).
- Mean-reversion by dev-bucket, **200-tick forward return**:
  - dev ∈ [+25, +35] (n=1797): **−17.3**
  - dev ∈ [+15, +25] (n=2836): **−10.1**
  - dev ∈ [−25, −15] (n=2954): **+0.9**
  - dev ∈ [−35, −25] (n=2565): **+1.1**
- **Reversion is strongly asymmetric — upside only**. Downside deviations barely revert in-sample. Likely "true" anchor is ~9990, not 10000.

### Fair-value estimators (RMS error predicting next mid, baseline ret std 2.17)
- Plain mid: **2.170**
- L1 microprice = (b1·av1 + a1·bv1)/(bv1+av1): **2.113**
- **L1+L2 volume-weighted mid: 2.036** ← best simple estimator.
- EMA(α=0.3): 2.753. EMA(α=0.1): 4.33. EMAs are strictly worse.
- Blending `(1−w)·mid + w·10000` doesn't improve — weights 0.002 marginal, 0.01+ hurts. Anchor reversion is too slow to help 1-tick predictions.

### Cross-product correlations (all Round 3 products)
- **Essentially uncorrelated with everything**: VELVETFRUIT_EXTRACT level 0.10 / return 0.006; VEV_4000..6500 all level |corr| ≤ 0.10 / return |corr| ≤ 0.008.
- No pair/basket/hedge candidate exists. HYDROGEL_PACK is an isolated instrument.
- No ConversionObservation fields apply in Round 3 (pure order-book product).

## Microstructure Patterns
- Two-tier symmetric market-maker bots. Inner bots at mid±8 (size ~12), outer bots at mid±10/11 (size ~25). Present every tick.
- L1 sits 5 units inside L2 spread (16 vs 21). L2 is the "real" liquidity backstop; L1 is the thin front-runner.
- All trades happen at L1 (mid±8). Nobody takes L2 — too expensive. Providing L1 liquidity is the only passive path.
- Buyer/seller always empty — all bot-to-bot.
- Tight-spread bursts (7.3% of ticks) = one bot temporarily improves. Best moments to grab a fill by posting inside.
- No intraday seasonality in vol across days. 1-tick return std ~2.17 in all three days.
- Squared-return AC(1) ≈ 0.12 → mild vol clustering but not regime-level.

## Strategy Implications
- **Primary edge: passive MM one tick inside the 16-wide bot spread** (quote at fair±7). 1-tick price priority over bots guarantees you take fills first. Round-trip captures 14 ticks if both sides fill.
- **Secondary edge: L1 imbalance lean** — corr +0.30 at N=1 is strong. Skew quotes with microprice or L1+L2 VWAP as fair.
- **Tertiary edge: upside anchor reversion** — at dev ≥ +25, expect ~−17 over 200 ticks. Edge > 8-tick crossing cost starts at dev ≈ +25 (net ≈ +9) and is material at +30.
- **Do NOT fade downside deviations** (−15 to −35) as mean-reversion — forward return is ~+1 (noise). Asymmetry must be respected.
- **EMA/momentum filters add nothing** — all RMS worse than microprice. Don't over-engineer.
- Position limit 200 is generous; L1 bot size 12 and 337 trades/day means inventory build is slow. Main risk is drift while inventory is one-sided (vol 31.9 over a day).

## Suggested Approach

**Primary strategy: two-sided market-making one tick inside the bot spread, driven by L1+L2 VWAP microprice, with inventory skew and an upside-only mean-reversion taker overlay.**

1. **Fair value (per-tick)**:
   `fair = (b1·av1 + a1·bv1 + b2·av2 + a2·bv2) / (bv1+av1+bv2+av2)`
   — the L1+L2 volume-weighted mid (RMS 2.04 vs next-mid). Fallback to L1 microprice if L2 missing; further fallback to `(b1+a1)/2`. Clip to `[9900, 10080]` for safety.

2. **Base quote placement**:
   - Buy at `round(fair) − 7`, size 20.
   - Sell at `round(fair) + 7`, size 20.
   - Undercuts the bot L1 at mid±8 by exactly 1 tick (guaranteed fill priority).

3. **Inventory skew**: shift both quotes by `−round(0.04 · pos)`. At pos = +50: quotes move 2 ticks down (bid to fair−9, ask to fair−5). At pos = +200 (hard limit), skew = 8 — pulls you fully off the long side.

4. **Imbalance lean (optional, small)**:
   `imb = (bv1 − av1) / (bv1 + av1)`; shift both quotes by `+round(3 · imb)`. At |imb|=1 this moves quotes 3 ticks — matches 0.30 corr × ret_std(≈2.17) × few-tick horizon edge.

5. **Upside mean-reversion taker overlay**:
   - If `fair − 10000 ≥ +25` AND `pos > −150`: cross and sell at `bid_price_1`, size `min(10, 180 + pos)`. Expected 200-tick P&L ≈ +10 per unit (17 ticks reversion − 8 tick crossing cost + quote-capture bonus); edge grows at larger dev.
   - **Do NOT mirror on downside** — forward return is ~0 for dev ≤ −15.
   - Scale overlay size with dev: size = 10 at dev=+25, size=15 at dev=+35, size=20 at dev=+45.

6. **Opportunistic taker on tight-spread ticks**:
   When L1 spread < 16 (7% of ticks), an aggressive quote is inside the bot range. If current best ask ≤ fair+4 (i.e., a "stale" cheap ask has appeared), lift it up to `min(av1, 200−pos)`. Mirror on the bid. This captures the residual 2,180 tight rows.

7. **Sizing & risk caps**:
   - Per-side passive size: 20 base, clipped to `max(1, 180 − |pos|)` near limit.
   - Hard-stop at |pos| = 180 (20 buffer below 200).
   - If |pos| > 150 for 100+ ticks, force unwind by quoting at fair ± 5 (more aggressive).

**Expected PnL**: with ~337 bot trades/day and 1-tick price priority, you should capture 30-50% of flow ≈ 100-170 fills/day × 4 qty × 7 ticks = **2,800-4,700/day gross from making**. Reversion overlay adds ~200-400/day on extreme dev events. Subtract adverse-selection (expect 20-30% of edge lost to drift).

**Risk considerations**:
- Inventory-vs-drift: if unfilled on one side during a mean-revert move, you can carry PnL-negative inventory (std of mid over 300 ticks ≈ 2.17·√300 ≈ 37 per unit). Skew coefficient 0.04 is aggressive enough to offset.
- Vol bursts (squared-ret AC(1) ≈ 0.12): if 1-tick |return| > 6 in a single tick, widen to fair ± 8 (match bots) for next 20 ticks.
- Sanity: max drawdown should be < 1500 given mid-range ≈ 190 and pos-limit 200. If backtest shows worse, fix inventory skew first.
