try:
    from datamodel import OrderDepth, TradingState, Order
except ModuleNotFoundError:
    from prosperity4bt.datamodel import OrderDepth, TradingState, Order
from typing import List, Tuple
import json

# ---------------------------------------------------------------------------
# Constants — HYDROGEL_PACK
# ---------------------------------------------------------------------------
HGP_PRODUCT = "HYDROGEL_PACK"
HGP_POSITION_LIMIT = 200
HGP_HARD_CAP = 180
HGP_PASSIVE_GATE = 150
HGP_ANCHOR = 10000
HGP_FAIR_CLIP_LO = 9880
HGP_FAIR_CLIP_HI = 10120
HGP_MAKE_SPREAD = 7
HGP_MAKE_SPREAD_WIDE = 8
HGP_VOL_BURST_RET = 6
HGP_VOL_BURST_TICKS = 20
HGP_MAKE_SIZE = 20
HGP_INV_SKEW_COEFF = 0.06
HGP_IMB_COEFF = 4
HGP_TAKER_DEV_THRESH = 30
HGP_FLATTEN_TICKS = 100

# ---------------------------------------------------------------------------
# Constants — VELVETFRUIT_EXTRACT
# ---------------------------------------------------------------------------
VFE_PRODUCT = "VELVETFRUIT_EXTRACT"
VFE_POSITION_LIMIT = 200

VFE_MICROPRICE_ALPHA = 2.0
VFE_EMA_BLEND = 0.3
VFE_EMA_SPAN = 500

VFE_MAKE_SPREAD = 3
VFE_MAKE_SIZE = 20

VFE_INV_SKEW = 0.02
VFE_INV_CUT_THRESH = 100
VFE_INV_SUPPRESS_THRESH = 180

VFE_THIN_SPREAD_MAX = 3
VFE_THIN_RATIO_LO = 0.25
VFE_THIN_RATIO_HI = 4.0
VFE_THIN_COOLDOWN = 3

VFE_TAKER_THRESH = 1
VFE_TAKER_MAX = 8

VFE_HARD_STOP_LO = 5180
VFE_HARD_STOP_HI = 5320


# ---------------------------------------------------------------------------
# HYDROGEL_PACK strategy
# ---------------------------------------------------------------------------

