---
name: ASH_COATED_OSMIUM Round 1 profile
description: Microstructure, fair-value anchor, and strategy knobs for ASH_COATED_OSMIUM (Round 1, position limit 80)
type: project
---

Tight stationary product anchored at ~10000 with a wide bot maker quoting ±8 (spread mode 16 on 64% of ticks, range 5-22).

**Why:** observed across all 3 days (-2, -1, 0); mid std only 3.8-5.2 per day; mid changes have AR(1) = -0.50 (bid-ask bounce); OU half-life of deviation from 100-tick rolling median is ~1.5 ticks.

**How to apply:** use a dynamic fair value (blend microprice + rolling median), not a static 10000. Order-flow imbalance `(bv1-av1)/(bv1+av1)` has 0.59 correlation with next-tick d_mid; 100-tick z-score < -2 implies +3.2 fwd5 move, > +2 implies -2.7. Taker sniping needs ≥4 tick cushion; static-fair sniping loses (snapped dislocations reflect fair drift). Market-make at ±3 inside mid with inventory skew (~-0.1·position). Trades are ~420/day at size median 5, all execute at touch (0% inside).

Key stats:
- L1 depth mean 14, L2 wall 25; L2 present 68% of ticks, L3 only 2.7%
- Empty-book 7.9% of ticks (handle gracefully)
- Wide-bot mean bid 9991.8 / ask 10008.6
- Position limit 80, safe working cap ~60
