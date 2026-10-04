from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = THIS_DIR.parent.parent
DATA_DIR = ROOT_DIR / "Historical Data" / "Round 1"
STRATEGY_PATH = ROOT_DIR / "Strategy and Testing" / "Round 1" / "StrategyMerged.py"

DAY_OFFSET = 200_000

INTARIAN_PRODUCT = "INTARIAN_PEPPER_ROOT"
ASH_PRODUCT = "ASH_COATED_OSMIUM"

POSITION_LIMITS = {
    INTARIAN_PRODUCT: 80,
    ASH_PRODUCT: 20,
}


def _ensure_strategy_path() -> None:
    strategy_dir = str(STRATEGY_PATH.parent)
    if strategy_dir not in sys.path:
        sys.path.insert(0, strategy_dir)


def load_trader_class(strategy_path: Path):
    _ensure_strategy_path()

    module_name = f"_merged_strategy_{strategy_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, strategy_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load strategy module from {strategy_path}")

    module = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(module)

    if not hasattr(module, "Trader"):
        raise AttributeError(f"Strategy file '{strategy_path.name}' does not define a Trader class.")
    return module.Trader


def load_price_data(data_dir: Path, days: List[int]) -> pd.DataFrame:
    frames = []
    day_index_map = {day: index for index, day in enumerate(sorted(days))}

    for day in sorted(days):
        csv_path = data_dir / f"prices_round_1_day_{day}.csv"
        if not csv_path.exists():
            continue

        df = pd.read_csv(csv_path, sep=";")
        df["global_timestamp"] = df["timestamp"].apply(lambda t: day_index_map[day] * DAY_OFFSET + t)
        frames.append(df)

    if not frames:
        raise FileNotFoundError(f"No price CSV files found in {data_dir}")

    combined = pd.concat(frames, ignore_index=True)
    price_vol_cols = [c for c in combined.columns if "price" in c or "volume" in c]
    for col in price_vol_cols:
        combined[col] = pd.to_numeric(combined[col], errors="coerce").fillna(0.0)

    combined.sort_values("global_timestamp", inplace=True)
    combined.reset_index(drop=True, inplace=True)
    return combined


def load_trade_data(data_dir: Path, days: List[int]) -> pd.DataFrame:
    frames = []
    day_index_map = {day: index for index, day in enumerate(sorted(days))}

    for day in sorted(days):
        csv_path = data_dir / f"trades_round_1_day_{day}.csv"
        if not csv_path.exists():
            continue

        df = pd.read_csv(csv_path, sep=";")
        df["day"] = day
        df["global_timestamp"] = df["timestamp"].apply(lambda t: day_index_map[day] * DAY_OFFSET + t)
        frames.append(df)

    if not frames:
        return pd.DataFrame(columns=["timestamp", "buyer", "seller", "symbol", "currency", "price", "quantity", "day", "global_timestamp"])

    combined = pd.concat(frames, ignore_index=True)
    combined.sort_values("global_timestamp", inplace=True)
    combined.reset_index(drop=True, inplace=True)
    return combined


def _build_order_depth(row: pd.Series):
    from datamodel import OrderDepth

    od = OrderDepth()
    for level in [1, 2, 3]:
        bid_p = int(row.get(f"bid_price_{level}", 0) or 0)
        bid_v = int(row.get(f"bid_volume_{level}", 0) or 0)
        ask_p = int(row.get(f"ask_price_{level}", 0) or 0)
        ask_v = int(row.get(f"ask_volume_{level}", 0) or 0)

        if bid_p > 0 and bid_v > 0:
            od.buy_orders[bid_p] = bid_v
        if ask_p > 0 and ask_v > 0:
            od.sell_orders[ask_p] = -ask_v

    return od


def _simulate_fills(orders, order_depth, current_position: int, position_limit: int):
    fills: List[Dict[str, Any]] = []
    cash_delta = 0.0
    pos_delta = 0

    remaining_asks: Dict[int, int] = {price: abs(volume) for price, volume in order_depth.sell_orders.items()}
    remaining_bids: Dict[int, int] = {price: volume for price, volume in order_depth.buy_orders.items()}

    for order in orders:
        if order.quantity == 0:
            continue

        if order.quantity > 0:
            headroom = max(0, position_limit - (current_position + pos_delta))
            remaining = min(order.quantity, headroom)
            if remaining <= 0:
                continue

            eligible = sorted([(p, v) for p, v in remaining_asks.items() if p <= order.price and v > 0], key=lambda x: x[0])
            for ask_price, ask_vol in eligible:
                if remaining <= 0:
                    break
                qty = min(remaining, ask_vol)
                fills.append({"side": "buy", "price": ask_price, "quantity": qty})
                cash_delta -= ask_price * qty
                pos_delta += qty
                remaining -= qty
                remaining_asks[ask_price] -= qty
        else:
            headroom = max(0, position_limit + (current_position + pos_delta))
            remaining = min(abs(order.quantity), headroom)
            if remaining <= 0:
                continue

            eligible = sorted([(p, v) for p, v in remaining_bids.items() if p >= order.price and v > 0], key=lambda x: -x[0])
            for bid_price, bid_vol in eligible:
                if remaining <= 0:
                    break
                qty = min(remaining, bid_vol)
                fills.append({"side": "sell", "price": bid_price, "quantity": qty})
                cash_delta += bid_price * qty
                pos_delta -= qty
                remaining -= qty
                remaining_bids[bid_price] -= qty

    return fills, cash_delta, pos_delta


