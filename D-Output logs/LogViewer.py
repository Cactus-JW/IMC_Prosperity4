import os
import csv
import io
import json
import argparse

import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.widgets import Button, Slider

# Constants
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "Round 3")

DAY_GAP = 10_000  # visual gap (timestamp units) between days in combined plots

SPEED_STEPS = {1: 100, 5: 500, 10: 1_000, 50: 5_000, 100: 10_000}

PRODUCT_COLORS = ["#4169E1", "#1008e4", "#2ecc71", "#f39c12", "#9b59b6"]
DAY_PALETTE   = ["#3655B9", "#0FD02F", "#e67e22", "#8e44ad", "#16a085"]
OWN_TRADE_COLOR = "#FFD700"  # gold — used for SUBMISSION trades on all charts


# ── Data loading ──────────────────────────────────────────────────────────────

def _build_day_offsets(days: list) -> tuple[dict, list]:
    """Build day offsets and separator timestamps for combined multi-day plots."""
    days_sorted = sorted(days)
    offsets = {}
    day_sep_ts = []

    for i, day in enumerate(days_sorted):
        offsets[day] = i * DAY_GAP
        if i < len(days_sorted) - 1:
            day_sep_ts.append(offsets[day] + DAY_GAP - DAY_GAP / 2)

    return offsets, day_sep_ts


def select_data_source() -> tuple[str, str]:
    """
    List available data sources under DATA_DIR and prompt user choice.

    Returns
    -------
    source_type : 'tick' or 'log'
    source_name : folder name for tick mode OR filename for log mode
    """
    tick_dirs = sorted([
        d for d in os.listdir(DATA_DIR)
        if os.path.isdir(os.path.join(DATA_DIR, d))
    ])
    log_files = sorted([
        f for f in os.listdir(DATA_DIR)
        if f.endswith(".log") and os.path.isfile(os.path.join(DATA_DIR, f))
    ])

    if not tick_dirs and not log_files:
        raise FileNotFoundError(
            f"No tick folders or .log files found in {DATA_DIR}"
        )

    entries = [("tick", d) for d in tick_dirs] + [("log", f) for f in log_files]

    print(f"\nAvailable sources in '{os.path.basename(DATA_DIR)}':")
    for i, (kind, name) in enumerate(entries):
        print(f"  [{i}] {kind.upper():4}  {name}")

    while True:
        raw = input(f"Select source (0-{len(entries) - 1}): ").strip()
        if raw.isdigit() and 0 <= int(raw) < len(entries):
            return entries[int(raw)]
        print(f"  Please enter a number between 0 and {len(entries) - 1}.")


def _normalize_market_df(df: pd.DataFrame):
    required_cols = ["timestamp", "product"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        raise ValueError(f"Market data missing required column(s): {missing}")

    df = df.copy()

    if "day" not in df.columns:
        df["day"] = 0

    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).copy()
    df["timestamp"] = df["timestamp"].astype(int)

    df["day"] = pd.to_numeric(df["day"], errors="coerce").fillna(0).astype(int)

    # Coerce common market numeric fields; blank strings in raw logs become NaN.
    numeric_cols = ["mid_price", "profit_and_loss"]
    for i in range(1, 4):
        numeric_cols.extend([
            f"bid_price_{i}", f"bid_volume_{i}",
            f"ask_price_{i}", f"ask_volume_{i}",
        ])
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    days = sorted(df["day"].unique().tolist())
    offsets, day_sep_ts = _build_day_offsets(days)
    df["plot_ts"] = df.apply(
        lambda r: int(r["timestamp"]) + offsets.get(int(r["day"]), 0), axis=1
    )

    products = sorted(df["product"].dropna().unique().tolist())
    return df, products, days, day_sep_ts, offsets


