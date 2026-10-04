---
name: MM+MR layered capacity double-count and direction collision
description: When two superimposed layers (market-making + mean-reversion) compute capacity independently, they can collide on the same side, exceed position limit, or have one layer sandbagging the other on opposite sides
type: feedback
---

When a strategy posts an MM bid + MM ask AND on top of that posts an MR bid (when below mean) or MR ask (when above mean), capacity accounting must subtract both. VelvetMM-MR.py only subtracts MM allocation when sizing the MR side (`remaining_buy = buy_cap - mm_bid_vol`), so the joint-fill case (MM bid fills + MR bid fills + MR is at-or-through best_ask = aggressive take) can blow through the 200 limit in a single tick.

A second, subtler problem: when z<0 the strategy posts a passive MM ask (size=MM_VOLUME, unsklewed when pos>=0) AND an aggressive MR bid (offset 0 ⇒ at best_ask). The MR bid is supposed to express bullish reversion conviction, but the MM ask is simultaneously selling at best_ask-1 — i.e. the strategy is buying and selling at adjacent prices in the same tick, paying the spread to itself across two layers.

**Why:** Layered "MM + alpha" strategies look modular but the layers share inventory. The MR layer's directional lean is structurally cancelled by the MM layer's symmetric quoting unless MM is suppressed on the off-side when MR is active.

**How to apply:** When reviewing combined MM+MR/alpha strategies, check (1) whether the off-side MM quote is reduced or removed when the alpha layer is active and directional, and (2) whether the MR price calc accounts for fills that would push position past limit when summed with concurrent MM fills.
