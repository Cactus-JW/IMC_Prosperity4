from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List, Dict
import json
import math


class Trader:
    # ---------- Strategy parameters ----------
    # All values chosen from the Day -2 / -1 / 0 analysis.

    PRODUCT = "ASH_COATED_OSMIUM"
    FV = 10000                    # Constant fair value (Prosperity meta + data support)
    POSITION_LIMIT = 80

    # Quoting parameters
    BASE_EDGE = 7                 # Baseline distance from FV for quotes
    LEAN_COEF = 3.0              # How much dev shifts quotes (price units)
    INV_COEF = 2.0                # How much inventory shifts quotes (price units)
    DEV_SCALE = 10.0              # Normalizer for dev (~2σ given std ≈ 5)
    LEAN_CLIP = 1.5               # Cap on dev_lean to avoid insane quotes
    BASE_SIZE = 10                # Default quote size per side
    SIZE_DEV_SCALE = 10.0         # Size multiplier scaling: 1 + |dev|/SIZE_DEV_SCALE

    # Taking parameters
    TAKE_THRESHOLD = 8            # |dev| above which we start crossing the spread
    TAKE_SCALE = 1.0              # Take size per unit of |dev| beyond threshold
    MAX_TAKE = 20                 # Hard cap per tick to avoid blowing up on outliers

    # Smoothing
    EWMA_ALPHA = 0.3              # Short filter on mid — kills bid-ask bounce

    # ---------- State serialization ----------
    # traderData is the only way to persist state across run() calls in Prosperity.

    def _load_state(self, trader_data: str) -> dict:
        if not trader_data:
            return {"mid_smooth": None}
        try:
            return json.loads(trader_data)
        except Exception:
            return {"mid_smooth": None}

    def _save_state(self, state_dict: dict) -> str:
        return json.dumps(state_dict)

    # ---------- Quote computation ----------

    def _compute_quotes(self, dev_smooth: float, q: int):
        """Returns (bid_price, ask_price, bid_size, ask_size)."""
        # Clipped dev lean: fade the excursion
        dev_lean = max(-self.LEAN_CLIP,
                       min(self.LEAN_CLIP, dev_smooth / self.DEV_SCALE))
        inv_lean = q / self.POSITION_LIMIT

        # Positive total_lean → shift quotes DOWN (want to sell more)
        total_lean = self.LEAN_COEF * dev_lean + self.INV_COEF * inv_lean

        raw_bid = self.FV - self.BASE_EDGE - total_lean
        raw_ask = self.FV + self.BASE_EDGE - total_lean

        # Quotes must be integers (Prosperity matches on int prices)
        bid_price = int(math.floor(raw_bid))
        ask_price = int(math.ceil(raw_ask))

        # Never cross ourselves
        if bid_price >= ask_price:
            bid_price = ask_price - 1

        # Size scales with opportunity
        size_mult = 1 + abs(dev_smooth) / self.SIZE_DEV_SCALE
        bid_size = int(self.BASE_SIZE * size_mult)
        ask_size = int(self.BASE_SIZE * size_mult)

        # Inventory skew on sizing
        if q > 0:
            bid_size = int(bid_size * max(0.0, 1 - q / self.POSITION_LIMIT))
            ask_size = int(ask_size * (1 + q / self.POSITION_LIMIT))
        elif q < 0:
            ask_size = int(ask_size * max(0.0, 1 + q / self.POSITION_LIMIT))
            bid_size = int(bid_size * (1 - q / self.POSITION_LIMIT))

        # Respect position limits (remaining room)
        bid_size = max(0, min(bid_size, self.POSITION_LIMIT - q))
        ask_size = max(0, min(ask_size, self.POSITION_LIMIT + q))

        return bid_price, ask_price, bid_size, ask_size

    def _compute_take(self, dev_smooth: float, q: int, best_bid: int,
                      best_ask: int):
        """Returns list of marketable orders (crossing the spread)."""
        orders = []

        if dev_smooth > self.TAKE_THRESHOLD:
            # Price is too high — sell into the bid
            qty = math.floor((dev_smooth - self.TAKE_THRESHOLD)
                             * self.TAKE_SCALE)
            qty = min(qty, self.MAX_TAKE, self.POSITION_LIMIT + q)
            if qty > 0:
                # Negative quantity = sell in Prosperity convention
                orders.append(Order(self.PRODUCT, best_bid, -qty))

        elif dev_smooth < -self.TAKE_THRESHOLD:
            # Price is too low — buy the ask
            qty = math.floor((abs(dev_smooth) - self.TAKE_THRESHOLD)
                             * self.TAKE_SCALE)
            qty = min(qty, self.MAX_TAKE, self.POSITION_LIMIT - q)
            if qty > 0:
                orders.append(Order(self.PRODUCT, best_ask, qty))

        return orders

    # ---------- Main loop ----------

    def run(self, state: TradingState):
        persisted = self._load_state(state.traderData)
        mid_smooth = persisted.get("mid_smooth", None)

        result: Dict[str, List[Order]] = {}

        if self.PRODUCT not in state.order_depths:
            return result, 0, self._save_state(persisted)

        order_depth: OrderDepth = state.order_depths[self.PRODUCT]
        orders: List[Order] = []

        # Need both sides to do anything meaningful
        if not order_depth.buy_orders or not order_depth.sell_orders:
            result[self.PRODUCT] = orders
            return result, 0, self._save_state(persisted)

        best_bid = max(order_depth.buy_orders.keys())
        best_ask = min(order_depth.sell_orders.keys())
        mid = (best_bid + best_ask) / 2.0

        # --- Smoothing (short EWMA to kill bid-ask bounce) ---
        if mid_smooth is None:
            mid_smooth = mid
        else:
            mid_smooth = self.EWMA_ALPHA * mid + (1 - self.EWMA_ALPHA) * mid_smooth

        dev_smooth = mid_smooth - self.FV

        # --- Current position ---
        q = state.position.get(self.PRODUCT, 0)

        # --- 1. Taking logic (cross the spread on big excursions) ---
        take_orders = self._compute_take(dev_smooth, q, best_bid, best_ask)
        orders.extend(take_orders)

        # Update projected position after takes (so quotes respect it)
        projected_q = q
        for o in take_orders:
            projected_q += o.quantity  # signed

        # --- 2. Passive quoting ---
        bid_price, ask_price, bid_size, ask_size = self._compute_quotes(
            dev_smooth, projected_q
        )

        if bid_size > 0:
            orders.append(Order(self.PRODUCT, bid_price, bid_size))
        if ask_size > 0:
            orders.append(Order(self.PRODUCT, ask_price, -ask_size))

        # --- 3. Final position-limit guard ---
        # The exchange hard-rejects orders that would breach limits; clip here
        # so backtest and live behave identically.
        net = q
        safe_orders = []
        for o in orders:
            if o.quantity > 0:
                allowed = max(0, self.POSITION_LIMIT - net)
                clipped = min(o.quantity, allowed)
            else:
                allowed = max(0, self.POSITION_LIMIT + net)
                clipped = -min(-o.quantity, allowed)
            if clipped != 0:
                safe_orders.append(Order(self.PRODUCT, o.price, clipped))
            net += clipped

        result[self.PRODUCT] = safe_orders

        # Diagnostic log each tick
        print(f"mid={mid:.2f} smooth={mid_smooth:.2f} "
              f"dev={dev_smooth:.2f} q={q} "
              f"bid={bid_price}@{bid_size} ask={ask_price}@{ask_size} "
              f"takes={len(take_orders)}")

        # --- 4. Persist state ---
        persisted["mid_smooth"] = mid_smooth

        conversions = 0
        return result, conversions, self._save_state(persisted)