try:
    # Submission/runtime layout (datamodel.py beside strategy root)
    from datamodel import OrderDepth, TradingState, Order
except ModuleNotFoundError:
    # prosperity3bt package layout
    from prosperity3bt.datamodel import OrderDepth, TradingState, Order
from typing import List, Tuple
import math
import json

ASH_PRODUCT = "ASH_COATED_OSMIUM"

# Strategy 1: Bollinger Band style
# class Trader:
#     def run(self, state: TradingState):
#         result = {}
#         for product in state.order_depths:
#             # Hard guard: this strategy is ASH-only.
#             if product != ASH_PRODUCT:
#                 result[product] = []
#                 continue

#             order_depth: OrderDepth = state.order_depths[product]
#             orders: List[Order] = []
            
#             # Simple fair value calculation: Mid-price of best bid/ask
#             if len(order_depth.buy_orders) > 0 and len(order_depth.sell_orders) > 0:
#                 best_bid = list(order_depth.buy_orders.keys())[0]
#                 best_ask = list(order_depth.sell_orders.keys())[0]
#                 mid_price = (best_bid + best_ask) / 2
#                 acceptable_price = mid_price 
#             else:
#                 acceptable_price = 10000 # Default if book is empty

#             # Mean Reversion Logic
#             if len(order_depth.sell_orders) != 0:
#                 best_ask, best_ask_amount = list(order_depth.sell_orders.items())[0]
#                 if int(best_ask) < acceptable_price - 2: # Buy if cheap
#                     orders.append(Order(product, best_ask, -best_ask_amount))
            
#             if len(order_depth.buy_orders) != 0:
#                 best_bid, best_bid_amount = list(order_depth.buy_orders.items())[0]
#                 if int(best_bid) > acceptable_price + 2: # Sell if expensive
#                     orders.append(Order(product, best_bid, -best_bid_amount))
            
#             result[product] = orders
            
#         return result, 0, ""
    
# Strategy 2: Momentum Trend Following    
# class Trader:
#     def run(self, state: TradingState):
#         result = {}
#         prev_mid = float(state.traderData) if state.traderData else 10000
        
#         for product in state.order_depths:
#             if product != ASH_PRODUCT:
#                 result[product] = []
#                 continue

#             order_depth = state.order_depths[product]
#             orders = []
            
#             if len(order_depth.buy_orders) > 0 and len(order_depth.sell_orders) > 0:
#                 best_bid = list(order_depth.buy_orders.keys())[0]
#                 best_ask = list(order_depth.sell_orders.keys())[0]
#                 current_mid = (best_bid + best_ask) / 2
                
#                 # Trend Logic: if rising, buy; if falling, sell
#                 if current_mid > prev_mid:
#                     orders.append(Order(product, best_ask, -list(order_depth.sell_orders.values())[0]))
#                 elif current_mid < prev_mid:
#                     orders.append(Order(product, best_bid, -list(order_depth.buy_orders.values())[0]))
                
#                 result[product] = orders
#                 traderData = str(current_mid)
#             else:
#                 traderData = str(prev_mid)
                
#         return result, 0, traderData

# Strategy 3: Spread Capture
# class Trader:
#     def run(self, state: TradingState):
#         result = {}
#         for product in state.order_depths:
#             if product != ASH_PRODUCT:
#                 result[product] = []
#                 continue

#             order_depth = state.order_depths[product]
#             orders = []
            
#             if len(order_depth.buy_orders) > 0 and len(order_depth.sell_orders) > 0:
#                 best_bid = list(order_depth.buy_orders.keys())[0]
#                 best_ask = list(order_depth.sell_orders.keys())[0]
                
#                 # Market Making: Post bid at best_bid + 1, ask at best_ask - 1
#                 fvg = 1
#                 orders.append(Order(product, best_bid + fvg, 10))
#                 orders.append(Order(product, best_ask - fvg, -10))
                
#             result[product] = orders
            
#         return result, 0, ""
    
