try:
    # Submission/runtime layout (datamodel.py beside strategy root)
    from datamodel import OrderDepth, TradingState, Order
except ModuleNotFoundError:
    # prosperity3bt package layout
    from prosperity4bt.datamodel import OrderDepth, TradingState, Order
from typing import List, Tuple
import json

# ---------------------------------------------------------------------------
# Constants — VELVETFRUIT_EXTRACT
# ---------------------------------------------------------------------------
VFE_PRODUCT = "VELVETFRUIT_EXTRACT"
VFE_POSITION_LIMIT = 200

# Fair value blending weights
VFE_MICROPRICE_ALPHA = 2.0      # imbalance coefficient in microprice (param-sweep candidate)
VFE_EMA_BLEND = 0.3             # weight on EMA vs microprice in fair value (param-sweep candidate)
VFE_EMA_SPAN = 500              # EMA span in ticks — matches OU half-life ~251 ticks (param-sweep candidate)

# Market-making quotes
VFE_MAKE_SPREAD = 3             # half-spread around fair for maker quotes (param-sweep candidate)
VFE_MAKE_SIZE = 20              # base lots per maker side (param-sweep candidate)

# Inventory skew
VFE_INV_SKEW = 0.02             # quote-shift per unit of position (param-sweep candidate)
VFE_INV_CUT_THRESH = 100        # cut trailing-side size when |pos| > this (param-sweep candidate)
VFE_INV_SUPPRESS_THRESH = 180   # suppress trailing side entirely when |pos| >= this (param-sweep candidate)

# Asymmetric-book guard
VFE_THIN_SPREAD_MAX = 3         # apply guard only when spread <= this (param-sweep candidate)
VFE_THIN_RATIO_LO = 0.25        # bv1/|av1| lower bound for "balanced" book (param-sweep candidate)
VFE_THIN_RATIO_HI = 4.0         # bv1/|av1| upper bound for "balanced" book (param-sweep candidate)
VFE_THIN_COOLDOWN = 3           # ticks to suppress thin-side join after guard triggers (param-sweep candidate)

# Aggressive taker layer
VFE_TAKER_THRESH = 1            # lift ask <= fair - thresh, hit bid >= fair + thresh (param-sweep candidate)
VFE_TAKER_MAX = 8               # max lots per taker order (param-sweep candidate)

# Hard band stop
VFE_HARD_STOP_LO = 5180         # do not post bids below this (param-sweep candidate)
VFE_HARD_STOP_HI = 5320         # do not post asks above this (param-sweep candidate)


# ---------------------------------------------------------------------------
# Helpers — VELVETFRUIT_EXTRACT
# ---------------------------------------------------------------------------

def _vfe_ema_alpha(span: int) -> float:
    """Decay factor for an EMA of the given span. α = 2/(span+1)."""
    return 2.0 / (span + 1)