def load_market_data_from_tick(tick_name: str):
    """
    Load the market_data_*.csv file from DATA_DIR/<tick_name>.

    Returns
    -------
    df, products, days, day_sep_ts, offsets
    """
    tick_path = os.path.join(DATA_DIR, tick_name)
    candidates = [
        f for f in os.listdir(tick_path)
        if f.startswith("market_data") and f.endswith(".csv")
    ]
    if not candidates:
        raise FileNotFoundError(f"No market_data CSV found in {tick_path}")

    df = pd.read_csv(os.path.join(tick_path, candidates[0]), sep=",")
    return _normalize_market_df(df)


def load_market_data_from_log(log_name: str):
    """
    Load market snapshots directly from a .log JSON file in DATA_DIR.
    """
    log_path = os.path.join(DATA_DIR, log_name)
    with open(log_path, encoding="utf-8") as f:
        data = json.load(f)

    activities = data.get("activitiesLog", "")
    rows = [row for row in activities.strip().split("\n") if row.strip()]
    if not rows:
        raise ValueError(f"activitiesLog is empty in {log_path}")

    reader = csv.DictReader(io.StringIO("\n".join(rows)), delimiter=";")
    df = pd.DataFrame(reader)
    if df.empty:
        raise ValueError(f"Could not parse activitiesLog rows from {log_path}")

    return _normalize_market_df(df)


