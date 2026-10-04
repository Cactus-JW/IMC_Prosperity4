from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List
import json

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PRODUCT        = "INTARIAN_PEPPER_ROOT"
POSITION_LIMIT = 80
SELL_VOL       = 10       # units per ask order in Phase 2
END_OF_DAY_TS  = 950_000  # stop selling, lock max position

# Spread coefficients from IntarianAnalysis.py Figure 7 findings:
#   ask offset ≈ 0.0012 * 0.30 * day_start_price above modelled_midprice
#   bid offset ≈ 0.0012 * 0.19 * day_start_price below  modelled_midprice
ASK_SPREAD_COEFF = 0.0012 * 0.3
BID_SPREAD_COEFF = 0.0012 * 0.19

# ---------------------------------------------------------------------------
# Helpers
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
        midprice = 10_000  # fallback — should never happen
    return round(midprice / 1_000) * 1_000


def _modelled_midprice(timestamp: int, day_start_price: int) -> float:
    """Linear drift model: price rises 1 unit per 1 000 ms of timestamp."""
    return day_start_price + timestamp / 1_000


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------

class Trader:

    def run(self, state: TradingState):
        # ── 1. Load or initialise persistent state ──────────────────────────
        if state.traderData:
            ts = json.loads(state.traderData)
        else:
            ts = {"day_start_price": None, "initial_fill_done": False}

        # Reset at the start of each new day (timestamp wraps back to 0)
        if state.timestamp == 0:
            ts["day_start_price"]    = None
            ts["initial_fill_done"]  = False

        # ── 2. Guard: only trade INTARIAN_PEPPER_ROOT ────────────────────────
        result: dict = {}

        if PRODUCT not in state.order_depths:
            return result, 0, json.dumps(ts)

        order_depth: OrderDepth = state.order_depths[PRODUCT]
        timestamp  = state.timestamp
        position   = state.position.get(PRODUCT, 0)
        orders: List[Order] = []

        # ── 3. Compute day_start_price on first valid tick ────────────────────
        if ts["day_start_price"] is None:
            ts["day_start_price"] = _compute_day_start_price(order_depth)

        day_start_price: int = ts["day_start_price"]

        # Flip initial_fill_done once position first reaches the limit
        if not ts["initial_fill_done"] and position >= POSITION_LIMIT:
            ts["initial_fill_done"] = True

        # ── 4. Compute modelled prices ────────────────────────────────────────
        mod_mid    = _modelled_midprice(timestamp, day_start_price)
        ask_spread = round(ASK_SPREAD_COEFF * day_start_price)
        bid_spread = round(BID_SPREAD_COEFF * day_start_price)
        ask_price  = round(mod_mid) + ask_spread
        bid_price  = round(mod_mid) - bid_spread
        capacity   = POSITION_LIMIT - position

        print(
            f"[{PRODUCT}] t={timestamp} pos={position} "
            f"day_start={day_start_price} mod_mid={mod_mid:.1f} "
            f"ask={ask_price} bid={bid_price} "
            f"fill_done={ts['initial_fill_done']}"
        )

        # ── 5. Phase dispatch ─────────────────────────────────────────────────

        # Phase 4 — end of day: only refill, then hold
        if timestamp >= END_OF_DAY_TS:
            if capacity > 0:
                orders.append(Order(PRODUCT, bid_price, capacity))
                print(f"  [Phase 4] BID {capacity}x {bid_price}")

        # Phase 1 — aggressive fill from day start until position hits limit
        elif not ts["initial_fill_done"]:
            remaining = capacity
            for ask_px, ask_qty in sorted(order_depth.sell_orders.items()):
                if remaining <= 0:
                    break
                fill_qty = min(remaining, -ask_qty)  # ask_qty is negative
                orders.append(Order(PRODUCT, ask_px, fill_qty))
                print(f"  [Phase 1] BUY {fill_qty}x {ask_px}")
                remaining -= fill_qty

        # Phase 2 — at max position, post ask to unwind slowly
        elif position >= POSITION_LIMIT:
            orders.append(Order(PRODUCT, ask_price, -SELL_VOL))
            print(f"  [Phase 2] ASK {SELL_VOL}x {ask_price}")

        # Phase 3 — sold some; refill passively before resuming selling
        else:
            orders.append(Order(PRODUCT, bid_price, capacity))
            print(f"  [Phase 3] BID {capacity}x {bid_price}")

        result[PRODUCT] = orders

        return result, 0, json.dumps(ts)