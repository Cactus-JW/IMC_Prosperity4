import json
import re
from pathlib import Path

import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.colors import to_hex
from matplotlib.widgets import Button

# This file lives in IMC/Strategy Evaluation/Round 1/
# parents[2] points to IMC/
BASE_DIR = Path(__file__).resolve().parents[2]

# Round 1 strategy output folder
RUN_ROOT_REL = "Output logs/Round 1/Intarian Pepper Root"
PRODUCT = "INTARIAN_PEPPER_ROOT"


def _tick_sort_key(name: str):
    m = re.search(r"(\d+)", name)
    return int(m.group(1)) if m else 10**9


def discover_runs(run_root_rel: str):
    run_root = BASE_DIR / run_root_rel
    if not run_root.exists():
        return []

    runs = []
    for d in sorted([p for p in run_root.iterdir() if p.is_dir()], key=lambda p: _tick_sort_key(p.name)):
        run_id = d.name
        market_path = d / f"market_data_{run_id}.csv"
        trades_path = d / f"trades_{run_id}.csv"
        json_path = d / f"{run_id}.json"
        if market_path.exists() and trades_path.exists() and json_path.exists():
            runs.append((run_id, str(d.relative_to(BASE_DIR)).replace("\\", "/"), run_id))
    return runs


RUNS = discover_runs(RUN_ROOT_REL)
COLORS = [plt.cm.tab20(i / max(1, len(RUNS))) for i in range(max(1, len(RUNS)))]


def load_run(folder_rel, run_id):
    folder = BASE_DIR / folder_rel

    market = pd.read_csv(folder / f"market_data_{run_id}.csv")
    if "product" in market.columns:
        market = market[market["product"] == PRODUCT]
    market = market.sort_values("timestamp").reset_index(drop=True)

    trades = pd.read_csv(folder / f"trades_{run_id}.csv")
    if "symbol" in trades.columns:
        trades = trades[trades["symbol"] == PRODUCT]
    trades = trades.sort_values("timestamp").reset_index(drop=True)
    trades["is_submission"] = (trades["buyer"] == "SUBMISSION") | (trades["seller"] == "SUBMISSION")

    profit_ts = []
    with open(folder / f"{run_id}.json", encoding="utf-8") as f:
        data = json.load(f)
    graph_log = data.get("graphLog", "")
    if graph_log:
        graph_rows = graph_log.strip().split("\n")[1:]
        for r in graph_rows:
            if ";" not in r:
                continue
            left, right = r.split(";", 1)
            try:
                profit_ts.append((int(left), float(right)))
            except ValueError:
                continue

    your_trades = trades[trades["is_submission"]].copy()
    position = 0
    position_events = [(0, 0)]
    for _, t in your_trades.iterrows():
        if t["buyer"] == "SUBMISSION":
            position += t["quantity"]
        else:
            position -= t["quantity"]
        position_events.append((int(t["timestamp"]), position))

    buy_fills = (
        your_trades[your_trades["buyer"] == "SUBMISSION"]
        .groupby("timestamp")["quantity"].sum().to_dict()
    )
    sell_fills = (
        your_trades[your_trades["seller"] == "SUBMISSION"]
        .groupby("timestamp")["quantity"].sum().to_dict()
    )

    orders_records = []
    log_path = folder / f"{run_id}.log"
    if log_path.exists():
        with open(log_path, encoding="utf-8") as f:
            log_data = json.load(f)

        for tick in log_data.get("logs", []):
            ts = tick.get("timestamp", 0)
            llog = tick.get("lambdaLog", "")
            bid_posted = sum(int(m) for m in re.findall(r"POSTING BID (\d+)x", llog))
            ask_posted = sum(int(m) for m in re.findall(r"POSTING ASK (\d+)x", llog))
            orders_records.append({
                "timestamp": ts,
                "bid_resting": max(0, bid_posted - buy_fills.get(ts, 0)),
                "ask_resting": max(0, ask_posted - sell_fills.get(ts, 0)),
            })

    orders_df = pd.DataFrame(orders_records) if orders_records else pd.DataFrame(
        columns=["timestamp", "bid_resting", "ask_resting"]
    )

    return {
        "market": market,
        "trades": trades,
        "profit_ts": profit_ts,
        "position_ts": position_events,
        "orders": orders_df,
    }


