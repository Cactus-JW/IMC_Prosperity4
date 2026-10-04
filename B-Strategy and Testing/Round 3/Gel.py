try:
    # Submission/runtime layout (datamodel.py beside strategy root)
    from datamodel import OrderDepth, TradingState, Order
except ModuleNotFoundError:
    # prosperity4bt package layout
    from prosperity4bt.datamodel import OrderDepth, TradingState, Order
from typing import List, Tuple
import json

# ---------------------------------------------------------------------------
# Constants — HYDROGEL_PACK
# ---------------------------------------------------------------------------
HGP_PRODUCT = "HYDROGEL_PACK"
HGP_POSITION_LIMIT = 200          # exchange position limit
HGP_HARD_CAP = 180                # internal hard cap: keep 20 headroom for takes
HGP_PASSIVE_GATE = 150            # gate passive quotes beyond this |pos|
HGP_ANCHOR = 10000                # long-run mean price anchor
HGP_FAIR_CLIP_LO = 9880           # safety clip on fair value
HGP_FAIR_CLIP_HI = 10120          # safety clip on fair value
HGP_MAKE_SPREAD = 7               # half-spread inside the 16-wide bot spread (param-sweep candidate)
HGP_MAKE_SPREAD_WIDE = 8          # widened half-spread during vol bursts (param-sweep candidate)
HGP_VOL_BURST_RET = 6             # |1-tick return| threshold to declare a vol burst (param-sweep candidate)
HGP_VOL_BURST_TICKS = 20          # how many ticks to keep widened spread after a vol burst (param-sweep candidate)
HGP_MAKE_SIZE = 20                # lots per maker side (param-sweep candidate)
HGP_INV_SKEW_COEFF = 0.06         # inventory skew coefficient: shift = -round(coeff * pos) (param-sweep candidate)
HGP_IMB_COEFF = 4                 # imbalance lean coefficient: shift = +round(coeff * imb) (param-sweep candidate)
HGP_TAKER_DEV_THRESH = 30         # upside taker overlay fires when dev >= this (param-sweep candidate)
HGP_FLATTEN_TICKS = 100           # force-flatten after |pos| > PASSIVE_GATE for this many ticks (param-sweep candidate)


# ---------------------------------------------------------------------------
# HYDROGEL_PACK helper
# ---------------------------------------------------------------------------

