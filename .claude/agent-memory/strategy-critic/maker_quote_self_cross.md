---
name: Maker quote self-cross from combined inv/imb shift
description: When a strategy applies a single shared shift to both bid_px and ask_px around fair, large imb_shift + inv_shift can push the bid above the ask, or above the best bid, causing self-crosses or aggressive lifts disguised as passive maker orders
type: feedback
---

When a maker layer computes bid_px = round(fair) - spread + total_shift and ask_px = round(fair) + spread + total_shift with no clamp to (best_ask-1) / (best_bid+1), large positive total_shift (long imbalance + short inventory) sends a buy order through the best ask — the order is no longer "passive" but a market take at unintended size.

**Why:** Saw this in HYDROGEL_PACK Round 3 (GelVelvetComb.py): HGP_IMB_COEFF=4 plus HGP_INV_SKEW_COEFF=0.06 over pos=±150 produces total_shift up to ±13. With active_spread=7 and a 16-tick bot spread (best_bid = mid-8, best_ask = mid+8), a +13 shift puts make_bid at mid+5 — which is 13 ticks above bot best_bid and 3 ticks below bot best_ask. Symmetric problem for negative shifts. The shift is supposed to express directional lean while "preserving spread", but in a tight 16-spread world the shift magnitude is comparable to the half-spread.

**How to apply:** When reviewing maker strategies that apply a directional shift to both quote sides, check whether (max |total_shift|) is < (active_half_spread − 1). If not, flag it as a self-cross / unintended-take risk. Always ask whether bid_px is clamped to <= best_ask − 1 and ask_px to >= best_bid + 1.