def main():
    if not RUNS:
        print(f"No valid runs found under: {BASE_DIR / RUN_ROOT_REL}")
        print("Expected each run folder to contain market_data_<run>.csv, trades_<run>.csv, <run>.json")
        return

    print(f"Loading {len(RUNS)} run(s) for {PRODUCT}...")
    run_data = {label: load_run(folder, run_id) for label, folder, run_id in RUNS}

    fig = plt.figure(figsize=(18, 26))
    fig.suptitle(f"{PRODUCT} - StrategyIntarian Evaluation", fontsize=13, fontweight="bold", y=0.999)

    n = len(RUNS)
    btn_w = 0.88 / n
    btn_start_x = 0.06
    btn_bottom = 0.970
    btn_height = 0.024

    toggle_state = {label: True for label, _, _ in RUNS}
    toggle_buttons = []

    fig.text(0.015, btn_bottom + btn_height / 2, "Show:", va="center", fontsize=8, color="grey")

    gs = gridspec.GridSpec(7, 1, figure=fig, top=0.956, bottom=0.04, hspace=0.55)
    ax_profit = fig.add_subplot(gs[0])
    ax_mid = fig.add_subplot(gs[1], sharex=ax_profit)
    ax_bidask = fig.add_subplot(gs[2], sharex=ax_profit)
    ax_trades = fig.add_subplot(gs[3], sharex=ax_profit)
    ax_pos = fig.add_subplot(gs[4], sharex=ax_profit)
    ax_orders = fig.add_subplot(gs[5], sharex=ax_profit)
    ax_vol = fig.add_subplot(gs[6], sharex=ax_profit)

    plot_axes = [ax_profit, ax_mid, ax_bidask, ax_trades, ax_pos, ax_orders, ax_vol]
    titles = [
        "Cumulative P&L",
        "Mid-Price",
        "Bid & Ask Prices (solid=bid, dashed=ask)",
        "Trade Prices  (o = your trades,  x = bot trades)",
        "Net Position",
        "Resting Orders on Book  (solid=bid, dashed=ask)",
        "Volume at Top of Book  (solid=bid, dashed=ask)",
    ]
    ylabels = ["P&L (XIRECS)", "Price", "Price", "Price", "Units", "Quantity", "Volume"]

    for ax, title, ylabel in zip(plot_axes, titles, ylabels):
        ax.set_title(title, fontsize=8.5, pad=3)
        ax.set_ylabel(ylabel, fontsize=8)
        ax.tick_params(labelsize=7)
        ax.grid(True, alpha=0.25, linewidth=0.5)

    ax_vol.set_xlabel("Timestamp", fontsize=9)
    for ax in plot_axes[:-1]:
        plt.setp(ax.get_xticklabels(), visible=False)

    artists = {label: [] for label, _, _ in RUNS}

    for i, (label, _, _) in enumerate(RUNS):
        color = COLORS[i]
        d = run_data[label]
        market = d["market"]
        trades = d["trades"]

        if d["profit_ts"]:
            ts, vals = zip(*d["profit_ts"])
            line, = ax_profit.plot(ts, vals, color=color, lw=1.3, label=label)
            artists[label].append(line)

        if not market.empty:
            line, = ax_mid.plot(market["timestamp"], market["mid_price"], color=color, lw=1.0)
            artists[label].append(line)

            b, = ax_bidask.plot(market["timestamp"], market["bid_price_1"], color=color, lw=1.0, ls="-")
            a, = ax_bidask.plot(market["timestamp"], market["ask_price_1"], color=color, lw=1.0, ls="--")
            artists[label].extend([b, a])

            bv, = ax_vol.plot(market["timestamp"], market["bid_volume_1"], color=color, lw=1.0, ls="-")
            av, = ax_vol.plot(market["timestamp"], market["ask_volume_1"], color=color, lw=1.0, ls="--")
            artists[label].extend([bv, av])

        if not trades.empty:
            my = trades[trades["is_submission"]]
            bot = trades[~trades["is_submission"]]
            if not my.empty:
                sc = ax_trades.scatter(my["timestamp"], my["price"], color=color, marker="o", s=22, zorder=3)
                artists[label].append(sc)
            if not bot.empty:
                sc = ax_trades.scatter(bot["timestamp"], bot["price"], color=color, marker="x", s=14, alpha=0.35, zorder=2)
                artists[label].append(sc)

        if d["position_ts"]:
            pts, pvals = zip(*d["position_ts"])
            st, = ax_pos.step(pts, pvals, color=color, lw=1.0, where="post")
            artists[label].append(st)

        orders = d["orders"]
        if not orders.empty:
            bl, = ax_orders.plot(orders["timestamp"], orders["bid_resting"], color=color, lw=1.0, ls="-")
            al, = ax_orders.plot(orders["timestamp"], orders["ask_resting"], color=color, lw=1.0, ls="--")
            artists[label].extend([bl, al])

    ax_profit.legend(fontsize=7.5, loc="upper left", ncol=4, framealpha=0.7)
    ax_profit.axhline(0, color="black", lw=0.5, ls=":")
    ax_pos.axhline(0, color="black", lw=0.5, ls=":")

    for i, (label, _, _) in enumerate(RUNS):
        color = COLORS[i]
        active_hex = to_hex(color)
        ax_btn = fig.add_axes([btn_start_x + i * btn_w, btn_bottom, btn_w - 0.006, btn_height])
        btn = Button(ax_btn, label, color=active_hex, hovercolor=active_hex)
        btn.label.set_fontsize(9)
        btn.label.set_fontweight("bold")
        btn.label.set_color("white")
        toggle_buttons.append(btn)

        def make_toggle(lbl, ax_b, col_hex):
            def on_click(_):
                toggle_state[lbl] = not toggle_state[lbl]
                ax_b.set_facecolor(col_hex if toggle_state[lbl] else "#aaaaaa")
                for artist in artists[lbl]:
                    artist.set_visible(toggle_state[lbl])
                fig.canvas.draw_idle()
            return on_click

        btn.on_clicked(make_toggle(label, ax_btn, active_hex))

    print("Rendering...")
    plt.show()


if __name__ == "__main__":
    main()
