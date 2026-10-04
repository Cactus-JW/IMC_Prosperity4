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

ASH_POSITION_LIMIT = 25
ASH_BAND_WIDTH = 2

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
class AshStrategy4:
    def __init__(self):
        self.product = ASH_PRODUCT
        self.fair_value = 10000
        self.position_limit = 80
        self.make_width = 2
        self.take_width = 1
        self.filter_volume = 10

    def get_filtered_mid(self, order_depth: OrderDepth) -> float:
        if not order_depth.buy_orders or not order_depth.sell_orders:
            return self.fair_value

        large_bids = []
        for price, qty in order_depth.buy_orders.items():
            if abs(qty) >= 10:
                large_bids.append(price)
        large_asks = [
            price for price, qty in order_depth.sell_orders.items()
            if abs(qty) >= self.filter_volume
        ]

        best_bid = max(large_bids) if large_bids else max(order_depth.buy_orders.keys())
        best_ask = min(large_asks) if large_asks else min(order_depth.sell_orders.keys())

        return (best_bid + best_ask) / 2

    def snipe(
        self,
        order_depth: OrderDepth,
        position: int,
        buy_volume: int,
        sell_volume: int,
        orders: List[Order],
    ) -> Tuple[int, int, int]:
        for ask_price in sorted(order_depth.sell_orders.keys()):
            if ask_price >= self.fair_value - self.take_width:
                break
            room = self.position_limit - (position + buy_volume)
            volume = min(-order_depth.sell_orders[ask_price], room)
            if volume > 0:
                orders.append(Order(self.product, ask_price, volume))
                buy_volume += volume
                print(f"SNIPE BUY {volume}x at {ask_price}")

        for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
            if bid_price <= self.fair_value + self.take_width:
                break
            room = self.position_limit + (position - sell_volume)
            volume = min(order_depth.buy_orders[bid_price], room)
            if volume > 0:
                orders.append(Order(self.product, bid_price, -volume))
                sell_volume += volume
                print(f"SNIPE SELL {volume}x at {bid_price}")

        return position, buy_volume, sell_volume

    def clear_position(
        self,
        order_depth: OrderDepth,
        position: int,
        buy_volume: int,
        sell_volume: int,
        orders: List[Order],
    ) -> Tuple[int, int]:
        position_after = position + buy_volume - sell_volume
        fair_bid = int(self.fair_value)
        fair_ask = int(self.fair_value) + 1

        buy_capacity = self.position_limit - (position + buy_volume)
        sell_capacity = self.position_limit + (position - sell_volume)

        if position_after > 0:
            if fair_bid in order_depth.buy_orders:
                clear_qty = min(order_depth.buy_orders[fair_bid], position_after)
                send_qty = min(sell_capacity, clear_qty)
                if send_qty > 0:
                    orders.append(Order(self.product, fair_bid, -send_qty))
                    sell_volume += send_qty
                    print(f"CLEAR SELL {send_qty}x at {fair_bid}")

        if position_after < 0:
            if fair_ask in order_depth.sell_orders:
                clear_qty = min(abs(order_depth.sell_orders[fair_ask]), abs(position_after))
                send_qty = min(buy_capacity, clear_qty)
                if send_qty > 0:
                    orders.append(Order(self.product, fair_ask, send_qty))
                    buy_volume += send_qty
                    print(f"CLEAR BUY {send_qty}x at {fair_ask}")

        return buy_volume, sell_volume

    def market_make(
        self,
        order_depth: OrderDepth,
        position: int,
        buy_volume: int,
        sell_volume: int,
        orders: List[Order],
    ):
        gap = 1
        asks_above_fair = [
            p for p in order_depth.sell_orders.keys()
            if p > self.fair_value + gap
        ]
        bids_below_fair = [
            p for p in order_depth.buy_orders.keys()
            if p < self.fair_value - gap
        ]

        best_ask = min(asks_above_fair) if asks_above_fair else int(self.fair_value) + self.make_width
        best_bid = max(bids_below_fair) if bids_below_fair else int(self.fair_value) - self.make_width

        our_ask = best_ask - gap
        our_bid = best_bid + gap

        buy_capacity = self.position_limit - (position + buy_volume)
        sell_capacity = self.position_limit + (position - sell_volume)

        if buy_capacity > 0:
            orders.append(Order(self.product, our_bid, buy_capacity))
            print(f"MM BID {buy_capacity}x at {our_bid}")

        if sell_capacity > 0:
            orders.append(Order(self.product, our_ask, -sell_capacity))
            print(f"MM ASK {sell_capacity}x at {our_ask}")

    def run(self, state: TradingState):
        result = {}
        for product in state.order_depths:
            if product != ASH_PRODUCT:
                result[product] = []
                continue

            orders = []
            buy_volume = 0
            sell_volume = 0

            order_depth = state.order_depths[product]
            position = state.position.get(product, 0)

            filtered_mid = self.get_filtered_mid(order_depth)
            if abs(filtered_mid - self.fair_value) > 20:
                print(f"WARNING: filtered mid {filtered_mid:.1f} far from fair value {self.fair_value}")

            print(f"ts={state.timestamp} | pos={position} | mid={filtered_mid:.1f}")

            position, buy_volume, sell_volume = self.snipe(
                order_depth, position, buy_volume, sell_volume, orders
            )

            buy_volume, sell_volume = self.clear_position(
                order_depth, position, buy_volume, sell_volume, orders
            )

            self.market_make(
                order_depth, position, buy_volume, sell_volume, orders
            )

            result[product] = orders
        return result, 0, state.traderData


def _run_ash(state: TradingState):
    strategy = AshStrategy4()
    result, _, _ = strategy.run(state)
    return {ASH_PRODUCT: result.get(ASH_PRODUCT, [])}


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

        ash_result = _run_ash(state)
        result.update(ash_result)

        return result, 0, json.dumps(persisted)