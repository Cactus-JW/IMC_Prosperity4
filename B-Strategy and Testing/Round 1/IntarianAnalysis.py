"""
IntarianAnalysis.py — Statistical analysis of the INTARIAN_PEPPER_ROOT market.

Analyses bid-ask spread, normalised price, orderbook depth and trade statistics
across days -2, -1 and 0 from Historical Data/Round 1/.

The asset has a known linear fair-value drift:
    fair_value(t) = DRIFT_RATE * timestamp + day_start_price
                  = 0.001 * timestamp + day_start_price

Prices are normalised by subtracting this drift before statistical analysis so
that each day's price appears stationary around its day_start_price.

NOTE — Improvements not yet implemented (potential future additions):
  1. Relative spread in basis points = (spread / mid_price) * 10_000.
     Normalises the absolute spread for the rising price level across days,
     enabling a like-for-like comparison of market tightness.
  2. Orderbook imbalance = bid_vol_1 / (bid_vol_1 + ask_vol_1).
     Values above 0.5 indicate buying pressure and below 0.5 selling pressure;
     a useful predictor of short-term price direction.
  3. Autocorrelation of spread and normalised mid-price returns (lags 1–20)
     via pd.Series.autocorr(). Reveals whether the spread is persistent
     (mean-averse) or quickly mean-reverting after shocks.
  4. Price impact from trades — compare the mid-price at a trade's timestamp
     with the mid-price 5–10 ticks later to estimate average impact per unit
     of volume traded.
  5. Bootstrap confidence intervals — with only N=3 days, normal approximations
     are unreliable. Resampling (with replacement) across days gives more honest
     uncertainty estimates for all reported statistics.
"""

import os

import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import gaussian_kde, kurtosis, skew

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATA_DIR = os.path.abspath(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "Historical Data", "Round 1",
    )
)

PRODUCT     = "INTARIAN_PEPPER_ROOT"
DAYS        = [-2, -1, 0]
DRIFT_RATE  = 1_000 / 1_000_000   # 0.001 price units per timestamp unit
ROLL_WINDOW = 50                   # ticks for rolling statistics

DAY_COLORS = {-2: "#3655B9", -1: "#0FD02F", 0: "#900FD0"}
DAY_LABELS = {-2: "Day -2", -1: "Day -1", 0: "Day 0"}

PRICE_FILES = {d: f"prices_round_1_day_{d}.csv" for d in DAYS}
TRADE_FILES = {d: f"trades_round_1_day_{d}.csv" for d in DAYS}

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_prices_day(day: int) -> pd.DataFrame:
    """Load and enrich the prices CSV for a single day."""
    path = os.path.join(DATA_DIR, PRICE_FILES[day])
    df = pd.read_csv(path, sep=";")
    df = df[df["product"] == PRODUCT].copy()

    # Defensively coerce all price / volume columns to numeric.
    price_vol_cols = [
        c for c in df.columns
        if any(c.startswith(p) for p in ("bid_price", "bid_volume", "ask_price", "ask_volume", "mid_price"))
    ]
    for col in price_vol_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["day"] = day

    # Spread is drift-free (drift cancels in the difference) — use raw prices.
    df["spread"] = df["ask_price_1"] - df["bid_price_1"]

    # Flag one-sided ticks (missing best bid OR best ask).
    # Also flag fully-empty ticks (both sides NaN) — these have mid_price == 0
    # which the exchange sets as a sentinel rather than a real price.
    # Do NOT interpolate — we want the raw NaN state for accurate counting.
    df["one_sided"]    = df["bid_price_1"].isna() | df["ask_price_1"].isna()
    df["fully_empty"]  = df["bid_price_1"].isna() & df["ask_price_1"].isna()

    # Detrended mid-price: removes the known linear drift so each day is
    # approximately stationary around its day_start_price.
    # Replace mid_price == 0 (fully-empty sentinel) with NaN so those ticks
    # do not corrupt the normalised price statistics.
    df["norm_mid"] = df["mid_price"].replace(0, np.nan) - DRIFT_RATE * df["timestamp"]

    return df.reset_index(drop=True)


def load_trades_day(day: int) -> pd.DataFrame:
    """Load and enrich the trades CSV for a single day."""
    path = os.path.join(DATA_DIR, TRADE_FILES[day])
    df = pd.read_csv(path, sep=";")
    df = df[df["symbol"] == PRODUCT].copy()

    df["price"]    = pd.to_numeric(df["price"],    errors="coerce")
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    df["day"]      = day

    # Normalised trade price: removes per-trade drift at its own timestamp.
    df["norm_price"] = df["price"] - DRIFT_RATE * df["timestamp"]

    return df.reset_index(drop=True)


def load_all_prices() -> dict[int, pd.DataFrame]:
    return {d: load_prices_day(d) for d in DAYS}


def load_all_trades() -> dict[int, pd.DataFrame]:
    return {d: load_trades_day(d) for d in DAYS}


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def compute_spread_stats(df: pd.DataFrame) -> dict:
    """Bid-ask spread statistics on two-sided ticks only."""
    total        = len(df)
    one_side     = df["one_sided"].sum()
    fully_empty  = df["fully_empty"].sum()
    two_sided_vals = df.loc[~df["one_sided"], "spread"].dropna()

    if two_sided_vals.empty:
        return {"n_ticks": total, "n_two_sided": 0, "pct_one_sided": 100.0,
                "pct_fully_empty": 100.0 * fully_empty / total}

    return {
        "n_ticks":          total,
        "n_two_sided":      len(two_sided_vals),
        "pct_one_sided":    100.0 * one_side / total,
        "pct_fully_empty":  100.0 * fully_empty / total,
        "mean":            two_sided_vals.mean(),
        "std":             two_sided_vals.std(),
        "median":          two_sided_vals.median(),
        "min":             two_sided_vals.min(),
        "max":             two_sided_vals.max(),
        "p25":             two_sided_vals.quantile(0.25),
        "p75":             two_sided_vals.quantile(0.75),
        "skew":            float(skew(two_sided_vals)),
        "excess_kurtosis": float(kurtosis(two_sided_vals)),  # Fisher (excess) by default
    }