# Strategy 4: Market making + sniping    
class Trader:

    def __init__(self):
        # ─────────────────────────────────────
        # CONFIG
        # ─────────────────────────────────────
        self.product        = ASH_PRODUCT
        self.fair_value     = 10000
        self.position_limit = 80
        self.make_width     = 2
        self.take_width     = 1
        self.filter_volume  = 10  # ignore orders smaller than this

    # ─────────────────────────────────────────
    # HELPER: filtered mid price
    # only looks at large orders to avoid noise
    # ─────────────────────────────────────────
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

    # ─────────────────────────────────────────
    # STEP 1: snipe mispriced orders
    # grab anything clearly below/above fair value
    # do this FIRST — guaranteed profit
    # ─────────────────────────────────────────
    def snipe(
        self,
        order_depth: OrderDepth,
        position: int,
        buy_volume: int,
        sell_volume: int,
        orders: List[Order]
    ) -> Tuple[int, int, int]:

        # grab underpriced asks (cheap — buy them)
        for ask_price in sorted(order_depth.sell_orders.keys()):
            if ask_price >= self.fair_value - self.take_width:
                break
            room   = self.position_limit - (position + buy_volume)
            volume = min(-order_depth.sell_orders[ask_price], room)
            if volume > 0:
                orders.append(Order(self.product, ask_price, volume))
                buy_volume += volume
                print(f"SNIPE BUY {volume}x at {ask_price}")

        # grab overpriced bids (expensive — sell to them)
        for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
            if bid_price <= self.fair_value + self.take_width:
                break
            room   = self.position_limit + (position - sell_volume)
            volume = min(order_depth.buy_orders[bid_price], room)
            if volume > 0:
                orders.append(Order(self.product, bid_price, -volume))
                sell_volume += volume
                print(f"SNIPE SELL {volume}x at {bid_price}")

        return position, buy_volume, sell_volume

    # ─────────────────────────────────────────
    # STEP 2: clear position at fair value
    # if we're too long/short, neutralize at zero edge
    # do this SECOND
    # ─────────────────────────────────────────
    def clear_position(
        self,
        order_depth: OrderDepth,
        position: int,
        buy_volume: int,
        sell_volume: int,
        orders: List[Order]
    ) -> Tuple[int, int]:

        position_after = position + buy_volume - sell_volume
        fair_bid = int(self.fair_value)      # sell at this if too long
        fair_ask = int(self.fair_value) + 1  # buy at this if too short

        buy_capacity  = self.position_limit - (position + buy_volume)
        sell_capacity = self.position_limit + (position - sell_volume)

        # too long → sell at fair value
        if position_after > 0:
            if fair_bid in order_depth.buy_orders:
                clear_qty = min(order_depth.buy_orders[fair_bid], position_after)
                send_qty  = min(sell_capacity, clear_qty)
                if send_qty > 0:
                    orders.append(Order(self.product, fair_bid, -send_qty))
                    sell_volume += send_qty
                    print(f"CLEAR SELL {send_qty}x at {fair_bid}")

        # too short → buy at fair value
        if position_after < 0:
            if fair_ask in order_depth.sell_orders:
                clear_qty = min(abs(order_depth.sell_orders[fair_ask]), abs(position_after))
                send_qty  = min(buy_capacity, clear_qty)
                if send_qty > 0:
                    orders.append(Order(self.product, fair_ask, send_qty))
                    buy_volume += send_qty
                    print(f"CLEAR BUY {send_qty}x at {fair_ask}")

        return buy_volume, sell_volume

    # ─────────────────────────────────────────
    # STEP 3: market make
    # undercut best existing orders by 1 tick
    # always at front of queue
    # do this LAST with remaining capacity
    # ─────────────────────────────────────────
    def market_make(
        self,
        order_depth: OrderDepth,
        position: int,
        buy_volume: int,
        sell_volume: int,
        orders: List[Order]
    ):
        # find best existing orders on right side of fair value
        asks_above_fair = [
            p for p in order_depth.sell_orders.keys()
            if p > self.fair_value + 1
        ]
        bids_below_fair = [
            p for p in order_depth.buy_orders.keys()
            if p < self.fair_value - 1
        ]

        # undercut by 1 tick, fall back to make_width if none found
        best_ask = min(asks_above_fair) if asks_above_fair else int(self.fair_value) + self.make_width
        best_bid = max(bids_below_fair) if bids_below_fair else int(self.fair_value) - self.make_width

        our_ask = best_ask - 1  # 1 tick better than best ask
        our_bid = best_bid + 1  # 1 tick better than best bid

        buy_capacity  = self.position_limit - (position + buy_volume)
        sell_capacity = self.position_limit + (position - sell_volume)

        if buy_capacity > 0:
            orders.append(Order(self.product, our_bid, buy_capacity))
            print(f"MM BID {buy_capacity}x at {our_bid}")

        if sell_capacity > 0:
            orders.append(Order(self.product, our_ask, -sell_capacity))
            print(f"MM ASK {sell_capacity}x at {our_ask}")

    # ─────────────────────────────────────────
    # MAIN RUN
    # ─────────────────────────────────────────
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

            # check fair value is still reasonable
            filtered_mid = self.get_filtered_mid(order_depth)
            if abs(filtered_mid - self.fair_value) > 20:
                print(f"WARNING: filtered mid {filtered_mid:.1f} far from fair value {self.fair_value}")

            print(f"ts={state.timestamp} | pos={position} | mid={filtered_mid:.1f}")

            # step 1: snipe
            position, buy_volume, sell_volume = self.snipe(
                order_depth, position, buy_volume, sell_volume, orders
            )

            # step 2: clear position
            buy_volume, sell_volume = self.clear_position(
                order_depth, position, buy_volume, sell_volume, orders
            )

            # step 3: market make
            self.market_make(
                order_depth, position, buy_volume, sell_volume, orders
            )

            result[product] = orders
        return result, 0, state.traderData