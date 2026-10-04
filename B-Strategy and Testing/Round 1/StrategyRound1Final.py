try:
    # Submission/runtime layout (datamodel.py beside strategy root)
    from datamodel import OrderDepth, TradingState, Order
except ModuleNotFoundError:
    # prosperity3bt package layout
    from prosperity3bt.datamodel import OrderDepth, TradingState, Order
from typing import List, Tuple
import math
import json


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
INTARIAN_PRODUCT = "INTARIAN_PEPPER_ROOT"
ASH_PRODUCT = "ASH_COATED_OSMIUM"

INTARIAN_POSITION_LIMIT = 80
INTARIAN_SELL_VOL = 10
INTARIAN_END_OF_DAY_TS = 950_000

ASH_POSITION_LIMIT = 80
ASH_MID_HISTORY_LEN = 100   # rolling window for median / std
ASH_FAIR_CLAMP_LO = 9995
ASH_FAIR_CLAMP_HI = 10005
ASH_IMB_COEFF = 4.7         # imbalance coefficient (from regression)
ASH_Z_COEFF = 0.5           # z-score coefficient (from regression)
ASH_QUOTE_SPREAD = 3        # half-spread around edge for maker quotes
ASH_MAKE_SIZE = 10          # max units per maker side
ASH_INV_SKEW = 0.1          # inventory skew: shift per unit of position
ASH_TAKER_THRESH = 4        # ticks of edge-beat required to lift/hit
ASH_TAKER_MAX = 15          # max taker clip per tick
ASH_SOFT_CAP = 60           # soft inventory cap (stop adding at same side)
ASH_HARD_STOP = 70          # hard stop: suppress add-side quoting

# Spread coefficients from IntarianAnalysis.py Figure 7 findings:
#   ask offset ≈ 0.0012 * 0.30 * day_start_price above modelled_midprice
#   bid offset ≈ 0.0012 * 0.19 * day_start_price below  modelled_midprice
ASK_SPREAD_COEFF = 0.0012 * 0.3
BID_SPREAD_COEFF = 0.0012 * 0.19


# ---------------------------------------------------------------------------
# Helpers for INTARIAN_PEPPER_ROOT
# ---------------------------------------------------------------------------
def _compute_day_start_price(order_depth: OrderDepth) -> int:
    """First-tick best-bid/ask midprice rounded to nearest 1 000."""
    bids = order_depth.buy_orders
    asks = order_depth.sell_orders
    if bids and asks:
        midprice = (max(bids.keys()) + min(asks.keys())) / 2
    elif bids:
        midprice = max(bids.keys())
    elif asks:
        midprice = min(asks.keys())
    else:
        midprice = 10_000
    return round(midprice / 1_000) * 1_000


def _modelled_midprice(timestamp: int, day_start_price: int) -> float:
    """Linear drift model: price rises 1 unit per 1 000 ms of timestamp."""
    return day_start_price + timestamp / 1_000


def _run_intarian(state: TradingState, persisted: dict):
    result = {}

    if INTARIAN_PRODUCT not in state.order_depths:
        return result, persisted

    if state.timestamp == 0:
        persisted["day_start_price"] = None
        persisted["initial_fill_done"] = False

    order_depth: OrderDepth = state.order_depths[INTARIAN_PRODUCT]
    timestamp = state.timestamp
    position = state.position.get(INTARIAN_PRODUCT, 0)
    orders: List[Order] = []

    if persisted["day_start_price"] is None:
        persisted["day_start_price"] = _compute_day_start_price(order_depth)

    day_start_price = persisted["day_start_price"]

    if not persisted["initial_fill_done"] and position >= INTARIAN_POSITION_LIMIT:
        persisted["initial_fill_done"] = True

    mod_mid = _modelled_midprice(timestamp, day_start_price)
    ask_spread = math.ceil(ASK_SPREAD_COEFF * day_start_price)
    bid_spread = math.ceil(BID_SPREAD_COEFF * day_start_price)
    ask_price = round(mod_mid) + ask_spread
    bid_price = round(mod_mid) - bid_spread
    capacity = INTARIAN_POSITION_LIMIT - position

    print(
        f"[{INTARIAN_PRODUCT}] t={timestamp} pos={position} "
        f"day_start={day_start_price} mod_mid={mod_mid:.1f} "
        f"ask={ask_price} bid={bid_price} "
        f"fill_done={persisted['initial_fill_done']}"
    )

    if timestamp >= INTARIAN_END_OF_DAY_TS:
        if capacity > 0:
            orders.append(Order(INTARIAN_PRODUCT, bid_price, capacity))
            print(f"  [Phase 4] BID {capacity}x {bid_price}")

    elif not persisted["initial_fill_done"]:
        remaining = capacity
        for ask_px, ask_qty in sorted(order_depth.sell_orders.items()):
            if remaining <= 0:
                break
            fill_qty = min(remaining, -ask_qty)
            orders.append(Order(INTARIAN_PRODUCT, ask_px, fill_qty))
            print(f"  [Phase 1] BUY {fill_qty}x {ask_px}")
            remaining -= fill_qty

    elif position >= INTARIAN_POSITION_LIMIT:
        orders.append(Order(INTARIAN_PRODUCT, ask_price, -INTARIAN_SELL_VOL))
        print(f"  [Phase 2] ASK {INTARIAN_SELL_VOL}x {ask_price}")

    else:
        orders.append(Order(INTARIAN_PRODUCT, bid_price, capacity))
        print(f"  [Phase 3] BID {capacity}x {bid_price}")

    result[INTARIAN_PRODUCT] = orders
    return result, persisted