def compute_mid_stats(df: pd.DataFrame) -> dict:
    """Statistics on the drift-normalised mid-price."""
    norm_mid = df["norm_mid"].dropna()
    return {
        "day_start_price": float(norm_mid.mean()),  # best estimate via mean detrending
        "norm_mean":       float(norm_mid.mean()),
        "norm_std":        float(norm_mid.std()),
        "norm_min":        float(norm_mid.min()),
        "norm_max":        float(norm_mid.max()),
    }


def compute_depth_stats(df: pd.DataFrame) -> dict:
    """Mean bid/ask volume at each orderbook level (NaN-skipping mean = average
    when the level IS present, not averaged over absent ticks)."""
    result = {}
    for lvl in [1, 2, 3]:
        result[f"mean_bid_vol_{lvl}"] = float(df[f"bid_volume_{lvl}"].mean())
        result[f"mean_ask_vol_{lvl}"] = float(df[f"ask_volume_{lvl}"].mean())
    return result


def compute_trade_stats(df: pd.DataFrame) -> dict:
    """Trade statistics for a single day (or pooled) trades DataFrame."""
    if df.empty:
        return {k: float("nan") for k in
                ["trade_count", "total_volume", "mean_size", "max_size", "vwap", "norm_vwap"]}
    qty = df["quantity"]
    return {
        "trade_count":  len(df),
        "total_volume": float(qty.sum()),
        "mean_size":    float(qty.mean()),
        "max_size":     float(qty.max()),
        # VWAP uses raw prices; norm_vwap uses drift-corrected prices per trade.
        "vwap":         float((df["price"]      * qty).sum() / qty.sum()),
        "norm_vwap":    float((df["norm_price"] * qty).sum() / qty.sum()),
    }


def compute_all_stats(
    prices_by_day: dict[int, pd.DataFrame],
    trades_by_day: dict[int, pd.DataFrame],
) -> dict:
    """Compute per-day and pooled-overall statistics."""
    stats: dict = {"by_day": {}, "overall": {}}

    for day in DAYS:
        stats["by_day"][day] = {
            "spread": compute_spread_stats(prices_by_day[day]),
            "mid":    compute_mid_stats(prices_by_day[day]),
            "depth":  compute_depth_stats(prices_by_day[day]),
            "trades": compute_trade_stats(trades_by_day[day]),
        }

    all_prices = pd.concat(prices_by_day.values(), ignore_index=True)
    all_trades = pd.concat(trades_by_day.values(), ignore_index=True)
    stats["overall"] = {
        "spread": compute_spread_stats(all_prices),
        "mid":    compute_mid_stats(all_prices),
        "depth":  compute_depth_stats(all_prices),
        "trades": compute_trade_stats(all_trades),
    }

    return stats


# ---------------------------------------------------------------------------
# Summary printing
# ---------------------------------------------------------------------------

def print_summary(stats: dict) -> None:
    """Print three formatted summary tables to stdout."""
    row_keys   = DAYS + ["Overall"]
    row_labels = [DAY_LABELS[d] for d in DAYS] + ["Overall"]

    def _get(day_or_overall, *keys):
        src = stats["by_day"][day_or_overall] if day_or_overall in DAYS else stats["overall"]
        val = src
        for k in keys:
            val = val[k]
        return val

    # ---- Table 1: Spread & normalised mid ----
    rows1 = []
    for key, label in zip(row_keys, row_labels):
        rows1.append({
            "":                  label,
            "Day Start Price":   _get(key, "mid",    "day_start_price"),
            "Norm Mid Mean":     _get(key, "mid",    "norm_mean"),
            "Norm Mid Std":      _get(key, "mid",    "norm_std"),
            "Spread Mean":       _get(key, "spread", "mean"),
            "Spread Std":        _get(key, "spread", "std"),
            "Spread Median":     _get(key, "spread", "median"),
            "Spread Min":        _get(key, "spread", "min"),
            "Spread Max":        _get(key, "spread", "max"),
            "Spread P25":        _get(key, "spread", "p25"),
            "Spread P75":        _get(key, "spread", "p75"),
            "Spread Skew":       _get(key, "spread", "skew"),
            "Spread Ex.Kurt":    _get(key, "spread", "excess_kurtosis"),
            "% One-Sided":       _get(key, "spread", "pct_one_sided"),
            "% Fully Empty":     _get(key, "spread", "pct_fully_empty"),
        })

    df1 = pd.DataFrame(rows1).set_index("").T
    print("\n" + "=" * 70)
    print("SPREAD & NORMALISED MID-PRICE STATISTICS")
    print("=" * 70)
    print(df1.round(4).to_string())

    # ---- Table 2: Trade stats ----
    rows2 = []
    for key, label in zip(row_keys, row_labels):
        rows2.append({
            "":              label,
            "Trade Count":   _get(key, "trades", "trade_count"),
            "Total Volume":  _get(key, "trades", "total_volume"),
            "Mean Size":     _get(key, "trades", "mean_size"),
            "Max Size":      _get(key, "trades", "max_size"),
            "VWAP":          _get(key, "trades", "vwap"),
            "Norm VWAP":     _get(key, "trades", "norm_vwap"),
        })

    df2 = pd.DataFrame(rows2).set_index("").T
    print("\n" + "=" * 70)
    print("TRADE STATISTICS")
    print("=" * 70)
    print(df2.round(4).to_string())

    # ---- Table 3: Orderbook depth ----
    rows3 = []
    for key, label in zip(row_keys, row_labels):
        depth = _get(key, "depth")
        rows3.append({
            "":        label,
            "Bid L1":  depth["mean_bid_vol_1"],
            "Ask L1":  depth["mean_ask_vol_1"],
            "Bid L2":  depth["mean_bid_vol_2"],
            "Ask L2":  depth["mean_ask_vol_2"],
            "Bid L3":  depth["mean_bid_vol_3"],
            "Ask L3":  depth["mean_ask_vol_3"],
        })

    df3 = pd.DataFrame(rows3).set_index("").T
    print("\n" + "=" * 70)
    print("MEAN ORDERBOOK DEPTH (volume when level is present)")
    print("=" * 70)
    print(df3.round(2).to_string())
    print()


# ---------------------------------------------------------------------------
# Figure 1 — Price overview (raw, normalised, spread) — 3 panels
# ---------------------------------------------------------------------------