def load_trades_from_tick(offsets: dict, tick_name: str) -> pd.DataFrame:
    """
    Load the trades_*.csv file from DATA_DIR/<tick_name>.
    Returns an empty DataFrame if the file is missing or empty.
    """
    tick_path = os.path.join(DATA_DIR, tick_name)
    candidates = [
        f for f in os.listdir(tick_path)
        if f.startswith("trades") and f.endswith(".csv")
    ]
    if not candidates:
        return pd.DataFrame()

    df = pd.read_csv(os.path.join(tick_path, candidates[0]), sep=",")
    if df.empty:
        return df

    if "day" not in df.columns:
        df["day"] = 0

    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).copy()
    df["timestamp"] = df["timestamp"].astype(int)

    df["day"] = pd.to_numeric(df["day"], errors="coerce").fillna(0).astype(int)
    for col in ("price", "quantity"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "price" in df.columns and "quantity" in df.columns:
        df = df.dropna(subset=["price", "quantity"]).copy()
    df["plot_ts"] = df.apply(
        lambda r: int(r["timestamp"]) + offsets.get(int(r["day"]), 0), axis=1
    )

    df["is_own_trade"] = (
        df["buyer"].fillna("") == "SUBMISSION"
    ) | (
        df["seller"].fillna("") == "SUBMISSION"
    )

    return df


def load_trades_from_log(offsets: dict, log_name: str) -> pd.DataFrame:
    """
    Load tradeHistory directly from a .log JSON file in DATA_DIR.
    Returns an empty DataFrame if no trades exist.
    """
    log_path = os.path.join(DATA_DIR, log_name)
    with open(log_path, encoding="utf-8") as f:
        data = json.load(f)

    trades = data.get("tradeHistory", [])
    if not trades:
        return pd.DataFrame()

    df = pd.DataFrame(trades)
    if df.empty or "timestamp" not in df.columns:
        return pd.DataFrame()

    if "day" not in df.columns:
        df["day"] = 0

    df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).copy()
    df["timestamp"] = df["timestamp"].astype(int)

    df["day"] = pd.to_numeric(df["day"], errors="coerce").fillna(0).astype(int)
    for col in ("price", "quantity"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    if "price" in df.columns and "quantity" in df.columns:
        df = df.dropna(subset=["price", "quantity"]).copy()
    df["plot_ts"] = df.apply(
        lambda r: int(r["timestamp"]) + offsets.get(int(r["day"]), 0), axis=1
    )

    buyer = df["buyer"].fillna("") if "buyer" in df.columns else ""
    seller = df["seller"].fillna("") if "seller" in df.columns else ""
    df["is_own_trade"] = (buyer == "SUBMISSION") | (seller == "SUBMISSION")

    return df


# ── Orderbook helpers ─────────────────────────────────────────────────────────

def _build_staircase(levels: list, direction: str):
    """
    Build a closed polygon (xs, ys) for a filled depth-chart staircase.

    Parameters
    ----------
    levels    : list of (price, volume), sorted best-first
    direction : 'left'  — bids extend leftward
                'right' — asks extend rightward
    """
    prices   = [p for p, _ in levels]
    cum_vols = list(np.cumsum([v for _, v in levels]))
    n = len(levels)

    xs: list = [prices[0]]
    ys: list = [0]

    for i in range(n):
        xs.append(prices[i])
        ys.append(cum_vols[i])
        if i < n - 1:
            xs.append(prices[i + 1])
            ys.append(cum_vols[i])

    step = -2 if direction == "left" else 2
    xs.extend([prices[-1] + step, prices[-1] + step])
    ys.extend([cum_vols[-1], 0])

    return xs, ys


def draw_orderbook(ax, prices_df: pd.DataFrame, ts: float, product: str):
    """Clear ax and redraw the orderbook depth chart for the snapshot nearest ts."""
    ax.cla()

    idx = int((prices_df["timestamp"] - ts).abs().idxmin())
    row = prices_df.loc[idx]

    mid       = float(row["mid_price"])
    actual_ts = int(row["timestamp"])
    day       = int(row["day"])

    bids, asks = [], []
    for i in range(1, 4):
        bp, bv = row[f"bid_price_{i}"], row[f"bid_volume_{i}"]
        if pd.notna(bp) and pd.notna(bv):
            bids.append((float(bp), float(bv)))
        ap, av = row[f"ask_price_{i}"], row[f"ask_volume_{i}"]
        if pd.notna(ap) and pd.notna(av):
            asks.append((float(ap), float(av)))

    bids.sort(key=lambda x: -x[0])
    asks.sort(key=lambda x:  x[0])

    if bids:
        bx, by = _build_staircase(bids, "left")
        ax.fill(bx, by, color="#2ecc71", alpha=0.45, label="Bids")
        ax.plot(bx, by, color="#27ae60", linewidth=1.2)

    if asks:
        ax_x, ax_y = _build_staircase(asks, "right")
        ax.fill(ax_x, ax_y, color="#e74c3c", alpha=0.45, label="Asks")
        ax.plot(ax_x, ax_y, color="#c0392b", linewidth=1.2)

    ax.axvline(x=mid, color="#3498db", linestyle="--", linewidth=1,
               alpha=0.8, label=f"Mid: {mid:.1f}")
    ax.set_title(f"{product} Orderbook\nts={actual_ts}  (day {day})", fontsize=9)
    ax.set_xlabel("Price", fontsize=8)
    ax.set_ylabel("Cumulative Volume", fontsize=8)
    ax.legend(fontsize=7, loc="upper center")
    ax.grid(True, alpha=0.3)
    ax.tick_params(labelsize=7)



def _day_color(idx: int) -> str:
    return DAY_PALETTE[idx % len(DAY_PALETTE)]


# ── Interactive price + orderbook chart ───────────────────────────────────────

def create_interactive_chart(
    product: str,
    prices_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    color: str,
):
    """
    Figure 1 (per product): interactive mid-price time series with an
    orderbook depth snapshot panel, slider, timelapse controls, and day
    navigation buttons.
    """
    days = sorted(prices_df["day"].unique().tolist())

    fig = plt.figure(figsize=(16, 9))
    fig.suptitle(
        f"{product}  —  Price History & Orderbook Snapshot",
        fontsize=13, fontweight="bold",
    )

    gs = gridspec.GridSpec(
        1, 2, width_ratios=[3, 1], wspace=0.30,
        left=0.07, right=0.97, top=0.88, bottom=0.22,
    )
    ax_price = fig.add_subplot(gs[0, 0])
    ax_book  = fig.add_subplot(gs[0, 1])
    ax_sl    = fig.add_axes([0.10, 0.14, 0.80, 0.03])

    _state = {"day_idx": 0, "playing": False, "speed_step": SPEED_STEPS[1]}
    _refs  = {}

    def _get_day_data():
        day = days[_state["day_idx"]]
        p = prices_df[prices_df["day"] == day].reset_index(drop=True)
        if not trades_df.empty and "symbol" in trades_df.columns:
            t = trades_df[
                (trades_df["day"] == day) & (trades_df["symbol"] == product)
            ].reset_index(drop=True)
        else:
            t = pd.DataFrame()
        return day, p, t

    def _draw_price_panel():
        day, day_prices, day_trades = _get_day_data()
        ax_price.cla()

        ts_min = float(day_prices["timestamp"].min())
        ts_max = float(day_prices["timestamp"].max())

        plot_prices = day_prices.copy()
        plot_prices.loc[plot_prices["mid_price"] == 0, "mid_price"] = np.nan
        plot_prices["mid_price"] = plot_prices["mid_price"].ffill()
        valid_mid = plot_prices["mid_price"].dropna()
        ymin = float(valid_mid.min())
        ymax = float(valid_mid.max())

        # Best bid / ask lines — same green/red as the orderbook snapshot so they
        # correspond directly to what the depth chart shows at each timestamp.
        if "bid_price_1" in plot_prices.columns:
            bid_line = plot_prices["bid_price_1"].replace(0, np.nan)
            ax_price.step(
                plot_prices["timestamp"], bid_line, where="post",
                color="#27ae60", linewidth=0.9, alpha=0.85,
                label="Best Bid", zorder=3,
            )
            valid_bid = bid_line.dropna()
            if not valid_bid.empty:
                ymin = min(ymin, float(valid_bid.min()))
                ymax = max(ymax, float(valid_bid.max()))

        if "ask_price_1" in plot_prices.columns:
            ask_line = plot_prices["ask_price_1"].replace(0, np.nan)
            ax_price.step(
                plot_prices["timestamp"], ask_line, where="post",
                color="#e74c3c", linewidth=0.9, alpha=0.85,
                label="Best Ask", zorder=3,
            )
            valid_ask = ask_line.dropna()
            if not valid_ask.empty:
                ymin = min(ymin, float(valid_ask.min()))
                ymax = max(ymax, float(valid_ask.max()))

        ax_price.plot(
            plot_prices["timestamp"], plot_prices["mid_price"],
            color=color, linewidth=0.75, label="Mid Price", zorder=2,
        )

        # Market trades as scatter points (sized by volume)
        if not day_trades.empty:
            market_trades = (
                day_trades[~day_trades["is_own_trade"]]
                if "is_own_trade" in day_trades.columns
                else day_trades
            )
            if not market_trades.empty:
                ax_price.scatter(
                    market_trades["timestamp"], market_trades["price"],
                    s=18, color="#888888", alpha=0.55, marker="o",
                    zorder=4, label="Market trades",
                )

        if "is_own_trade" in day_trades.columns:
            own = day_trades[day_trades["is_own_trade"]]
            if not own.empty:
                ax_price.scatter(
                    own["timestamp"], own["price"],
                    s=55, color=OWN_TRADE_COLOR, marker="*",
                    edgecolors="black", linewidths=0.5,
                    zorder=6, label="Our trades",
                )

        cursor_line = ax_price.axvline(
            x=ts_min, color="orange", linestyle=":", linewidth=1.5,
            alpha=0.85, zorder=4, label="Snapshot cursor",
        )

        ax_price.set_xlabel("Timestamp", fontsize=9)
        ax_price.set_ylabel("Price", fontsize=9)
        ax_price.set_title(f"{product}  Mid Price  —  Day {day}", fontsize=10)
        ax_price.legend(fontsize=8)
        ax_price.grid(True, alpha=0.3)
        ax_price.set_ylim(ymin - 2, ymax + 2)
        ax_price.set_xlim(ts_min, ts_max)

        return cursor_line, ts_min, ts_max, day_prices

    def _refresh():
        cursor_line, ts_min, ts_max, day_prices = _draw_price_panel()
        _refs["cursor"]     = cursor_line
        _refs["ts_min"]     = ts_min
        _refs["ts_max"]     = ts_max
        _refs["day_prices"] = day_prices

        slider.eventson = False
        slider.valmin = ts_min
        slider.valmax = ts_max
        slider.ax.set_xlim(ts_min, ts_max)
        slider.set_val(ts_min)
        slider.eventson = True

        draw_orderbook(ax_book, day_prices, ts_min, product)
        fig.canvas.draw_idle()

    # Initial draw
    cursor_line, ts_min, ts_max, day_prices = _draw_price_panel()
    _refs["cursor"]     = cursor_line
    _refs["ts_min"]     = ts_min
    _refs["ts_max"]     = ts_max
    _refs["day_prices"] = day_prices

    slider = Slider(
        ax_sl, "Snapshot", ts_min, ts_max,
        valinit=ts_min, valstep=100, color="orange",
    )
    draw_orderbook(ax_book, day_prices, ts_min, product)

    def on_slide(val):
        _refs["cursor"].set_xdata([val, val])
        draw_orderbook(ax_book, _refs["day_prices"], val, product)
        fig.canvas.draw_idle()

    slider.on_changed(on_slide)

    # Timelapse
    timer = fig.canvas.new_timer(interval=50)

    def _advance():
        new_val = slider.val + _state["speed_step"]
        if new_val >= _refs["ts_max"]:
            new_val = _refs["ts_max"]
            timer.stop()
            _state["playing"] = False
            btn_play.label.set_text("Play")
        slider.set_val(new_val)

    timer.add_callback(_advance)

    btn_play = Button(fig.add_axes([0.10, 0.05, 0.11, 0.06]), "Play")

    def _toggle_play(event):
        if _state["playing"]:
            timer.stop()
            _state["playing"] = False
            btn_play.label.set_text("Play")
        else:
            timer.start()
            _state["playing"] = True
            btn_play.label.set_text("Pause")

    btn_play.on_clicked(_toggle_play)

    fig.text(0.235, 0.078, "Speed:", fontsize=8, ha="right", va="center")

    speed_btns = []
    for mult, rect in zip(
        [1, 5, 10, 50, 100],
        [[0.25, 0.05, 0.07, 0.06], [0.33, 0.05, 0.07, 0.06],
         [0.41, 0.05, 0.07, 0.06], [0.49, 0.05, 0.07, 0.06],
         [0.57, 0.05, 0.07, 0.06]],
    ):
        def _make_cb(m):
            def _cb(event):
                _state["speed_step"] = SPEED_STEPS[m]
            return _cb
        b = Button(fig.add_axes(rect), f"{mult}x")
        b.on_clicked(_make_cb(mult))
        speed_btns.append(b)

    btn_prev = Button(fig.add_axes([0.70, 0.05, 0.11, 0.06]), "◀ Prev Day")
    btn_next = Button(fig.add_axes([0.83, 0.05, 0.11, 0.06]), "Next Day ▶")

    def _go_prev(event):
        if _state["playing"]:
            timer.stop()
            _state["playing"] = False
            btn_play.label.set_text("Play")
        if _state["day_idx"] > 0:
            _state["day_idx"] -= 1
            _refresh()

    def _go_next(event):
        if _state["playing"]:
            timer.stop()
            _state["playing"] = False
            btn_play.label.set_text("Play")
        if _state["day_idx"] < len(days) - 1:
            _state["day_idx"] += 1
            _refresh()

    btn_prev.on_clicked(_go_prev)
    btn_next.on_clicked(_go_next)

    # Prevent garbage collection of widgets
    fig._slider     = slider
    fig._timer      = timer
    fig._play_state = _state
    fig._btn_play   = btn_play
    fig._speed_btns = speed_btns
    fig._btn_prev   = btn_prev
    fig._btn_next   = btn_next
    fig._refs       = _refs

    return fig


# ── Trade analysis chart (2 × 3 grid) ────────────────────────────────────────

def create_trade_analysis(
    product: str,
    prices_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    day_sep_ts: list,
    color: str,
):
    """
    Figure 2 (per product): 2×3 static analysis panels.

    [0,0] Trade price vs time + bid-ask spread shading
    [0,1] Cumulative trade volume
    [0,2] Bid-ask spread over time
    [1,0] Trade price distribution histogram
    [1,1] Profit & Loss over time
    [1,2] Trade volume per tick
    """
    prod_trades = (
        trades_df[trades_df["symbol"] == product].copy()
        if not trades_df.empty and "symbol" in trades_df.columns
        else pd.DataFrame()
    )

    # Forward-fill any zero-value spikes so they hold the last valid price.
    prices_df = prices_df.copy()
    for col in ("mid_price", "bid_price_1", "ask_price_1"):
        if col in prices_df.columns:
            prices_df.loc[prices_df[col] == 0, col] = np.nan
            prices_df[col] = prices_df[col].ffill()

    fig = plt.figure(figsize=(18, 13))
    fig.suptitle(f"{product}  —  Trade Analysis", fontsize=13, fontweight="bold")

    gs = gridspec.GridSpec(3, 3, hspace=0.45, wspace=0.30,
                           left=0.06, right=0.97, top=0.93, bottom=0.06)
    axes = np.empty((2, 3), dtype=object)
    for r in range(2):
        for c in range(3):
            axes[r, c] = fig.add_subplot(gs[r, c])
    ax_inv = fig.add_subplot(gs[2, :])

    def _add_day_seps(ax):
        for sep_ts in day_sep_ts:
            ax.axvline(x=sep_ts, color="gray", linestyle="--",
                       linewidth=0.8, alpha=0.55)

    # ── [0,0] Trade price vs time + spread shading ───────────────────────────
    ax = axes[0, 0]
    ax.fill_between(
        prices_df["plot_ts"],
        prices_df["bid_price_1"],
        prices_df["ask_price_1"],
        alpha=0.15, color=color, label="Bid-Ask Spread",
    )
    ax.plot(
        prices_df["plot_ts"], prices_df["mid_price"],
        color=color, linewidth=0.6, label="Mid Price", zorder=2,
    )
    if not prod_trades.empty:
        max_qty = prod_trades["quantity"].max()
        has_flag = "is_own_trade" in prod_trades.columns
        for i, (day_val, grp) in enumerate(prod_trades.groupby("day")):
            market = grp[~grp["is_own_trade"]] if has_flag else grp
            own    = grp[grp["is_own_trade"]]  if has_flag else pd.DataFrame()

            if not market.empty:
                sizes = (market["quantity"] / max_qty * 20 + 4).values
                ax.scatter(
                    market["plot_ts"], market["price"],
                    s=sizes, color=_day_color(i), alpha=0.55, zorder=3,
                    label=f"Market trades day {day_val}",
                )
            if not own.empty:
                sizes_own = (own["quantity"] / max_qty * 20 + 4).values
                ax.scatter(
                    own["plot_ts"], own["price"],
                    s=sizes_own * 2, color=OWN_TRADE_COLOR, marker="*",
                    edgecolors="black", linewidths=0.5, alpha=0.9, zorder=4,
                    label=f"Our trades day {day_val}",
                )
    _add_day_seps(ax)
    ax.set_title("Trade Price vs Time (with Spread)", fontsize=10)
    ax.set_xlabel("Timestamp", fontsize=8)
    ax.set_ylabel("Price", fontsize=8)
    ax.legend(fontsize=7)
    ax.grid(True, alpha=0.3)

    # ── [0,1] Cumulative trade volume ────────────────────────────────────────
    ax = axes[0, 1]
    if not prod_trades.empty:
        sorted_t = prod_trades.sort_values("plot_ts").copy()
        sorted_t["cum_qty"] = sorted_t["quantity"].cumsum()
        for i, (day_val, grp) in enumerate(sorted_t.groupby("day", sort=True)):
            ax.fill_between(
                grp["plot_ts"],
                grp["cum_qty"] - grp["quantity"],
                grp["cum_qty"],
                step="post", color=_day_color(i), alpha=0.45,
                label=f"Day {day_val}",
            )
        ax.step(
            sorted_t["plot_ts"], sorted_t["cum_qty"],
            where="post", color="black", linewidth=1.0, alpha=0.7,
        )
    _add_day_seps(ax)
    ax.set_title("Cumulative Trade Volume", fontsize=10)
    ax.set_xlabel("Timestamp", fontsize=8)
    ax.set_ylabel("Cumulative Quantity", fontsize=8)
    ax.legend(fontsize=7)
    ax.grid(True, alpha=0.3)

    # ── [0,2] Bid-ask spread over time ───────────────────────────────────────
    ax = axes[0, 2]
    spread = prices_df["ask_price_1"] - prices_df["bid_price_1"]
    ax.scatter(prices_df["plot_ts"], spread, s=2, color=color, alpha=0.6)
    _add_day_seps(ax)
    ax.set_title("Bid-Ask Spread Over Time", fontsize=10)
    ax.set_xlabel("Timestamp", fontsize=8)
    ax.set_ylabel("Spread (ticks)", fontsize=8)
    ax.grid(True, alpha=0.3)

    # ── [1,0] Trade price distribution ──────────────────────────────────────
    ax = axes[1, 0]
    if not prod_trades.empty:
        all_prices = prod_trades["price"].dropna()
        bins = np.linspace(all_prices.min() - 1, all_prices.max() + 1, 35)
        for i, (day_val, grp) in enumerate(prod_trades.groupby("day")):
            ax.hist(grp["price"], bins=bins, density=True,
                    color=_day_color(i), alpha=0.55, label=f"Day {day_val}")
        mean_p = all_prices.mean()
        ax.axvline(x=mean_p, color="black", linestyle="--",
                   linewidth=1.2, label=f"Mean: {mean_p:.1f}")
    ax.set_title("Trade Price Distribution", fontsize=10)
    ax.set_xlabel("Price", fontsize=8)
    ax.set_ylabel("Density", fontsize=8)
    ax.legend(fontsize=7)
    ax.grid(True, alpha=0.3)

    # ── [1,1] Profit & Loss over time ────────────────────────────────────────
    ax = axes[1, 1]
    if "profit_and_loss" in prices_df.columns:
        pnl_df = prices_df[prices_df["profit_and_loss"] != 0].copy()
        if not pnl_df.empty:
            for i, (day_val, grp) in enumerate(pnl_df.groupby("day")):
                ax.plot(grp["plot_ts"], grp["profit_and_loss"],
                        color=_day_color(i), linewidth=1.0,
                        label=f"Day {day_val}")
            ax.axhline(y=0, color="gray", linestyle="--",
                       linewidth=0.8, alpha=0.6)
        else:
            ax.text(0.5, 0.5, "No P&L data", transform=ax.transAxes,
                    ha="center", va="center", fontsize=10, color="gray")
    else:
        ax.text(0.5, 0.5, "No profit_and_loss column",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=10, color="gray")
    _add_day_seps(ax)
    ax.set_title("Profit & Loss Over Time", fontsize=10)
    ax.set_xlabel("Timestamp", fontsize=8)
    ax.set_ylabel("P&L", fontsize=8)
    ax.legend(fontsize=7)
    ax.grid(True, alpha=0.3)

    # ── [1,2] Trade volume per tick ──────────────────────────────────────────
    ax = axes[1, 2]
    if not prod_trades.empty:
        ts_range = prod_trades["plot_ts"].max() - prod_trades["plot_ts"].min()
        bar_width = max(100, ts_range / 300)
        for i, (day_val, grp) in enumerate(prod_trades.groupby("day")):
            ax.bar(grp["plot_ts"], grp["quantity"],
                   width=bar_width, color=_day_color(i), alpha=0.65,
                   label=f"Day {day_val}")
    _add_day_seps(ax)
    ax.set_title("Trade Volume per Tick", fontsize=10)
    ax.set_xlabel("Timestamp", fontsize=8)
    ax.set_ylabel("Quantity", fontsize=8)
    ax.legend(fontsize=7)
    ax.grid(True, alpha=0.3)

    # ── [2, :] Inventory (position) over time ────────────────────────────────
    ax = ax_inv
    inv_ts_min = float(prices_df["plot_ts"].min())
    inv_ts_max = float(prices_df["plot_ts"].max())

    if not prod_trades.empty and "is_own_trade" in prod_trades.columns:
        own = prod_trades[prod_trades["is_own_trade"]].sort_values("plot_ts").copy()
        if not own.empty:
            own["signed_qty"] = own.apply(
                lambda r: r["quantity"] if str(r.get("buyer", "")) == "SUBMISSION"
                else -r["quantity"],
                axis=1,
            )
            own["inventory"] = own["signed_qty"].cumsum()
            for i, (day_val, grp) in enumerate(own.groupby("day", sort=True)):
                # Determine full time bounds for this day from the price data
                day_prices = prices_df[prices_df["day"] == day_val]
                day_ts_min = float(day_prices["plot_ts"].min()) if not day_prices.empty else inv_ts_min
                day_ts_max = float(day_prices["plot_ts"].max()) if not day_prices.empty else inv_ts_max

                xs = grp["plot_ts"].tolist()
                ys = grp["inventory"].tolist()

                # Starting inventory before this group's first trade
                start_inv = ys[0] - grp["signed_qty"].iloc[0]

                # Prepend anchor at day start so the zero-position region is visible
                xs = [day_ts_min] + xs
                ys = [start_inv] + ys

                # Append anchor at day end to extend the flat post-trade line
                xs = xs + [day_ts_max]
                ys = ys + [ys[-1]]

                ax.step(
                    xs, ys,
                    where="post", color=_day_color(i), linewidth=1.2,
                    label=f"Day {day_val}",
                )
                ax.fill_between(
                    xs, 0, ys,
                    step="post", color=_day_color(i), alpha=0.20,
                )
        else:
            ax.text(0.5, 0.5, "No own trades found",
                    transform=ax.transAxes, ha="center", va="center",
                    fontsize=10, color="gray")
    else:
        ax.text(0.5, 0.5, "No trade data",
                transform=ax.transAxes, ha="center", va="center",
                fontsize=10, color="gray")
    ax.axhline(y=0, color="gray", linestyle="--", linewidth=0.8, alpha=0.6)
    _add_day_seps(ax)
    ax.set_title("Inventory (Position) Over Time", fontsize=10)
    ax.set_xlabel("Timestamp", fontsize=8)
    ax.set_ylabel("Net Position", fontsize=8)
    ax.set_xlim(inv_ts_min, inv_ts_max)
    ax.legend(fontsize=7)
    ax.grid(True, alpha=0.3)

    return fig


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Visualize IMC logs/ticks with optional product filtering."
    )
    parser.add_argument(
        "stocks",
        nargs="*",
        help="Optional product symbols to plot (e.g. SQUID_INK HYDROGEL_PACK).",
    )
    args = parser.parse_args()

    source_type, source_name = select_data_source()

    if source_type == "tick":
        market_df, products, days, day_sep_ts, offsets = load_market_data_from_tick(source_name)
        trades_df = load_trades_from_tick(offsets, source_name)
    else:
        market_df, products, days, day_sep_ts, offsets = load_market_data_from_log(source_name)
        trades_df = load_trades_from_log(offsets, source_name)

    selected_products = products
    if args.stocks:
        requested = set(args.stocks)
        selected_products = [p for p in products if p in requested]
        missing = sorted(requested - set(products))
        if missing:
            print(f"Warning: requested stock(s) not found in data: {', '.join(missing)}")
        if not selected_products:
            raise ValueError("None of the requested stocks were found in the selected source.")

    for i, product in enumerate(selected_products):
        color       = PRODUCT_COLORS[i % len(PRODUCT_COLORS)]
        prod_prices = market_df[market_df["product"] == product].copy()

        create_interactive_chart(product, prod_prices, trades_df, color)
        create_trade_analysis(product, prod_prices, trades_df, day_sep_ts, color)

    plt.show()