def _run_hydrogel(state: TradingState, persisted: dict) -> Tuple[dict, dict]:
    if HGP_PRODUCT not in state.order_depths:
        return {}, persisted

    order_depth: OrderDepth = state.order_depths[HGP_PRODUCT]
    position: int = state.position.get(HGP_PRODUCT, 0)
    orders: List[Order] = []

    has_bid = bool(order_depth.buy_orders)
    has_ask = bool(order_depth.sell_orders)

    if not (has_bid and has_ask):
        return {}, persisted

    sorted_bids = sorted(order_depth.buy_orders.keys(), reverse=True)
    sorted_asks = sorted(order_depth.sell_orders.keys())

    best_bid = sorted_bids[0]
    best_ask = sorted_asks[0]
    bv1 = order_depth.buy_orders[best_bid]
    av1 = -order_depth.sell_orders[best_ask]

    b2 = sorted_bids[1] if len(sorted_bids) >= 2 else None
    a2 = sorted_asks[1] if len(sorted_asks) >= 2 else None
    bv2 = order_depth.buy_orders[b2] if b2 is not None else 0
    av2 = (-order_depth.sell_orders[a2]) if a2 is not None else 0

    total_vol = bv1 + av1 + bv2 + av2
    if total_vol > 0:
        if b2 is not None and a2 is not None:
            vwap = (best_bid * av1 + best_ask * bv1 + b2 * av2 + a2 * bv2) / total_vol
        else:
            l1_vol = bv1 + av1
            vwap = (best_bid * av1 + best_ask * bv1) / l1_vol if l1_vol > 0 else (best_bid + best_ask) / 2.0
    else:
        vwap = (best_bid + best_ask) / 2.0

    fair = max(HGP_FAIR_CLIP_LO, min(HGP_FAIR_CLIP_HI, vwap))
    mid = (best_bid + best_ask) / 2.0
    spread = best_ask - best_bid
    dev = fair - HGP_ANCHOR

    prev_mid: float = persisted.get("hgp_prev_mid", mid)
    ret = mid - prev_mid
    persisted["hgp_prev_mid"] = mid

    vol_burst_remaining: int = persisted.get("hgp_vol_burst_remaining", 0)
    if abs(ret) > HGP_VOL_BURST_RET:
        vol_burst_remaining = HGP_VOL_BURST_TICKS
    elif vol_burst_remaining > 0:
        vol_burst_remaining -= 1
    persisted["hgp_vol_burst_remaining"] = vol_burst_remaining

    active_spread = HGP_MAKE_SPREAD_WIDE if vol_burst_remaining > 0 else HGP_MAKE_SPREAD

    imb_denom = bv1 + av1
    imb = (bv1 - av1) / imb_denom if imb_denom > 0 else 0.0
    imb_shift = round(HGP_IMB_COEFF * imb)
    inv_shift = -round(HGP_INV_SKEW_COEFF * position)
    total_shift = inv_shift + imb_shift

    over_cap_ticks: int = persisted.get("hgp_over_cap_ticks", 0)

    buy_room = HGP_HARD_CAP - position
    sell_room = HGP_HARD_CAP + position

    committed_buy = 0
    committed_sell = 0

    if abs(position) > HGP_PASSIVE_GATE:
        over_cap_ticks += 1
    else:
        over_cap_ticks = 0

    if over_cap_ticks >= HGP_FLATTEN_TICKS:
        if position > 0 and has_bid:
            flatten_qty = min(position - HGP_PASSIVE_GATE, bv1, sell_room)
            if flatten_qty > 0:
                orders.append(Order(HGP_PRODUCT, best_bid, -flatten_qty))
                committed_sell += flatten_qty
                print(f"  [HGP FLATTEN] SELL {flatten_qty}x {best_bid} pos={position}")
        elif position < 0 and has_ask:
            flatten_qty = min(-position - HGP_PASSIVE_GATE, av1, buy_room)
            if flatten_qty > 0:
                orders.append(Order(HGP_PRODUCT, best_ask, flatten_qty))
                committed_buy += flatten_qty
                print(f"  [HGP FLATTEN] BUY {flatten_qty}x {best_ask} pos={position}")

    persisted["hgp_over_cap_ticks"] = over_cap_ticks

    if dev >= HGP_TAKER_DEV_THRESH and position > -HGP_HARD_CAP and abs(position) < HGP_PASSIVE_GATE:
        if dev >= 45:
            taker_size = 20
        elif dev >= 35:
            taker_size = 15
        else:
            taker_size = 10

        taker_sell_room = sell_room - committed_sell
        taker_qty = min(taker_size, bv1, taker_sell_room)
        if taker_qty > 0:
            orders.append(Order(HGP_PRODUCT, best_bid, -taker_qty))
            committed_sell += taker_qty
            print(f"  [HGP TAKER] SELL {taker_qty}x {best_bid} dev={dev:.1f} size_tier={taker_size}")

    fair_r = round(fair)
    make_bid_px = fair_r - active_spread + total_shift
    make_ask_px = fair_r + active_spread + total_shift

    if abs(position) < HGP_PASSIVE_GATE:
        make_buy_room = buy_room - committed_buy
        make_buy_qty = min(HGP_MAKE_SIZE, make_buy_room)
        if make_buy_qty > 0:
            orders.append(Order(HGP_PRODUCT, make_bid_px, make_buy_qty))
            print(
                f"  [HGP MAKE] BID {make_buy_qty}x {make_bid_px} "
                f"fair={fair:.2f} inv_shift={inv_shift} imb_shift={imb_shift} spread={active_spread}"
            )

        make_sell_room = sell_room - committed_sell
        make_sell_qty = min(HGP_MAKE_SIZE, make_sell_room)
        if make_sell_qty > 0:
            orders.append(Order(HGP_PRODUCT, make_ask_px, -make_sell_qty))
            print(
                f"  [HGP MAKE] ASK {make_sell_qty}x {make_ask_px} "
                f"fair={fair:.2f} inv_shift={inv_shift} imb_shift={imb_shift} spread={active_spread}"
            )

    print(
        f"[HGP] t={state.timestamp} pos={position} mid={mid:.2f} fair={fair:.2f} "
        f"dev={dev:.1f} L1spread={spread} active_spread={active_spread} "
        f"inv_shift={inv_shift} imb_shift={imb_shift} vol_burst={vol_burst_remaining}"
    )

    return {HGP_PRODUCT: orders}, persisted


