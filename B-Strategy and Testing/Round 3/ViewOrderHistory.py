import os

import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
from matplotlib.widgets import Button, Slider, RadioButtons

# Constants
DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "A-Historical Data", "Round 3"))
PRODUCTS = [
    "HYDROGEL_PACK",
    "VELVETFRUIT_EXTRACT",
    "VEV_4000",
    "VEV_4500",
    "VEV_5000",
    "VEV_5100",
    "VEV_5200",
    "VEV_5300",
    "VEV_5400",
    "VEV_5500",
    "VEV_6000",
    "VEV_6500",
]
DAY_GAP = 10_000  # visual gap (timestamp units) between days

# Timelapse: timestamp units advanced per 50 ms timer tick at each speed setting
SPEED_STEPS = {1: 100, 5: 500, 10: 1_000, 50: 5_000, 100: 10_000}

PRODUCT_COLORS = {p: "#4169E1" for p in PRODUCTS}
DAY_COLORS = {0: "#3655B9", 1: "#0FD02F", 2: "#900FD0"}


# Data loading
def load_prices(product: str):
    """
    Load and combine prices CSV for all days.

    Returns
    -------
    combined    : pd.DataFrame        — prices rows with a 'plot_ts' column
    day_sep_tss : list[int]           — x-positions of each day-boundary separator
    offsets     : dict[int, int]      — per-day timestamp offset applied
    """
    days_fnames = [
        (0, "prices_round_3_day_0.csv"),
        (1, "prices_round_3_day_1.csv"),
        (2, "prices_round_3_day_2.csv"),
    ]
    frames = []
    offset = 0
    day_sep_tss = []
    offsets = {}

    for i, (day, fname) in enumerate(days_fnames):
        path = os.path.join(DATA_DIR, fname)
        df = pd.read_csv(path, sep=";")
        df = df[df["product"] == product].copy()
        # Replace 0 mid_price (one-sided order book) with linearly interpolated
        # values so the price line stays continuous rather than dropping to 0.
        df["mid_price"] = (
            df["mid_price"]
            .replace(0, np.nan)
            .interpolate(method="linear")
            .ffill()
            .bfill()
        )
        df["plot_ts"] = df["timestamp"] + offset
        offsets[day] = offset
        frames.append(df)

        # After each day except the last, record the separator and advance offset
        if i < len(days_fnames) - 1:
            day_sep_tss.append(int(df["plot_ts"].max()) + DAY_GAP // 2)
            offset = int(df["plot_ts"].max()) + DAY_GAP

    combined = pd.concat(frames, ignore_index=True)
    return combined, day_sep_tss, offsets


def load_trades(product: str, offsets: dict) -> pd.DataFrame:
    """
    Load and combine trades CSV for all days, using the per-day timestamp
    offsets computed by load_prices so the x-axes line up.
    """
    frames = []

    for day, fname in [
        (0, "trades_round_3_day_0.csv"),
        (1, "trades_round_3_day_1.csv"),
        (2, "trades_round_3_day_2.csv"),
    ]:
        path = os.path.join(DATA_DIR, fname)
        df = pd.read_csv(path, sep=";")
        df = df[df["symbol"] == product].copy()
        df["day"] = day
        df["plot_ts"] = df["timestamp"] + offsets[day]
        frames.append(df)

    return pd.concat(frames, ignore_index=True)


# Orderbook depth chart helper
def _build_staircase(levels: list, direction: str):
    """
    Build closed polygon (xs, ys) for a filled depth-chart staircase.

    Parameters
    ----------
    levels    : list of (price, volume), already sorted best-first
    direction : 'left'  — bids (staircase extends leftward from best bid)
                'right' — asks (staircase extends rightward from best ask)
    """
    prices = [p for p, _ in levels]
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

    # Extend one unit beyond the last level then drop back to zero
    step = -2 if direction == "left" else 2
    xs.extend([prices[-1] + step, prices[-1] + step])
    ys.extend([cum_vols[-1], 0])

    return xs, ys


# Help with the candle plotting
def resample_ohlc(df, bucket_size):
    """Aggregates tick data into OHLC candles."""
    if bucket_size == "Tick":
        return df

    # Bucket timestamps
    df = df.copy()
    df['ts_bucket'] = (df['timestamp'] // int(bucket_size)) * int(bucket_size)

    ohlc = df.groupby('ts_bucket')['mid_price'].agg(['first', 'max', 'min', 'last'])
    ohlc.columns = ['open', 'high', 'low', 'close']
    ohlc['timestamp'] = ohlc.index
    return ohlc


def draw_orderbook(ax, prices_df: pd.DataFrame, ts: float, product: str):
    """Clear ax and redraw the orderbook depth chart for the snapshot nearest ts (raw timestamp)."""
    ax.cla()

    idx = int((prices_df["timestamp"] - ts).abs().idxmin())
    row = prices_df.loc[idx]

    mid = float(row["mid_price"])
    actual_ts = int(row["timestamp"])
    day = int(row["day"])

    bids, asks = [], []
    for i in range(1, 4):
        bp, bv = row[f"bid_price_{i}"], row[f"bid_volume_{i}"]
        if pd.notna(bp) and pd.notna(bv):
            bids.append((float(bp), float(bv)))

        ap, av = row[f"ask_price_{i}"], row[f"ask_volume_{i}"]
        if pd.notna(ap) and pd.notna(av):
            asks.append((float(ap), float(av)))

    bids.sort(key=lambda x: -x[0])  # best (highest) bid first
    asks.sort(key=lambda x: x[0])   # best (lowest) ask first

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


# Interactive price + orderbook chart
def create_interactive_chart(
    product: str,
    prices_df: pd.DataFrame,
    trades_df: pd.DataFrame,
):
    days = sorted(prices_df["day"].unique().tolist())  # e.g. [0, 1, 2]
    color = PRODUCT_COLORS.get(product, "#3498db")

    fig = plt.figure(figsize=(16, 9))
    fig.suptitle(
        f"{product}  —  Price History & Orderbook Snapshot",
        fontsize=13, fontweight="bold",
    )

    gs = gridspec.GridSpec(
        1, 2,
        width_ratios=[3, 1],
        wspace=0.30,
        left=0.07, right=0.97,
        top=0.88, bottom=0.22,
    )
    ax_price = fig.add_subplot(gs[0, 0])
    ax_book = fig.add_subplot(gs[0, 1])
    ax_sl = fig.add_axes([0.10, 0.14, 0.80, 0.03])

    _state = {"day_idx": 0, "playing": False, "speed_step": SPEED_STEPS[1], "res": "Tick"}
    _refs = {}  # live references updated on each day switch

    def _get_day_data():
        day = days[_state["day_idx"]]
        p = prices_df[prices_df["day"] == day].reset_index(drop=True)
        t = (
            trades_df[trades_df["day"] == day].reset_index(drop=True)
            if not trades_df.empty
            else pd.DataFrame()
        )
        return day, p, t

    def _draw_price_panel():
        day, day_prices, _ = _get_day_data()
        ax_price.cla()

        # 1. Calculate boundaries
        ts_min = float(day_prices["timestamp"].min())
        ts_max = float(day_prices["timestamp"].max())

        # 2. Get Resampled Data
        data = resample_ohlc(day_prices, _state["res"])

        # Calculate limits based on the resampled data for better visuals
        ymin = float(data['low'].min()) if _state["res"] != "Tick" else float(day_prices["mid_price"].min())
        ymax = float(data['high'].max()) if _state["res"] != "Tick" else float(day_prices["mid_price"].max())

        # 2. Conditional Plotting
        if _state["res"] == "Tick":
            ax_price.plot(day_prices["timestamp"], day_prices["mid_price"],
                        color=color, linewidth=0.75, label="Mid Price", zorder=2)
            # Keep bid/ask lines for tick view
            ax_price.plot(day_prices["timestamp"], day_prices["bid_price_1"].replace(0, np.nan),
                        color="#27ae60", linewidth=0.9, alpha=0.85, label="Best Bid")
            ax_price.plot(day_prices["timestamp"], day_prices["ask_price_1"].replace(0, np.nan),
                        color="#e74c3c", linewidth=0.9, alpha=0.85, label="Best Ask")
        else:
            # Draw Candles
            width = int(_state["res"]) * 0.8 # Slightly wider for visibility
            ax_price.vlines(data['timestamp'], data['low'], data['high'], color='black', lw=0.5, zorder=2)
            up = data[data['close'] >= data['open']]
            down = data[data['close'] < data['open']]
            ax_price.bar(up['timestamp'], up['close']-up['open'], width, bottom=up['open'], color="#2ecc71", zorder=3)
            ax_price.bar(down['timestamp'], down['open']-down['close'], width, bottom=down['close'], color='#e74c3c', zorder=3)

        # 3. Common Formatting
        cursor_x = _refs.get("cursor_val", ts_min) # Fallback to ts_min if not set
        cursor_line = ax_price.axvline(
            x=cursor_x, color="orange", linestyle=":", linewidth=1.5,
            alpha=0.85, zorder=4, label="Snapshot cursor",
        )

        ax_price.set_title(f"{product} — Day {day} ({_state['res']} resolution)", fontsize=10)
        ax_price.set_ylim(ymin - 2, ymax + 2)
        ax_price.grid(True, alpha=0.3)
        ax_price.legend(fontsize=8)

        return cursor_line, float(day_prices["timestamp"].min()), float(day_prices["timestamp"].max()), day_prices

    def _refresh():
        """Redraw price panel, reset slider to day start, redraw orderbook."""
        cursor_line, ts_min, ts_max, day_prices = _draw_price_panel()
        _refs["cursor"] = cursor_line
        _refs["ts_min"] = ts_min
        _refs["ts_max"] = ts_max
        _refs["day_prices"] = day_prices

        # Update slider range without firing callbacks
        slider.eventson = False
        slider.valmin = ts_min
        slider.valmax = ts_max
        slider.ax.set_xlim(ts_min, ts_max)
        slider.set_val(ts_min)
        slider.eventson = True

        draw_orderbook(ax_book, day_prices, ts_min, product)
        fig.canvas.draw_idle()

    # Initialise first day
    cursor_line, ts_min, ts_max, day_prices = _draw_price_panel()
    _refs["cursor"] = cursor_line
    _refs["ts_min"] = ts_min
    _refs["ts_max"] = ts_max
    _refs["day_prices"] = day_prices

    # Slider
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

    # Timelapse controls
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
    speed_rects = [
        [0.25, 0.05, 0.07, 0.06],
        [0.33, 0.05, 0.07, 0.06],
        [0.41, 0.05, 0.07, 0.06],
        [0.49, 0.05, 0.07, 0.06],
        [0.57, 0.05, 0.07, 0.06],
    ]
    for mult, rect in zip([1, 5, 10, 50, 100], speed_rects):
        def _make_cb(m):
            def _cb(event):
                _state["speed_step"] = SPEED_STEPS[m]
            return _cb
        b = Button(fig.add_axes(rect), f"{mult}x")
        b.on_clicked(_make_cb(mult))
        speed_btns.append(b)

    # Day navigation buttons
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

    # Keep all widget references alive (prevent garbage collection)
    fig._slider = slider
    fig._timer = timer
    fig._play_state = _state
    fig._btn_play = btn_play
    fig._speed_btns = speed_btns
    fig._btn_prev = btn_prev
    fig._btn_next = btn_next
    fig._refs = _refs

    return fig


# Trade analysis chart
def create_trade_analysis(
    product: str,
    prices_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    day_sep_tss: list,
):
    fig, axes = plt.subplots(2, 2, figsize=(16, 10))
    fig.suptitle(f"{product}  —  Trade Analysis", fontsize=13, fontweight="bold")
    plt.subplots_adjust(hspace=0.35, wspace=0.30,
                        left=0.07, right=0.97, top=0.92, bottom=0.07)

    color = PRODUCT_COLORS.get(product, "#3498db")

    def _add_day_sep(ax):
        for sep_ts in day_sep_tss:
            ax.axvline(x=sep_ts, color="gray", linestyle="--",
                       linewidth=0.8, alpha=0.55)

    # [0, 0]  Trade price vs time  +  spread shading
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

    if not trades_df.empty:
        for day_val, grp in trades_df.groupby("day"):
            sizes = (grp["quantity"] / trades_df["quantity"].max() * 20 + 4).values
            ax.scatter(
                grp["plot_ts"], grp["price"],
                s=sizes, color=DAY_COLORS[day_val], alpha=0.65, zorder=3,
                label=f"Trades day {day_val}",
            )

    _add_day_sep(ax)
    ax.set_title("Trade Price vs Time (with Spread)", fontsize=10)
    ax.set_xlabel("Timestamp", fontsize=8)
    ax.set_ylabel("Price", fontsize=8)
    ax.legend(fontsize=7)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_locator(mticker.MaxNLocator(nbins=6))
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f'{int(x):,}'))

    # [0, 1]  Cumulative trade volume over time
    ax = axes[0, 1]
    if not trades_df.empty:
        sorted_trades = trades_df.sort_values("plot_ts").copy()
        sorted_trades["cum_qty"] = sorted_trades["quantity"].cumsum()

        # Shade each day's contribution in its own colour
        for day_val, grp in sorted_trades.groupby("day", sort=True):
            ax.fill_between(
                grp["plot_ts"],
                grp["cum_qty"] - grp["quantity"],
                grp["cum_qty"],
                step="post",
                color=DAY_COLORS[day_val], alpha=0.45,
                label=f"Day {day_val}",
            )

        # Single cumulative line on top
        ax.step(
            sorted_trades["plot_ts"], sorted_trades["cum_qty"],
            where="post", color="black", linewidth=1.0, alpha=0.7,
        )

    _add_day_sep(ax)
    ax.set_title("Cumulative Trade Volume", fontsize=10)
    ax.set_xlabel("Timestamp", fontsize=8)
    ax.set_ylabel("Cumulative Quantity", fontsize=8)
    ax.legend(fontsize=7)
    ax.grid(True, alpha=0.3)

    # [1, 0]  Bid-Ask spread over time
    ax = axes[1, 0]
    spread = prices_df["ask_price_1"] - prices_df["bid_price_1"]
    ax.scatter(prices_df["plot_ts"], spread, s=2, color=color, alpha=0.6)

    _add_day_sep(ax)
    ax.set_title("Bid-Ask Spread Over Time", fontsize=10)
    ax.set_xlabel("Timestamp", fontsize=8)
    ax.set_ylabel("Spread (ticks)", fontsize=8)
    ax.grid(True, alpha=0.3)

    # [1, 1]  Trade price distribution  (histogram, one colour per day)
    ax = axes[1, 1]
    if not trades_df.empty:
        all_prices = trades_df["price"].dropna()
        bins = np.linspace(all_prices.min() - 1, all_prices.max() + 1, 35)

        for day_val, grp in trades_df.groupby("day"):
            ax.hist(
                grp["price"], bins=bins, density=True,
                color=DAY_COLORS[day_val], alpha=0.55,
                label=f"Day {day_val}",
            )

        mean_price = all_prices.mean()
        ax.axvline(x=mean_price, color="black", linestyle="--",
                   linewidth=1.2, label=f"Mean: {mean_price:.1f}")

    ax.set_title("Trade Price Distribution", fontsize=10)
    ax.set_xlabel("Price", fontsize=8)
    ax.set_ylabel("Density", fontsize=8)
    ax.legend(fontsize=7)
    ax.grid(True, alpha=0.3)

    return fig


# Main
if __name__ == "__main__":
    for product in PRODUCTS:
        prices_df, day_sep_tss, offsets = load_prices(product)
        trades_df = load_trades(product, offsets)

        create_interactive_chart(product, prices_df, trades_df)
        create_trade_analysis(product, prices_df, trades_df, day_sep_tss)

    plt.show()
