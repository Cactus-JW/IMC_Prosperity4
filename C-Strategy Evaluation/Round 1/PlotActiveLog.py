import argparse
import io
import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


DEFAULT_LOG = Path(__file__).resolve().parent.parent / "active log" / "209262.log"


def load_log(log_path: Path) -> dict:
    return json.loads(log_path.read_text(encoding="utf-8"))


def parse_activities(log_data: dict) -> pd.DataFrame:
    text = log_data.get("activitiesLog", "").strip()
    if not text:
        return pd.DataFrame()

    df = pd.read_csv(io.StringIO(text), sep=";")

    numeric_cols = [
        "day",
        "timestamp",
        "bid_price_1",
        "bid_volume_1",
        "bid_price_2",
        "bid_volume_2",
        "bid_price_3",
        "bid_volume_3",
        "ask_price_1",
        "ask_volume_1",
        "ask_price_2",
        "ask_volume_2",
        "ask_price_3",
        "ask_volume_3",
        "mid_price",
        "profit_and_loss",
    ]

    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "timestamp" in df.columns:
        df = df.sort_values(["timestamp", "product"]).reset_index(drop=True)

    return df


def parse_trades(log_data: dict) -> pd.DataFrame:
    trades = log_data.get("tradeHistory", [])
    if not trades:
        return pd.DataFrame(
            columns=["timestamp", "symbol", "buyer", "seller", "currency", "price", "quantity"]
        )

    df = pd.DataFrame(trades)

    for col in ["timestamp", "price", "quantity"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["buyer"] = df.get("buyer", "").fillna("")
    df["seller"] = df.get("seller", "").fillna("")
    df["is_submission"] = (df["buyer"] == "SUBMISSION") | (df["seller"] == "SUBMISSION")

    if "timestamp" in df.columns:
        df = df.sort_values("timestamp").reset_index(drop=True)

    return df


def build_position_events(trades_df: pd.DataFrame, products: list[str]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}

    for product in products:
        t = trades_df[(trades_df["symbol"] == product) & (trades_df["is_submission"])].copy()
        position = 0
        rows = [{"timestamp": 0, "position": 0}]

        for _, row in t.iterrows():
            qty = int(row["quantity"]) if pd.notna(row["quantity"]) else 0
            if row["buyer"] == "SUBMISSION":
                position += qty
            elif row["seller"] == "SUBMISSION":
                position -= qty
            rows.append({"timestamp": int(row["timestamp"]), "position": position})

        out[product] = pd.DataFrame(rows)

    return out


def summarize(activities_df: pd.DataFrame, trades_df: pd.DataFrame) -> None:
    if activities_df.empty:
        print("No activitiesLog data found.")
        return

    last_ts = int(activities_df["timestamp"].max())
    final_rows = activities_df[activities_df["timestamp"] == last_ts]

    print(f"Final timestamp: {last_ts}")
    print("Final PnL by product:")
    total = 0.0
    for _, row in final_rows.iterrows():
        product = row["product"]
        pnl = float(row["profit_and_loss"])
        total += pnl
        print(f"  {product}: {pnl:,.2f}")
    print(f"Total PnL: {total:,.2f}")

    if not trades_df.empty:
        print(f"Total trades in log: {len(trades_df)}")
        print(f"Submission trades: {int(trades_df['is_submission'].sum())}")


def plot_log(
    activities_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    title: str,
    save_path: Path | None,
    show_plot: bool,
) -> None:
    if activities_df.empty:
        raise ValueError("activitiesLog is empty; cannot plot.")

    products = sorted(activities_df["product"].dropna().unique().tolist())
    positions = build_position_events(trades_df, products)

    color_map = {
        products[i]: plt.cm.tab10(i / max(1, len(products)))
        for i in range(len(products))
    }

    sub = trades_df[trades_df["is_submission"]] if not trades_df.empty else pd.DataFrame()
    buys = sub[sub["buyer"] == "SUBMISSION"] if not sub.empty else pd.DataFrame()
    sells = sub[sub["seller"] == "SUBMISSION"] if not sub.empty else pd.DataFrame()
    market = trades_df[~trades_df["is_submission"]] if not trades_df.empty else pd.DataFrame()

    def nonzero_series(frame: pd.DataFrame, column: str) -> pd.DataFrame:
        return frame[frame[column].notna() & (frame[column] != 0)]

    for product in products:
        color = color_map[product]
        product_df = activities_df[activities_df["product"] == product].copy()
        if product_df.empty:
            continue

        fig, axes = plt.subplots(3, 2, figsize=(16, 10), sharex=True)
        axes = axes.ravel()
        fig.suptitle(f"{title} - {product}", fontsize=13, fontweight="bold")

        # 1) Product PnL
        axes[0].plot(product_df["timestamp"], product_df["profit_and_loss"], color=color, linewidth=1.6)
        axes[0].set_ylabel("PnL")
        axes[0].set_title("Cumulative PnL")
        axes[0].grid(alpha=0.3)

        # 2) Mid and top-of-book prices
        mid_df = nonzero_series(product_df, "mid_price")
        bid_df = nonzero_series(product_df, "bid_price_1")
        ask_df = nonzero_series(product_df, "ask_price_1")
        if not mid_df.empty:
            axes[1].plot(mid_df["timestamp"], mid_df["mid_price"], color="black", linewidth=1.3, label="mid")
        if not bid_df.empty:
            axes[1].plot(bid_df["timestamp"], bid_df["bid_price_1"], color=color, linewidth=1.1, linestyle="-", label="bid_1")
        if not ask_df.empty:
            axes[1].plot(ask_df["timestamp"], ask_df["ask_price_1"], color=color, linewidth=1.1, linestyle="--", label="ask_1")
        axes[1].set_ylabel("Price")
        axes[1].set_title("Mid / Best Bid / Best Ask")
        axes[1].grid(alpha=0.3)
        if axes[1].get_legend_handles_labels()[0]:
            axes[1].legend(fontsize=8)

        # 3) Submission trades
        b = buys[buys["symbol"] == product] if not buys.empty else pd.DataFrame()
        s = sells[sells["symbol"] == product] if not sells.empty else pd.DataFrame()
        if not b.empty:
            axes[2].scatter(b["timestamp"], b["price"], marker="^", s=34, color=color, label="submission buy")
        if not s.empty:
            axes[2].scatter(s["timestamp"], s["price"], marker="v", s=34, color=color, alpha=0.8, label="submission sell")
        axes[2].set_ylabel("Price")
        axes[2].set_title("Submission Trades")
        axes[2].grid(alpha=0.3)
        handles, labels = axes[2].get_legend_handles_labels()
        if handles:
            axes[2].legend(fontsize=8)

        # 4) Market trades (non-submission)
        m = market[market["symbol"] == product] if not market.empty else pd.DataFrame()
        if not m.empty:
            axes[3].scatter(m["timestamp"], m["price"], marker="x", s=18, color=color, alpha=0.5)
        axes[3].set_ylabel("Price")
        axes[3].set_title("Market Trades")
        axes[3].grid(alpha=0.3)

        # 5) Reconstructed submission position
        p = positions[product]
        axes[4].step(p["timestamp"], p["position"], where="post", color=color, linewidth=1.4)
        axes[4].axhline(0, color="black", linewidth=0.8, linestyle=":")
        axes[4].set_ylabel("Position")
        axes[4].set_title("Reconstructed Position")
        axes[4].grid(alpha=0.3)

        # 6) Top-of-book volume
        axes[5].plot(product_df["timestamp"], product_df["bid_volume_1"], color=color, linewidth=1.0, linestyle="-", label="bid_vol_1")
        axes[5].plot(product_df["timestamp"], product_df["ask_volume_1"], color=color, linewidth=1.0, linestyle="--", label="ask_vol_1")
        axes[5].set_ylabel("Volume")
        axes[5].set_title("Best Level Volumes")
        axes[5].grid(alpha=0.3)
        axes[5].legend(fontsize=8)

        for ax in axes:
            ax.set_xlabel("Timestamp")

        plt.tight_layout(rect=[0, 0, 1, 0.96])

        if save_path is not None:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            out_file = save_path.with_name(f"{save_path.stem}_{product}.png")
            fig.savefig(out_file, dpi=150)
            print(f"Saved figure to: {out_file}")

        if not show_plot:
            plt.close(fig)

    if show_plot:
        plt.show()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot Strategy Evaluation active .log JSON files")
    parser.add_argument("log_path", nargs="?", default=str(DEFAULT_LOG), help="Path to .log file")
    parser.add_argument("--save", default="", help="Optional output image path (png)")
    parser.add_argument("--no-show", action="store_true", help="Do not open an interactive plot window")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    log_path = Path(args.log_path).resolve()
    if not log_path.exists():
        raise FileNotFoundError(f"Log file not found: {log_path}")

    log_data = load_log(log_path)
    activities_df = parse_activities(log_data)
    trades_df = parse_trades(log_data)

    summarize(activities_df, trades_df)

    save_path = Path(args.save).resolve() if args.save else None
    plot_log(
        activities_df,
        trades_df,
        title=f"Trading Log: {log_path.name}",
        save_path=save_path,
        show_plot=not args.no_show,
    )


if __name__ == "__main__":
    main()