# ---------------------------------------------------------------------------
# Helpers for ASH_COATED_OSMIUM
# ---------------------------------------------------------------------------
def _median(values: list) -> float:
    """Return the median of a non-empty list."""
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 == 1 else (s[mid - 1] + s[mid]) / 2.0


def _run_ash(state: TradingState, persisted: dict) -> Tuple[dict, dict]:
    """Dynamic-fair market-maker with imbalance + z-score edge and inventory skew."""
    if ASH_PRODUCT not in state.order_depths:
        return {}, persisted

    order_depth: OrderDepth = state.order_depths[ASH_PRODUCT]
    position = state.position.get(ASH_PRODUCT, 0)
    orders: List[Order] = []

    # --- Restore cross-tick history ---
    mid_history: list = persisted.get("ash_mid_history", [])

    # --- Derive mid-price from book; fallback to last known or 10000 ---
    has_bid = bool(order_depth.buy_orders)
    has_ask = bool(order_depth.sell_orders)

    if has_bid and has_ask:
        best_bid = max(order_depth.buy_orders.keys())
        best_ask = min(order_depth.sell_orders.keys())
        bv1 = order_depth.buy_orders[best_bid]      # positive int
        av1 = -order_depth.sell_orders[best_ask]    # make positive

        # Microprice: volume-weighted touch
        total_vol = bv1 + av1
        microprice = (best_bid * av1 + best_ask * bv1) / total_vol if total_vol > 0 else (best_bid + best_ask) / 2.0

        mid = (best_bid + best_ask) / 2.0
        imbalance = (bv1 - av1) / (bv1 + av1) if (bv1 + av1) > 0 else 0.0
    else:
        # One-sided or empty book — use last mid; skip taker layer this tick
        mid = mid_history[-1] if mid_history else 10000.0
        microprice = mid
        best_bid = max(order_depth.buy_orders.keys()) if has_bid else None
        best_ask = min(order_depth.sell_orders.keys()) if has_ask else None
        bv1 = order_depth.buy_orders[best_bid] if has_bid else 0
        av1 = -order_depth.sell_orders[best_ask] if has_ask else 0
        imbalance = 0.0

    # --- Update rolling history (keep last ASH_MID_HISTORY_LEN ticks) ---
    mid_history.append(mid)
    if len(mid_history) > ASH_MID_HISTORY_LEN:
        mid_history = mid_history[-ASH_MID_HISTORY_LEN:]

    # --- Rolling median and std over history ---
    if len(mid_history) >= 2:
        rolling_median = _median(mid_history)
        mean_h = sum(mid_history) / len(mid_history)
        variance = sum((x - mean_h) ** 2 for x in mid_history) / len(mid_history)
        sd100 = math.sqrt(variance) if variance > 0 else 1.0
    else:
        rolling_median = 10000.0
        sd100 = 1.0

    # --- Fair value: 60% microprice + 40% rolling median, clamped ---
    raw_fair = 0.6 * microprice + 0.4 * rolling_median
    fair = max(ASH_FAIR_CLAMP_LO, min(ASH_FAIR_CLAMP_HI, raw_fair))

    # --- z-score of current mid vs rolling median ---
    z_score = (mid - rolling_median) / sd100

    # --- Directional edge estimate (1-tick-ahead mid) ---
    edge = fair + ASH_IMB_COEFF * imbalance - ASH_Z_COEFF * z_score

    # --- Inventory skew: shift quotes to lean against position ---
    inv_skew = -ASH_INV_SKEW * position  # negative at long pos, positive at short
    edge_skewed = edge + inv_skew

    bid_px = round(edge_skewed) - ASH_QUOTE_SPREAD
    ask_px = round(edge_skewed) + ASH_QUOTE_SPREAD

    print(
        f"[ASH] t={state.timestamp} pos={position} mid={mid:.2f} "
        f"microprice={microprice:.2f} median={rolling_median:.2f} "
        f"fair={fair:.2f} imb={imbalance:.3f} z={z_score:.3f} "
        f"edge={edge:.2f} edge_skw={edge_skewed:.2f} "
        f"bid={bid_px} ask={ask_px}"
    )

    # --- Taker layer: lift ask / hit bid when touch beats edge by >= 4 ticks ---
    if has_ask and best_ask is not None:
        if best_ask <= edge - ASH_TAKER_THRESH:
            room = ASH_POSITION_LIMIT - position
            clip = min(av1, room, ASH_TAKER_MAX)
            if clip > 0:
                orders.append(Order(ASH_PRODUCT, best_ask, clip))
                print(f"  [ASH TAKER] BUY {clip}x at {best_ask} (edge={edge:.2f})")

    if has_bid and best_bid is not None:
        if best_bid >= edge + ASH_TAKER_THRESH:
            room = ASH_POSITION_LIMIT + position   # room to go short
            clip = min(bv1, room, ASH_TAKER_MAX)
            if clip > 0:
                orders.append(Order(ASH_PRODUCT, best_bid, -clip))
                print(f"  [ASH TAKER] SELL {clip}x at {best_bid} (edge={edge:.2f})")

    # --- Maker quotes with inventory hard-stop ---
    # Estimate committed volume from taker orders placed above
    taker_buy = sum(o.quantity for o in orders if o.quantity > 0)
    taker_sell = sum(-o.quantity for o in orders if o.quantity < 0)
    net_pos_after_taker = position + taker_buy - taker_sell

    # Buy side: suppress when already long >= HARD_STOP
    if net_pos_after_taker < ASH_HARD_STOP:
        buy_room = ASH_POSITION_LIMIT - (position + taker_buy)
        make_buy_qty = min(ASH_MAKE_SIZE, buy_room)
        # Reduce size toward soft cap
        if net_pos_after_taker >= ASH_SOFT_CAP:
            make_buy_qty = max(0, make_buy_qty // 2)
        if make_buy_qty > 0:
            orders.append(Order(ASH_PRODUCT, bid_px, make_buy_qty))
            print(f"  [ASH MAKE] BID {make_buy_qty}x at {bid_px}")

    # Sell side: suppress when already short >= HARD_STOP
    if net_pos_after_taker > -ASH_HARD_STOP:
        sell_room = ASH_POSITION_LIMIT - (position + taker_sell)  # room to sell more
        make_sell_qty = min(ASH_MAKE_SIZE, sell_room)
        if net_pos_after_taker <= -ASH_SOFT_CAP:
            make_sell_qty = max(0, make_sell_qty // 2)
        if make_sell_qty > 0:
            orders.append(Order(ASH_PRODUCT, ask_px, -make_sell_qty))
            print(f"  [ASH MAKE] ASK {make_sell_qty}x at {ask_px}")

    # --- Persist updated history ---
    persisted["ash_mid_history"] = mid_history

    return {ASH_PRODUCT: orders}, persisted


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

        persisted.setdefault("day_start_price", None)
        persisted.setdefault("initial_fill_done", False)

        result = {}

        intarian_result, persisted = _run_intarian(state, persisted)
        result.update(intarian_result)

        ash_result, persisted = _run_ash(state, persisted)
        result.update(ash_result)

        return result, 0, json.dumps(persisted)