def plot_price_overview(prices_by_day: dict[int, pd.DataFrame]) -> plt.Figure:
    fig, axes = plt.subplots(3, 1, figsize=(14, 10))
    fig.suptitle(
        f"{PRODUCT}  —  Price Overview (3 Days)",
        fontsize=13, fontweight="bold",
    )
    plt.subplots_adjust(hspace=0.45, left=0.08, right=0.97, top=0.93, bottom=0.07)

    ax0, ax1, ax2 = axes

    for day, df in prices_by_day.items():
        color = DAY_COLORS[day]
        label = DAY_LABELS[day]

        # Panel 0: raw mid-price
        ax0.plot(df["timestamp"], df["mid_price"],
                 color=color, linewidth=0.7, label=label)

        # Panel 1: normalised mid-price (drift removed)
        ax1.plot(df["timestamp"], df["norm_mid"],
                 color=color, linewidth=0.7, label=label)
        # Dashed horizontal line at the day's mean norm_mid (= estimated day_start_price)
        mean_norm = df["norm_mid"].mean()
        ax1.axhline(mean_norm, color=color, linestyle="--", linewidth=1.0, alpha=0.55)

        # Panel 2: bid-ask spread (two-sided ticks only)
        two_sided = df[~df["one_sided"]]
        ax2.scatter(two_sided["timestamp"], two_sided["spread"],
                    s=1.5, color=color, alpha=0.4, label=label)

    ax0.set_title("Raw Mid-Price vs Timestamp", fontsize=10)
    ax0.set_xlabel("Timestamp", fontsize=8)
    ax0.set_ylabel("Mid Price", fontsize=8)
    ax0.legend(fontsize=8)
    ax0.grid(True, alpha=0.3)

    ax1.set_title(
        "Normalised Mid-Price (drift removed)  —  should be approximately flat",
        fontsize=10,
    )
    ax1.set_xlabel("Timestamp", fontsize=8)
    ax1.set_ylabel("Norm Mid Price", fontsize=8)
    ax1.legend(fontsize=8)
    ax1.grid(True, alpha=0.3)

    ax2.set_title("Bid-Ask Spread vs Timestamp  (two-sided ticks only)", fontsize=10)
    ax2.set_xlabel("Timestamp", fontsize=8)
    ax2.set_ylabel("Spread (price units)", fontsize=8)
    ax2.legend(fontsize=8, markerscale=5)
    ax2.grid(True, alpha=0.3)

    return fig


# ---------------------------------------------------------------------------
# Figure 2 — Spread distribution (histogram + KDE) — 1 × 3 subplots
# ---------------------------------------------------------------------------

def plot_spread_distribution(prices_by_day: dict[int, pd.DataFrame]) -> plt.Figure:
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    fig.suptitle(
        f"{PRODUCT}  —  Spread Distribution by Day",
        fontsize=13, fontweight="bold",
    )
    plt.subplots_adjust(wspace=0.30, left=0.07, right=0.97, top=0.88, bottom=0.12)

    for ax, day in zip(axes, DAYS):
        df    = prices_by_day[day]
        vals  = df.loc[~df["one_sided"], "spread"].dropna()
        color = DAY_COLORS[day]

        if vals.empty:
            ax.set_title(f"{DAY_LABELS[day]}  (no data)")
            continue

        # Integer-width bins — spread values are whole numbers.
        lo   = int(vals.min()) - 1
        hi   = int(vals.max()) + 2
        bins = np.arange(lo, hi, 1.0)

        ax.hist(vals, bins=bins, density=True,
                color=color, alpha=0.65, edgecolor="white", linewidth=0.5)

        # KDE overlay
        x_kde = np.linspace(lo, hi, 400)
        kde   = gaussian_kde(vals)
        ax.plot(x_kde, kde(x_kde), color="black", linewidth=1.5, label="KDE")

        mean_val   = vals.mean()
        median_val = vals.median()
        ax.axvline(mean_val,   color="red",    linestyle="--", linewidth=1.2,
                   label=f"Mean: {mean_val:.2f}")
        ax.axvline(median_val, color="orange", linestyle=":",  linewidth=1.2,
                   label=f"Median: {median_val:.1f}")

        ax.set_title(DAY_LABELS[day], fontsize=10)
        ax.set_xlabel("Spread (price units)", fontsize=8)
        ax.set_ylabel("Density", fontsize=8)
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3)

    return fig


# ---------------------------------------------------------------------------
# Figure 3 — Orderbook depth bars — 1 × 3 subplots (one per level)
# ---------------------------------------------------------------------------

def plot_depth_bars(prices_by_day: dict[int, pd.DataFrame]) -> plt.Figure:
    fig, axes = plt.subplots(1, 3, figsize=(14, 5))
    fig.suptitle(
        f"{PRODUCT}  —  Mean Orderbook Depth by Level",
        fontsize=13, fontweight="bold",
    )
    plt.subplots_adjust(wspace=0.35, left=0.07, right=0.97, top=0.88, bottom=0.15)

    x       = np.arange(len(DAYS))
    bar_w   = 0.35
    x_ticks = [DAY_LABELS[d] for d in DAYS]

    for ax, lvl in zip(axes, [1, 2, 3]):
        bid_means = [prices_by_day[d][f"bid_volume_{lvl}"].mean() for d in DAYS]
        ask_means = [prices_by_day[d][f"ask_volume_{lvl}"].mean() for d in DAYS]
        colors    = [DAY_COLORS[d] for d in DAYS]

        ax.bar(x - bar_w / 2, bid_means, width=bar_w, color=colors, alpha=0.85)
        ax.bar(x + bar_w / 2, ask_means, width=bar_w, color=colors, alpha=0.45,
               hatch="//", edgecolor="white")

        ax.set_xticks(x)
        ax.set_xticklabels(x_ticks, fontsize=8)
        ax.set_title(f"Level {lvl}", fontsize=10)
        ax.set_ylabel("Mean Volume (when level present)", fontsize=8)
        ax.grid(axis="y", alpha=0.3)

        # Legend: solid fill = Bid, hatched = Ask
        bid_patch = mpatches.Patch(facecolor="grey", alpha=0.85,  label="Bid")
        ask_patch = mpatches.Patch(facecolor="grey", alpha=0.45, hatch="//",
                                   edgecolor="white", label="Ask")
        ax.legend(handles=[bid_patch, ask_patch], fontsize=8)

    return fig


