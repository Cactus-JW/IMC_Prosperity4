try:
    from datamodel import TradingState, Order
except ModuleNotFoundError:
    from prosperity4bt.datamodel import TradingState, Order

import json
from typing import List, Dict, Any, Tuple

# =============================================================================
# PER-PRODUCT CONFIGURATIONS
# =============================================================================

CONFIGS: Dict[str, Any] = {
    # -- VELVETFRUIT_EXTRACT --------------------------------------------------
    "VELVETFRUIT_EXTRACT": dict(
        pos_limit=200, initial_ema=5250.0, sigma=15.63, ema_span=500,
        fair_mode="ema_micro", micro_weight=0.20,
        mm_half_spread=2, mm_volume=15, inv_skew_price=0.02,
        mr_z_threshold=0.75, mr_agg_z=2.0, mr_volume=20, mr_agg_volume=10,
        min_inner_vol=10, mr_buy=True, mr_sell=True,
    ),
    # -- HYDROGEL_PACK --------------------------------------------------------
    "HYDROGEL_PACK": dict(
        pos_limit=200, initial_ema=10000.0, sigma=31.94, ema_span=2000,
        fair_mode="l2_vwap", micro_weight=0.0,
        mm_half_spread=7, mm_volume=12, inv_skew_price=0.04,
        mr_z_threshold=0.50, mr_agg_z=0.94, mr_volume=15, mr_agg_volume=10,
        min_inner_vol=4, mr_buy=False, mr_sell=True,
    ),
    # -- VEV_4000 (deep ITM call) ---------------------------------------------
    # Changed to pure_micro to prevent EMA lag lockouts
    "VEV_4000": dict(
        pos_limit=200, initial_ema=1250.0, sigma=15.65, ema_span=350,
        fair_mode="pure_micro", micro_weight=1.0,
        mm_half_spread=8, mm_volume=8, inv_skew_price=0.02,
        mr_z_threshold=0.75, mr_agg_z=2.0, mr_volume=15, mr_agg_volume=8,
        min_inner_vol=2, mr_buy=True, mr_sell=True,
    ),
    # -- VEV_4500 (deep ITM call) ---------------------------------------------
    "VEV_4500": dict(
        pos_limit=200, initial_ema=750.0, sigma=15.64, ema_span=450,
        fair_mode="pure_micro", micro_weight=1.0,
        mm_half_spread=6, mm_volume=7, inv_skew_price=0.02,
        mr_z_threshold=0.75, mr_agg_z=2.0, mr_volume=15, mr_agg_volume=8,
        min_inner_vol=2, mr_buy=True, mr_sell=True,
    ),
    # -- VEV_5000 (near-ATM call) ---------------------------------------------
    "VEV_5000": dict(
        pos_limit=200, initial_ema=255.0, sigma=14.38, ema_span=630,
        fair_mode="pure_micro", micro_weight=1.0,
        mm_half_spread=2, mm_volume=10, inv_skew_price=0.02,
        mr_z_threshold=0.75, mr_agg_z=2.0, mr_volume=15, mr_agg_volume=8,
        min_inner_vol=3, mr_buy=True, mr_sell=True,
    ),
    # -- VEV_5100 (slightly OTM call) -----------------------------------------
    "VEV_5100": dict(
        pos_limit=200, initial_ema=166.8, sigma=12.74, ema_span=650,
        fair_mode="pure_micro", micro_weight=1.0,
        mm_half_spread=1, mm_volume=12, inv_skew_price=0.02,
        mr_z_threshold=0.75, mr_agg_z=2.0, mr_volume=15, mr_agg_volume=8,
        min_inner_vol=4, mr_buy=True, mr_sell=True,
    ),
    # -- VEV_5200 (OTM call) --------------------------------------------------
    "VEV_5200": dict(
        pos_limit=200, initial_ema=95.5, sigma=9.66, ema_span=565,
        fair_mode="pure_micro", micro_weight=1.0,
        mm_half_spread=1, mm_volume=12, inv_skew_price=0.03,
        mr_z_threshold=0.75, mr_agg_z=2.0, mr_volume=12, mr_agg_volume=6,
        min_inner_vol=4, mr_buy=True, mr_sell=True,
    ),
    # -- VEV_5300 (OTM call) --------------------------------------------------
    "VEV_5300": dict(
        pos_limit=200, initial_ema=46.8, sigma=6.23, ema_span=440,
        fair_mode="pure_micro", micro_weight=1.0,
        mm_half_spread=1, mm_volume=12, inv_skew_price=0.04,
        mr_z_threshold=0.75, mr_agg_z=2.0, mr_volume=10, mr_agg_volume=5,
        min_inner_vol=4, mr_buy=True, mr_sell=True,
    ),
    # -- VEV_5400 (OTM call, spread=1) ----------------------------------------
    "VEV_5400": dict(
        pos_limit=200, initial_ema=16.0, sigma=3.43, ema_span=440,
        fair_mode="pure_micro", micro_weight=1.0,
        mm_half_spread=1, mm_volume=15, inv_skew_price=0.05,
        mr_z_threshold=0.75, mr_agg_z=2.0, mr_volume=10, mr_agg_volume=5,
        min_inner_vol=5, mr_buy=True, mr_sell=True,
    ),
    # -- VEV_5500 (deep OTM call, spread=1) -----------------------------------
    "VEV_5500": dict(
        pos_limit=200, initial_ema=6.6, sigma=1.74, ema_span=260,
        fair_mode="pure_micro", micro_weight=1.0,
        mm_half_spread=1, mm_volume=15, inv_skew_price=0.10,
        mr_z_threshold=0.75, mr_agg_z=2.0, mr_volume=8, mr_agg_volume=3,
        min_inner_vol=5, mr_buy=True, mr_sell=True,
    ),
}


class Trader:

    @staticmethod
    def _fair_value(od, best_bid: int, best_ask: int, ema_mid: float, cfg: dict) -> float:
        mid = (best_bid + best_ask) / 2.0

        if cfg["fair_mode"] == "l2_vwap":
            sorted_bids = sorted(od.buy_orders.keys(), reverse=True)[:2]
            sorted_asks = sorted(od.sell_orders.keys())[:2]
            numerator = denominator = 0.0
            for b, a in zip(sorted_bids, sorted_asks):
                bv = od.buy_orders[b]
                av = abs(od.sell_orders[a])
                numerator  += b * av + a * bv
                denominator += bv + av
            return numerator / denominator if denominator > 0 else mid

        bv1 = od.buy_orders.get(best_bid, 0)
        av1 = abs(od.sell_orders.get(best_ask, 0