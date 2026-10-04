---
name: Anchor-based fair-clip blind to regime drift
description: Strategies that hard-clip fair to a band around an assumed long-run anchor will quote stale fair if the anchor itself drifts mid-day or across days
type: feedback
---

When fair is clipped to [ANCHOR-X, ANCHOR+X] and ANCHOR is a constant (e.g. 10000 for HYDROGEL_PACK, or implied via VFE_HARD_STOP_LO/HI 5180/5320 for VELVETFRUIT_EXTRACT), any sustained regime shift outside that band causes the strategy to (1) post stale quotes that get one-sided picked off, and (2) trip the upside-deviation taker ad infinitum if mean reversion stops. VFE per the data-explorer profile already showed end-of-day drifts up to +28; over a multi-day live run the band [5180, 5320] is plausible to breach, at which point all maker bids or asks get pinned to the band edge.

**Why:** In Round 1 SQUID_INK had similar anchor-based logic that broke when the regime shifted; mean-reversion strategies are systematically vulnerable to this.

**How to apply:** Any time a strategy has a hardcoded ANCHOR or HARD_STOP band, ask: (a) what's the worst observed deviation in the data, (b) is the band wider than that, and (c) what does the strategy do when fair is pinned to the band edge for many consecutive ticks? If the answer to (c) is "keep posting at the edge", that is a failure mode.