# ---------------------------------------------------------------------------
# Figure 4 — Rolling analysis (spread mean + normalised vol) — 2 × 3 grid
# ---------------------------------------------------------------------------

def plot_rolling_analysis(prices_by_day: dict[int, pd.DataFrame]) -> plt.Figure:
    fig, axes = plt.subplots(2, 3, figsize=(16, 8))
    fig.suptitle(
        f"{PRODUCT}  —  Rolling Analysis by Day  (window = {ROLL_WINDOW} ticks)",
        fontsize=13, fontweight="bold",
    )
    plt.subplots_adjust(hspace=0.45, wspace=0.30,
                        left=0.07, right=0.97, top=0.92, bottom=0.08)

    for col_idx, day in enumerate(DAYS):
        df    = prices_by_day[day].copy().reset_index(drop=True)
        color = DAY_COLORS[day]
        label = DAY_LABELS[day]

        # ---- Top row: rolling mean spread (two-sided ticks only) ----
        # NOTE: the rolling window is over the count of two-sided ticks, not
        # wall-clock time, because one-sided ticks are excluded before rolling.
        two_sided = df[~df["one_sided"]].copy().reset_index(drop=True)
        two_sided["roll_mean_spread"] = (
            two_sided["spread"]
            .rolling(window=ROLL_WINDOW, min_periods=10)
            .mean()
        )

        ax_top = axes[0, col_idx]
        ax_top.plot(two_sided["timestamp"], two_sided["roll_mean_spread"],
                    color=color, linewidth=1.0)
        ax_top.set_title(f"{label}  —  Rolling Mean Spread", fontsize=9)
        ax_top.set_xlabel("Timestamp", fontsize=8)
        ax_top.set_ylabel("Spread", fontsize=8)
        ax_top.grid(True, alpha=0.3)

        # ---- Bottom row: rolling std of normalised mid (realised vol proxy) ----
        df["roll_std_norm"] = (
            df["norm_mid"]
            .rolling(window=ROLL_WINDOW, min_periods=10)
            .std()
        )

        ax_bot = axes[1, col_idx]
        ax_bot.plot(df["timestamp"], df["roll_std_norm"],
                    color=color, linewidth=1.0)
        ax_bot.set_title(f"{label}  —  Rolling Std Norm Mid (realised vol)", fontsize=9)
        ax_bot.set_xlabel("Timestamp", fontsize=8)
        ax_bot.set_ylabel("Std (price units)", fontsize=8)
        ax_bot.grid(True, alpha=0.3)

    return fig


# ---------------------------------------------------------------------------
# Helper — per-day modelled fair-value anchor
# ---------------------------------------------------------------------------

def _compute_day_start_price(df: pd.DataFrame) -> float:
    """First valid midprice (mid_price > 0), rounded to nearest 1 000."""
    first_mid = df.loc[df["mid_price"] > 0, "mid_price"].iloc[0]
    return round(first_mid / 1_000) * 1_000


# ---------------------------------------------------------------------------
# Figure 5 — Bid & Ask KDE vs modelled midprice  (two-sided ticks only)
# ---------------------------------------------------------------------------

def plot_bid_ask_kde_vs_model(prices_by_day: dict[int, pd.DataFrame]) -> plt.Figure:
    """
    KDE of (best_bid − modelled_mid) and (best_ask − modelled_mid) per day.
    Only strictly two-sided ticks are included; any tick missing either side
    (golden-book / one-sided) is excluded and reserved for Figure 6.

        modelled_midprice = timestamp / 1 000 + day_start_price
        day_start_price   = first valid midprice rounded to nearest 1 000

    CheckButtons on the right let you show/hide each side independently.
    """
    from matplotlib.widgets import CheckButtons

    fig, axes = plt.subplots(1, 3, figsize=(17, 5))
    fig.suptitle(
        f"{PRODUCT}  —  Best Bid & Ask Offset from Modelled Midprice"
        "  (two-sided ticks only)",
        fontsize=13, fontweight="bold",
    )
    plt.subplots_adjust(wspace=0.35, left=0.06, right=0.84, top=0.85, bottom=0.12)

    bid_artists: list = []
    ask_artists: list = []

    for ax, day in zip(axes, DAYS):
        df        = prices_by_day[day]
        day_start = _compute_day_start_price(df)

        # Strictly two-sided: bid_price_1 AND ask_price_1 both present.
        # one_sided == True whenever either side is NaN, so ~one_sided is safe.
        two_sided = df[~df["one_sided"]].copy()
        mod_mid   = DRIFT_RATE * two_sided["timestamp"] + day_start

        bid_off = (two_sided["bid_price_1"] - mod_mid).dropna()
        ask_off = (two_sided["ask_price_1"] - mod_mid).dropna()

        if bid_off.empty or ask_off.empty:
            ax.set_title(f"{DAY_LABELS[day]}  (no two-sided data)")
            continue

        x_lo  = min(bid_off.min(), ask_off.min()) - 1
        x_hi  = max(bid_off.max(), ask_off.max()) + 1
        x_kde = np.linspace(x_lo, x_hi, 600)

        kde_bid = gaussian_kde(bid_off)
        kde_ask = gaussian_kde(ask_off)

        # Bar charts underlying the KDEs (integer-width bins, density=True)
        bins_b = np.arange(int(np.floor(bid_off.min())) - 1,
                           int(np.ceil(bid_off.max())) + 2, 1.0)
        bins_a = np.arange(int(np.floor(ask_off.min())) - 1,
                           int(np.ceil(ask_off.max())) + 2, 1.0)
        _, _, b_bars = ax.hist(bid_off, bins=bins_b, density=True,
                               color="#2255CC", alpha=0.30,
                               edgecolor="white", linewidth=0.4)
        _, _, a_bars = ax.hist(ask_off, bins=bins_a, density=True,
                               color="#CC3322", alpha=0.30,
                               edgecolor="white", linewidth=0.4)
        bid_artists.extend(b_bars)
        ask_artists.extend(a_bars)

        # Bid artists (line + fill + mean vline)
        b_line, = ax.plot(x_kde, kde_bid(x_kde), color="#2255CC", linewidth=1.8,
                          label=f"Best Bid  μ={bid_off.mean():.2f}")
        b_fill  = ax.fill_between(x_kde, kde_bid(x_kde), alpha=0.20, color="#2255CC")
        b_mean  = ax.axvline(bid_off.mean(), color="#2255CC", linestyle=":", linewidth=1.2)
        bid_artists.extend([b_line, b_fill, b_mean])

        # Ask artists (line + fill + mean vline)
        a_line, = ax.plot(x_kde, kde_ask(x_kde), color="#CC3322", linewidth=1.8,
                          label=f"Best Ask  μ={ask_off.mean():.2f}")
        a_fill  = ax.fill_between(x_kde, kde_ask(x_kde), alpha=0.20, color="#CC3322")
        a_mean  = ax.axvline(ask_off.mean(), color="#CC3322", linestyle=":", linewidth=1.2)
        ask_artists.extend([a_line, a_fill, a_mean])

        # Modelled mid reference line — always visible
        ax.axvline(0, color="black", linestyle="--", linewidth=1.0, alpha=0.5,
                   label="Modelled Mid (0)")

        ax.set_title(
            f"{DAY_LABELS[day]}  (day_start={day_start:.0f})\n"
            f"n={len(bid_off)} two-sided ticks",
            fontsize=9,
        )
        ax.set_xlabel("Price offset from modelled midprice", fontsize=8)
        ax.set_ylabel("Density", fontsize=8)
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3)

    # ---- CheckButtons widget ----
    ax_chk = fig.add_axes([0.86, 0.38, 0.12, 0.22])
    check  = CheckButtons(ax_chk, ["Best Bid", "Best Ask"], [True, True])
    check.ax.set_title("Show", fontsize=8, pad=4)

    state = {"bid": True, "ask": True}

    def _on_toggle_5(label: str) -> None:
        if label == "Best Bid":
            state["bid"] = not state["bid"]
            for a in bid_artists:
                a.set_visible(state["bid"])
        else:
            state["ask"] = not state["ask"]
            for a in ask_artists:
                a.set_visible(state["ask"])
        fig.canvas.draw_idle()

    check.on_clicked(_on_toggle_5)

    return fig


