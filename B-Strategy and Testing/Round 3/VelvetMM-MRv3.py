try:
    from datamodel import TradingState, Order
except ModuleNotFoundError:
    from prosperity4bt.datamodel import TradingState, Order

import json
from typing import List, Dict, Any, Tuple

# =============================================================================
# PER-PRODUCT CONFIGURATIONS
#
# fair_mode options:
#   "ema_micro"  — fair = (1-w)*ema_mid + w*microprice  (most products)
#   "l2_vwap"    — fair = L1+L2 cross-weighted VWAP     (HYDROGEL_PACK only)
#
# Suppress logic: when placing an MR order on one side (Layer 2 or 3), the MM
# quote on the OPPOSITE side is suppressed to avoid simultaneous self-cross.
# Suppress fires at mr_z_threshold (Layer 2 onset), not just mr_agg_z.
#
# mr_buy / mr_sell: controls whether aggressive MR buying/selling is enabled.
# =============================================================================

CONFIGS: Dict[str, Any] = {
    # -- VELVETFRUIT_EXTRACT --------------------------------------------------
    # Spread=5, bv1~25, sigma=15.6, OU hl~279. Symmetric, strong imbalance signal.
    "VELVETFRUIT_EXTRACT": dict(
        pos_limit=200, initial_ema=5264.0, sigma=15.63, ema_span=500,
        fair_mode="ema_micro", micro_weight=0.20,
        mm_half_spread=2, mm_volume=15, inv_skew_price=0.04,
        mr_z_threshold=0.75, mr_agg_z=1.5, mr_volume=20, mr_agg_volume=10,
        min_inner_vol=10, mr_buy=True, mr_sell=True,
    ),
    # -- HYDROGEL_PACK --------------------------------------------------------
    # Spread=16 (bot at mid±8), bv1~12, sigma=32, OU hl~301.
    # Bots have rigid spread — passive inside quotes never fill. Must cross aggressively.
    # Fair = L1+L2 cross-VWAP; EMA used only as long-run z-score anchor.
    "HYDROGEL_PACK": dict(
        pos_limit=200, initial_ema=10000.0, sigma=31.94, ema_span=2000,
        fair_mode="l2_vwap", micro_weight=0.0,
        mm_half_spread=6, mm_volume=12, inv_skew_price=0.04,
        mr_z_threshold=0.4, mr_agg_z=1.1, mr_volume=15, mr_agg_volume=10,
        min_inner_vol=4, mr_buy=True, mr_sell=True,
    ),
    # -- VEV_4000 (deep ITM call, delta≈1) ------------------------------------
    # Tracks VFE-4000. Spread=21, bv1~11, sigma=15.65, OU hl~175 (faster).
    # Bots have rigid wide spreads — passive inside quotes never fill.
    # Must cross spread aggressively (mr_agg_z lowered to 1.0) to get fills.
    "VEV_4000": dict(
        pos_limit=200, initial_ema=1250.0, sigma=15.65, ema_span=350,
        fair_mode="ema_micro", micro_weight=0.15,
        mm_half_spread=8, mm_volume=8, inv_skew_price=0.02,
        mr_z_threshold=0.75, mr_agg_z=1.0, mr_volume=15, mr_agg_volume=8,
        min_inner_vol=4, mr_buy=True, mr_sell=True,
    ),
    # -- VEV_4500 (deep ITM call, delta≈1) ------------------------------------
    # Tracks VFE-4500. Spread=16, bv1~9, sigma=15.64, OU hl~226.
    "VEV_4500": dict(
        pos_limit=200, initial_ema=750.0, sigma=15.64, ema_span=450,
        fair_mode="ema_micro", micro_weight=0.15,
        mm_half_spread=6, mm_volume=7, inv_skew_price=0.02,
        mr_z_threshold=0.75, mr_agg_z=1.0, mr_volume=15, mr_agg_volume=8,
        min_inner_vol=4, mr_buy=True, mr_sell=True,
    ),
    # -- VEV_5000 (near-ATM call) ---------------------------------------------
    # Spread=6, bv1~11, sigma=14.38, OU hl~316. Mixed intrinsic+time value.
    "VEV_5000": dict(
        pos_limit=200, initial_ema=255.0, sigma=14.38, ema_span=630,
        fair_mode="ema_micro", micro_weight=0.20,
        mm_half_spread=2, mm_volume=10, inv_skew_price=0.02,
        mr_z_threshold=0.75, mr_agg_z=1.0, mr_volume=15, mr_agg_volume=8,
        min_inner_vol=5, mr_buy=True, mr_sell=True,
    ),
    # -- VEV_5100 (slightly OTM call) -----------------------------------------
    # Spread=4, bv1~22, sigma=12.74, OU hl~326. Thick inner tier.
    "VEV_5100": dict(
        pos_limit=200, initial_ema=166.8, sigma=12.74, ema_span=650,
        fair_mode="ema_micro", micro_weight=0.20,
        mm_half_spread=1, mm_volume=12, inv_skew_price=0.02,
        mr_z_threshold=0.75, mr_agg_z=1.0, mr_volume=15, mr_agg_volume=8,
        min_inner_vol=10, mr_buy=True, mr_sell=True,
    ),
    # -- VEV_5200 (OTM call) --------------------------------------------------
    # Spread=3, bv1~25, sigma=9.66, OU hl~283. Narrow spread.
    "VEV_5200": dict(
        pos_limit=200, initial_ema=95.5, sigma=9.66, ema_span=565,
        fair_mode="ema_micro", micro_weight=0.20,
        mm_half_spread=1, mm_volume=12, inv_skew_price=0.03,
        mr_z_threshold=0.75, mr_agg_z=1.0, mr_volume=12, mr_agg_volume=6,
        min_inner_vol=10, mr_buy=True, mr_sell=True,
    ),
    # -- VEV_5300 (OTM call) --------------------------------------------------
    # Spread=2, bv1~22, sigma=6.23, OU hl~221. Very tight — quotes at market level.
    "VEV_5300": dict(
        pos_limit=200, initial_ema=46.8, sigma=6.23, ema_span=440,
        fair_mode="ema_micro", micro_weight=0.15,
        mm_half_spread=1, mm_volume=12, inv_skew_price=0.04,
        mr_z_threshold=0.75, mr_agg_z=1.0, mr_volume=10, mr_agg_volume=5,
        min_inner_vol=10, mr_buy=True, mr_sell=True,
    ),
    # -- VEV_5400 (OTM call, spread=1) ----------------------------------------
    # Spread=1, bv1~22, sigma=3.43, OU hl~220. Price skew has no room — only volume skew.
    "VEV_5400": dict(
        pos_limit=200, initial_ema=16.0, sigma=3.43, ema_span=440,
        fair_mode="ema_micro", micro_weight=0.10,
        mm_half_spread=1, mm_volume=15, inv_skew_price=0.05,
        mr_z_threshold=0.75, mr_agg_z=1.0, mr_volume=10, mr_agg_volume=5,
        min_inner_vol=10, mr_buy=True, mr_sell=True,
    ),
    # -- VEV_5500 (deep OTM call, spread=1) -----------------------------------
    # Spread=1, bv1~22, sigma=1.74, OU hl~131. Fastest reversion in the suite.
    "VEV_5500": dict(
        pos_limit=200, initial_ema=6.6, sigma=1.74, ema_span=260,
        fair_mode="ema_micro", micro_weight=0.10,
        mm_half_spread=1, mm_volume=15, inv_skew_price=0.10,
        mr_z_threshold=0.75, mr_agg_z=1.0, mr_volume=8, mr_agg_volume=3,
        min_inner_vol=10, mr_buy=True, mr_sell=True,
    ),
}