# ---------------------------------------------------------------------------
# VELVETFRUIT_EXTRACT strategy
# ---------------------------------------------------------------------------

def _vfe_ema_alpha(span: int) -> float:
    return 2.0 / (span + 1)


def _run_vfe(state: TradingState, persisted: dict) -> Tuple[dict, dict]:
    if VFE_PRODUCT not in state.order_depths:
        return {}, persisted

    order_depth: OrderDepth = state.order_depths[VFE_PRODUCT]
    position = state.position.get(VFE_PRODUCT, 0)
    orders: List[Order] = []

    has_bid = bool(order_depth.buy_orders)
    has_ask = bool(order_depth.sell_orders)

    if not (has_bid and has_ask):
        return {}, persisted

    best_bid = max(order_depth.buy_orders.keys())
    best_ask = min(order_depth.sell_orders.keys())
    bv1 = order_depth.buy_orders[best_bid]
    av1_raw = order_depth.sell_orders[best_ask]
    av1 = -av1_raw

    spread = best_ask - best_bid
    mid = (best_bid + best_ask) / 2.0

    total_vol = bv1 + av1
    imbalance = (bv1 - av1) / total_vol if total_vol > 0 else 0.0
    microprice = mid + VFE_MICROPRICE_ALPHA * imbalance

    ema_alpha = _vfe_ema_alpha(VFE_EMA_SPAN)
    prev_ema = persisted.get("vfe_ema", mid)
    ema = prev_ema + ema_alpha * (mid - prev_ema)
    persisted["vfe_ema"] = ema

    fair = (1.0 - VFE_EMA_BLEND) * microprice + VFE_EMA_BLEND * ema

    bid_cooldown: int = persisted.get("vfe_bid_cooldown", 0)
    ask_cooldown: int = persisted.get("vfe_ask_cooldown", 0)

    if spread <= VFE_THIN_SPREAD_MAX and av1 > 0:
        ratio = bv1 / av1
        if ratio < VFE_THIN_RATIO_LO:
            bid_cooldown = VFE_THIN_COOLDOWN
        elif ratio > VFE_THIN_RATIO_HI:
            ask_cooldown = VFE_THIN_COOLDOWN

    bid_cooldown = max(0, bid_cooldown - 1)
    ask_cooldown = max(0, ask_cooldown - 1)
    persisted["vfe_bid_cooldown"] = bid_cooldown
    persisted["vfe_ask_cooldown"] = ask_cooldown

    inv_shift = -position * VFE_INV_SKEW
    skewed_fair = fair + inv_shift

    bid_px = round(skewed_fair) - VFE_MAKE_SPREAD
    ask_px = round(skewed_fair) + VFE_MAKE_SPREAD

    bid_px = max(bid_px, VFE_HARD_STOP_LO)
    ask_px = min(ask_px, VFE_HARD_STOP_HI)

    taker_buy = 0
    taker_sell = 0

    buy_room = VFE_POSITION_LIMIT - position
    sell_room = VFE_POSITION_LIMIT + position

    if best_ask <= fair - VFE_TAKER_THRESH:
        clip = min(av1, VFE_TAKER_MAX, buy_room)
        if clip > 0:
            orders.append(Order(VFE_PRODUCT, best_ask, clip))
            taker_buy += clip
            print(f"  [VFE TAKER] BUY {clip}x at {best_ask} (fair={fair:.2f})")

    if best_bid >= fair + VFE_TAKER_THRESH:
        clip = min(bv1, VFE_TAKER_MAX, sell_room)
        if clip > 0:
            orders.append(Order(VFE_PRODUCT, best_bid, -clip))
            taker_sell += clip
            print(f"  [VFE TAKER] SELL {clip}x at {best_bid} (fair={fair:.2f})")

    pos_after_taker = position + taker_buy - taker_sell
    buy_room_mm = VFE_POSITION_LIMIT - pos_after_taker
    sell_room_mm = VFE_POSITION_LIMIT + pos_after_taker

    abs_pos = abs(pos_after_taker)

    if abs_pos >= VFE_INV_SUPPRESS_THRESH:
        if pos_after_taker > 0:
            bid_size = 0
            ask_size = VFE_MAKE_SIZE
        else:
            bid_size = VFE_MAKE_SIZE
            ask_size = 0
    elif abs_pos > VFE_INV_CUT_THRESH:
        if pos_after_taker > 0:
            bid_size = max(0, VFE_MAKE_SIZE // 2)
            ask_size = VFE_MAKE_SIZE
        else:
            bid_size = VFE_MAKE_SIZE
            ask_size = max(0, VFE_MAKE_SIZE // 2)
    else:
        bid_size = VFE_MAKE_SIZE
        ask_size = VFE_MAKE_SIZE

    bid_size = min(bid_size, buy_room_mm)
    ask_size = min(ask_size, sell_room_mm)

    if bid_cooldown > 0:
        bid_size = 0
    if ask_cooldown > 0:
        ask_size = 0

    if bid_size > 0 and bid_px >= VFE_HARD_STOP_LO:
        orders.append(Order(VFE_PRODUCT, bid_px, bid_size))
        print(f"  [VFE MAKE] BID {bid_size}x at {bid_px} (fair={fair:.2f} skew_fair={skewed_fair:.2f})")

    if ask_size > 0 and ask_px <= VFE_HARD_STOP_HI:
        orders.append(Order(VFE_PRODUCT, ask_px, -ask_size))
        print(f"  [VFE MAKE] ASK {ask_size}x at {ask_px} (fair={fair:.2f} skew_fair={skewed_fair:.2f})")

    print(
        f"[VFE] t={state.timestamp} pos={position} mid={mid:.2f} "
        f"microprice={microprice:.2f} ema={ema:.2f} fair={fair:.2f} "
        f"imb={imbalance:.3f} spread={spread} "
        f"bid_cd={bid_cooldown} ask_cd={ask_cooldown}"
    )

    return {VFE_PRODUCT: orders}, persisted


# ---------------------------------------------------------------------------
# Trader
# ---------------------------------------------------------------------------
class Trader:
    def run(self, state: TradingState):
        if state.traderData:
            try:
                persisted = json.loads(state.traderData)
            except json.JSONDecodeError:
                persisted = {}
        else:
            persisted = {}

        persisted.setdefault("hgp_over_cap_ticks", 0)
        persisted.setdefault("hgp_prev_mid", 10000.0)
        persisted.setdefault("hgp_vol_burst_remaining", 0)

        result = {}

        hgp_result, persisted = _run_hydrogel(state, persisted)
        result.update(hgp_result)

        vfe_result, persisted = _run_vfe(state, persisted)
        result.update(vfe_result)

        return result, 0, json.dumps(persisted)
