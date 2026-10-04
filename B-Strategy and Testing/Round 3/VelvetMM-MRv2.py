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
POSITION_LIMIT = 200

# -- Adaptive fair value ------------------------------------------------------
# EMA with span ~500 tracks the slow OU process (half-life ~251 ticks).
# Only used as the mean-reversion anchor — not hardcoded so regime drift is
# handled automatically.
EMA_SPAN     = 500
EMA_ALPHA    = 2 / (EMA_SPAN + 1)
MICRO_WEIGHT = 0.20          # fair = (1-w)*ema_mid + w*microprice

# -- Market making ------------------------------------------------------------
# Profile recommendation: quote at fair ± 2, size 15/side.
MM_HALF_SPREAD = 2
MM_VOLUME      = 15
# Price skew proportional to inventory: shifts both quotes toward offloading.
# At pos=+100: skew = -2 ticks → bid lower, ask lower (eager to sell).
INV_SKEW_PRICE = 0.02

# -- Mean reversion -----------------------------------------------------------
MR_Z_THRESHOLD = 0.75        # minimum |z| to place deep passive MR orders
MR_VOLUME      = 20          # deep passive volume at |z| = 2σ (linear from threshold)
MR_AGG_VOLUME  = 10          # aggressive take size fired at |z| > MR_AGG_Z
MR_AGG_Z       = 2.0

# -- Thin-book filter ---------------------------------------------------------
# Asymmetric inner tiers predict directional drift (profile finding). Skip MM.
MIN_INNER_VOL  = 10