class Trader:

    @staticmethod
    def _fair_value(od, best_bid: int, best_ask: int, ema_mid: float, cfg: dict) -> float:
        mid = (best_bid + best_ask) / 2.0

        if cfg["fair_mode"] == "l2_vwap":
            # L1+L2 cross-weighted VWAP (bid_price × ask_vol + ask_price × bid_vol).
            # Proven best predictor for HYDROGEL_PACK (RMS 2.04 vs next-mid).
            sorted_bids = sorted(od.buy_orders.keys(), reverse=True)[:2]
            sorted_asks = sorted(od.sell_orders.keys())[:2]
            numerator = denominator = 0.0
            for b, a in zip(sorted_bids, sorted_asks):
                bv = od.buy_orders[b]
                av = abs(od.sell_orders[a])
                numerator  += b * av + a * bv
                denominator += bv + av
            return numerator / denominator if denominator > 0 else mid

        # ema_micro: blend EMA anchor with L1 microprice
        bv1 = od.buy_orders.get(best_bid, 0)
        av1 = abs(od.sell_orders.get(best_ask, 0))
        total = bv1 + av1
        micro = (best_bid * av1 + best_ask * bv1) / total if total > 0 else mid
        w = cfg["micro_weight"]
        return (1.0 - w) * ema_mid + w * micro

    @staticmethod
    def _handle(
        state: TradingState,
        product: str,
        cfg: dict,
        td: dict,
    ) -> Tuple[List[Order], dict]:

        od = state.order_depths[product]
        if not od.buy_orders or not od.sell_orders:
            return [], td

        orders: List[Order] = []
        pos       = state.position.get(product, 0)
        pos_limit = cfg["pos_limit"]

        best_bid = max(od.buy_orders)
        best_ask = min(od.sell_orders)
        mid      = (best_bid + best_ask) / 2.0
        bv1      = od.buy_orders.get(best_bid, 0)
        av1      = abs(od.sell_orders.get(best_ask, 0))

        # ── Adaptive EMA (long-run mean / MR anchor) --------------------------
        ema_alpha = 2.0 / (cfg["ema_span"] + 1)
        ema_mid   = td.get("ema_mid", cfg["initial_ema"])
        ema_mid   = ema_alpha * mid + (1.0 - ema_alpha) * ema_mid

        # ── Fair value for quoting --------------------------------------------
        fair     = Trader._fair_value(od, best_bid, best_ask, ema_mid, cfg)
        fair_int = round(fair)

        # ── Z-score vs adaptive EMA anchor ------------------------------------
        z     = (mid - ema_mid) / cfg["sigma"]
        abs_z = abs(z)

        # ── Capacity ---------------------------------------------------------
        buy_cap  = pos_limit - pos
        sell_cap = pos_limit + pos

        # ── Inventory price skew (shifts quotes toward offloading) ------------
        price_skew = -round(cfg["inv_skew_price"] * pos)

        # ── MM quote prices (fair-based, double-clamped inside market) --------
        half     = cfg["mm_half_spread"]
        mm_bid_px = min(fair_int - half + price_skew, best_ask - 1)
        mm_ask_px = max(fair_int + half + price_skew, best_bid + 1)
        # Pull any out-of-market quotes to the competitive edge.
        # For narrow spreads (≤2) this lands both quotes at best_bid/best_ask.
        mm_bid_px = max(mm_bid_px, best_bid)
        mm_ask_px = min(mm_ask_px, best_ask)
        mm_valid  = mm_bid_px < mm_ask_px

        # ── Thin-book filter (asymmetric inner tier → directional drift risk) -
        thin_inner = bv1 < cfg["min_inner_vol"] or av1 < cfg["min_inner_vol"]

        # ── Suppress opposing MM side when placing MR (Layer 3 only) ----------
        # Suppress only fires when Layer 3 aggressive orders are placed, NOT
        # at Layer 2 onset — suppressing at z > mr_z_threshold caused one-sided
        # MM fills and runaway inventory accumulation.
        mr_agg_z    = cfg["mr_agg_z"]
        mr_z_thresh = cfg["mr_z_threshold"]

        # Position guard: prevent MR from deepening inventory past 60% of limit.
        # e.g. at pos=-120 don't MR-sell further; let MM two-sided quotes unwind.
        inv_guard_pct = 0.60
        pos_guard_buy  = pos < pos_limit * inv_guard_pct
        pos_guard_sell = pos > -pos_limit * inv_guard_pct

        placing_agg_buy  = (z < -mr_agg_z)   and cfg["mr_buy"]  and pos_guard_buy
        placing_agg_sell = (z >  mr_agg_z)   and cfg["mr_sell"] and pos_guard_sell
        # Suppress only when actually placing a Layer-3 aggressive order.
        suppress_mm_ask  = placing_agg_buy
        suppress_mm_bid  = placing_agg_sell

        # ── LAYER 1: Market Making --------------------------------------------
        mm_bid_vol = mm_ask_vol = 0

        if mm_valid and not thin_inner:
            inv_ratio = pos / pos_limit
            if inv_ratio >= 0:
                mm_bid_vol = max(0, int(cfg["mm_volume"] * (1.0 - inv_ratio)))
                mm_ask_vol = cfg["mm_volume"]
            else:
                mm_bid_vol = cfg["mm_volume"]
                mm_ask_vol = max(0, int(cfg["mm_volume"] * (1.0 + inv_ratio)))

            mm_bid_vol = min(mm_bid_vol, buy_cap)
            mm_ask_vol = min(mm_ask_vol, sell_cap)

            if mm_bid_vol > 0 and not suppress_mm_bid:
                orders.append(Order(product, mm_bid_px, mm_bid_vol))
                print(f"[{product[:5]} MM BID] {mm_bid_vol:>3}x@{mm_bid_px} "
                      f"fair={fair:.1f} sk={price_skew:+d} pos={pos:+d}")

            if mm_ask_vol > 0 and not suppress_mm_ask:
                orders.append(Order(product, mm_ask_px, -mm_ask_vol))
                print(f"[{product[:5]} MM ASK] {mm_ask_vol:>3}x@{mm_ask_px} "
                      f"fair={fair:.1f} sk={price_skew:+d} pos={pos:+d}")
        else:
            print(f"[{product[:5]} MM SKIP] thin={thin_inner} valid={mm_valid} "
                  f"bv1={bv1} av1={av1}")

        # ── Remaining capacity after MM ---------------------------------------
        actual_bid = mm_bid_vol if not suppress_mm_bid else 0
        actual_ask = mm_ask_vol if not suppress_mm_ask else 0
        rem_buy  = max(0, buy_cap  - actual_bid)
        rem_sell = max(0, sell_cap - actual_ask)

        # ── LAYER 2: MR aggressive (intermediate) ----------------------------
        # Cross the spread at the best available price. Prosperity orders reset
        # each tick so passive outside-spread orders never fill; must take liquidity.
        # Volume scales linearly from 0 at mr_z_threshold up to mr_volume at mr_agg_z.
        z_range  = max(mr_agg_z - mr_z_thresh, 1e-9)
        mr_alpha = max(0.0, min((abs_z - mr_z_thresh) / z_range, 1.0)) if abs_z >= mr_z_thresh else 0.0
        mr_vol   = int(cfg["mr_volume"] * mr_alpha)

        if z < 0 and mr_vol > 0 and cfg["mr_buy"] and pos_guard_buy:
            mr_bid_vol = min(mr_vol, rem_buy)
            if mr_bid_vol > 0:
                orders.append(Order(product, best_ask, mr_bid_vol))
                print(f"[{product[:5]} MR BID] {mr_bid_vol:>3}x@{best_ask} "
                      f"z={z:+.2f} a={mr_alpha:.2f}")
            rem_buy = max(0, rem_buy - mr_bid_vol)

        if z > 0 and mr_vol > 0 and cfg["mr_sell"] and pos_guard_sell:
            mr_ask_vol = min(mr_vol, rem_sell)
            if mr_ask_vol > 0:
                orders.append(Order(product, best_bid, -mr_ask_vol))
                print(f"[{product[:5]} MR ASK] {mr_ask_vol:>3}x@{best_bid} "
                      f"z={z:+.2f} a={mr_alpha:.2f}")
            rem_sell = max(0, rem_sell - mr_ask_vol)

        # ── LAYER 3: MR aggressive take at extreme deviation ------------------
        # Cross the spread to capture strong mean-reversion signal.
        # Opposing MM quote already suppressed above.
        if placing_agg_buy and rem_buy > 0:
            agg_vol = min(cfg["mr_agg_volume"], rem_buy)
            orders.append(Order(product, best_ask, agg_vol))
            print(f"[{product[:5]} AGG BUY] {agg_vol:>3}x@{best_ask} z={z:+.2f}")

        if placing_agg_sell and rem_sell > 0:
            agg_vol = min(cfg["mr_agg_volume"], rem_sell)
            orders.append(Order(product, best_bid, -agg_vol))
            print(f"[{product[:5]} AGG SLL] {agg_vol:>3}x@{best_bid} z={z:+.2f}")

        # ── Tick summary ------------------------------------------------------
        print(f"[{product[:5]} ST] t={state.timestamp} pos={pos:+d} "
              f"mid={mid:.1f} ema={ema_mid:.1f} z={z:+.3f}")

        td["ema_mid"] = ema_mid
        return orders, td

    def run(self, state: TradingState):
        try:
            all_td = json.loads(state.traderData) if state.traderData else {}
        except Exception:
            all_td = {}

        result: Dict[str, List[Order]] = {}
        for product, cfg in CONFIGS.items():
            if product not in state.order_depths:
                continue
            td = all_td.get(product, {})
            orders, td = Trader._handle(state, product, cfg, td)
            result[product] = orders
            all_td[product] = td

        return result, 0, json.dumps(all_td)