def _run_vfe(state: TradingState, persisted: dict) -> Tuple[dict, dict]:
    """
    Three-layer strategy for VELVETFRUIT_EXTRACT:
      1. Passive MM at fair ± MAKE_SPREAD with inventory skew and asymmetric-book guard.
      2. Aggressive taker: lift cheap asks / hit rich bids.
      3. Hard band: no quotes outside [5180, 5320].
    """
    if VFE_PRODUCT not in state.order_depths:
        return {}, persisted

    order_depth: OrderDepth = state.order_depths[VFE_PRODUCT]
    position = state.position.get(VFE_PRODUCT, 0)
    orders: List[Order] = []

    # -----------------------------------------------------------------------
    # Book snapshot
    # -----------------------------------------------------------------------
    has_bid = bool(order_depth.buy_orders)
    has_ask = bool(order_depth.sell_orders)

    if not (has_bid and has_ask):
        # One-sided book — nothing safe to quote against; skip this tick
        return {}, persisted

    best_bid = max(order_depth.buy_orders.keys())
    best_ask = min(order_depth.sell_orders.keys())
    bv1 = order_depth.buy_orders[best_bid]          # positive
    av1_raw = order_depth.sell_orders[best_ask]     # negative (convention)
    av1 = -av1_raw                                  # make positive for arithmetic

    spread = best_ask - best_bid
    mid = (best_bid + best_ask) / 2.0

    # -----------------------------------------------------------------------
    # Fair value: 0.7 * microprice + 0.3 * EMA(mid, span=500)
    # microprice = mid + α * (bv1 − av1) / (bv1 + av1), α = 2.0
    # -----------------------------------------------------------------------
    total_vol = bv1 + av1
    imbalance = (bv1 - av1) / total_vol if total_vol > 0 else 0.0
    microprice = mid + VFE_MICROPRICE_ALPHA * imbalance

    # EMA update
    ema_alpha = _vfe_ema_alpha(VFE_EMA_SPAN)
    prev_ema = persisted.get("vfe_ema", mid)
    ema = prev_ema + ema_alpha * (mid - prev_ema)
    persisted["vfe_ema"] = ema

    fair = (1.0 - VFE_EMA_BLEND) * microprice + VFE_EMA_BLEND * ema

    # -----------------------------------------------------------------------
    # Asymmetric-book guard: track per-side cooldown
    # If spread <= 3 and book is heavily one-sided, ban joining the thin side
    # for VFE_THIN_COOLDOWN ticks (thin side = direction of imminent drift).
    # -----------------------------------------------------------------------
    bid_cooldown: int = persisted.get("vfe_bid_cooldown", 0)
    ask_cooldown: int = persisted.get("vfe_ask_cooldown", 0)

    if spread <= VFE_THIN_SPREAD_MAX and av1 > 0:
        ratio = bv1 / av1
        if ratio < VFE_THIN_RATIO_LO:
            # bid side is thin — bids are about to be consumed; ban new bids
            bid_cooldown = VFE_THIN_COOLDOWN
        elif ratio > VFE_THIN_RATIO_HI:
            # ask side is thin — asks are about to be consumed; ban new asks
            ask_cooldown = VFE_THIN_COOLDOWN

    # Decrement cooldowns (floor at 0)
    bid_cooldown = max(0, bid_cooldown - 1)
    ask_cooldown = max(0, ask_cooldown - 1)
    persisted["vfe_bid_cooldown"] = bid_cooldown
    persisted["vfe_ask_cooldown"] = ask_cooldown

    # -----------------------------------------------------------------------
    # Inventory skew: shift both quotes by −pos * INV_SKEW
    # This leans quotes against the current inventory to mean-revert position.
    # -----------------------------------------------------------------------
    inv_shift = -position * VFE_INV_SKEW
    skewed_fair = fair + inv_shift

    # Quote prices (round to nearest int — exchange uses integer prices)
    bid_px = round(skewed_fair) - VFE_MAKE_SPREAD
    ask_px = round(skewed_fair) + VFE_MAKE_SPREAD

    # Hard band enforcement
    bid_px = max(bid_px, VFE_HARD_STOP_LO)
    ask_px = min(ask_px, VFE_HARD_STOP_HI)

    # -----------------------------------------------------------------------
    # Taker layer: take aggressively mispriced touches
    # Lift any ask <= fair - TAKER_THRESH, hit any bid >= fair + TAKER_THRESH
    # Size: min(available, TAKER_MAX, room to limit)
    # -----------------------------------------------------------------------
    taker_buy = 0
    taker_sell = 0

    buy_room = VFE_POSITION_LIMIT - position
    sell_room = VFE_POSITION_LIMIT + position  # room to go short

    # Taker buy
    if best_ask <= fair - VFE_TAKER_THRESH:
        clip = min(av1, VFE_TAKER_MAX, buy_room)
        if clip > 0:
            orders.append(Order(VFE_PRODUCT, best_ask, clip))
            taker_buy += clip
            print(f"  [VFE TAKER] BUY {clip}x at {best_ask} (fair={fair:.2f})")

    # Taker sell
    if best_bid >= fair + VFE_TAKER_THRESH:
        clip = min(bv1, VFE_TAKER_MAX, sell_room)
        if clip > 0:
            orders.append(Order(VFE_PRODUCT, best_bid, -clip))
            taker_sell += clip
            print(f"  [VFE TAKER] SELL {clip}x at {best_bid} (fair={fair:.2f})")

    # Recompute position after taker to size maker orders correctly
    pos_after_taker = position + taker_buy - taker_sell
    buy_room_mm = VFE_POSITION_LIMIT - pos_after_taker
    sell_room_mm = VFE_POSITION_LIMIT + pos_after_taker

    # -----------------------------------------------------------------------
    # Maker sizes with inventory suppression
    # -----------------------------------------------------------------------
    abs_pos = abs(pos_after_taker)

    if abs_pos >= VFE_INV_SUPPRESS_THRESH:
        # Near limit: suppress the trailing (inventory-adding) side entirely
        if pos_after_taker > 0:
            # Long: suppress bids, allow asks
            bid_size = 0
            ask_size = VFE_MAKE_SIZE
        else:
            # Short: suppress asks, allow bids
            bid_size = VFE_MAKE_SIZE
            ask_size = 0
    elif abs_pos > VFE_INV_CUT_THRESH:
        # Elevated inventory: halve the bad-side size
        if pos_after_taker > 0:
            bid_size = max(0, VFE_MAKE_SIZE // 2)
            ask_size = VFE_MAKE_SIZE
        else:
            bid_size = VFE_MAKE_SIZE
            ask_size = max(0, VFE_MAKE_SIZE // 2)
    else:
        bid_size = VFE_MAKE_SIZE
        ask_size = VFE_MAKE_SIZE

    # Clip to position limits
    bid_size = min(bid_size, buy_room_mm)
    ask_size = min(ask_size, sell_room_mm)

    # Apply asymmetric-book cooldown
    if bid_cooldown > 0:
        bid_size = 0
    if ask_cooldown > 0:
        ask_size = 0

    # Post maker orders (only if within hard band and non-zero size)
    if bid_size > 0 and bid_px >= VFE_HARD_STOP_LO:
        orders.append(Order(VFE_PRODUCT, bid_px, bid_size))
        print(f"  [VFE MAKE] BID {bid_size}x at {bid_px} (fair={fair:.2f} skew_fair={skewed_fair:.2f})")

    if ask_size > 0 and ask_px <= VFE_HARD_STOP_HI:
        orders.append(Order(VFE_PRODUCT, ask_px, -ask_size))
        print(f"  [VFE MAKE] ASK {ask_size}x at {ask_px} (fair={fair:.2f} skew_fair={skewed_fair:.2f})")

    print(
        f"[VFE] t={state.timestamp} pos={position} mid={mid:.2f} "
        f"microprice={microprice:.2f} ema={ema:.2f} fair={fair:.2f} "
        f"imb={imbalance:.3f} spread={spread} "
        f"bid_cd={bid_cooldown} ask_cd={ask_cooldown}"
    )

    return {VFE_PRODUCT: orders}, persisted


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

        result = {}

        vfe_result, persisted = _run_vfe(state, persisted)
        result.update(vfe_result)

        return result, 0, json.dumps(persisted)