# -- Initial EMA seed (warm-start only — not a hardcoded mean anchor) ---------
INITIAL_EMA    = 5250.098
SIGMA          = 15.630      # std dev for z-score
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

        ema_mid = td.get("ema_mid", INITIAL_EMA)

        # ── Book snapshot -----------------------------------------------------
        if PRODUCT not in state.order_depths:
            td["ema_mid"] = ema_mid
            return {PRODUCT: orders}, conversions, json.dumps(td)

        od = state.order_depths[PRODUCT]
        if not od.buy_orders or not od.sell_orders:
            td["ema_mid"] = ema_mid
            return {PRODUCT: orders}, conversions, json.dumps(td)

        best_bid = max(od.buy_orders)
        best_ask = min(od.sell_orders)
        mid      = (best_bid + best_ask) / 2.0
        pos      = state.position.get(PRODUCT, 0)

        bv1 = od.buy_orders.get(best_bid, 0)
        av1 = abs(od.sell_orders.get(best_ask, 0))

        # ── Update adaptive EMA -----------------------------------------------
        ema_mid = EMA_ALPHA * mid + (1.0 - EMA_ALPHA) * ema_mid

        # ── Microprice (imbalance-weighted fair) ------------------------------
        # When bv1 > av1 (thick bids) → price tends up → microprice > mid.
        # Corr(imbalance, next_return) = +0.281 per profile.
        total_inner = bv1 + av1
        microprice  = (best_bid * av1 + best_ask * bv1) / total_inner if total_inner > 0 else mid

        # ── Blended fair value -----------------------------------------------
        fair = (1.0 - MICRO_WEIGHT) * ema_mid + MICRO_WEIGHT * microprice

        # ── Z-score vs adaptive mean -----------------------------------------
        z     = (mid - ema_mid) / SIGMA
        abs_z = abs(z)

        # Alpha for MR volume: 0 at threshold, 1 at 2σ
        mr_alpha = max(0.0, min((abs_z - MR_Z_THRESHOLD) / (MR_AGG_Z - MR_Z_THRESHOLD), 1.0))

        # ── Capacity tracking ------------------------------------------------
        buy_cap  = POSITION_LIMIT - pos
        sell_cap = POSITION_LIMIT + pos

        # ── Inventory price skew (shifts both quotes to help offload) ---------
        price_skew = -round(INV_SKEW_PRICE * pos)  # negative when long

        # ── MM quote prices (fair-based, clamped strictly inside spread) ------
        fair_int  = round(fair)
        mm_bid_px = min(fair_int - MM_HALF_SPREAD + price_skew, best_ask - 1)
        mm_ask_px = max(fair_int + MM_HALF_SPREAD + price_skew, best_bid + 1)
        mm_quotes_valid = mm_bid_px < mm_ask_px

        # ── Thin-book filter -------------------------------------------------
        thin_inner = bv1 < MIN_INNER_VOL or av1 < MIN_INNER_VOL

        # ── Suppress conflicting MM side at extremes -------------------------
        # When firing aggressive MR take, don't simultaneously quote the other
        # side at a conflicting price.
        suppress_mm_ask = z < -MR_AGG_Z
        suppress_mm_bid = z >  MR_AGG_Z

        # ── LAYER 1: Market Making -------------------------------------------
        mm_bid_vol = 0
        mm_ask_vol = 0

        if mm_quotes_valid and not thin_inner:
            inv_ratio = pos / POSITION_LIMIT

            if inv_ratio >= 0:
                mm_bid_vol = max(0, int(MM_VOLUME * (1.0 - inv_ratio)))
                mm_ask_vol = MM_VOLUME
            else:
                mm_bid_vol = MM_VOLUME
                mm_ask_vol = max(0, int(MM_VOLUME * (1.0 + inv_ratio)))

            mm_bid_vol = min(mm_bid_vol, buy_cap)
            mm_ask_vol = min(mm_ask_vol, sell_cap)

            if mm_bid_vol > 0 and not suppress_mm_bid:
                orders.append(Order(PRODUCT, mm_bid_px, mm_bid_vol))
                print(f"[MM BID] {mm_bid_vol:>3}x @ {mm_bid_px}  "
                      f"fair={fair:.1f}  skew={price_skew:+d}  pos={pos:+d}")

            if mm_ask_vol > 0 and not suppress_mm_ask:
                orders.append(Order(PRODUCT, mm_ask_px, -mm_ask_vol))
                print(f"[MM ASK] {mm_ask_vol:>3}x @ {mm_ask_px}  "
                      f"fair={fair:.1f}  skew={price_skew:+d}  pos={pos:+d}")
        else:
            print(f"[MM SKIP] thin={thin_inner}  valid={mm_quotes_valid}  bv1={bv1}  av1={av1}")

        # ── Remaining capacity after MM layer ---------------------------------
        actual_mm_bid = mm_bid_vol if not suppress_mm_bid else 0
        actual_mm_ask = mm_ask_vol if not suppress_mm_ask else 0
        rem_buy  = max(0, buy_cap  - actual_mm_bid)
        rem_sell = max(0, sell_cap - actual_mm_ask)

        # ── LAYER 2: MR deep passive -----------------------------------------
        # Post one tick OUTSIDE the bot's inner tier (best_bid-1 / best_ask+1).
        # These sit behind the bot's ~40-lot outer tier and fill only on larger
        # moves — no price overlap with MM quotes, so no layer collision.
        mr_vol = int(MR_VOLUME * mr_alpha) if abs_z >= MR_Z_THRESHOLD else 0

        if z < 0 and mr_vol > 0:
            mr_bid_px  = best_bid - 1
            mr_bid_vol = min(mr_vol, rem_buy)
            if mr_bid_vol > 0:
                orders.append(Order(PRODUCT, mr_bid_px, mr_bid_vol))
                print(f"[MR DEEP BID] {mr_bid_vol:>3}x @ {mr_bid_px}  "
                      f"z={z:+.2f}  alpha={mr_alpha:.2f}")
            rem_buy = max(0, rem_buy - mr_bid_vol)

        elif z > 0 and mr_vol > 0:
            mr_ask_px  = best_ask + 1
            mr_ask_vol = min(mr_vol, rem_sell)
            if mr_ask_vol > 0:
                orders.append(Order(PRODUCT, mr_ask_px, -mr_ask_vol))
                print(f"[MR DEEP ASK] {mr_ask_vol:>3}x @ {mr_ask_px}  "
                      f"z={z:+.2f}  alpha={mr_alpha:.2f}")
            rem_sell = max(0, rem_sell - mr_ask_vol)

        # ── LAYER 3: MR aggressive take at ±2σ --------------------------------
        # Cross the spread to capture strong mean-reversion signal.
        # MM on the conflicting side is already suppressed above.
        if z < -MR_AGG_Z and rem_buy > 0:
            agg_vol = min(MR_AGG_VOLUME, rem_buy)
            orders.append(Order(PRODUCT, best_ask, agg_vol))
            print(f"[MR AGG BUY] {agg_vol:>3}x @ {best_ask}  z={z:+.2f}")

        elif z > MR_AGG_Z and rem_sell > 0:
            agg_vol = min(MR_AGG_VOLUME, rem_sell)
            orders.append(Order(PRODUCT, best_bid, -agg_vol))
            print(f"[MR AGG SELL] {agg_vol:>3}x @ {best_bid}  z={z:+.2f}")

        # ── Tick summary ------------------------------------------------------
        print(f"[STATE] t={state.timestamp}  pos={pos:+d}  mid={mid:.1f}  "
              f"ema={ema_mid:.1f}  fair={fair:.1f}  z={z:+.3f}")

        # ── Persist cross-tick state ------------------------------------------
        td["ema_mid"] = ema_mid
        return {PRODUCT: orders}, conversions, json.dumps(td)