# ---------------------------------------------------------------------------
# Figure 6 — Golden book KDE vs modelled midprice  (one-sided ticks only)
# ---------------------------------------------------------------------------

def plot_golden_book_kde_vs_model(prices_by_day: dict[int, pd.DataFrame]) -> plt.Figure:
    """
    KDE of (available-side price − modelled_mid) for golden-book (one-sided) ticks.
      Bid-only ticks  →  KDE of (bid_price_1 − modelled_mid)
      Ask-only ticks  →  KDE of (ask_price_1 − modelled_mid)
    Fully-empty ticks (both sides NaN) are excluded entirely.

    CheckButtons mirror Figure 5.
    """
    from matplotlib.widgets import CheckButtons

    fig, axes = plt.subplots(1, 3, figsize=(17, 5))
    fig.suptitle(
        f"{PRODUCT}  —  Golden Book Price Offset from Modelled Midprice"
        "  (one-sided ticks only)",
        fontsize=13, fontweight="bold",
    )
    plt.subplots_adjust(wspace=0.35, left=0.06, right=0.84, top=0.85, bottom=0.12)

    bid_artists: list = []
    ask_artists: list = []

    for ax, day in zip(axes, DAYS):
        df        = prices_by_day[day]
        day_start = _compute_day_start_price(df)
        mod_mid   = DRIFT_RATE * df["timestamp"] + day_start

        # Bid-only: bid present, ask absent.  Fully-empty ticks have bid absent
        # too, so this mask is already exclusive of fully-empty by construction.
        bid_only_mask = df["bid_price_1"].notna() & df["ask_price_1"].isna()
        ask_only_mask = df["ask_price_1"].notna() & df["bid_price_1"].isna()

        bid_off = (df.loc[bid_only_mask, "bid_price_1"]
                   - mod_mid.loc[bid_only_mask]).dropna()
        ask_off = (df.loc[ask_only_mask, "ask_price_1"]
                   - mod_mid.loc[ask_only_mask]).dropna()

        n_bid   = len(bid_off)
        n_ask   = len(ask_off)
        n_empty = int(df["fully_empty"].sum())

        ax.set_title(
            f"{DAY_LABELS[day]}  (day_start={day_start:.0f})\n"
            f"bid-only={n_bid}  ask-only={n_ask}  fully-empty={n_empty}",
            fontsize=9,
        )
        ax.set_xlabel("Price offset from modelled midprice", fontsize=8)
        ax.set_ylabel("Density", fontsize=8)
        ax.grid(True, alpha=0.3)

        plotted = False

        if n_bid >= 2:
            bins_b = np.arange(int(np.floor(bid_off.min())) - 1,
                               int(np.ceil(bid_off.max())) + 2, 1.0)
            _, _, b_bars = ax.hist(bid_off, bins=bins_b, density=True,
                                   color="#2255CC", alpha=0.30,
                                   edgecolor="white", linewidth=0.4)
            bid_artists.extend(b_bars)
            x_kde = np.linspace(bid_off.min() - 1, bid_off.max() + 1, 400)
            kde   = gaussian_kde(bid_off)
            b_line, = ax.plot(x_kde, kde(x_kde), color="#2255CC", linewidth=1.8,
                              label=f"Bid-only  μ={bid_off.mean():.2f}")
            b_fill  = ax.fill_between(x_kde, kde(x_kde), alpha=0.20, color="#2255CC")
            b_mean  = ax.axvline(bid_off.mean(), color="#2255CC", linestyle=":", linewidth=1.2)
            bid_artists.extend([b_line, b_fill, b_mean])
            plotted = True

        if n_ask >= 2:
            bins_a = np.arange(int(np.floor(ask_off.min())) - 1,
                               int(np.ceil(ask_off.max())) + 2, 1.0)
            _, _, a_bars = ax.hist(ask_off, bins=bins_a, density=True,
                                   color="#CC3322", alpha=0.30,
                                   edgecolor="white", linewidth=0.4)
            ask_artists.extend(a_bars)
            x_kde = np.linspace(ask_off.min() - 1, ask_off.max() + 1, 400)
            kde   = gaussian_kde(ask_off)
            a_line, = ax.plot(x_kde, kde(x_kde), color="#CC3322", linewidth=1.8,
                              label=f"Ask-only  μ={ask_off.mean():.2f}")
            a_fill  = ax.fill_between(x_kde, kde(x_kde), alpha=0.20, color="#CC3322")
            a_mean  = ax.axvline(ask_off.mean(), color="#CC3322", linestyle=":", linewidth=1.2)
            ask_artists.extend([a_line, a_fill, a_mean])
            plotted = True

        if plotted:
            ax.axvline(0, color="black", linestyle="--", linewidth=1.0, alpha=0.5,
                       label="Modelled Mid (0)")
        else:
            ax.text(0.5, 0.5, "No one-sided data", ha="center", va="center",
                    transform=ax.transAxes, fontsize=10, color="grey")

        ax.legend(fontsize=7)

    # ---- CheckButtons widget ----
    ax_chk = fig.add_axes([0.86, 0.38, 0.12, 0.22])
    check  = CheckButtons(ax_chk, ["Bid-only", "Ask-only"], [True, True])
    check.ax.set_title("Show", fontsize=8, pad=4)

    state = {"bid": True, "ask": True}

    def _on_toggle_6(label: str) -> None:
        if label == "Bid-only":
            state["bid"] = not state["bid"]
            for a in bid_artists:
                a.set_visible(state["bid"])
        else:
            state["ask"] = not state["ask"]
            for a in ask_artists:
                a.set_visible(state["ask"])
        fig.canvas.draw_idle()

    check.on_clicked(_on_toggle_6)

    return fig


