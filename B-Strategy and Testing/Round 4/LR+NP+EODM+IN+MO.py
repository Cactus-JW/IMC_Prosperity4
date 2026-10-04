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
# Momentum (Layer 5) also suppresses the opposing MM quote when signal fires.
#
# mr_buy / mr_sell: controls whether aggressive MR buying/selling is enabled.
#
# Momentum layer (Layer 2 — runs before MR to get first pick of capacity):
#   mo_enabled     — activates the layer (default False)
#   mo_fast_span   — short EMA period in ticks (default 3)
#   mo_slow_span   — medium EMA period in ticks (default 20)
#   mo_threshold   — signal threshold in sigma units (default 0.20)
#   mo_volume      — max units to trade per tick (default 25)
# Signal = (fast_ema - slow_ema) / sigma. Positive → trend-buy, negative → trend-sell.
# =============================================================================

CONFIGS: Dict[str, Any] = {
    # -- VELVETFRUIT_EXTRACT --------------------------------------------------
    # Spread=5, bv1~25, sigma=15.6, OU hl~279. Symmetric, strong imbalance signal.
    # MR thresholds raised and volumes cut so MR only fires on genuine extremes,
    # leaving capacity for the momentum layer.
    "VELVETFRUIT_EXTRACT": dict(
        pos_limit=200, initial_ema=5250.0, sigma=15.63, ema_span=1000,
        fair_mode="ema_micro", micro_weight=0.20,
        mm_half_spread=3, mm_volume=15, inv_skew_price=0.02,
        mr_z_threshold=1.10, mr_agg_z=2.50, mr_volume=8, mr_agg_volume=5,
        min_inner_vol=10, mr_buy=True, mr_sell=True,
        eod_enabled=True, eod_gradient_z=1.5, eod_fast_span=50,
        mo_enabled=True, mo_fast_span=3, mo_slow_span=20, mo_threshold=0.20, mo_volume=25,
    ),
    # -- HYDROGEL_PACK --------------------------------------------------------
    # Spread=16 (bot at mid±8), bv1~12, sigma=32, OU hl~301.
    # Bots have rigid spread — passive inside quotes never fill. Must cross aggressively.
    # Fair = L1+L2 cross-VWAP; EMA used only as long-run z-score anchor.
    "HYDROGEL_PACK": dict(
        pos_limit=200, initial_ema=10000.0, sigma=31.94, ema_span=2000,
        fair_mode="l2_vwap", micro_weight=0.0,
        mm_half_spread=7, mm_volume=12, inv_skew_price=0.04,
        mr_z_threshold=1.10, mr_agg_z=2.20, mr_volume=8, mr_agg_volume=5,
        min_inner_vol=4, mr_buy=True, mr_sell=True,
        eod_enabled=True, eod_gradient_z=1.5, eod_fast_span=50,
        mo_enabled=True, mo_fast_span=3, mo_slow_span=20, mo_threshold=0.20, mo_volume=25,
    ),
    # -- VEV_4000 (deep ITM call, delta≈1) ------------------------------------
    # Tracks VFE-4000. Spread=21, bv1~11, sigma=15.65, OU hl~175 (faster).
    # Bots have rigid wide spreads — passive inside quotes never fill.
    # Must cross spread aggressively (mr_agg_z lowered to 1.0) to get fills.
    "VEV_4000": dict(
        pos_limit=200, initial_ema=1250.0, sigma=15.65, ema_span=600,
        fair_mode="ema_micro", micro_weight=0.15,
        mm_half_spread=8, mm_volume=8, inv_skew_price=0.02,
        mr_z_threshold=1.0, mr_agg_z=1.75, mr_volume=10, mr_agg_volume=5,
        min_inner_vol=4, mr_buy=True, mr_sell=True,
        eod_enabled=True, eod_gradient_z=1.5, eod_fast_span=50,
    ),
    # -- VEV_4500 (deep ITM call, delta≈1) ------------------------------------
    # Tracks VFE-4500. Spread=16, bv1~9, sigma=15.64, OU hl~226.
    "VEV_4500": dict(
        pos_limit=200, initial_ema=750.0, sigma=15.64, ema_span=450,
        fair_mode="ema_micro", micro_weight=0.15,
        mm_half_spread=6, mm_volume=7, inv_skew_price=0.02,
        mr_z_threshold=0.75, mr_agg_z=1.0, mr_volume=15, mr_agg_volume=8,
        min_inner_vol=4, mr_buy=True, mr_sell=True,
        eod_enabled=True, eod_gradient_z=1.5, eod_fast_span=50,
    ),
    # -- VEV_5000 (near-ATM call) ---------------------------------------------
    # Spread=6, bv1~11, sigma=14.38, OU hl~316. Mixed intrinsic+time value.
    "VEV_5000": dict(
        pos_limit=200, initial_ema=255.0, sigma=14.38, ema_span=630,
        fair_mode="ema_micro", micro_weight=0.20,
        mm_half_spread=2, mm_volume=10, inv_skew_price=0.02,
        mr_z_threshold=0.75, mr_agg_z=1.0, mr_volume=15, mr_agg_volume=8,
        min_inner_vol=5, mr_buy=True, mr_sell=True,
        eod_enabled=True, eod_gradient_z=1.5, eod_fast_span=50,
    ),
    # -- VEV_5100 (slightly OTM call) -----------------------------------------
    # Spread=4, bv1~22, sigma=12.74, OU hl~326. Thick inner tier.
    "VEV_5100": dict(
        pos_limit=200, initial_ema=166.8, sigma=12.74, ema_span=650,
        fair_mode="ema_micro", micro_weight=0.20,
        mm_half_spread=1, mm_volume=12, inv_skew_price=0.02,
        mr_z_threshold=0.75, mr_agg_z=1.0, mr_volume=15, mr_agg_volume=8,
        min_inner_vol=10, mr_buy=True, mr_sell=True,
        eod_enabled=True, eod_gradient_z=1.5, eod_fast_span=50,
    ),
    # -- VEV_5200 (OTM call) --------------------------------------------------
    # Spread=3, bv1~25, sigma=9.66, OU hl~283. Narrow spread.
    "VEV_5200": dict(
        pos_limit=200, initial_ema=95.5, sigma=9.66, ema_span=565,
        fair_mode="ema_micro", micro_weight=0.20,
        mm_half_spread=1, mm_volume=12, inv_skew_price=0.03,
        mr_z_threshold=0.75, mr_agg_z=1.0, mr_volume=12, mr_agg_volume=6,
        min_inner_vol=10, mr_buy=True, mr_sell=True,
        eod_enabled=True, eod_gradient_z=1.5, eod_fast_span=50,
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
    # Insider layer: mark_1 has positive 50-tick info leakage (follow),
    # mark_14 has very negative leakage (fade). Signal decays with ~50-tick half-life.
    "VEV_5500": dict(
        pos_limit=200, initial_ema=6.6, sigma=1.74, ema_span=260,
        fair_mode="ema_micro", micro_weight=0.10,
        mm_half_spread=1, mm_volume=15, inv_skew_price=0.10,
        mr_z_threshold=0.75, mr_agg_z=1.0, mr_volume=8, mr_agg_volume=3,
        min_inner_vol=10, mr_buy=True, mr_sell=True,
        insider_enabled=True,
        insider_follow=["mark_1"],
        insider_fade=["mark_14"],
        insider_decay=0.9863,   # e^(-1/72) → half-life ≈ 50 ticks
        insider_threshold=3.0,  # net signal units before acting
        insider_volume=5,       # max units to trade per tick on signal
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

        # ── Fast EMA (gradient detector for EOD momentum) ---------------------
        fast_alpha = 2.0 / (cfg.get("eod_fast_span", 50) + 1)
        fast_ema   = td.get("fast_ema_mid", mid)
        fast_ema   = fast_alpha * mid + (1.0 - fast_alpha) * fast_ema

        # ── Fair value for quoting --------------------------------------------
        fair     = Trader._fair_value(od, best_bid, best_ask, ema_mid, cfg)
        fair_int = round(fair)

        # ── Z-score vs adaptive EMA anchor ------------------------------------
        z     = (mid - ema_mid) / cfg["sigma"]
        abs_z = abs(z)

        # ── Momentum EMA pair (fast-moving trend signal) ----------------------
        # Dual-EMA crossover: signal = (fast_ema - slow_ema) / sigma.
        # Computed here so the EMAs are persisted even on EOD early returns.
        mo_enabled  = cfg.get("mo_enabled", False)
        mo_fast_ema = td.get("mo_fast_ema", mid)
        mo_slow_ema = td.get("mo_slow_ema", mid)
        mo_signal   = 0.0
        if mo_enabled:
            mo_fast_alpha = 2.0 / (cfg.get("mo_fast_span", 5) + 1)
            mo_slow_alpha = 2.0 / (cfg.get("mo_slow_span", 25) + 1)
            mo_fast_ema   = mo_fast_alpha * mid + (1.0 - mo_fast_alpha) * mo_fast_ema
            mo_slow_ema   = mo_slow_alpha * mid + (1.0 - mo_slow_alpha) * mo_slow_ema
            mo_signal     = (mo_fast_ema - mo_slow_ema) / cfg["sigma"]

        mo_thresh = cfg.get("mo_threshold", 0.30)

        # ── Capacity ---------------------------------------------------------
        buy_cap  = pos_limit - pos
        sell_cap = pos_limit + pos

        # ── EOD momentum (last 50 000 ticks of each day) ----------------------
        t_mod  = state.timestamp % 1_000_000
        in_eod = t_mod >= 950_000
        if not in_eod:
            td.pop("eod_triggered",     None)
            td.pop("eod_momentum_side", None)

        if cfg.get("eod_enabled", False):
            if in_eod and td.get("eod_triggered", False):
                # Already triggered — keep filling toward pos_limit, then hold
                side = td.get("eod_momentum_side", "")
                if side == "buy" and buy_cap > 0:
                    orders.append(Order(product, best_ask, buy_cap))
                    print(f"[{product[:5]} EOD HOD] {buy_cap:>3}x@{best_ask} pos={pos:+d}")
                elif side == "sell" and sell_cap > 0:
                    orders.append(Order(product, best_bid, -sell_cap))
                    print(f"[{product[:5]} EOD HOD] {sell_cap:>3}x@{best_bid} pos={pos:+d}")
                td["ema_mid"]      = ema_mid
                td["fast_ema_mid"] = fast_ema
                td["mo_fast_ema"]  = mo_fast_ema
                td["mo_slow_ema"]  = mo_slow_ema
                return orders, td

            if in_eod:
                grad_z     = (fast_ema - ema_mid) / cfg["sigma"]
                eod_thresh = cfg.get("eod_gradient_z", 1.5)
                if abs(grad_z) >= eod_thresh:
                    side = "buy" if grad_z > 0 else "sell"
                    td["eod_triggered"]     = True
                    td["eod_momentum_side"] = side
                    if side == "buy" and buy_cap > 0:
                        orders.append(Order(product, best_ask, buy_cap))
                        print(f"[{product[:5]} EOD BUY] {buy_cap:>3}x@{best_ask} "
                              f"grad_z={grad_z:+.2f} pos={pos:+d}")
                    elif side == "sell" and sell_cap > 0:
                        orders.append(Order(product, best_bid, -sell_cap))
                        print(f"[{product[:5]} EOD SLL] {sell_cap:>3}x@{best_bid} "
                              f"grad_z={grad_z:+.2f} pos={pos:+d}")
                    td["ema_mid"]      = ema_mid
                    td["fast_ema_mid"] = fast_ema
                    td["mo_fast_ema"]  = mo_fast_ema
                    td["mo_slow_ema"]  = mo_slow_ema
                    return orders, td
                # else: no trigger yet — fall through to normal MM + MR + MO

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

        # ── Suppress opposing MM side when placing MR or MO (any layer) ------
        # MR suppress fires at mr_z_threshold (Layer 2 onset).
        # MO suppress fires when momentum signal exceeds mo_threshold.
        # Both prevent posting a passive MM quote against an aggressive directional bet.
        mr_agg_z    = cfg["mr_agg_z"]
        mr_z_thresh = cfg["mr_z_threshold"]
        placing_agg_buy  = (z < -mr_agg_z)   and cfg["mr_buy"]
        placing_agg_sell = (z >  mr_agg_z)   and cfg["mr_sell"]
        suppress_mm_ask  = ((z < -mr_z_thresh) and cfg["mr_buy"])  or (mo_enabled and mo_signal > mo_thresh)
        suppress_mm_bid  = ((z >  mr_z_thresh) and cfg["mr_sell"]) or (mo_enabled and mo_signal < -mo_thresh)

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

        # ── LAYER 2: Fast momentum trend-following ----------------------------
        # Runs before MR so it gets first pick of post-MM capacity.
        # Fires when the very-short EMA (mo_fast_span ticks) has pulled away from
        # the medium-short EMA (mo_slow_span ticks) by mo_threshold sigma units.
        # Opposing MM quote is already suppressed via suppress_mm_ask/bid above.
        if mo_enabled:
            mo_vol = cfg.get("mo_volume", 10)
            if mo_signal > mo_thresh and rem_buy > 0:
                vol = min(mo_vol, rem_buy)
                orders.append(Order(product, best_ask, vol))
                print(f"[{product[:5]} MO BUY] {vol:>3}x@{best_ask} "
                      f"mo={mo_signal:+.3f}")
                rem_buy = max(0, rem_buy - vol)
            elif mo_signal < -mo_thresh and rem_sell > 0:
                vol = min(mo_vol, rem_sell)
                orders.append(Order(product, best_bid, -vol))
                print(f"[{product[:5]} MO SLL] {vol:>3}x@{best_bid} "
                      f"mo={mo_signal:+.3f}")
                rem_sell = max(0, rem_sell - vol)

        # ── LAYER 3: MR aggressive (intermediate) ----------------------------
        # Cross the spread at the best available price. Prosperity orders reset
        # each tick so passive outside-spread orders never fill; must take liquidity.
        # Volume scales linearly from 0 at mr_z_threshold up to mr_volume at mr_agg_z.
        z_range  = max(mr_agg_z - mr_z_thresh, 1e-9)
        mr_alpha = max(0.0, min((abs_z - mr_z_thresh) / z_range, 1.0)) if abs_z >= mr_z_thresh else 0.0
        mr_vol   = int(cfg["mr_volume"] * mr_alpha)

        if z < 0 and mr_vol > 0 and cfg["mr_buy"]:
            mr_bid_vol = min(mr_vol, rem_buy)
            if mr_bid_vol > 0:
                orders.append(Order(product, best_ask, mr_bid_vol))
                print(f"[{product[:5]} MR BID] {mr_bid_vol:>3}x@{best_ask} "
                      f"z={z:+.2f} a={mr_alpha:.2f}")
            rem_buy = max(0, rem_buy - mr_bid_vol)

        if z > 0 and mr_vol > 0 and cfg["mr_sell"]:
            mr_ask_vol = min(mr_vol, rem_sell)
            if mr_ask_vol > 0:
                orders.append(Order(product, best_bid, -mr_ask_vol))
                print(f"[{product[:5]} MR ASK] {mr_ask_vol:>3}x@{best_bid} "
                      f"z={z:+.2f} a={mr_alpha:.2f}")
            rem_sell = max(0, rem_sell - mr_ask_vol)

        # ── LAYER 4: MR aggressive take at extreme deviation ------------------
        # Cross the spread to capture strong mean-reversion signal.
        # Opposing MM quote already suppressed above.
        if placing_agg_buy and rem_buy > 0:
            agg_vol = min(cfg["mr_agg_volume"], rem_buy)
            orders.append(Order(product, best_ask, agg_vol))
            print(f"[{product[:5]} AGG BUY] {agg_vol:>3}x@{best_ask} z={z:+.2f}")
            rem_buy = max(0, rem_buy - agg_vol)

        if placing_agg_sell and rem_sell > 0:
            agg_vol = min(cfg["mr_agg_volume"], rem_sell)
            orders.append(Order(product, best_bid, -agg_vol))
            print(f"[{product[:5]} AGG SLL] {agg_vol:>3}x@{best_bid} z={z:+.2f}")
            rem_sell = max(0, rem_sell - agg_vol)

        # ── LAYER 5: Insider signal (VEV_5500 only) ---------------------------
        # mark_1 has positive 50-tick info leakage → follow direction.
        # mark_14 has very negative leakage → fade direction.
        # Signal is quantity-weighted and decays with ~50-tick half-life.
        if cfg.get("insider_enabled", False):
            sig = td.get("insider_signal", 0.0) * cfg["insider_decay"]

            for trade in state.market_trades.get(product, []):
                qty = trade.quantity
                for name in cfg["insider_follow"]:
                    if trade.buyer == name:
                        sig += qty
                    elif trade.seller == name:
                        sig -= qty
                for name in cfg["insider_fade"]:
                    if trade.buyer == name:
                        sig -= qty
                    elif trade.seller == name:
                        sig += qty

            sig = max(-100.0, min(100.0, sig))
            td["insider_signal"] = sig

            thresh   = cfg["insider_threshold"]
            ins_vol  = cfg["insider_volume"]

            if sig >= thresh and rem_buy > 0:
                vol = min(ins_vol, rem_buy)
                orders.append(Order(product, best_ask, vol))
                print(f"[{product[:5]} INS BUY] {vol:>3}x@{best_ask} sig={sig:+.2f}")
            elif sig <= -thresh and rem_sell > 0:
                vol = min(ins_vol, rem_sell)
                orders.append(Order(product, best_bid, -vol))
                print(f"[{product[:5]} INS SLL] {vol:>3}x@{best_bid} sig={sig:+.2f}")

        # ── Tick summary ------------------------------------------------------
        print(f"[{product[:5]} ST] t={state.timestamp} pos={pos:+d} "
              f"mid={mid:.1f} ema={ema_mid:.1f} z={z:+.3f} mo={mo_signal:+.3f}")

        td["ema_mid"]      = ema_mid
        td["fast_ema_mid"] = fast_ema
        td["mo_fast_ema"]  = mo_fast_ema
        td["mo_slow_ema"]  = mo_slow_ema
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
