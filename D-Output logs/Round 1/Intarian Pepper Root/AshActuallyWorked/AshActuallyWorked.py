from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List


# Strategy 1: Basic Market Maker — quote 20 lots 1 tick outside best bid/ask
class Trader:
    def run(self, state: TradingState):
        result = {}
        for product in state.order_depths:
            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []

            if len(order_depth.buy_orders) > 0 and len(order_depth.sell_orders) > 0:
                best_bid = max(order_depth.buy_orders.keys())
                best_ask = min(order_depth.sell_orders.keys())

                orders.append(Order(product, best_bid + 1, 20))   # passive buy
                orders.append(Order(product, best_ask - 1, -20))  # passive sell

            result[product] = orders

        return result, 0, ""