# ---------------------------------------------------------------------------
# Bid/Ask offset scaling analysis
# ---------------------------------------------------------------------------

def compute_bid_ask_offset_stats(
    prices_by_day: dict[int, pd.DataFrame],
) -> list[dict]:
    """
    Per-day mean/median bid & ask offset from modelled midprice, both in
    absolute price units and in basis points of day_start_price.

    Returns a list of dicts (one per day) for easy DataFrame construction.
    """
    rows = []
    for day in DAYS:
        df        = prices_by_day[day]
        day_start = _compute_day_start_price(df)
        two_sided = df[~df["one_sided"]].copy()
        mod_mid   = DRIFT_RATE * two_sided["timestamp"] + day_start

        bid_off = (two_sided["bid_price_1"] - mod_mid).dropna()
        ask_off = (two_sided["ask_price_1"] - mod_mid).dropna()

        if bid_off.empty or ask_off.empty:
            continue

        rows.append({
            "Day":                  DAY_LABELS[day],
            "Day Start Price":      day_start,
            "Bid Off Mean":         bid_off.mean(),
            "Ask Off Mean":         ask_off.mean(),
            "Bid Off Median":       bid_off.median(),
            "Ask Off Median":       ask_off.median(),
            # Relative to day_start_price — does the offset scale with price?
            "Bid Off Mean (bps)":   bid_off.mean()   / day_start * 10_000,
            "Ask Off Mean (bps)":   ask_off.mean()   / day_start * 10_000,
            "Bid Off Median (bps)": bid_off.median() / day_start * 10_000,
            "Ask Off Median (bps)": ask_off.median() / day_start * 10_000,
        })
    return rows


def print_offset_analysis(prices_by_day: dict[int, pd.DataFrame]) -> None:
    """Print absolute and relative bid/ask offset table, plus per-day shift."""
    rows = compute_bid_ask_offset_stats(prices_by_day)
    if not rows:
        print("No offset data available.")
        return

    df = pd.DataFrame(rows).set_index("Day")

    print("\n" + "=" * 80)
    print("BID/ASK OFFSET FROM MODELLED MID — ABSOLUTE AND RELATIVE")
    print("Hypothesis: offset scales with day_start_price (like spread ~= 0.012 % of price)")
    print("=" * 80)
    print(df.round(4).to_string())

    # Day-over-day shift in mean absolute offset
    if len(rows) >= 2:
        print("\n  Day-over-day shift in mean absolute offset:")
        for i in range(1, len(rows)):
            prev, curr = rows[i - 1], rows[i]
            bid_delta = curr["Bid Off Mean"] - prev["Bid Off Mean"]
            ask_delta = curr["Ask Off Mean"] - prev["Ask Off Mean"]
            print(
                f"    {prev['Day']} → {curr['Day']}:  "
                f"Bid mean Δ = {bid_delta:+.4f}  |  Ask mean Δ = {ask_delta:+.4f}"
            )
        print()

    # Coefficient of variation of the bps values — low CV means stable ratio
    bid_bps  = [r["Bid Off Mean (bps)"]  for r in rows]
    ask_bps  = [r["Ask Off Mean (bps)"]  for r in rows]
    bid_cv   = np.std(bid_bps) / abs(np.mean(bid_bps)) if np.mean(bid_bps) != 0 else float("nan")
    ask_cv   = np.std(ask_bps) / abs(np.mean(ask_bps)) if np.mean(ask_bps) != 0 else float("nan")
    print(
        f"  CV of bid-offset bps across days: {bid_cv:.3f}  "
        f"(CV of ask-offset bps: {ask_cv:.3f})"
    )
    print(
        "  → CV near 0 means the offset is a stable fraction of price; "
        "CV ≫ 0 means it drifts independently."
    )


# ---------------------------------------------------------------------------
# Figure 7 — Bid/Ask offset normalised by day_start_price (bps)
# ---------------------------------------------------------------------------

