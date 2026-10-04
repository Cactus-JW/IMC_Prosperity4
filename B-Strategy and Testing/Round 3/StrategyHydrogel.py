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
        self.take_width     = 1
        self.filter_volume  = 10

        # ── Spread config ─────────────────────
        # Width is now DYNAMIC based on inventory
        # rather than a single fixed value.
        self.make_width_flat = 1      # tight spread when flat  (pos ≈ 0)
        self.make_width_max  = 4      # widest spread at position limit

        # ── EMA fair value ────────────────────
        self.ema_alpha       = 0.15
        self.ema_fast_alpha  = 0.40
        self.ema_min_ticks   = 5

        # ── Inventory skewing ─────────────────
        # Skew is now THREE-ZONE:
        #   Zone 1 [0, skew_start)      → flat, tight spread, full quoting
        #   Zone 2 [skew_start, cutoff) → lean quotes, widening spread
        #   Zone 3 [cutoff, 1.0]        → suppress adding side, max skew
        self.skew_start     = 0.25    # start leaning at 25% of limit (±20)
        self.skew_cutoff    = 0.85    # suppress adding side at 85%   (±68)
        self.max_skew_ticks = 4       # max ticks of lean at full position

        # ── Anti-mid-price guard ──────────────
        # Minimum distance our quotes must be from the mid price.
        # Prevents being "picked off" at the mid.
        self.min_edge_from_mid = 1    # always at least 1 tick from mid

        # ── Drift hedge ───────────────────────
        # When EMA is trending (regime shift), bias the fair value
        # slightly in the trend direction to avoid holding inventory
        # that depreciates with the drift.
        self.drift_hedge_ticks = 2    # ticks to shift fv against the trend

        # ── Seed fair value ───────────────────
        self._fair_value_seed = 10000

    # ─────────────────────────────────────────
    # STATE
    # ─────────────────────────────────────────
    def _load_state(self, trader_data: str) -> dict:
        if not trader_data:
            return {
                "ema_slow":   self._fair_value_seed,
                "ema_fast":   self._fair_value_seed,
                "tick_count": 0,
            }
        try:
            return json.loads(trader_data)
        except Exception:
            return {
                "ema_slow":   self._fair_value_seed,
                "ema_fast":   self._fair_value_seed,
                "tick_count": 0,
            }

    def _save_state(self, state_dict: dict) -> str:
        return json.dumps(state_dict)

    # ─────────────────────────────────────────
    # EMA FAIR VALUE
    # ─────────────────────────────────────────
    def _update_ema(self, state_dict: dict, mid: float) -> dict:
        state_dict["ema_slow"] = (
            self.ema_alpha * mid
            + (1 - self.ema_alpha) * state_dict["ema_slow"]
        )
        state_dict["ema_fast"] = (
            self.ema_fast_alpha * mid
            + (1 - self.ema_fast_alpha) * state_dict["ema_fast"]
        )
        state_dict["tick_count"] += 1
        return state_dict

    def _fair_value(self, state_dict: dict) -> float:
        if state_dict["tick_count"] < self.ema_min_ticks:
            return self._fair_value_seed
        return state_dict["ema_slow"]

    def _drift_direction(self, state_dict: dict) -> int:
        """
        Returns the direction the EMA is trending.
         +1 → fast EMA above slow EMA (price rising)
         -1 → fast EMA below slow EMA (price falling)
          0 → no significant trend
        """
        diff = state_dict["ema_fast"] - state_dict["ema_slow"]
        if diff > 3:
            return +1
        if diff < -3:
            return -1
        return 0

    def _adjusted_fair_value(self, state_dict: dict) -> float:
        """
        Drift-hedged fair value.

        If the price is trending DOWN, shift our fair value DOWN by
        drift_hedge_ticks so we stop buying at a price that will
        depreciate. This directly addresses the P&L drift problem.

        If trending UP, shift fair value UP so we stop selling cheap.
        """
        fv        = self._fair_value(state_dict)
        direction = self._drift_direction(state_dict)
        # Shift fv IN the direction of the trend so our quotes follow it
        return fv + direction * self.drift_hedge_ticks

    # ─────────────────────────────────────────
    # DYNAMIC SPREAD WIDTH
    # Tight when flat, wide when loaded.
    # This is the core "middle ground" fix.
    # ─────────────────────────────────────────
    def _dynamic_make_width(self, position: int) -> int:
        """
        Linearly interpolate make_width between:
          make_width_flat (at position = 0)
          make_width_max  (at position = ±limit)

        This means:
          pos=0   → width=1  (aggressive, get filled)
          pos=±40 → width=2  (moderate lean)
          pos=±80 → width=4  (wide, discourage more fills)
        """
        ratio = abs(position) / self.position_limit
        width = self.make_width_flat + ratio * (self.make_width_max - self.make_width_flat)
        return max(1, round(width))

    # ─────────────────────────────────────────
    # INVENTORY SKEW
    # Three-zone: flat / lean / suppress
    # ─────────────────────────────────────────
    def _inventory_skew(self, position: int) -> Tuple[float, float]:
        """
        Zone 1 — flat    : no skew, symmetric quotes
        Zone 2 — leaning : linear skew, both sides shift
        Zone 3 — loaded  : max skew, adding side suppressed separately

        Returns (bid_skew, ask_skew) in ticks.
        Positive bid_skew  → bid moves DOWN  (less eager to buy)
        Negative ask_skew  → ask moves DOWN  (more eager to sell)
        """
        limit     = self.position_limit
        ratio     = position / limit
        abs_ratio = abs(ratio)

        # Zone 1: flat — no skew at all
        if abs_ratio < self.skew_start:
            return 0.0, 0.0

        # Zone 2 & 3: linear skew
        skew_intensity = (abs_ratio - self.skew_start) / (1.0 - self.skew_start)
        skew_ticks     = skew_intensity * self.max_skew_ticks

        if position > 0:
            # Long: lower bid more than ask
            # → discourages more buys, encourages sells
            bid_skew = skew_ticks          # bid drops (we're less eager to buy)
            ask_skew = skew_ticks * 0.5    # ask drops slightly (entice buyers)
        else:
            # Short: raise ask more than bid
            # → discourages more sells, encourages buys
            bid_skew = -skew_ticks * 0.5   # bid rises slightly (entice sellers)
            ask_skew = -skew_ticks         # ask rises (we're less eager to sell)

        return bid_skew, ask_skew

    def _should_quote_bid(self, position: int, buy_volume: int) -> bool:
        """Suppress bid entirely in Zone 3 long."""
        projected = position + buy_volume
        return projected / self.position_limit < self.skew_cutoff

    def _should_quote_ask(self, position: int, sell_volume: int) -> bool:
        """Suppress ask entirely in Zone 3 short."""
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
    # STEP 1: snipe mispriced orders
    # Uses drift-adjusted fair value so we don't
    # snipe buys into a falling market.
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
    # STEP 3: market make
    # Now implements all three optimisations:
    #   1. Dynamic width (tight when flat)
    #   2. Inventory lean (shift quotes, not suppress)
    #   3. Anti-mid guard (never quote at mid)
    # ─────────────────────────────────────────
    def market_make(
        self,
        order_depth: OrderDepth,
        fair_value: float,
        position: int,
        buy_volume: int,
        sell_volume: int,
        orders: List[Order],
    ):
        mid = self.get_filtered_mid(order_depth, fair_value)

        # ── 1. Dynamic spread width ───────────
        # Tight when flat → guaranteed fills and spread capture
        # Wide when loaded → discourages further inventory build
        make_width = self._dynamic_make_width(position)

        # ── Reference prices ──────────────────
        # Always anchor to best existing quotes on the correct side,
        # then undercut by 1 tick to be at the front of the queue.
        asks_above = [p for p in order_depth.sell_orders if p > fair_value]
        bids_below = [p for p in order_depth.buy_orders  if p < fair_value]

        base_ask = min(asks_above) if asks_above else int(fair_value) + make_width
        base_bid = max(bids_below) if bids_below else int(fair_value) - make_width

        # Undercut by 1 tick — front of queue
        raw_ask = base_ask - 1
        raw_bid = base_bid + 1

        # ── 2. Inventory lean ─────────────────
        bid_skew, ask_skew = self._inventory_skew(position)
        our_bid = round(raw_bid - bid_skew)
        our_ask = round(raw_ask - ask_skew)

        # ── 3. Anti-mid-price guard ───────────
        # If skew or market conditions push our quote to/past the mid,
        # clamp it back to at least min_edge_from_mid ticks away.
        # This prevents being picked off by faster traders at the mid.
        mid_int = round(mid)
        our_bid = min(our_bid, mid_int - self.min_edge_from_mid)
        our_ask = max(our_ask, mid_int + self.min_edge_from_mid)

        # Safety: never cross the spread after all adjustments
        if our_bid >= our_ask:
            our_bid = our_ask - 1

        # ── Suppress adding side in Zone 3 ────
        quote_bid = self._should_quote_bid(position, buy_volume)
        quote_ask = self._should_quote_ask(position, sell_volume)

        buy_capacity  = self.position_limit - (position + buy_volume)
        sell_capacity = self.position_limit + (position - sell_volume)

        if quote_bid and buy_capacity > 0:
            orders.append(Order(self.product, our_bid, buy_capacity))
            print(
                f"MM BID {buy_capacity}x @ {our_bid}  "
                f"(width={make_width}, skew={bid_skew:+.1f}, "
                f"mid={mid:.1f}, pos={position:+d})"
            )

        if quote_ask and sell_capacity > 0:
            orders.append(Order(self.product, our_ask, -sell_capacity))
            print(
                f"MM ASK {sell_capacity}x @ {our_ask}  "
                f"(width={make_width}, skew={ask_skew:+.1f}, "
                f"mid={mid:.1f}, pos={position:+d})"
            )

    # ─────────────────────────────────────────
    # MAIN RUN
    # ─────────────────────────────────────────
    def run(self, state: TradingState):
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

            # ── Update EMA ────────────────────
            fv_prev      = self._fair_value(s)
            filtered_mid = self.get_filtered_mid(order_depth, fv_prev)
            s            = self._update_ema(s, filtered_mid)

            # Use drift-adjusted fair value throughout
            fair_value    = self._adjusted_fair_value(s)
            drift_dir     = self._drift_direction(s)
            make_width    = self._dynamic_make_width(position)
            bid_skew, ask_skew = self._inventory_skew(position)

            # ── Diagnostics ───────────────────
            print(
                f"ts={state.timestamp} | pos={position:+d} | "
                f"mid={filtered_mid:.1f} | fv={fair_value:.2f} | "
                f"ema_slow={s['ema_slow']:.2f} | "
                f"drift={'UP' if drift_dir > 0 else 'DOWN' if drift_dir < 0 else 'FLAT'} | "
                f"width={make_width} | "
                f"skew=({bid_skew:+.1f},{ask_skew:+.1f})"
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
                order_depth, fair_value,
                position, buy_volume, sell_volume, orders,
            )

            result[product] = orders

        trader_data = self._save_state(s)
        return result, 0, trader_data