from datamodel import OrderDepth, TradingState, Order
from typing import List, Tuple
import json

HYDROGEL_PRODUCT = "HYDROGEL_PACK"


class Trader:

    def __init__(self):
        # ─────────────────────────────────────
        # CONFIG
        # ─────────────────────────────────────
        self.product        = HYDROGEL_PRODUCT
        self.position_limit = 80
        self.make_width     = 2
        self.take_width     = 1
        self.filter_volume  = 10

        # ── SMA config ────────────────────────
        self.sma_short_period = 10    # fast SMA  — reacts quickly to price moves
        self.sma_long_period  = 40    # slow SMA  — defines the trend direction
        self.sma_min_ticks    = 40    # ticks before SMA is trusted (need full window)

        # ── Inventory skewing ─────────────────
        self.skew_start     = 0.30
        self.skew_cutoff    = 0.85
        self.max_skew_ticks = 4

        # ── Signal thresholds ─────────────────
        # How far must short SMA be above/below long SMA to trigger a signal?
        self.signal_threshold = 2.0   # ticks — avoids noise crossovers
        # How aggressively to size into a trend signal (fraction of capacity)
        self.trend_size_frac  = 0.50  # use 50% of remaining capacity on signals

        # ── Seed fair value ───────────────────
        self._fair_value_seed = 10000

    # ─────────────────────────────────────────
    # STATE: load/save SMA price history
    # traderData is the only persistent store
    # ─────────────────────────────────────────
    def _load_state(self, trader_data: str) -> dict:
        if not trader_data:
            return {
                "price_history": [],   # list of filtered mid prices, newest last
                "tick_count":    0,
            }
        try:
            return json.loads(trader_data)
        except Exception:
            return {
                "price_history": [],
                "tick_count":    0,
            }

    def _save_state(self, state_dict: dict) -> str:
        return json.dumps(state_dict)

    # ─────────────────────────────────────────
    # SMA CALCULATIONS
    # Appends latest mid to history, trims to
    # long window, computes both SMAs.
    # ─────────────────────────────────────────
    def _update_sma(self, state_dict: dict, mid: float) -> dict:
        state_dict["price_history"].append(mid)

        # Only keep as many prices as we need for the long SMA
        if len(state_dict["price_history"]) > self.sma_long_period:
            state_dict["price_history"] = state_dict["price_history"][-self.sma_long_period:]

        state_dict["tick_count"] += 1
        return state_dict

    def _compute_smas(self, state_dict: dict) -> Tuple[float | None, float | None]:
        """
        Returns (sma_short, sma_long).
        Returns None for either if not enough data yet.
        """
        history = state_dict["price_history"]
        n       = len(history)

        sma_short = (
            sum(history[-self.sma_short_period:]) / self.sma_short_period
            if n >= self.sma_short_period else None
        )
        sma_long = (
            sum(history[-self.sma_long_period:]) / self.sma_long_period
            if n >= self.sma_long_period else None
        )

        return sma_short, sma_long

    def _fair_value(self, state_dict: dict) -> float:
        """
        Fair value = short SMA when available (most responsive to recent price).
        Falls back to long SMA, then seed value during warm-up.
        """
        sma_short, sma_long = self._compute_smas(state_dict)
        if sma_short is not None:
            return sma_short
        if sma_long is not None:
            return sma_long
        return self._fair_value_seed

    def _get_signal(self, state_dict: dict) -> str:
        """
        Trend signal from SMA crossover.

        Returns
        -------
        'BUY'     short SMA > long SMA by threshold  → uptrend,  lean long
        'SELL'    short SMA < long SMA by threshold  → downtrend, lean short
        'NEUTRAL' SMAs too close or not enough data
        """
        sma_short, sma_long = self._compute_smas(state_dict)

        if sma_short is None or sma_long is None:
            return "NEUTRAL"

        diff = sma_short - sma_long

        if diff > self.signal_threshold:
            return "BUY"
        if diff < -self.signal_threshold:
            return "SELL"
        return "NEUTRAL"

    # ─────────────────────────────────────────
    # INVENTORY SKEW
    # Same logic as EMA strategy — skew quotes
    # based on current position size.
    # ─────────────────────────────────────────
    def _inventory_skew(self, position: int) -> Tuple[float, float]:
        limit     = self.position_limit
        ratio     = position / limit
        abs_ratio = abs(ratio)

        if abs_ratio < self.skew_start:
            return 0.0, 0.0

        skew_intensity = (abs_ratio - self.skew_start) / (1.0 - self.skew_start)
        skew_ticks     = skew_intensity * self.max_skew_ticks

        if position > 0:
            bid_skew = skew_ticks
            ask_skew = skew_ticks * 0.6
        else:
            bid_skew = -skew_ticks * 0.6
            ask_skew = -skew_ticks

        return bid_skew, ask_skew

    def _should_quote_bid(self, position: int, buy_volume: int) -> bool:
        projected = position + buy_volume
        return projected / self.position_limit < self.skew_cutoff

    def _should_quote_ask(self, position: int, sell_volume: int) -> bool:
        projected = position - sell_volume
        return projected / self.position_limit > -self.skew_cutoff

    # ─────────────────────────────────────────
    # HELPER: filtered mid price
    # ─────────────────────────────────────────
    def get_filtered_mid(self, order_depth: OrderDepth, fair_value: float) -> float:
        if not order_depth.buy_orders or not order_depth.sell_orders:
            return fair_value

        large_bids = [
            p for p, q in order_depth.buy_orders.items()
            if abs(q) >= self.filter_volume
        ]
        large_asks = [
            p for p, q in order_depth.sell_orders.items()
            if abs(q) >= self.filter_volume
        ]

        best_bid = max(large_bids) if large_bids else max(order_depth.buy_orders.keys())
        best_ask = min(large_asks) if large_asks else min(order_depth.sell_orders.keys())

        return (best_bid + best_ask) / 2

    # ─────────────────────────────────────────
    # STEP 0: trend follow
    # Fire directional orders when SMA crossover
    # gives a clear signal. Runs BEFORE sniping
    # so we build position into the trend first.
    # ─────────────────────────────────────────
    def trend_follow(
        self,
        order_depth: OrderDepth,
        fair_value: float,
        signal: str,
        position: int,
        buy_volume: int,
        sell_volume: int,
        orders: List[Order],
    ) -> Tuple[int, int]:
        """
        On a BUY signal  → aggressively lift cheap asks up to trend_size_frac of capacity.
        On a SELL signal → aggressively hit rich bids up to trend_size_frac of capacity.
        On NEUTRAL       → do nothing here, let market making handle it.
        """
        if signal == "BUY":
            capacity  = self.position_limit - (position + buy_volume)
            target    = max(0, int(capacity * self.trend_size_frac))
            filled    = 0
            for ask_price in sorted(order_depth.sell_orders.keys()):
                if filled >= target:
                    break
                # Only lift asks that are at or below fair value + make_width
                # (don't chase — only buy if price is still reasonable)
                if ask_price > fair_value + self.make_width:
                    break
                vol = min(-order_depth.sell_orders[ask_price], target - filled)
                if vol > 0:
                    orders.append(Order(self.product, ask_price, vol))
                    buy_volume += vol
                    filled     += vol
                    print(f"TREND BUY  {vol}x @ {ask_price}  (signal=BUY)")

        elif signal == "SELL":
            capacity  = self.position_limit + (position - sell_volume)
            target    = max(0, int(capacity * self.trend_size_frac))
            filled    = 0
            for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
                if filled >= target:
                    break
                # Only hit bids at or above fair value - make_width
                if bid_price < fair_value - self.make_width:
                    break
                vol = min(order_depth.buy_orders[bid_price], target - filled)
                if vol > 0:
                    orders.append(Order(self.product, bid_price, -vol))
                    sell_volume += vol
                    filled      += vol
                    print(f"TREND SELL {vol}x @ {bid_price}  (signal=SELL)")

        return buy_volume, sell_volume

    # ─────────────────────────────────────────
    # STEP 1: snipe mispriced orders
    # ─────────────────────────────────────────
    def snipe(
        self,
        order_depth: OrderDepth,
        fair_value: float,
        position: int,
        buy_volume: int,
        sell_volume: int,
        orders: List[Order],
    ) -> Tuple[int, int, int]:

        for ask_price in sorted(order_depth.sell_orders.keys()):
            if ask_price >= fair_value - self.take_width:
                break
            room   = self.position_limit - (position + buy_volume)
            volume = min(-order_depth.sell_orders[ask_price], room)
            if volume > 0:
                orders.append(Order(self.product, ask_price, volume))
                buy_volume += volume
                print(f"SNIPE BUY  {volume}x @ {ask_price}  (fv={fair_value:.1f})")

        for bid_price in sorted(order_depth.buy_orders.keys(), reverse=True):
            if bid_price <= fair_value + self.take_width:
                break
            room   = self.position_limit + (position - sell_volume)
            volume = min(order_depth.buy_orders[bid_price], room)
            if volume > 0:
                orders.append(Order(self.product, bid_price, -volume))
                sell_volume += volume
                print(f"SNIPE SELL {volume}x @ {bid_price}  (fv={fair_value:.1f})")

        return position, buy_volume, sell_volume

    # ─────────────────────────────────────────
    # STEP 2: clear position at fair value
    # ─────────────────────────────────────────
    def clear_position(
        self,
        order_depth: OrderDepth,
        fair_value: float,
        position: int,
        buy_volume: int,
        sell_volume: int,
        orders: List[Order],
    ) -> Tuple[int, int]:

        position_after = position + buy_volume - sell_volume
        fair_bid       = int(fair_value)
        fair_ask       = int(fair_value) + 1

        buy_capacity  = self.position_limit - (position + buy_volume)
        sell_capacity = self.position_limit + (position - sell_volume)

        if position_after > 0 and fair_bid in order_depth.buy_orders:
            clear_qty = min(order_depth.buy_orders[fair_bid], position_after)
            send_qty  = min(sell_capacity, clear_qty)
            if send_qty > 0:
                orders.append(Order(self.product, fair_bid, -send_qty))
                sell_volume += send_qty
                print(f"CLEAR SELL {send_qty}x @ {fair_bid}")

        if position_after < 0 and fair_ask in order_depth.sell_orders:
            clear_qty = min(abs(order_depth.sell_orders[fair_ask]), abs(position_after))
            send_qty  = min(buy_capacity, clear_qty)
            if send_qty > 0:
                orders.append(Order(self.product, fair_ask, send_qty))
                buy_volume += send_qty
                print(f"CLEAR BUY  {send_qty}x @ {fair_ask}")

        return buy_volume, sell_volume

    # ─────────────────────────────────────────
    # STEP 3: market make with inventory skew
    # ─────────────────────────────────────────
    def market_make(
        self,
        order_depth: OrderDepth,
        fair_value: float,
        signal: str,
        position: int,
        buy_volume: int,
        sell_volume: int,
        orders: List[Order],
    ):
        # Widen spread when signal is neutral and SMAs are converging
        # (low conviction = take less risk)
        effective_width = self.make_width

        asks_above = [p for p in order_depth.sell_orders if p > fair_value + 1]
        bids_below = [p for p in order_depth.buy_orders  if p < fair_value - 1]

        base_ask = (min(asks_above) if asks_above else int(fair_value) + effective_width)
        base_bid = (max(bids_below) if bids_below else int(fair_value) - effective_width)

        raw_ask = base_ask - 1
        raw_bid = base_bid + 1

        # Inventory skew
        bid_skew, ask_skew = self._inventory_skew(position)

        # Signal lean: on a BUY signal, tighten the ask (more eager to sell into
        # the trend at a good price); on SELL signal, tighten the bid.
        signal_ask_adj = -1 if signal == "BUY"  else 0
        signal_bid_adj =  1 if signal == "SELL" else 0

        our_bid = round(raw_bid - bid_skew + signal_bid_adj)
        our_ask = round(raw_ask - ask_skew + signal_ask_adj)

        # Safety: never cross the spread
        if our_bid >= our_ask:
            our_bid = our_ask - 1

        quote_bid = self._should_quote_bid(position, buy_volume)
        quote_ask = self._should_quote_ask(position, sell_volume)

        buy_capacity  = self.position_limit - (position + buy_volume)
        sell_capacity = self.position_limit + (position - sell_volume)

        if quote_bid and buy_capacity > 0:
            orders.append(Order(self.product, our_bid, buy_capacity))
            print(f"MM BID {buy_capacity}x @ {our_bid}  "
                  f"(skew={bid_skew:+.1f}, signal={signal})")

        if quote_ask and sell_capacity > 0:
            orders.append(Order(self.product, our_ask, -sell_capacity))
            print(f"MM ASK {sell_capacity}x @ {our_ask}  "
                  f"(skew={ask_skew:+.1f}, signal={signal})")

    # ─────────────────────────────────────────
    # MAIN RUN
    # ─────────────────────────────────────────
    def run(self, state: TradingState):
        # ── Load persistent state ─────────────
        s = self._load_state(state.traderData)

        result = {}

        for product in state.order_depths:
            if product != HYDROGEL_PRODUCT:
                result[product] = []
                continue

            orders:      List[Order] = []
            buy_volume:  int         = 0
            sell_volume: int         = 0

            order_depth = state.order_depths[product]
            position    = state.position.get(product, 0)

            # ── Update SMA history ────────────
            fv_prev      = self._fair_value(s)
            filtered_mid = self.get_filtered_mid(order_depth, fv_prev)
            s            = self._update_sma(s, filtered_mid)
            fair_value   = self._fair_value(s)
            signal       = self._get_signal(s)
            sma_s, sma_l = self._compute_smas(s)

            # ── Diagnostics ───────────────────
            bid_skew, ask_skew = self._inventory_skew(position)
            warming_up = state.timestamp < self.sma_min_ticks
            sma_s_str = f"{sma_s:.2f}" if sma_s is not None else "n/a"
            sma_l_str = f"{sma_l:.2f}" if sma_l is not None else "n/a"
            print(
                f"ts={state.timestamp} | pos={position:+d} | "
                f"mid={filtered_mid:.1f} | fv={fair_value:.2f} | "
                f"sma_short={sma_s_str} | "
                f"sma_long={sma_l_str} | "
                f"signal={signal} | "
                f"skew=({bid_skew:+.1f},{ask_skew:+.1f}) | "
                f"{'WARMING UP' if warming_up else 'LIVE'}"
            )

            # ── Step 0: trend follow ──────────
            # Only trade trend signals once SMAs are warmed up
            if not warming_up and signal != "NEUTRAL":
                buy_volume, sell_volume = self.trend_follow(
                    order_depth, fair_value, signal,
                    position, buy_volume, sell_volume, orders,
                )

            # ── Step 1: snipe ─────────────────
            position, buy_volume, sell_volume = self.snipe(
                order_depth, fair_value,
                position, buy_volume, sell_volume, orders,
            )

            # ── Step 2: clear position ────────
            buy_volume, sell_volume = self.clear_position(
                order_depth, fair_value,
                position, buy_volume, sell_volume, orders,
            )

            # ── Step 3: market make ───────────
            self.market_make(
                order_depth, fair_value, signal,
                position, buy_volume, sell_volume, orders,
            )

            result[product] = orders

        # ── Persist state ─────────────────────
        trader_data = self._save_state(s)
        return result, 0, trader_data