def plot_offset_scaling(prices_by_day: dict[int, pd.DataFrame]) -> plt.Figure:
    """
    KDE of (bid/ask offset) / day_start_price in basis points, one curve
    per day, overlaid on a single pair of panels (bid left, ask right).
    If the offset scales proportionally with price the KDE peaks align across days.
    """
    fig, (ax_bid, ax_ask) = plt.subplots(1, 2, figsize=(14, 5))
    fig.suptitle(
        f"{PRODUCT}  —  Bid & Ask Offset Normalised by Day Start Price (bps)\n"
        "Aligned peaks → offset scales proportionally with price level",
        fontsize=12, fontweight="bold",
    )
    plt.subplots_adjust(wspace=0.30, left=0.07, right=0.97, top=0.85, bottom=0.12)

    for day in DAYS:
        df        = prices_by_day[day]
        day_start = _compute_day_start_price(df)
        two_sided = df[~df["one_sided"]].copy()
        mod_mid   = DRIFT_RATE * two_sided["timestamp"] + day_start

        bid_off_bps = ((two_sided["bid_price_1"] - mod_mid) / day_start * 10_000).dropna()
        ask_off_bps = ((two_sided["ask_price_1"] - mod_mid) / day_start * 10_000).dropna()

        if bid_off_bps.empty or ask_off_bps.empty:
            continue

        color = DAY_COLORS[day]
        label = f"{DAY_LABELS[day]}  (start={day_start:.0f})  μ={bid_off_bps.mean():.3f} bps"

        for ax, vals, side in [
            (ax_bid, bid_off_bps, "Bid"),
            (ax_ask, ask_off_bps, "Ask"),
        ]:
            lbl = f"{DAY_LABELS[day]}  (start={day_start:.0f})  μ={vals.mean():.3f} bps"
            x_kde = np.linspace(vals.min() - 0.5, vals.max() + 0.5, 600)
            kde   = gaussian_kde(vals)
            ax.plot(x_kde, kde(x_kde), color=color, linewidth=1.8, label=lbl)
            ax.fill_between(x_kde, kde(x_kde), alpha=0.12, color=color)
            ax.axvline(vals.mean(), color=color, linestyle=":", linewidth=1.1)

    for ax, title in [(ax_bid, "Best Bid offset (bps)"), (ax_ask, "Best Ask offset (bps)")]:
        ax.axvline(0, color="black", linestyle="--", linewidth=1.0, alpha=0.5,
                   label="Modelled Mid (0 bps)")
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("Offset / day_start_price × 10 000 (bps)", fontsize=8)
        ax.set_ylabel("Density", fontsize=8)
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3)

    return fig


# ---------------------------------------------------------------------------
# Crossing-mid analysis  (the small secondary peaks in Figure 5)
# ---------------------------------------------------------------------------

def compute_crossing_stats(
    prices_by_day: dict[int, pd.DataFrame],
) -> list[dict]:
    """
    Isolate the ticks where the best bid crosses ABOVE the modelled mid
    (bid_off > 0) or the best ask crosses BELOW it (ask_off < 0).
    These produce the small secondary peaks in Figure 5's KDE.

    Returns per-day summary rows.
    """
    rows = []
    for day in DAYS:
        df        = prices_by_day[day]
        day_start = _compute_day_start_price(df)
        two_sided = df[~df["one_sided"]].copy()
        n_two     = len(two_sided)
        mod_mid   = DRIFT_RATE * two_sided["timestamp"] + day_start

        bid_off = (two_sided["bid_price_1"] - mod_mid).dropna()
        ask_off = (two_sided["ask_price_1"] - mod_mid).dropna()

        # Keep only the crossing side of zero
        bid_cross = bid_off[bid_off > 0]   # bid above modelled mid
        ask_cross = ask_off[ask_off < 0]   # ask below modelled mid

        if bid_cross.empty and ask_cross.empty:
            continue

        def _kde_mode(s: pd.Series) -> float:
            """KDE peak (mode) fitted only to crossing events."""
            if len(s) < 2:
                return float("nan")
            x = np.linspace(s.min() - 0.5, s.max() + 0.5, 3000)
            return float(x[np.argmax(gaussian_kde(s)(x))])

        def _stats(s: pd.Series) -> dict:
            if s.empty:
                return dict(n=0, freq_pct=0.0, mean=float("nan"), median=float("nan"),
                            mode=float("nan"), mean_bps=float("nan"),
                            median_bps=float("nan"), mode_bps=float("nan"))
            m = _kde_mode(s)
            return dict(
                n          = len(s),
                freq_pct   = 100.0 * len(s) / n_two,
                mean       = s.mean(),
                median     = s.median(),
                mode       = m,
                mean_bps   = s.mean()   / day_start * 10_000,
                median_bps = s.median() / day_start * 10_000,
                mode_bps   = m          / day_start * 10_000,
            )

        bs = _stats(bid_cross)
        as_ = _stats(ask_cross)

        rows.append({
            "Day":                   DAY_LABELS[day],
            "Day Start":             day_start,
            # Bid-crosses-above stats
            "Bid Cross n":           bs["n"],
            "Bid Cross freq%":       bs["freq_pct"],
            "Bid Cross Mean":        bs["mean"],
            "Bid Cross Median":      bs["median"],
            "Bid Cross Mode":        bs["mode"],
            "Bid Cross Mean (bps)":  bs["mean_bps"],
            "Bid Cross Med (bps)":   bs["median_bps"],
            "Bid Cross Mode (bps)":  bs["mode_bps"],
            # Ask-crosses-below stats
            "Ask Cross n":           as_["n"],
            "Ask Cross freq%":       as_["freq_pct"],
            "Ask Cross Mean":        as_["mean"],
            "Ask Cross Median":      as_["median"],
            "Ask Cross Mode":        as_["mode"],
            "Ask Cross Mean (bps)":  as_["mean_bps"],
            "Ask Cross Med (bps)":   as_["median_bps"],
            "Ask Cross Mode (bps)":  as_["mode_bps"],
        })
    return rows


