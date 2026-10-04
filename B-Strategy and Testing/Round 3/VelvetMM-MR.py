try:
    from datamodel import TradingState, Order
except ModuleNotFoundError:
    from prosperity4bt.datamodel import TradingState, Order

import json
from typing import List

# =============================================================================
# PARAMETERS
# =============================================================================
PRODUCT        = "VELVETFRUIT_EXTRACT"
POSITION_LIMIT = 200           # hard position limit

# -- Market Making -----------------------------------------------------------
MM_TICK        = 1             # ticks inside best bid/ask for MM quotes
MM_VOLUME      = 20            # base volume per MM side

# -- Mean Reversion ----------------------------------------------------------
MR_MAX_VOLUME  = 20            # volume at ±2σ deviation
MR_OFFSET_MAX  = 5            # max price offset from best ask/bid at the mean
                               # (decreases linearly to 0 at ±2σ)

# -- Gaussian parameters (fitted to Round 3, days 0-2, N=30 000) ------------
MU    = 5250.098               # fitted mean mid-price
SIGMA = 15.630                 # fitted std dev
# =============================================================================


class Trader:

    def run(self, state: TradingState):
        orders: List[Order] = []
        conversions = 0

        # ── Cross-tick state --------------------------------------------------
        try:
            td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            td = {}

        mm_cum_buys  = td.get("mm_buys",  0)   # cumulative filled MM buy volume
        mm_cum_sells = td.get("mm_sells", 0)   # cumulative filled MM sell volume

        # ── Book snapshot -----------------------------------------------------
        if PRODUCT not in state.order_depths:
            return {PRODUCT: orders}, conversions, json.dumps(td)

        od = state.order_depths[PRODUCT]
        if not od.buy_orders or not od.sell_orders:
            return {PRODUCT: orders}, conversions, json.dumps(td)

        best_bid = max(od.buy_orders)
        best_ask = min(od.sell_orders)
        mid      = (best_bid + best_ask) / 2.0
        spread   = best_ask - best_bid
        pos      = state.position.get(PRODUCT, 0)

        # ── Inventory logging: accumulate own fills this tick -----------------
        if PRODUCT in state.own_trades:
            for trade in state.own_trades[PRODUCT]:
                if trade.quantity > 0:
                    mm_cum_buys  += trade.quantity
                    print(f"[FILL BUY ] {trade.quantity:>4}x @ {trade.price}  "
                          f"mm_net={mm_cum_buys - mm_cum_sells:+d}  pos={pos}")
                else:
                    mm_cum_sells += abs(trade.quantity)
                    print(f"[FILL SELL] {abs(trade.quantity):>4}x @ {trade.price}  "
                          f"mm_net={mm_cum_buys - mm_cum_sells:+d}  pos={pos}")

        mm_net = mm_cum_buys - mm_cum_sells   # positive = net long accumulated by MM

        # ── Buy / sell capacity -----------------------------------------------
        buy_cap  = POSITION_LIMIT - pos   # max additional units we can buy
        sell_cap = POSITION_LIMIT + pos   # max additional units we can sell

        # ── LAYER 1 — Market Making -------------------------------------------
        # Inventory skew: scale down the inventory-adding side proportionally.
        # At pos = 0         → both sides at full MM_VOLUME
        # At pos = +LIMIT    → bid volume = 0, ask stays at MM_VOLUME
        # At pos = -LIMIT    → ask volume = 0, bid stays at MM_VOLUME
        inv_ratio = pos / POSITION_LIMIT   # −1 .. +1

        if inv_ratio >= 0:
            mm_bid_vol = max(0, int(MM_VOLUME * (1.0 - inv_ratio)))
            mm_ask_vol = MM_VOLUME
        else:
            mm_bid_vol = MM_VOLUME
            mm_ask_vol = max(0, int(MM_VOLUME * (1.0 + inv_ratio)))

        mm_bid_px = best_bid + MM_TICK
        mm_ask_px = best_ask - MM_TICK

        # Only post when MM bid and ask don't cross (spread must be > 2 * MM_TICK)
        if spread > 2 * MM_TICK:
            mm_bid_vol = min(mm_bid_vol, buy_cap)
            mm_ask_vol = min(mm_ask_vol, sell_cap)
            if mm_bid_vol > 0:
                orders.append(Order(PRODUCT, mm_bid_px, mm_bid_vol))
                print(f"[MM  BID ] {mm_bid_vol:>4}x @ {mm_bid_px}  (inv_ratio={inv_ratio:+.2f})")
            if mm_ask_vol > 0:
                orders.append(Order(PRODUCT, mm_ask_px, -mm_ask_vol))
                print(f"[MM  ASK ] {mm_ask_vol:>4}x @ {mm_ask_px}  (inv_ratio={inv_ratio:+.2f})")
        else:
            print(f"[MM  SKIP] spread={spread} <= {2 * MM_TICK}, no MM quotes")

        # ── LAYER 2 — Mean Reversion ------------------------------------------
        # alpha scales linearly from 0 (at mean) to 1 (at ±2σ).
        # Volume  : mr_vol = int(MR_MAX_VOLUME * alpha)   — 0 at mean, MR_MAX_VOLUME at ±2σ
        # Price   : offset = int(MR_OFFSET_MAX * (1−alpha)) ticks away from best ask/bid
        #           → passive deep bid/ask near mean; aggressive at ±2σ (hits best ask/bid)
        #
        # MM and MR orders are linearly superposed — each layer is computed
        # independently and appended to the same order list.

        z     = (mid - MU) / SIGMA          # signed z-score (negative = below mean)
        alpha = min(abs(z) / 2.0, 1.0)     # 0 at mean, 1 at ±2σ
        mr_vol = int(MR_MAX_VOLUME * alpha)

        remaining_buy  = buy_cap  - mm_bid_vol   # capacity left after MM allocation
        remaining_sell = sell_cap - mm_ask_vol

        if z < 0 and mr_vol > 0:
            # Below mean → MR buy
            price_offset = int(MR_OFFSET_MAX * (1.0 - alpha))
            mr_bid_px    = best_ask - price_offset       # at 2σ: best_ask; near mean: best_ask - OFFSET_MAX
            mr_bid_vol   = min(mr_vol, max(0, remaining_buy))
            if mr_bid_vol > 0:
                orders.append(Order(PRODUCT, mr_bid_px, mr_bid_vol))
                print(f"[MR  BID ] {mr_bid_vol:>4}x @ {mr_bid_px}  "
                      f"(z={z:+.3f}  alpha={alpha:.2f}  offset={price_offset})")

        elif z > 0 and mr_vol > 0:
            # Above mean → MR sell
            price_offset = int(MR_OFFSET_MAX * (1.0 - alpha))
            mr_ask_px    = best_bid + price_offset       # at 2σ: best_bid; near mean: best_bid + OFFSET_MAX
            mr_ask_vol   = min(mr_vol, max(0, remaining_sell))
            if mr_ask_vol > 0:
                orders.append(Order(PRODUCT, mr_ask_px, -mr_ask_vol))
                print(f"[MR  ASK ] {mr_ask_vol:>4}x @ {mr_ask_px}  "
                      f"(z={z:+.3f}  alpha={alpha:.2f}  offset={price_offset})")

        # ── Tick summary ------------------------------------------------------
        print(
            f"[STATE] t={state.timestamp}  pos={pos:+d}  mid={mid:.1f}  "
            f"z={z:+.3f}  mm_net={mm_net:+d}  "
            f"buy_cap={buy_cap}  sell_cap={sell_cap}"
        )

        # ── Persist cross-tick state ------------------------------------------
        td = {"mm_buys": mm_cum_buys, "mm_sells": mm_cum_sells}

        return {PRODUCT: orders}, conversions, json.dumps(td)