def _run_hydrogel(state: TradingState, persisted: dict) -> Tuple[dict, dict]:
    """
    Two-sided market-maker for HYDROGEL_PACK.

    Fair value: L1+L2 volume-weighted mid (best simple estimator per analysis, RMS 2.04).
    Layers (in priority order):
      0. Force-flatten: cross spread when stuck above PASSIVE_GATE for HGP_FLATTEN_TICKS ticks.
      1. Upside mean-reversion taker overlay: sell aggressively when fair−10000 >= +25 (upside-only).
      2. Passive maker quotes: bid at round(fair)−spread, ask at round(fair)+spread, size 20
         each side; continuous inventory skew (−round(0.04·pos)) and imbalance lean (+round(3·imb)).
         Spread widens from 7 to 8 during vol bursts (1-tick |ret| > 6).
    """
    if HGP_PRODUCT not in state.order_depths:
        return {}, persisted

    order_depth: OrderDepth = state.order_depths[HGP_PRODUCT]
    position: int = state.position.get(HGP_PRODUCT, 0)
    orders: List[Order] = []

    # ------------------------------------------------------------------ book
    has_bid = bool(order_depth.buy_orders)
    has_ask = bool(order_depth.sell_orders)

    if not (has_bid and has_ask):
        # One-sided book — skip this tick entirely to be safe
        return {}, persisted

    # Sort levels: bids descending, asks ascending
    sorted_bids = sorted(order_depth.buy_orders.keys(), reverse=True)
    sorted_asks = sorted(order_depth.sell_orders.keys())

    best_bid = sorted_bids[0]
    best_ask = sorted_asks[0]
    bv1 = order_depth.buy_orders[best_bid]          # positive
    av1 = -order_depth.sell_orders[best_ask]         # make positive

    # L2 levels (if present)
    b2 = sorted_bids[1] if len(sorted_bids) >= 2 else None
    a2 = sorted_asks[1] if len(sorted_asks) >= 2 else None
    bv2 = order_depth.buy_orders[b2] if b2 is not None else 0       # positive
    av2 = (-order_depth.sell_orders[a2]) if a2 is not None else 0   # make positive

    # L1+L2 volume-weighted mid (RMS 2.04 vs next mid — best simple estimator)
    # fair = (b1·av1 + a1·bv1 + b2·av2 + a2·bv2) / (bv1+av1+bv2+av2)
    total_vol = bv1 + av1 + bv2 + av2
    if total_vol > 0:
        if b2 is not None and a2 is not None:
            vwap = (best_bid * av1 + best_ask * bv1 + b2 * av2 + a2 * bv2) / total_vol
        else:
            # Fallback to L1 microprice if L2 is missing
            l1_vol = bv1 + av1
            vwap = (best_bid * av1 + best_ask * bv1) / l1_vol if l1_vol > 0 else (best_bid + best_ask) / 2.0
    else:
        vwap = (best_bid + best_ask) / 2.0

    # Safety clip
    fair = max(HGP_FAIR_CLIP_LO, min(HGP_FAIR_CLIP_HI, vwap))

    mid = (best_bid + best_ask) / 2.0
    spread = best_ask - best_bid

    # Deviation from long-run anchor (used for taker overlay)
    dev = fair - HGP_ANCHOR

    # ------------------------------------------------------------------ vol burst detection
    prev_mid: float = persisted.get("hgp_prev_mid", mid)
    ret = mid - prev_mid
    persisted["hgp_prev_mid"] = mid

    vol_burst_remaining: int = persisted.get("hgp_vol_burst_remaining", 0)
    if abs(ret) > HGP_VOL_BURST_RET:
        vol_burst_remaining = HGP_VOL_BURST_TICKS
    elif vol_burst_remaining > 0:
        vol_burst_remaining -= 1
    persisted["hgp_vol_burst_remaining"] = vol_burst_remaining

    # Widen spread during vol burst
    active_spread = HGP_MAKE_SPREAD_WIDE if vol_burst_remaining > 0 else HGP_MAKE_SPREAD

    # ------------------------------------------------------------------ signals
    # L1 imbalance: positive → price likely to rise → lean quotes up
    imb_denom = bv1 + av1
    imb = (bv1 - av1) / imb_denom if imb_denom > 0 else 0.0
    imb_shift = round(HGP_IMB_COEFF * imb)

    # Inventory skew: negative when long (lean asks down to sell), positive when short
    inv_shift = -round(HGP_INV_SKEW_COEFF * position)

    # Combined shift applied to both bid and ask
    total_shift = inv_shift + imb_shift

    # ------------------------------------------------------------------ cross-tick state
    over_cap_ticks: int = persisted.get("hgp_over_cap_ticks", 0)

    # ------------------------------------------------------------------ position headroom
    buy_room = HGP_HARD_CAP - position        # units we can still buy (hard cap 180)
    sell_room = HGP_HARD_CAP + position       # units we can still sell short (hard cap 180)

    # Track committed volume from taker/flatten orders to avoid double-counting
    committed_buy = 0
    committed_sell = 0

    # ======================================================================
    # Layer 0: force-flatten when stuck above PASSIVE_GATE for too long
    # ======================================================================
    if abs(position) > HGP_PASSIVE_GATE:
        over_cap_ticks += 1
    else:
        over_cap_ticks = 0

    if over_cap_ticks >= HGP_FLATTEN_TICKS:
        # Cross the spread to reduce inventory
        if position > 0 and has_bid:
            flatten_qty = min(position - HGP_PASSIVE_GATE, bv1, sell_room)
            if flatten_qty > 0:
                orders.append(Order(HGP_PRODUCT, best_bid, -flatten_qty))
                committed_sell += flatten_qty
                print(f"  [HGP FLATTEN] SELL {flatten_qty}x {best_bid} pos={position}")
        elif position < 0 and has_ask:
            flatten_qty = min(-position - HGP_PASSIVE_GATE, av1, buy_room)
            if flatten_qty > 0:
                orders.append(Order(HGP_PRODUCT, best_ask, flatten_qty))
                committed_buy += flatten_qty
                print(f"  [HGP FLATTEN] BUY {flatten_qty}x {best_ask} pos={position}")

    persisted["hgp_over_cap_ticks"] = over_cap_ticks

    # ======================================================================
    # Layer 1: upside mean-reversion taker overlay (UPSIDE ONLY)
    # Analysis: at dev >= +25, 200-tick forward return = -17.3 (vs -1 at downside).
    # Downside deviations do NOT mirror — forward return is ~0 for dev <= -15.
    # Scale taker size: 10 at dev=25, 15 at dev=35, 20 at dev=45.
    # ======================================================================
    if dev >= HGP_TAKER_DEV_THRESH and position > -HGP_HARD_CAP and abs(position) < HGP_PASSIVE_GATE:
        if dev >= 45:
            taker_size = 20
        elif dev >= 35:
            taker_size = 15
        else:
            taker_size = 10

        taker_sell_room = sell_room - committed_sell
        taker_qty = min(taker_size, bv1, taker_sell_room)
        if taker_qty > 0:
            orders.append(Order(HGP_PRODUCT, best_bid, -taker_qty))
            committed_sell += taker_qty
            print(f"  [HGP TAKER] SELL {taker_qty}x {best_bid} dev={dev:.1f} size_tier={taker_size}")

    # ======================================================================
    # Layer 2: passive maker quotes
    # Bid at round(fair) - active_spread + total_shift
    # Ask at round(fair) + active_spread + total_shift
    # Both quotes shift together to express directional lean while preserving spread.
    # ======================================================================
    fair_r = round(fair)
    make_bid_px = fair_r - active_spread + total_shift
    make_ask_px = fair_r + active_spread + total_shift

    # Gate passive quotes at |pos| >= PASSIVE_GATE (avoid adding to an already extreme book)
    if abs(position) < HGP_PASSIVE_GATE:
        make_buy_room = buy_room - committed_buy
        make_buy_qty = min(HGP_MAKE_SIZE, make_buy_room)
        if make_buy_qty > 0:
            orders.append(Order(HGP_PRODUCT, make_bid_px, make_buy_qty))
            print(
                f"  [HGP MAKE] BID {make_buy_qty}x {make_bid_px} "
                f"fair={fair:.2f} inv_shift={inv_shift} imb_shift={imb_shift} spread={active_spread}"
            )

        make_sell_room = sell_room - committed_sell
        make_sell_qty = min(HGP_MAKE_SIZE, make_sell_room)
        if make_sell_qty > 0:
            orders.append(Order(HGP_PRODUCT, make_ask_px, -make_sell_qty))
            print(
                f"  [HGP MAKE] ASK {make_sell_qty}x {make_ask_px} "
                f"fair={fair:.2f} inv_shift={inv_shift} imb_shift={imb_shift} spread={active_spread}"
            )

    print(
        f"[HGP] t={state.timestamp} pos={position} mid={mid:.2f} fair={fair:.2f} "
        f"dev={dev:.1f} L1spread={spread} active_spread={active_spread} "
        f"inv_shift={inv_shift} imb_shift={imb_shift} vol_burst={vol_burst_remaining}"
    )

    return {HGP_PRODUCT: orders}, persisted


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------
class Trader:
    def run(self, state: TradingState):
        if state.traderData:
            try:
                persisted = json.loads(state.traderData)
            except json.JSONDecodeError:
                persisted = {}
        else:
            persisted = {}

        # Ensure all cross-tick keys exist with safe defaults
        persisted.setdefault("hgp_over_cap_ticks", 0)
        persisted.setdefault("hgp_prev_mid", 10000.0)
        persisted.setdefault("hgp_vol_burst_remaining", 0)

        result = {}

        hgp_result, persisted = _run_hydrogel(state, persisted)
        result.update(hgp_result)

        return result, 0, json.dumps(persisted)
