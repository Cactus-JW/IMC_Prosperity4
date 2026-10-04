"""
AshAnalysis.py — Statistical analysis of the ASH_COATED_OSMIUM market.

Analyses bid-ask spread, deviation from fair value, orderbook depth, trade
statistics, jump/dip events, and mean-reversion dynamics across days -2, -1
and 0 from Historical Data/Round 1/.

The asset has a known CONSTANT fair value:
    fair_value = 10 000

Unlike INTARIAN_PEPPER_ROOT there is no linear drift, so prices are analysed
directly as deviations from FAIR_VALUE:
    dev(t) = mid_price(t) − 10 000

This means the deviation should be approximately stationary around zero, with
occasional jumps and dips (short-lived excursions away from fair value).

Key analyses specific to this asset:
  1. Deviation from fair value — distribution, percentiles, skew/kurtosis.
  2. Jump/dip event detection — ticks where |dev| > JUMP_THRESHOLD.
     Reports frequency, typical magnitude and excursion duration.
  3. Mean-reversion dynamics — AR(1) fit to the deviation series gives an
     estimated half-life of reversion back to 10 000.
  4. Autocorrelation of tick returns and of the deviation level (lags 1–20).
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

PRODUCT        = "ASH_COATED_OSMIUM"
DAYS           = [-2, -1, 0]
FAIR_VALUE     = 10_000          # known constant fair value — no drift
JUMP_THRESHOLD = 10              # |dev| > this → flagged as a jump/dip event
ROLL_WINDOW    = 50              # ticks for rolling statistics
AC_LAGS        = 20             # autocorrelation lags to display

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

    price_vol_cols = [
        c for c in df.columns
        if any(c.startswith(p) for p in ("bid_price", "bid_volume", "ask_price", "ask_volume", "mid_price"))
    ]
    for col in price_vol_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["day"] = day

    # Spread (drift-free — no correction needed since there is no drift).
    df["spread"] = df["ask_price_1"] - df["bid_price_1"]

    # Flag one-sided / fully-empty ticks.
    df["one_sided"]   = df["bid_price_1"].isna() | df["ask_price_1"].isna()
    df["fully_empty"] = df["bid_price_1"].isna() & df["ask_price_1"].isna()

    # Deviation from constant fair value.
    # Replace mid_price == 0 (exchange sentinel for fully-empty ticks) with NaN.
    df["dev"] = df["mid_price"].replace(0, np.nan) - FAIR_VALUE

    # Tick-to-tick return (change in mid_price).
    df["ret"] = df["dev"].diff()

    # Jump/dip flag: |deviation| exceeds the threshold.
    df["is_jump"] = df["dev"].abs() > JUMP_THRESHOLD

    return df.reset_index(drop=True)


def load_trades_day(day: int) -> pd.DataFrame:
    """Load and enrich the trades CSV for a single day."""
    path = os.path.join(DATA_DIR, TRADE_FILES[day])
    df = pd.read_csv(path, sep=";")
    df = df[df["symbol"] == PRODUCT].copy()

    df["price"]    = pd.to_numeric(df["price"],    errors="coerce")
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    df["day"]      = day

    # Trade deviation from fair value.
    df["dev"] = df["price"] - FAIR_VALUE

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
        "excess_kurtosis": float(kurtosis(two_sided_vals)),
    }


def compute_dev_stats(df: pd.DataFrame) -> dict:
    """Statistics on the deviation from fair value."""
    dev = df["dev"].dropna()
    if dev.empty:
        return {}
    return {
        "mean":            float(dev.mean()),
        "std":             float(dev.std()),
        "median":          float(dev.median()),
        "min":             float(dev.min()),
        "max":             float(dev.max()),
        "p5":              float(dev.quantile(0.05)),
        "p95":             float(dev.quantile(0.95)),
        "skew":            float(skew(dev)),
        "excess_kurtosis": float(kurtosis(dev)),
    }


def compute_jump_stats(df: pd.DataFrame) -> dict:
    """
    Jump/dip event statistics.

    An 'event' is a contiguous run of ticks where |dev| > JUMP_THRESHOLD.
    Reports frequency, mean/max magnitude, and mean excursion duration.
    """
    dev = df["dev"].dropna()
    n_ticks = len(dev)

    # Count ticks flagged as jumps.
    jump_mask = dev.abs() > JUMP_THRESHOLD
    n_jump_ticks = int(jump_mask.sum())

    # Identify contiguous excursion runs.
    # Label each tick with a run ID so we can group by run.
    run_id = (jump_mask != jump_mask.shift()).cumsum()
    jump_runs = dev[jump_mask].groupby(run_id[jump_mask])
    durations  = jump_runs.count()
    magnitudes = jump_runs.apply(lambda s: s.abs().max())

    n_events = len(durations)

    return {
        "n_jump_ticks":   n_jump_ticks,
        "pct_jump_ticks": 100.0 * n_jump_ticks / n_ticks if n_ticks else 0.0,
        "n_events":       n_events,
        "mean_duration":  float(durations.mean()) if n_events else float("nan"),
        "max_duration":   int(durations.max())    if n_events else 0,
        "mean_magnitude": float(magnitudes.mean()) if n_events else float("nan"),
        "max_magnitude":  float(magnitudes.max())  if n_events else float("nan"),
    }


def compute_mean_reversion_stats(df: pd.DataFrame) -> dict:
    """
    Fit AR(1) to deviation series:  dev[t] = α + β * dev[t-1] + ε

    β < 1 (and > 0) → mean-reverting; half-life = -ln(2) / ln(β) ticks.
    β < 0 → over-shooting / oscillating.
    """
    dev = df["dev"].dropna().reset_index(drop=True)
    if len(dev) < 10:
        return {}

    y = dev.iloc[1:].values
    x = dev.iloc[:-1].values

    # OLS: β = cov(x,y) / var(x)
    beta  = float(np.cov(x, y, ddof=1)[0, 1] / np.var(x, ddof=1))
    alpha = float(y.mean() - beta * x.mean())
    resid = y - (alpha + beta * x)

    if 0 < beta < 1:
        half_life = float(-np.log(2) / np.log(beta))
    else:
        half_life = float("nan")

    return {
        "ar1_alpha":  alpha,
        "ar1_beta":   beta,
        "ar1_resid_std": float(resid.std()),
        "half_life_ticks": half_life,
    }


def compute_depth_stats(df: pd.DataFrame) -> dict:
    """Mean bid/ask volume at each orderbook level."""
    result = {}
    for lvl in [1, 2, 3]:
        result[f"mean_bid_vol_{lvl}"] = float(df[f"bid_volume_{lvl}"].mean())
        result[f"mean_ask_vol_{lvl}"] = float(df[f"ask_volume_{lvl}"].mean())
    return result


def compute_trade_stats(df: pd.DataFrame) -> dict:
    """Trade statistics for a single day (or pooled) trades DataFrame."""
    if df.empty:
        return {k: float("nan") for k in
                ["trade_count", "total_volume", "mean_size", "max_size", "vwap", "dev_vwap"]}
    qty = df["quantity"]
    return {
        "trade_count":  len(df),
        "total_volume": float(qty.sum()),
        "mean_size":    float(qty.mean()),
        "max_size":     float(qty.max()),
        "vwap":         float((df["price"] * qty).sum() / qty.sum()),
        "dev_vwap":     float((df["dev"]   * qty).sum() / qty.sum()),
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
            "dev":    compute_dev_stats(prices_by_day[day]),
            "depth":  compute_depth_stats(prices_by_day[day]),
            "trades": compute_trade_stats(trades_by_day[day]),
            "jumps":  compute_jump_stats(prices_by_day[day]),
            "mr":     compute_mean_reversion_stats(prices_by_day[day]),
        }

    all_prices = pd.concat(prices_by_day.values(), ignore_index=True)
    all_trades = pd.concat(trades_by_day.values(), ignore_index=True)
    stats["overall"] = {
        "spread": compute_spread_stats(all_prices),
        "dev":    compute_dev_stats(all_prices),
        "depth":  compute_depth_stats(all_prices),
        "trades": compute_trade_stats(all_trades),
        "jumps":  compute_jump_stats(all_prices),
        "mr":     compute_mean_reversion_stats(all_prices),
    }

    return stats


# ---------------------------------------------------------------------------
# Summary printing
# ---------------------------------------------------------------------------

def print_summary(stats: dict) -> None:
    """Print formatted summary tables to stdout."""
    row_keys   = DAYS + ["Overall"]
    row_labels = [DAY_LABELS[d] for d in DAYS] + ["Overall"]

    def _get(day_or_overall, *keys):
        src = stats["by_day"][day_or_overall] if day_or_overall in DAYS else stats["overall"]
        val = src
        for k in keys:
            val = val[k]
        return val

    # ---- Table 1: Deviation & spread ----
    rows1 = []
    for key, label in zip(row_keys, row_labels):
        rows1.append({
            "":               label,
            "Dev Mean":       _get(key, "dev", "mean"),
            "Dev Std":        _get(key, "dev", "std"),
            "Dev Median":     _get(key, "dev", "median"),
            "Dev Min":        _get(key, "dev", "min"),
            "Dev Max":        _get(key, "dev", "max"),
            "Dev P5":         _get(key, "dev", "p5"),
            "Dev P95":        _get(key, "dev", "p95"),
            "Dev Skew":       _get(key, "dev", "skew"),
            "Dev Ex.Kurt":    _get(key, "dev", "excess_kurtosis"),
            "Spread Mean":    _get(key, "spread", "mean"),
            "Spread Std":     _get(key, "spread", "std"),
            "Spread Median":  _get(key, "spread", "median"),
            "% One-Sided":    _get(key, "spread", "pct_one_sided"),
            "% Fully Empty":  _get(key, "spread", "pct_fully_empty"),
        })

    df1 = pd.DataFrame(rows1).set_index("").T
    print("\n" + "=" * 70)
    print(f"DEVIATION FROM FAIR VALUE ({FAIR_VALUE}) & SPREAD STATISTICS")
    print("=" * 70)
    print(df1.round(4).to_string())

    # ---- Table 2: Jump/dip events ----
    rows2 = []
    for key, label in zip(row_keys, row_labels):
        rows2.append({
            "":               label,
            "Jump Ticks":     _get(key, "jumps", "n_jump_ticks"),
            "Jump Ticks %":   _get(key, "jumps", "pct_jump_ticks"),
            "Num Events":     _get(key, "jumps", "n_events"),
            "Mean Duration":  _get(key, "jumps", "mean_duration"),
            "Max Duration":   _get(key, "jumps", "max_duration"),
            "Mean Magnitude": _get(key, "jumps", "mean_magnitude"),
            "Max Magnitude":  _get(key, "jumps", "max_magnitude"),
        })

    df2 = pd.DataFrame(rows2).set_index("").T
    print("\n" + "=" * 70)
    print(f"JUMP / DIP EVENTS  (|dev| > {JUMP_THRESHOLD})")
    print("=" * 70)
    print(df2.round(3).to_string())

    # ---- Table 3: Mean-reversion ----
    rows3 = []
    for key, label in zip(row_keys, row_labels):
        rows3.append({
            "":                 label,
            "AR(1) a":          _get(key, "mr", "ar1_alpha"),
            "AR(1) b":          _get(key, "mr", "ar1_beta"),
            "Resid Std":        _get(key, "mr", "ar1_resid_std"),
            "Half-Life (ticks)":_get(key, "mr", "half_life_ticks"),
        })

    df3 = pd.DataFrame(rows3).set_index("").T
    print("\n" + "=" * 70)
    print("MEAN-REVERSION DYNAMICS  (AR(1) fit to deviation series)")
    print("  dev[t] = a + b*dev[t-1] + e")
    print("  Half-life = -ln(2)/ln(b)  ticks  (only valid when 0 < b < 1)")
    print("=" * 70)
    print(df3.round(4).to_string())

    # ---- Table 4: Trade stats ----
    rows4 = []
    for key, label in zip(row_keys, row_labels):
        rows4.append({
            "":              label,
            "Trade Count":   _get(key, "trades", "trade_count"),
            "Total Volume":  _get(key, "trades", "total_volume"),
            "Mean Size":     _get(key, "trades", "mean_size"),
            "Max Size":      _get(key, "trades", "max_size"),
            "VWAP":          _get(key, "trades", "vwap"),
            "Dev VWAP":      _get(key, "trades", "dev_vwap"),
        })

    df4 = pd.DataFrame(rows4).set_index("").T
    print("\n" + "=" * 70)
    print("TRADE STATISTICS")
    print("=" * 70)
    print(df4.round(4).to_string())

    # ---- Table 5: Orderbook depth ----
    rows5 = []
    for key, label in zip(row_keys, row_labels):
        depth = _get(key, "depth")
        rows5.append({
            "":        label,
            "Bid L1":  depth["mean_bid_vol_1"],
            "Ask L1":  depth["mean_ask_vol_1"],
            "Bid L2":  depth["mean_bid_vol_2"],
            "Ask L2":  depth["mean_ask_vol_2"],
            "Bid L3":  depth["mean_bid_vol_3"],
            "Ask L3":  depth["mean_ask_vol_3"],
        })

    df5 = pd.DataFrame(rows5).set_index("").T
    print("\n" + "=" * 70)
    print("MEAN ORDERBOOK DEPTH (volume when level is present)")
    print("=" * 70)
    print(df5.round(2).to_string())
    print()


# ---------------------------------------------------------------------------
# Figure 1 — Price overview (raw mid, deviation, spread) — 3 panels
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

        ax0.plot(df["timestamp"], df["mid_price"], color=color, linewidth=0.7, label=label)
        ax1.plot(df["timestamp"], df["dev"],        color=color, linewidth=0.7, label=label)

        two_sided = df[~df["one_sided"]]
        ax2.scatter(two_sided["timestamp"], two_sided["spread"],
                    s=1.5, color=color, alpha=0.4, label=label)

    # Reference lines
    ax0.axhline(FAIR_VALUE, color="black", linestyle="--", linewidth=1.0,
                alpha=0.5, label=f"Fair Value ({FAIR_VALUE})")
    ax1.axhline(0,          color="black", linestyle="--", linewidth=1.0,
                alpha=0.5, label="Fair Value (dev=0)")
    ax1.axhline( JUMP_THRESHOLD, color="red",  linestyle=":", linewidth=1.0,
                alpha=0.5, label=f"Jump threshold (±{JUMP_THRESHOLD})")
    ax1.axhline(-JUMP_THRESHOLD, color="red",  linestyle=":", linewidth=1.0, alpha=0.5)

    ax0.set_title("Raw Mid-Price vs Timestamp", fontsize=10)
    ax0.set_xlabel("Timestamp", fontsize=8)
    ax0.set_ylabel("Mid Price", fontsize=8)
    ax0.legend(fontsize=8)
    ax0.grid(True, alpha=0.3)

    ax1.set_title(
        f"Deviation from Fair Value ({FAIR_VALUE})  —  should hover near zero",
        fontsize=10,
    )
    ax1.set_xlabel("Timestamp", fontsize=8)
    ax1.set_ylabel("Dev = mid − fair_value", fontsize=8)
    ax1.legend(fontsize=8)
    ax1.grid(True, alpha=0.3)

    ax2.set_title("Bid-Ask Spread vs Timestamp  (two-sided ticks only)", fontsize=10)
    ax2.set_xlabel("Timestamp", fontsize=8)
    ax2.set_ylabel("Spread (price units)", fontsize=8)
    ax2.legend(fontsize=8, markerscale=5)
    ax2.grid(True, alpha=0.3)

    return fig


# ---------------------------------------------------------------------------
# Figure 2 — Deviation distribution (histogram + KDE) — 1 × 3 subplots
# ---------------------------------------------------------------------------

def plot_deviation_distribution(prices_by_day: dict[int, pd.DataFrame]) -> plt.Figure:
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    fig.suptitle(
        f"{PRODUCT}  —  Deviation from Fair Value Distribution by Day",
        fontsize=13, fontweight="bold",
    )
    plt.subplots_adjust(wspace=0.30, left=0.07, right=0.97, top=0.88, bottom=0.12)

    for ax, day in zip(axes, DAYS):
        df    = prices_by_day[day]
        vals  = df["dev"].dropna()
        color = DAY_COLORS[day]

        if vals.empty:
            ax.set_title(f"{DAY_LABELS[day]}  (no data)")
            continue

        lo   = int(np.floor(vals.min())) - 2
        hi   = int(np.ceil(vals.max()))  + 2
        bins = np.arange(lo, hi + 1, 1.0)

        ax.hist(vals, bins=bins, density=True,
                color=color, alpha=0.65, edgecolor="white", linewidth=0.5)

        x_kde = np.linspace(lo, hi, 500)
        kde   = gaussian_kde(vals)
        ax.plot(x_kde, kde(x_kde), color="black", linewidth=1.5, label="KDE")

        mean_val   = vals.mean()
        median_val = vals.median()
        ax.axvline(0,          color="grey",   linestyle="--", linewidth=1.0,
                   label=f"Fair value (0)")
        ax.axvline(mean_val,   color="red",    linestyle="--", linewidth=1.2,
                   label=f"Mean: {mean_val:.2f}")
        ax.axvline(median_val, color="orange", linestyle=":",  linewidth=1.2,
                   label=f"Median: {median_val:.1f}")
        ax.axvline( JUMP_THRESHOLD, color="red", linestyle=":", linewidth=0.9, alpha=0.5)
        ax.axvline(-JUMP_THRESHOLD, color="red", linestyle=":", linewidth=0.9, alpha=0.5,
                   label=f"Jump thresh (±{JUMP_THRESHOLD})")

        ax.set_title(
            f"{DAY_LABELS[day]}\nstd={vals.std():.2f}  skew={float(skew(vals)):.3f}",
            fontsize=9,
        )
        ax.set_xlabel("Deviation from fair value", fontsize=8)
        ax.set_ylabel("Density", fontsize=8)
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3)

    return fig


# ---------------------------------------------------------------------------
# Figure 3 — Spread distribution (histogram + KDE) — 1 × 3 subplots
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

        lo   = int(vals.min()) - 1
        hi   = int(vals.max()) + 2
        bins = np.arange(lo, hi, 1.0)

        ax.hist(vals, bins=bins, density=True,
                color=color, alpha=0.65, edgecolor="white", linewidth=0.5)

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
# Figure 4 — Orderbook depth bars — 1 × 3 subplots (one per level)
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

        bid_patch = mpatches.Patch(facecolor="grey", alpha=0.85,  label="Bid")
        ask_patch = mpatches.Patch(facecolor="grey", alpha=0.45, hatch="//",
                                   edgecolor="white", label="Ask")
        ax.legend(handles=[bid_patch, ask_patch], fontsize=8)

    return fig


# ---------------------------------------------------------------------------
# Figure 5 — Rolling analysis (rolling spread + rolling vol of deviation)
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

        # ---- Bottom row: rolling std of deviation (realised vol proxy) ----
        df["roll_std_dev"] = (
            df["dev"]
            .rolling(window=ROLL_WINDOW, min_periods=10)
            .std()
        )

        ax_bot = axes[1, col_idx]
        ax_bot.plot(df["timestamp"], df["roll_std_dev"], color=color, linewidth=1.0)
        ax_bot.set_title(f"{label}  —  Rolling Std of Deviation (realised vol)", fontsize=9)
        ax_bot.set_xlabel("Timestamp", fontsize=8)
        ax_bot.set_ylabel("Std (price units)", fontsize=8)
        ax_bot.grid(True, alpha=0.3)

    return fig


# ---------------------------------------------------------------------------
# Figure 6 — Jump/dip timeline with highlighted events
# ---------------------------------------------------------------------------

def plot_jump_timeline(prices_by_day: dict[int, pd.DataFrame]) -> plt.Figure:
    """
    One panel per day showing the deviation series with jump/dip events
    highlighted in red, plus a histogram of excursion durations and
    a histogram of peak magnitudes.
    """
    fig = plt.figure(figsize=(16, 10))
    fig.suptitle(
        f"{PRODUCT}  —  Jump / Dip Event Analysis  (|dev| > {JUMP_THRESHOLD})",
        fontsize=13, fontweight="bold",
    )
    # Layout: 3 deviation time-series on top row, 2 histograms on bottom row
    gs = fig.add_gridspec(2, 3, hspace=0.5, wspace=0.35,
                          left=0.07, right=0.97, top=0.92, bottom=0.08)

    ax_ts   = [fig.add_subplot(gs[0, i]) for i in range(3)]
    ax_dur  =  fig.add_subplot(gs[1, 0])
    ax_mag  =  fig.add_subplot(gs[1, 1])
    ax_ret  =  fig.add_subplot(gs[1, 2])

    all_durations:  list[float] = []
    all_magnitudes: list[float] = []

    for ax, day in zip(ax_ts, DAYS):
        df    = prices_by_day[day].copy().reset_index(drop=True)
        color = DAY_COLORS[day]
        dev   = df["dev"]

        # Normal ticks (grey) and jump ticks (red)
        normal_mask = ~df["is_jump"] & dev.notna()
        jump_mask   =  df["is_jump"] & dev.notna()

        ax.plot(df.loc[normal_mask, "timestamp"], dev.loc[normal_mask],
                color=color, linewidth=0.5, alpha=0.7)
        ax.scatter(df.loc[jump_mask, "timestamp"], dev.loc[jump_mask],
                   color="red", s=3, zorder=3, label=f"Jump/dip (n={jump_mask.sum()})")

        ax.axhline( JUMP_THRESHOLD, color="red", linestyle=":", linewidth=1.0, alpha=0.6)
        ax.axhline(-JUMP_THRESHOLD, color="red", linestyle=":", linewidth=1.0, alpha=0.6)
        ax.axhline(0, color="black", linestyle="--", linewidth=0.8, alpha=0.4)

        ax.set_title(f"{DAY_LABELS[day]}", fontsize=9)
        ax.set_xlabel("Timestamp", fontsize=8)
        ax.set_ylabel("Dev from fair value", fontsize=8)
        ax.legend(fontsize=7, markerscale=4)
        ax.grid(True, alpha=0.3)

        # Collect event-level stats for the pooled histograms below.
        dev_notna = dev.dropna()
        jump_m    = dev_notna.abs() > JUMP_THRESHOLD
        run_id    = (jump_m != jump_m.shift()).cumsum()
        if jump_m.any():
            runs      = dev_notna[jump_m].groupby(run_id[jump_m])
            all_durations.extend(runs.count().tolist())
            all_magnitudes.extend(runs.apply(lambda s: s.abs().max()).tolist())

    # Duration histogram (pooled across all days)
    if all_durations:
        max_dur = int(max(all_durations))
        bins_d  = np.arange(0.5, max_dur + 1.5, 1.0)
        ax_dur.hist(all_durations, bins=bins_d, color="#CC3322", alpha=0.75,
                    edgecolor="white")
        ax_dur.set_title("Excursion Duration Distribution (all days)", fontsize=9)
        ax_dur.set_xlabel("Duration (ticks)", fontsize=8)
        ax_dur.set_ylabel("Count of events", fontsize=8)
        ax_dur.axvline(np.mean(all_durations), color="black", linestyle="--",
                       linewidth=1.2, label=f"Mean: {np.mean(all_durations):.1f}")
        ax_dur.legend(fontsize=8)
        ax_dur.grid(axis="y", alpha=0.3)
    else:
        ax_dur.text(0.5, 0.5, "No jump events", ha="center", va="center",
                    transform=ax_dur.transAxes, fontsize=10, color="grey")

    # Magnitude histogram (pooled)
    if all_magnitudes:
        lo_m  = int(np.floor(min(all_magnitudes)))
        hi_m  = int(np.ceil(max(all_magnitudes))) + 2
        bins_m = np.arange(lo_m, hi_m, 1.0)
        ax_mag.hist(all_magnitudes, bins=bins_m, color="#2255CC", alpha=0.75,
                    edgecolor="white")
        ax_mag.set_title("Peak Magnitude Distribution (all days)", fontsize=9)
        ax_mag.set_xlabel("|dev| at peak of excursion", fontsize=8)
        ax_mag.set_ylabel("Count of events", fontsize=8)
        ax_mag.axvline(np.mean(all_magnitudes), color="black", linestyle="--",
                       linewidth=1.2, label=f"Mean: {np.mean(all_magnitudes):.1f}")
        ax_mag.legend(fontsize=8)
        ax_mag.grid(axis="y", alpha=0.3)
    else:
        ax_mag.text(0.5, 0.5, "No jump events", ha="center", va="center",
                    transform=ax_mag.transAxes, fontsize=10, color="grey")

    # Return distribution (tick-to-tick changes in mid_price) — all days pooled
    all_rets = pd.concat(
        [df["ret"].dropna() for df in prices_by_day.values()],
        ignore_index=True,
    )
    if not all_rets.empty:
        lo_r  = int(np.floor(all_rets.min())) - 1
        hi_r  = int(np.ceil(all_rets.max()))  + 1
        bins_r = np.arange(lo_r, hi_r + 1, 1.0)
        ax_ret.hist(all_rets, bins=bins_r, density=True, color="#555555",
                    alpha=0.65, edgecolor="white")
        x_kde = np.linspace(lo_r, hi_r, 500)
        ax_ret.plot(x_kde, gaussian_kde(all_rets)(x_kde),
                    color="black", linewidth=1.5, label="KDE")
        ax_ret.set_title(
            f"Tick Return Distribution (all days)\n"
            f"std={all_rets.std():.3f}  skew={float(skew(all_rets)):.3f}  "
            f"kurt={float(kurtosis(all_rets)):.3f}",
            fontsize=9,
        )
        ax_ret.set_xlabel("Δ mid_price per tick", fontsize=8)
        ax_ret.set_ylabel("Density", fontsize=8)
        ax_ret.axvline(0, color="red", linestyle="--", linewidth=1.0, alpha=0.6)
        ax_ret.legend(fontsize=8)
        ax_ret.grid(True, alpha=0.3)

    return fig


# ---------------------------------------------------------------------------
# Figure 7 — Autocorrelation (returns + deviation level)
# ---------------------------------------------------------------------------

def plot_autocorrelation(prices_by_day: dict[int, pd.DataFrame]) -> plt.Figure:
    """
    2 × 3 grid:
      Top row:    autocorrelation of tick returns (Δ mid_price) — one panel per day.
      Bottom row: autocorrelation of deviation level — one panel per day.

    Significant negative autocorrelation in returns at lag 1 is a hallmark of
    a mean-reverting asset: each tick partially corrects the previous move.
    Positive autocorrelation in the deviation level decays toward zero as
    the speed of mean-reversion increases (faster reversion → steeper decay).
    """
    fig, axes = plt.subplots(2, 3, figsize=(16, 8))
    fig.suptitle(
        f"{PRODUCT}  —  Autocorrelation by Day  (lags 1–{AC_LAGS})",
        fontsize=13, fontweight="bold",
    )
    plt.subplots_adjust(hspace=0.50, wspace=0.30,
                        left=0.07, right=0.97, top=0.92, bottom=0.08)

    conf_bound = 1.96 / np.sqrt(10_000)  # approx 95% CI for n≈10 000

    for col_idx, day in enumerate(DAYS):
        df    = prices_by_day[day]
        color = DAY_COLORS[day]
        label = DAY_LABELS[day]

        for row_idx, (series_key, series_label) in enumerate([
            ("ret", "Tick Return (Δ mid)"),
            ("dev", "Deviation Level"),
        ]):
            ax  = axes[row_idx, col_idx]
            ser = df[series_key].dropna()

            lags = range(1, AC_LAGS + 1)
            ac   = [ser.autocorr(lag=k) for k in lags]

            ax.bar(list(lags), ac, color=color, alpha=0.75, width=0.7)
            ax.axhline(0,           color="black", linewidth=0.8)
            ax.axhline( conf_bound, color="red", linestyle="--", linewidth=0.9,
                        alpha=0.7, label=f"95% CI (±{conf_bound:.3f})")
            ax.axhline(-conf_bound, color="red", linestyle="--", linewidth=0.9, alpha=0.7)

            ax.set_title(f"{label}  —  {series_label}", fontsize=9)
            ax.set_xlabel("Lag", fontsize=8)
            ax.set_ylabel("Autocorrelation", fontsize=8)
            ax.set_xlim(0, AC_LAGS + 1)
            ax.set_ylim(-0.5, 1.0)
            ax.legend(fontsize=7)
            ax.grid(True, alpha=0.3)

    return fig


# ---------------------------------------------------------------------------
# Figure 8 — Bid & Ask KDE vs fair value  (two-sided ticks only)
# ---------------------------------------------------------------------------

def plot_bid_ask_kde_vs_fair(prices_by_day: dict[int, pd.DataFrame]) -> plt.Figure:
    """
    KDE of (best_bid − FAIR_VALUE) and (best_ask − FAIR_VALUE) per day.
    Only strictly two-sided ticks are included.
    """
    from matplotlib.widgets import CheckButtons

    fig, axes = plt.subplots(1, 3, figsize=(17, 5))
    fig.suptitle(
        f"{PRODUCT}  —  Best Bid & Ask Offset from Fair Value ({FAIR_VALUE})"
        "  (two-sided ticks only)",
        fontsize=13, fontweight="bold",
    )
    plt.subplots_adjust(wspace=0.35, left=0.06, right=0.84, top=0.85, bottom=0.12)

    bid_artists: list = []
    ask_artists: list = []

    for ax, day in zip(axes, DAYS):
        df        = prices_by_day[day]
        two_sided = df[~df["one_sided"]].copy()

        bid_off = (two_sided["bid_price_1"] - FAIR_VALUE).dropna()
        ask_off = (two_sided["ask_price_1"] - FAIR_VALUE).dropna()

        if bid_off.empty or ask_off.empty:
            ax.set_title(f"{DAY_LABELS[day]}  (no two-sided data)")
            continue

        x_lo  = min(bid_off.min(), ask_off.min()) - 1
        x_hi  = max(bid_off.max(), ask_off.max()) + 1
        x_kde = np.linspace(x_lo, x_hi, 600)

        kde_bid = gaussian_kde(bid_off)
        kde_ask = gaussian_kde(ask_off)

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

        b_line, = ax.plot(x_kde, kde_bid(x_kde), color="#2255CC", linewidth=1.8,
                          label=f"Best Bid  μ={bid_off.mean():.2f}")
        b_fill  = ax.fill_between(x_kde, kde_bid(x_kde), alpha=0.20, color="#2255CC")
        b_mean  = ax.axvline(bid_off.mean(), color="#2255CC", linestyle=":", linewidth=1.2)
        bid_artists.extend([b_line, b_fill, b_mean])

        a_line, = ax.plot(x_kde, kde_ask(x_kde), color="#CC3322", linewidth=1.8,
                          label=f"Best Ask  μ={ask_off.mean():.2f}")
        a_fill  = ax.fill_between(x_kde, kde_ask(x_kde), alpha=0.20, color="#CC3322")
        a_mean  = ax.axvline(ask_off.mean(), color="#CC3322", linestyle=":", linewidth=1.2)
        ask_artists.extend([a_line, a_fill, a_mean])

        ax.axvline(0, color="black", linestyle="--", linewidth=1.0, alpha=0.5,
                   label=f"Fair Value ({FAIR_VALUE})")

        ax.set_title(
            f"{DAY_LABELS[day]}  (n={len(bid_off)} two-sided ticks)",
            fontsize=9,
        )
        ax.set_xlabel("Price offset from fair value", fontsize=8)
        ax.set_ylabel("Density", fontsize=8)
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3)

    ax_chk = fig.add_axes([0.86, 0.38, 0.12, 0.22])
    check  = CheckButtons(ax_chk, ["Best Bid", "Best Ask"], [True, True])
    check.ax.set_title("Show", fontsize=8, pad=4)

    state = {"bid": True, "ask": True}

    def _on_toggle(label: str) -> None:
        if label == "Best Bid":
            state["bid"] = not state["bid"]
            for a in bid_artists:
                a.set_visible(state["bid"])
        else:
            state["ask"] = not state["ask"]
            for a in ask_artists:
                a.set_visible(state["ask"])
        fig.canvas.draw_idle()

    check.on_clicked(_on_toggle)

    return fig


# ---------------------------------------------------------------------------
# Figure 9 — Golden book KDE vs fair value  (one-sided ticks only)
# ---------------------------------------------------------------------------

def plot_golden_book_kde_vs_fair(prices_by_day: dict[int, pd.DataFrame]) -> plt.Figure:
    """KDE of the available-side price offset from FAIR_VALUE on one-sided ticks."""
    from matplotlib.widgets import CheckButtons

    fig, axes = plt.subplots(1, 3, figsize=(17, 5))
    fig.suptitle(
        f"{PRODUCT}  —  Golden Book Price Offset from Fair Value ({FAIR_VALUE})"
        "  (one-sided ticks only)",
        fontsize=13, fontweight="bold",
    )
    plt.subplots_adjust(wspace=0.35, left=0.06, right=0.84, top=0.85, bottom=0.12)

    bid_artists: list = []
    ask_artists: list = []

    for ax, day in zip(axes, DAYS):
        df = prices_by_day[day]

        bid_only_mask = df["bid_price_1"].notna() & df["ask_price_1"].isna()
        ask_only_mask = df["ask_price_1"].notna() & df["bid_price_1"].isna()

        bid_off = (df.loc[bid_only_mask, "bid_price_1"] - FAIR_VALUE).dropna()
        ask_off = (df.loc[ask_only_mask, "ask_price_1"] - FAIR_VALUE).dropna()

        n_bid   = len(bid_off)
        n_ask   = len(ask_off)
        n_empty = int(df["fully_empty"].sum())

        ax.set_title(
            f"{DAY_LABELS[day]}\nbid-only={n_bid}  ask-only={n_ask}  fully-empty={n_empty}",
            fontsize=9,
        )
        ax.set_xlabel("Price offset from fair value", fontsize=8)
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
                       label=f"Fair Value ({FAIR_VALUE})")
        else:
            ax.text(0.5, 0.5, "No one-sided data", ha="center", va="center",
                    transform=ax.transAxes, fontsize=10, color="grey")

        ax.legend(fontsize=7)

    ax_chk = fig.add_axes([0.86, 0.38, 0.12, 0.22])
    check  = CheckButtons(ax_chk, ["Bid-only", "Ask-only"], [True, True])
    check.ax.set_title("Show", fontsize=8, pad=4)

    state = {"bid": True, "ask": True}

    def _on_toggle(label: str) -> None:
        if label == "Bid-only":
            state["bid"] = not state["bid"]
            for a in bid_artists:
                a.set_visible(state["bid"])
        else:
            state["ask"] = not state["ask"]
            for a in ask_artists:
                a.set_visible(state["ask"])
        fig.canvas.draw_idle()

    check.on_clicked(_on_toggle)

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

    print("Generating figures...")
    plot_price_overview(prices_by_day)
    plot_deviation_distribution(prices_by_day)
    plot_spread_distribution(prices_by_day)
    plot_depth_bars(prices_by_day)
    plot_rolling_analysis(prices_by_day)
    plot_jump_timeline(prices_by_day)
    plot_autocorrelation(prices_by_day)
    plot_bid_ask_kde_vs_fair(prices_by_day)
    plot_golden_book_kde_vs_fair(prices_by_day)

    plt.show()


if __name__ == "__main__":
    main()