def run_backtest(strategy_path: Path, data_dir: Path, days: List[int]) -> Dict[str, Any]:
    from datamodel import Listing, Observation, Trade, TradingState

    TraderClass = load_trader_class(strategy_path)
    trader = TraderClass()

    prices_df = load_price_data(data_dir, days)
    trades_df = load_trade_data(data_dir, days)

    all_products = sorted(prices_df["product"].unique().tolist())
    all_timestamps = sorted(prices_df["global_timestamp"].unique())

    trade_lookup: Dict[int, pd.DataFrame] = {}
    if not trades_df.empty:
        for gts, grp in trades_df.groupby("global_timestamp"):
            trade_lookup[int(gts)] = grp

    trader_data = ""
    position: Dict[str, int] = {}
    cash_balance = 0.0
    pnl_curve: List[List[Any]] = []
    trades: List[Dict[str, Any]] = []
    errors: List[str] = []
    prev_own_trades: Dict[str, List[Trade]] = {}
    prev_order_depths: Dict[str, Any] = {}

    for global_ts in all_timestamps:
        tick_rows = prices_df[prices_df["global_timestamp"] == global_ts]

        order_depths = dict(prev_order_depths)
        mid_prices: Dict[str, float] = {}
        listings: Dict[str, Listing] = {}

        for _, row in tick_rows.iterrows():
            product = row["product"]
            order_depths[product] = _build_order_depth(row)
            mid_prices[product] = float(row["mid_price"]) if not pd.isna(row["mid_price"]) else 0.0
            listings[product] = Listing(product, product, "XIRECS")

        for product in all_products:
            if product not in listings:
                listings[product] = Listing(product, product, "XIRECS")

        for product in all_products:
            if product not in mid_prices and product in order_depths:
                od = order_depths[product]
                if od.buy_orders and od.sell_orders:
                    mid_prices[product] = (max(od.buy_orders.keys()) + min(od.sell_orders.keys())) / 2.0

        prev_order_depths = dict(order_depths)

        market_trades: Dict[str, List[Trade]] = {}
        if global_ts in trade_lookup:
            for _, t in trade_lookup[global_ts].iterrows():
                buyer = str(t.get("buyer", "") or "")
                seller = str(t.get("seller", "") or "")
                if buyer == "SUBMISSION" or seller == "SUBMISSION":
                    continue
                sym = str(t["symbol"])
                trade_obj = Trade(
                    symbol=sym,
                    price=int(float(t["price"])),
                    quantity=int(t["quantity"]),
                    buyer=buyer,
                    seller=seller,
                    timestamp=int(global_ts),
                )
                market_trades.setdefault(sym, []).append(trade_obj)

        state = TradingState(
            traderData=trader_data,
            timestamp=int(global_ts),
            listings=listings,
            order_depths=copy.deepcopy(order_depths),
            own_trades=prev_own_trades,
            market_trades=market_trades,
            position=dict(position),
            observations=Observation({}, {}),
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                result, _conversions, trader_data = trader.run(state)
            except Exception as exc:
                errors.append(f"tick {global_ts}: {type(exc).__name__}: {exc}")
                result = {}

        current_own_trades: Dict[str, List[Trade]] = {}
        for product, product_orders in (result or {}).items():
            if product not in order_depths:
                continue

            cur_pos = position.get(product, 0)
            fills, cash_delta, pos_delta = _simulate_fills(product_orders, order_depths[product], cur_pos, POSITION_LIMITS.get(product, 20))

            cash_balance += cash_delta
            position[product] = cur_pos + pos_delta

            for fill in fills:
                fill["timestamp"] = int(global_ts)
                fill["symbol"] = product
                trades.append(fill)

                trade_obj = Trade(
                    symbol=product,
                    price=fill["price"],
                    quantity=fill["quantity"],
                    buyer="SUBMISSION" if fill["side"] == "buy" else "",
                    seller="" if fill["side"] == "buy" else "SUBMISSION",
                    timestamp=int(global_ts),
                )
                current_own_trades.setdefault(product, []).append(trade_obj)

        prev_own_trades = current_own_trades
        mtm = sum(position.get(product, 0) * mid_prices.get(product, 0.0) for product in all_products)
        pnl_curve.append([int(global_ts), round(cash_balance + mtm, 4)])

    return {
        "pnl_curve": pnl_curve,
        "trades": trades,
        "errors": errors,
        "products": all_products,
        "days_used": days,
        "final_pnl": pnl_curve[-1][1] if pnl_curve else 0.0,
        "final_positions": position,
    }


def main():
    days = [-2, -1, 0]

    if not STRATEGY_PATH.exists():
        raise FileNotFoundError(f"Strategy file not found: {STRATEGY_PATH}")

    if not DATA_DIR.exists():
        raise FileNotFoundError(f"Historical data directory not found: {DATA_DIR}")

    print(f"Strategy : {STRATEGY_PATH}")
    print(f"Data dir : {DATA_DIR}")
    print(f"Days     : {days}")

    bt = run_backtest(STRATEGY_PATH, DATA_DIR, days)

    print(f"Rows in PnL curve : {len(bt['pnl_curve'])}")
    print(f"Trades filled     : {len(bt['trades'])}")
    print(f"Final PnL         : {bt['final_pnl']}")
    print(f"Final positions   : {bt['final_positions']}")

    if bt["errors"]:
        print("Errors:")
        for err in bt["errors"][:10]:
            print(f"  - {err}")


if __name__ == "__main__":
    main()