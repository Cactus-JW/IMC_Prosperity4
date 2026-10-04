from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List

POSITION_LIMIT = 80  # Adjust to match the actual exchange limit for INTARIAN_PEPPER_ROOT

class Trader:

    def run(self, state: TradingState):
        print("traderData: " + state.traderData)
        print("Observations: " + str(state.observations))

        result = {}

        for product in state.order_depths:
            if product != "INTARIAN_PEPPER_ROOT":
                continue

            order_depth: OrderDepth = state.order_depths[product]
            orders: List[Order] = []

            current_position = state.position.get(product, 0)
            capacity = POSITION_LIMIT - current_position

            print(f"Position: {current_position}, capacity remaining: {capacity}")

            if capacity <= 0 or len(order_depth.buy_orders) == 0:
                result[product] = orders
                continue

            best_bid = max(order_depth.buy_orders.keys())
            bid_price = best_bid

            print(f"Best bid: {best_bid} -> bidding at: {bid_price} for {capacity}x")
            orders.append(Order(product, bid_price, capacity))

            result[product] = orders

        traderData = ""
        conversions = 0
        return result, conversions, traderData