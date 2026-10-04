try:
    # Submission/runtime layout (datamodel.py beside strategy root)
    from datamodel import OrderDepth, TradingState, Order
except ModuleNotFoundError:
    # prosperity3bt package layout
    from prosperity3bt.datamodel import OrderDepth, TradingState, Order
from typing import List
import math
import json


INTARIAN_PRODUCT = "INTARIAN_PEPPER_ROOT"
INTARIAN_POSITION_LIMIT = 100
INTARIAN_SELL_VOL = 10
INTARIAN_END_OF_DAY_TS = 950_000

# Spread coefficients from the merged strategy fit.
ASK_SPREAD_COEFF = 0.0012 * 0.3
BID_SPREAD_COEFF = 0.0012 * 0.19


def _compute_day_start_price(order_depth: OrderDepth) -> int:
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

        return result, 0, json.dumps(persisted)