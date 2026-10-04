# Analysis: VELVETFRUIT_EXTRACT — 2026-04-24

## Key Statistics
- Sample: 30,000 mid snapshots and 1,372 trades across days 0-2 (ts 0 → 999,900 each day)
- Mid: mean 5250.1, stdev 15.63, range [5198, 5300] — anchored band ~±50 around 5250
- Per-day end-to-end drift: day0 −6.0, day1 +20.5, day2 +28.0 (mid moves persist, not zero-sum)
- Linear-trend R² per day is low (0.01–0.08), so drift is noisy, not a reliable signal
- 1-tick return stdev = 1.131, |ret| percentiles p50=1.0, p90=2.0, p99=3.0, max=5.5
- 24.7% of ticks have zero mid-change; move distribution is discrete half-ticks
- AC(1) of returns = −0.1585 (strong single-tick mean reversion), AC(≥2) < 0.01 (noise)
- AC(1) of mid-levels φ = 0.99724 → OU half-life ≈ 251 ticks (slow drift back to local mean)
- 78.1% of mids are on .5 half-ticks — tick size is 1 but quoting is half-tick granular

## Microstructure Patterns
- Dominant book shape: 2-level symmetric with top spread = 5, tier gap = 1 on both sides (6,938 / 30k snapshots). Wider-book variant: top spread 6 with ~60-lot single tier (5,475 snapshots)
- Spread distribution: 5 → 74.2%, 6 → 18.2%, 2 → 3.4%, 3 → 3.3%; never crosses, rarely 1 (0.6%) or 4 (0.2%)
- Outer (widest) spread: 6 in 75% of ticks, 7 in 25% — a consistent two-tier MM bot footprint
- Top-of-book volume is bimodal: ~15–25 lots (tight-spread regime, spread 5) and ~55–75 lots (spread 6). Symmetric: mean bv1 ≈ mean av1 = 37.8
- Spread 2 and 3 snapshots are **asymmetric**: one side has ~35 lots (heavy) while the other has ~10 lots — the thin side is the direction of imminent drift (aggressor just lifted a level)
- Order-book imbalance → next-tick return correlation = +0.281 (n=30k), decays to +0.19 at 3 ticks, +0.14 at 5, +0.11 at 10, +0.08 at 20 — short-horizon edge, half-life ≈ 10 ticks
- Conditional mean next return: prev tick +1 → −0.144; prev tick −1 → +0.179; prev 0 → −0.044 (clear 1-tick reversion)
- Trade sizes cluster uniformly at 3–8 (190–233 each), heavy tail to 15; mean qty 6.03; total 8,269 lots over 3 days (≈ 2,756 lots/day, 1 trade/22 ticks)

## Strategy Implications
- Two distinct edges co-exist: (1) **passive MM inside the bot spread** — at top spread 5 (74% of book), quoting at mid ± 2 sits tighter than the bots' ±2.5 inner tier; (2) **imbalance signal** — fades 1-tick moves and predicts direction over ~10 ticks
- Slow OU (half-life 251) means inventory can be held over dozens of ticks without large MtM drag — but the ±50 band means a 200-lot position at an unfavorable tail is worth roughly 200 × 25 = 5,000 seashells of adverse MtM risk
- The asymmetric thin-side books (spread 2/3) are NOT free money: the thin side is where price is about to go (aggressor already consumed it); joining that side's quote will get run over
- Tick-by-tick reversion (AC1 = −0.16) + imbalance signal both point to **skewing** quotes rather than centering, especially when bv1/av1 diverge
- Spread 6 regime = wider bot, higher volumes; good regime for passive MM since our improved quotes rarely face adverse selection from a tight counterparty
- Position-limit 200 is generous vs typical trade size 6; fills will be many small lots, so inventory builds slowly

## Suggested Approach
**Fair value model:** Microprice-skewed anchor. `fair = 0.5*(bid1+ask1) + α*(bv1−|av1|)/(bv1+|av1|)` with α ≈ 2.0 (empirically imbalance corr ≈ 0.28 with stdev 1.13 1-tick returns, so ~0.28 × 1.13 / stdev(imb) ≈ 2 seashells slope). Combine with a slow EMA of mid (span ≈ 500 ticks, matching OU half-life 251) as an anchor prior, blended 0.7 * microprice + 0.3 * ema.

**Quoting:**
- **Primary MM layer** at `fair − 2` / `fair + 2` in size ~15 lots per side. This sits inside the dominant 5-wide bot spread and inside the 6-wide regime's inner tier. Do not improve when spread ≤ 3 (tight regime — just post top-of-book joiners).
- **Inventory skew:** shift both quotes by `−pos * 0.03` seashells (position 200 → skew of 6 ticks), and cut size on the bad side when |pos| > 100. At |pos| ≥ 180 suppress the trailing side entirely.
- **Asymmetric-book guard:** if spread ≤ 3 AND bv1/|av1| ratio outside [0.25, 4.0], do not join the thin side for 3 ticks — that is the predicted drift direction.

**Aggressive take layer:** lift any ask ≤ fair − 1 or hit any bid ≥ fair + 1, size up to min(available, 8 lots, 200 − pos). This captures the rare spread-1/2/3 dislocations where the far side is cheap relative to microprice.

**Risk considerations:** (i) mid never visited outside 5198–5300, so use hard-stops beyond 5180/5320 for worst-case inventory liquidation, treating anything further as regime break; (ii) AC(1) = −0.16 implies do NOT chase — if our own quote got filled and mid moved through it, re-evaluate the imbalance before re-quoting; (iii) the ±50 drawdown envelope × 200 limit = 10k max MtM; size primary quotes so expected per-trade adverse selection stays below capture of 2 seashells per round-trip.