def print_crossing_analysis(prices_by_day: dict[int, pd.DataFrame]) -> None:
    """Print the crossing-mid stats table and day-over-day scaling check."""
    rows = compute_crossing_stats(prices_by_day)
    if not rows:
        print("No crossing data found.")
        return

    df = pd.DataFrame(rows).set_index("Day")

    print("\n" + "=" * 80)
    print("CROSSING-MID EVENTS — bid > modelled_mid  /  ask < modelled_mid")
    print("(the small secondary peaks in the Figure 5 KDE)")
    print("=" * 80)

    # Split into two sub-tables for readability
    bid_cols = ["Day Start", "Bid Cross n", "Bid Cross freq%",
                "Bid Cross Mean", "Bid Cross Median", "Bid Cross Mode",
                "Bid Cross Mean (bps)", "Bid Cross Med (bps)", "Bid Cross Mode (bps)"]
    ask_cols = ["Day Start", "Ask Cross n", "Ask Cross freq%",
                "Ask Cross Mean", "Ask Cross Median", "Ask Cross Mode",
                "Ask Cross Mean (bps)", "Ask Cross Med (bps)", "Ask Cross Mode (bps)"]

    print("\n  -- Bid crosses ABOVE modelled mid --")
    print(df[bid_cols].round(4).to_string())

    print("\n  -- Ask crosses BELOW modelled mid --")
    print(df[ask_cols].round(4).to_string())

    print(
        "\n  NOTE: Mode = KDE peak fitted to crossing events only (tight bandwidth)."
        "\n        Mean is pulled toward 0 by shallow marginal crossings near ask/bid_off ~ 0."
        "\n        Mode is the more meaningful 'typical crossing depth'."
    )

    # Coefficient of variation of bps values across days
    def _cv(vals):
        a = np.array(vals, dtype=float)
        a = a[~np.isnan(a)]
        return np.std(a) / abs(np.mean(a)) if np.mean(a) != 0 else float("nan")

    bid_bps_vals = [r["Bid Cross Mean (bps)"] for r in rows]
    ask_bps_vals = [r["Ask Cross Mean (bps)"] for r in rows]
    print(
        f"\n  CV of bid-crossing bps across days: {_cv(bid_bps_vals):.3f}  "
        f"|  CV of ask-crossing bps: {_cv(ask_bps_vals):.3f}"
    )
    print(
        "  → CV near 0 means crossing depth is a stable fraction of price level."
    )


# ---------------------------------------------------------------------------
# Figure 8 — KDE of crossing-mid events, normalised by day_start_price (bps)
# ---------------------------------------------------------------------------

def plot_crossing_kde(prices_by_day: dict[int, pd.DataFrame]) -> plt.Figure:
    """
    1x3 grid of KDE plots — one panel per day — showing only the ticks where:
      bid_off > 0  (bid above modelled mid, blue)
      ask_off < 0  (ask below modelled mid, red)
    Offsets are normalised to bps of day_start_price so day-to-day shifts
    caused purely by price level are removed.
    """
    fig, axes = plt.subplots(1, 3, figsize=(17, 5))
    fig.suptitle(
        f"{PRODUCT}  —  Crossing-Mid Offset  (bps, two-sided ticks only)\n"
        "bid > modelled mid (blue)  |  ask < modelled mid (red)",
        fontsize=12, fontweight="bold",
    )
    plt.subplots_adjust(wspace=0.32, left=0.06, right=0.97, top=0.84, bottom=0.12)

    for ax, day in zip(axes, DAYS):
        df        = prices_by_day[day]
        day_start = _compute_day_start_price(df)
        two_sided = df[~df["one_sided"]].copy()
        mod_mid   = DRIFT_RATE * two_sided["timestamp"] + day_start

        bid_off = (two_sided["bid_price_1"] - mod_mid).dropna()
        ask_off = (two_sided["ask_price_1"] - mod_mid).dropna()

        bid_cross_bps = (bid_off[bid_off > 0] / day_start * 10_000)
        ask_cross_bps = (ask_off[ask_off < 0] / day_start * 10_000)

        n_bid = len(bid_cross_bps)
        n_ask = len(ask_cross_bps)

        ax.set_title(
            f"{DAY_LABELS[day]}  (day_start={day_start:.0f})\n"
            f"bid-cross n={n_bid}  |  ask-cross n={n_ask}",
            fontsize=9,
        )
        ax.axvline(0, color="black", linestyle="--", linewidth=1.0, alpha=0.5,
                   label="Modelled Mid (0 bps)")
        ax.set_xlabel("Crossing offset / day_start_price x 10 000 (bps)", fontsize=8)
        ax.set_ylabel("Density", fontsize=8)
        ax.grid(True, alpha=0.3)

        if n_bid >= 2:
            x = np.linspace(bid_cross_bps.min() - 0.2, bid_cross_bps.max() + 0.2, 500)
            k = gaussian_kde(bid_cross_bps)
            mode_bid = float(x[np.argmax(k(x))])
            ax.plot(x, k(x), color="#2255CC", linewidth=1.8,
                    label=f"Bid cross  μ={bid_cross_bps.mean():.3f}  mode={mode_bid:.3f} bps")
            ax.fill_between(x, k(x), alpha=0.18, color="#2255CC")
            ax.axvline(bid_cross_bps.mean(), color="#2255CC", linestyle=":", linewidth=1.1)
            ax.axvline(mode_bid, color="#2255CC", linestyle="-", linewidth=1.2, alpha=0.7)

        if n_ask >= 2:
            x = np.linspace(ask_cross_bps.min() - 0.2, ask_cross_bps.max() + 0.2, 500)
            k = gaussian_kde(ask_cross_bps)
            mode_ask = float(x[np.argmax(k(x))])
            ax.plot(x, k(x), color="#CC3322", linewidth=1.8,
                    label=f"Ask cross  μ={ask_cross_bps.mean():.3f}  mode={mode_ask:.3f} bps")
            ax.fill_between(x, k(x), alpha=0.18, color="#CC3322")
            ax.axvline(ask_cross_bps.mean(), color="#CC3322", linestyle=":", linewidth=1.1)
            ax.axvline(mode_ask, color="#CC3322", linestyle="-", linewidth=1.2, alpha=0.7)

        if n_bid < 2 and n_ask < 2:
            ax.text(0.5, 0.5, "Insufficient crossing events",
                    ha="center", va="center", transform=ax.transAxes,
                    fontsize=10, color="grey")

        ax.legend(fontsize=7)

    return fig


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("Loading prices and trades...")
    prices_by_day = load_all_prices()
    trades_by_day = load_all_trades()

    print("Computing statistics...")
    stats = compute_all_stats(prices_by_day, trades_by_day)

    print_summary(stats)
    print_offset_analysis(prices_by_day)
    print_crossing_analysis(prices_by_day)

    print("Generating figures...")
    plot_price_overview(prices_by_day)
    plot_spread_distribution(prices_by_day)
    plot_depth_bars(prices_by_day)
    plot_rolling_analysis(prices_by_day)
    plot_bid_ask_kde_vs_model(prices_by_day)
    plot_golden_book_kde_vs_model(prices_by_day)
    plot_offset_scaling(prices_by_day)
    plot_crossing_kde(prices_by_day)

    plt.show()


if __name__ == "__main__":
    main()
