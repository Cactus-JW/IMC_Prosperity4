from datamodel import OrderDepth, UserId, TradingState, Order
from typing import List
import json

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PRODUCT        = "INTARIAN_PEPPER_ROOT"
POSITION_LIMIT = 80
SELL_VOL       = 10       # max units to sell per tick in Phase 2
END_OF_DAY_TS  = 950_000  # stop selling, lock max position

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
            ts["day_start_price"]   = None
            ts["initial_fill_done"] = False

        # ── 2. Guard: only trade INTARIAN_PEPPER_ROOT ────────────────────────
        result: dict = {}

        if PRODUCT not in state.order_depths:
            return result, 0, json.dumps(ts)

        order_depth: OrderDepth = state.order_depths[PRODUCT]
        timestamp = state.timestamp
        position  = state.position.get(PRODUCT, 0)
        orders: List[Order] = []

        # ── 3. Compute day_start_price on first valid tick ────────────────────
        if ts["day_start_price"] is None:
            ts["day_start_price"] = _compute_day_start_price(order_depth)

        day_start_price: int = ts["day_start_price"]

        # Flip initial_fill_done once position first reaches the limit
        if not ts["initial_fill_done"] and position >= POSITION_LIMIT:
            ts["initial_fill_done"] = True

        # ── 4. Compute modelled midprice ──────────────────────────────────────
        mod_mid  = _modelled_midprice(timestamp, day_start_price)
        capacity = POSITION_LIMIT - position

        print(
            f"[{PRODUCT}] t={timestamp} pos={position} "
            f"day_start={day_start_price} mod_mid={mod_mid:.1f} "
            f"fill_done={ts['initial_fill_done']}"
        )

        # ── 5. Phase dispatch ─────────────────────────────────────────────────

        # Phase 4 — end of day: take any asks below fair value to refill, then hold
        if timestamp >= END_OF_DAY_TS:
            remaining = capacity
            for ask_px, ask_qty in sorted(order_depth.sell_orders.items()):
                if ask_px >= mod_mid or remaining <= 0:
                    break
                fill_qty = min(-ask_qty, remaining)  # ask_qty is negative
                orders.append(Order(PRODUCT, ask_px, fill_qty))
                print(f"  [Phase 4] BUY {fill_qty}x {ask_px}")
                remaining -= fill_qty

        # Phase 1 — aggressive fill from day start until position hits limit
        elif not ts["initial_fill_done"]:
            remaining = capacity
            for ask_px, ask_qty in sorted(order_depth.sell_orders.items()):
                if remaining <= 0:
                    break
                fill_qty = min(-ask_qty, remaining)  # ask_qty is negative
                orders.append(Order(PRODUCT, ask_px, fill_qty))
                print(f"  [Phase 1] BUY {fill_qty}x {ask_px}")
                remaining -= fill_qty

        # Phase 2 — at max position: sell into any bids priced above fair value
        elif position >= POSITION_LIMIT:
            remaining = SELL_VOL
            for bid_px, bid_qty in sorted(order_depth.buy_orders.items(), reverse=True):
                if bid_px <= mod_mid or remaining <= 0:
                    break
                fill_qty = min(bid_qty, remaining)
                orders.append(Order(PRODUCT, bid_px, -fill_qty))
                print(f"  [Phase 2] SELL {fill_qty}x {bid_px}")
                remaining -= fill_qty

        # Phase 3 — position dropped below max: buy into any asks below fair value
        else:
            remaining = capacity
            for ask_px, ask_qty in sorted(order_depth.sell_orders.items()):
                if ask_px >= mod_mid or remaining <= 0:
                    break
                fill_qty = min(-ask_qty, remaining)  # ask_qty is negative
                orders.append(Order(PRODUCT, ask_px, fill_qty))
                print(f"  [Phase 3] BUY {fill_qty}x {ask_px}")
                remaining -= fill_qty

        result[PRODUCT] = orders

        return result, 0, json.dumps(ts)