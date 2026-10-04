"""
Generates the figures used in README.md into docs/images/.

Usage (from the repo root):
    python docs/make_figures.py                 # all figures
    python docs/make_figures.py r1_prices r5    # only figures whose name contains these strings
    python docs/make_figures.py --rerun-backtests

The Round 4 figure needs backtest PnL. It is cached in docs/data/r4_backtest_pnl.csv;
if that file is missing (or --rerun-backtests is passed) the three Round 4 strategies
are backtested with the patched local backtester in "Prosperity 4 Backtester/".
"""

import glob
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap
from scipy.optimize import brentq
from scipy.stats import norm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "docs", "images")
DATA_DIR = os.path.join(ROOT, "docs", "data")
HIST = os.path.join(ROOT, "A-Historical Data")
LIVE = os.path.join(ROOT, "D-Output logs")
BACKTESTER = os.path.join(ROOT, "Prosperity 4 Backtester", "imc-prosperity-4-backtester-main")


def _load_module(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Reuse the repo's existing backtest-log parser
_bt_metrics = _load_module("compute_bt_metrics", os.path.join(ROOT, "B-Strategy and Testing", "compute_bt_metrics.py"))
extract_activities_csv = _bt_metrics._extract_activities_csv

# =============================================================================
# STYLE
# =============================================================================
SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_2 = "#52514e"
MUTED = "#8a8984"
GRID = "#e6e5e1"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
DIVERGING = LinearSegmentedColormap.from_list(
    "blue_gray_red", ["#184f95", "#3987e5", "#f0efec", "#e34948", "#a32a2a"]
)

plt.rcParams.update({
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "font.family": "DejaVu Sans",
    "font.size": 10,
    "text.color": TEXT,
    "axes.labelcolor": TEXT_2,
    "axes.titlecolor": TEXT,
    "axes.titlesize": 12,
    "axes.titleweight": "bold",
    "axes.titlelocation": "left",
    "axes.edgecolor": GRID,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "axes.axisbelow": True,
    "grid.color": GRID,
    "grid.linewidth": 0.8,
    "xtick.color": TEXT_2,
    "ytick.color": TEXT_2,
    "xtick.major.size": 0,
    "ytick.major.size": 0,
    "legend.frameon": False,
    "lines.linewidth": 2,
})


def _thousands(ax, axis="y"):
    fmt = matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}")
    (ax.yaxis if axis == "y" else ax.xaxis).set_major_formatter(fmt)


def _subtitle(fig, text):
    fig.text(0.01, 0.005, text, color=MUTED, fontsize=8.5, ha="left", va="bottom")


def _save(fig, name):
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{name}.png")
    fig.savefig(path, dpi=150, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    print(f"saved {os.path.relpath(path, ROOT)}")


def _load_prices(round_no: int) -> pd.DataFrame:
    paths = sorted(glob.glob(os.path.join(HIST, f"Round {round_no}", f"prices_round_{round_no}_day_*.csv")))
    df = pd.concat([pd.read_csv(p, sep=";") for p in paths], ignore_index=True)
    # Mid price is meaningless when one side of the book is empty
    df = df[df["bid_price_1"].notna() & df["ask_price_1"].notna()].copy()
    return df.sort_values(["day", "timestamp"])


def _live_log(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _graph_log(log: dict) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(log["graphLog"]), sep=";")


# =============================================================================
# FIGURES
# =============================================================================
def fig_r1_prices():
    """Round 1: Osmium is pinned near 10,000; Pepper Root climbs +1,000 per day."""
    df = _load_prices(1)
    days = sorted(df["day"].unique())
    df["t"] = df["day"].map({d: i for i, d in enumerate(days)}) + df["timestamp"] / 1_000_000

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2))
    osm = df[df["product"] == "ASH_COATED_OSMIUM"]
    axes[0].plot(osm["t"], osm["mid_price"], color=SERIES[0], lw=0.6)
    axes[0].axhline(10_000, color=TEXT, lw=1, ls="--")
    axes[0].set_title("ASH_COATED_OSMIUM: mean-reverts around 10,000")
    axes[0].set_ylim(9_970, 10_030)

    ipr = df[df["product"] == "INTARIAN_PEPPER_ROOT"]
    axes[1].plot(ipr["t"], ipr["mid_price"], color=SERIES[1], lw=1)
    axes[1].set_title("INTARIAN_PEPPER_ROOT: +1,000 every day")

    for ax in axes:
        ax.set_xticks(range(len(days) + 1))
        ax.set_xlabel("Trading day")
        ax.set_ylabel("Mid price")
        _thousands(ax)
    _subtitle(fig, "Source: IMC historical data, Round 1 (days -2 to 0)")
    fig.tight_layout()
    _save(fig, "r1_prices")


def fig_r1_fill_test():
    """Round 1: live PnL of 'bid best_bid + N for full capacity' on Pepper Root."""
    rows = []
    for path in glob.glob(os.path.join(LIVE, "Round 1", "Intarian Pepper Root", "Tick*", "Tick*.json")):
        name = os.path.basename(path)[:-5]
        rows.append((int(name[4:]), _live_log(path)["profit"]))
    rows.sort()
    offsets, pnl = zip(*rows)

    fig, ax = plt.subplots(figsize=(9, 4.2))
    x = np.arange(len(offsets))
    ax.bar(x, pnl, width=0.7, color=SERIES[1])
    ax.set_xticks(x, [f"+{o}" for o in offsets])
    ax.set_xlabel("Bid price = best bid + N ticks")
    ax.set_ylabel("Live PnL (SeaShells)")
    ax.set_title("Paying up to fill faster wins on a trending product")
    ax.grid(axis="x", visible=False)
    _thousands(ax)
    for i in (0, len(pnl) - 1):
        ax.annotate(f"{pnl[i]:,.0f}", (x[i], pnl[i]), xytext=(0, 4), textcoords="offset points",
                    ha="center", color=TEXT, fontsize=9)
    _subtitle(fig, "Source: Round 1 live submissions, INTARIAN_PEPPER_ROOT buy-to-limit (80) test")
    fig.tight_layout()
    _save(fig, "r1_fill_test")


# -- Black-Scholes helpers, same as B-Strategy and Testing/Round 3/iv_smile.py ---------------
# (that script runs its whole analysis at import time, so the two functions are copied here)
def bs_call(S, K, T, sigma):
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0)
    d1 = (np.log(S / K) + 0.5 * sigma**2 * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return S * norm.cdf(d1) - K * norm.cdf(d2)


def implied_vol(price, S, K, T, tol=1e-6, low=1e-4, high=30.0):
    if price <= max(S - K, 0.0) + tol or price >= S:
        return np.nan
    f = lambda v: bs_call(S, K, T, v) - price
    if f(low) * f(high) > 0:
        return np.nan
    return brentq(f, low, high, xtol=tol)


def fig_r3_iv_smile():
    """Round 3: implied-volatility smile across the VEV voucher strikes."""
    df = _load_prices(3)
    opts = df[df["product"].str.startswith("VEV_")].copy()
    und = df[df["product"] == "VELVETFRUIT_EXTRACT"][["day", "timestamp", "mid_price"]].rename(columns={"mid_price": "S"})
    opts["K"] = opts["product"].str.split("_").str[1].astype(float)
    opts = opts.merge(und, on=["day", "timestamp"], how="inner")
    elapsed = opts["day"] * 10_000 + opts["timestamp"] / 100
    opts["T"] = (3 * 10_000 - elapsed) / 10_000
    opts["bin"] = (elapsed // 1000).astype(int)
    agg = (opts.groupby(["day", "bin", "K"])
           .agg(price=("mid_price", "median"), S=("S", "median"), T=("T", "median"))
           .reset_index())
    agg["iv"] = [implied_vol(r.price, r.S, r.K, r.T) for r in agg.itertuples()]
    agg = agg.dropna(subset=["iv"])
    agg = agg[agg["T"] > 0.01]
    agg["m"] = np.log(agg["K"] / agg["S"]) / np.sqrt(agg["T"])

    fig, ax = plt.subplots(figsize=(9, 4.6))
    # Draw the noisy near-expiry day first so earlier days stay visible on top
    groups = list(agg.groupby("day"))
    for i, (day, g) in reversed(list(enumerate(groups))):
        ax.scatter(g["m"], g["iv"], s=30, color=SERIES[i], edgecolor=SURFACE, linewidth=1, label=f"Day {day}", zorder=3 + (len(groups) - i))
    handles, labels = ax.get_legend_handles_labels()
    order = sorted(range(len(labels)), key=lambda k: labels[k])
    handles, labels = [handles[k] for k in order], [labels[k] for k in order]
    coeffs = np.polyfit(agg["m"], agg["iv"], 2)
    m_fit = np.linspace(agg["m"].min(), agg["m"].max(), 200)
    ax.plot(m_fit, np.poly1d(coeffs)(m_fit), color=TEXT, lw=1.5, ls="--", label="Quadratic fit")
    ax.axvline(0, color=MUTED, lw=1)
    ax.set_xlabel("Moneyness  m = ln(K / S) / √T")
    ax.set_ylabel("Implied volatility")
    ax.set_title("VEV vouchers: implied volatility smile")
    ax.legend(handles + ax.get_legend_handles_labels()[0][len(handles):],
              labels + ax.get_legend_handles_labels()[1][len(labels):], loc="upper center", ncol=4)
    _subtitle(fig, "Source: Round 3 historical data. Black-Scholes with r = 0, T in days to expiry; one point per strike per 1,000-tick bin")
    fig.tight_layout()
    _save(fig, "r3_iv_smile")


def fig_r3_live_pnl():
    """Round 3: live PnL of an early submission vs the final one."""
    runs = [
        ("Early version (438179)", os.path.join(LIVE, "Round 3", "438179.json"), SERIES[1]),
        ("Final version (477494)", os.path.join(LIVE, "Round 3", "477494", "477494.json"), SERIES[0]),
    ]
    fig, ax = plt.subplots(figsize=(9, 4.2))
    for label, path, color in runs:
        g = _graph_log(_live_log(path))
        ax.plot(g["timestamp"], g["value"], color=color, label=label)
        ax.annotate(f"{g['value'].iloc[-1]:,.0f}", (g["timestamp"].iloc[-1], g["value"].iloc[-1]),
                    xytext=(6, 0), textcoords="offset points", va="center", color=TEXT, fontsize=9)
    ax.axhline(0, color=MUTED, lw=1)
    ax.set_xlabel("Timestamp")
    ax.set_ylabel("PnL (SeaShells)")
    ax.set_title("Round 3 live PnL: early vs final strategy")
    ax.legend(loc="upper left")
    ax.set_xlim(right=ax.get_xlim()[1] * 1.08)
    _thousands(ax)
    _thousands(ax, "x")
    _subtitle(fig, "Source: Round 3 live submission logs")
    fig.tight_layout()
    _save(fig, "r3_live_pnl")


def fig_r3_sweep():
    """Round 3: VELVETFRUIT_EXTRACT mean-reversion threshold sweep."""
    d = pd.read_csv(os.path.join(ROOT, "sweeps", "r3v3_2026-04-26_10-19-16.csv"))
    d = d[d["product_swept"] == "VELVETFRUIT_EXTRACT"]
    d = d[d["mm_half_spread"] == d["mm_half_spread"].max()]
    grid = d.pivot(index="mr_z_threshold", columns="mr_agg_z", values="vfe_pnl").sort_index(ascending=False)

    fig, ax = plt.subplots(figsize=(7.5, 4.6))
    cmap = LinearSegmentedColormap.from_list("seq_blue", SEQ_BLUE)
    im = ax.imshow(grid.values, cmap=cmap, aspect="auto")
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    ax.set_xticks(range(grid.shape[1]), [f"{v:g}" for v in grid.columns])
    ax.set_yticks(range(grid.shape[0]), [f"{v:g}" for v in grid.index])
    ax.set_xticks(np.arange(-0.5, grid.shape[1]), minor=True)
    ax.set_yticks(np.arange(-0.5, grid.shape[0]), minor=True)
    ax.grid(which="minor", color=SURFACE, linewidth=3)
    ax.tick_params(which="minor", length=0)
    lo, hi = grid.values.min(), grid.values.max()
    for (r, c), v in np.ndenumerate(grid.values):
        dark = (v - lo) / (hi - lo) > 0.55
        ax.text(c, r, f"{v / 1000:,.1f}k", ha="center", va="center", fontsize=10,
                color="#ffffff" if dark else TEXT, weight="bold" if v == hi else "normal")
    ax.set_xlabel("Aggressive z-score (mr_agg_z)")
    ax.set_ylabel("Entry z-score (mr_z_threshold)")
    ax.set_title("VELVETFRUIT_EXTRACT backtest PnL by mean-reversion thresholds")
    cbar = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(colors=TEXT_2, length=0)
    cbar.ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v / 1000:,.0f}k"))
    _subtitle(fig, f"Source: sweeps/r3v3 parameter sweep, Round 3 days 0-2, mm_half_spread = {int(d['mm_half_spread'].iloc[0])}")
    fig.tight_layout()
    _save(fig, "r3_sweep")


R4_VARIANTS = [
    ("LR+NP+EODM", "Base: LR + NP + EODM"),
    ("LR+NP+EODM+IN", "+ Insider signal (IN)"),
    ("LR+NP+EODM+IN+MO", "+ Insider + Momentum (MO)"),
]


def _r4_backtest_pnl(rerun: bool) -> pd.DataFrame:
    cache = os.path.join(DATA_DIR, "r4_backtest_pnl.csv")
    if os.path.exists(cache) and not rerun:
        return pd.read_csv(cache)

    frames = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, _ in R4_VARIANTS:
            algo = os.path.join(ROOT, "B-Strategy and Testing", "Round 4", f"{name}.py")
            out = os.path.join(tmp, f"{name}.log")
            print(f"backtesting {name} on Round 4 ...")
            subprocess.run([sys.executable, "-m", "prosperity4bt", algo, "4", "--out", out],
                           cwd=BACKTESTER, check=True, stdout=subprocess.DEVNULL)
            act = pd.read_csv(io.StringIO(extract_activities_csv(out)), sep=";")
            pnl = act.groupby(["day", "timestamp"])["profit_and_loss"].sum().reset_index()
            # every 10th tick (plus the final one) keeps the cache small
            pnl = pd.concat([pnl.iloc[::10], pnl.iloc[[-1]]]).drop_duplicates()
            pnl["variant"] = name
            frames.append(pnl)
    df = pd.concat(frames, ignore_index=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    df.to_csv(cache, index=False)
    return df


def fig_r4_backtest(rerun: bool = False):
    """Round 4: cumulative backtest PnL as layers are added to the strategy."""
    df = _r4_backtest_pnl(rerun)
    days = sorted(df["day"].unique())
    # With --merge-pnl the backtester already offsets each day's timestamps by 1,000,000
    df["t"] = df["timestamp"] / 1_000_000

    # The +IN variant matches its trader names against "mark_1"/"mark_14" but the data uses
    # "Mark 01"/"Mark 14", so the insider layer never fires and its PnL equals the base.
    base = df[df["variant"] == "LR+NP+EODM"].set_index(["day", "timestamp"])["profit_and_loss"]
    ins = df[df["variant"] == "LR+NP+EODM+IN"].set_index(["day", "timestamp"])["profit_and_loss"]
    plotted = R4_VARIANTS
    if base.equals(ins):
        plotted = [(R4_VARIANTS[0][0], "Base: LR + NP + EODM (+ IN gives identical PnL)"), R4_VARIANTS[2]]

    fig, ax = plt.subplots(figsize=(9, 4.4))
    for i, (name, label) in enumerate(plotted):
        g = df[df["variant"] == name].sort_values("t")
        ax.plot(g["t"], g["profit_and_loss"], color=SERIES[i], lw=1.6, label=label)
        ax.annotate(f"{g['profit_and_loss'].iloc[-1]:,.0f}", (g["t"].iloc[-1], g["profit_and_loss"].iloc[-1]),
                    xytext=(6, 0), textcoords="offset points", va="center", color=TEXT, fontsize=9)
    ax.axhline(0, color=MUTED, lw=1)
    ax.set_xticks(np.arange(len(days)) + 0.5, [f"Day {d}" for d in days])
    ax.set_xticks(range(len(days) + 1), minor=True)
    ax.grid(axis="x", which="major", visible=False)
    ax.grid(axis="x", which="minor", visible=True)
    ax.set_xlim(0, len(days) * 1.1)
    ax.set_ylabel("Cumulative PnL (SeaShells)")
    ax.set_title("Round 4: backtest PnL as strategy layers are added")
    ax.legend(loc="upper left")
    _thousands(ax)
    _subtitle(fig, "Source: local prosperity4bt backtests on Round 4 data (days 1-3)")
    fig.tight_layout()
    _save(fig, "r4_backtest")


def fig_r5_correlation():
    """Round 5: return correlations of the 50 products, grouped by family."""
    df = _load_prices(5)
    mids = df.pivot_table(index=["day", "timestamp"], columns="product", values="mid_price").sort_index()
    # 1,000-timestamp returns (10 ticks) within each day, so the overnight jump is not counted
    sampled = mids[mids.index.get_level_values("timestamp") % 1000 == 0]
    rets = sampled.groupby(level="day").diff().dropna(how="all")
    products = sorted(rets.columns)
    corr = rets[products].corr()

    two_word = ("GALAXY_SOUNDS", "OXYGEN_SHAKE", "SLEEP_POD", "UV_VISOR")
    families = [next((f for f in two_word if p.startswith(f)), p.split("_")[0]) for p in products]
    starts = [i for i in range(len(products)) if i == 0 or families[i] != families[i - 1]]

    fig, ax = plt.subplots(figsize=(10, 9))
    im = ax.imshow(corr.values, cmap=DIVERGING, vmin=-1, vmax=1)
    ax.grid(False)
    for s in ax.spines.values():
        s.set_visible(False)
    for b in starts[1:]:
        ax.axhline(b - 0.5, color=SURFACE, lw=2)
        ax.axvline(b - 0.5, color=SURFACE, lw=2)
    centers = [(s + (starts[k + 1] if k + 1 < len(starts) else len(products))) / 2 - 0.5 for k, s in enumerate(starts)]
    names = [families[s].replace("_", " ").title().replace("Uv ", "UV ") for s in starts]
    ax.set_xticks(centers, names, rotation=45, ha="right", fontsize=9.5)
    ax.set_yticks(centers, names, fontsize=9.5)
    ax.set_title("Round 5: only the Pebbles and Snackpack families are linked")
    cbar = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02)
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(colors=TEXT_2, length=0)
    cbar.set_label("Correlation of 10-tick mid-price returns", color=TEXT_2)
    _subtitle(fig, "Source: Round 5 historical data (days 2-4). 50 products in 10 families of 5, sorted by name within each family")
    fig.tight_layout()
    _save(fig, "r5_correlation")


FIGURES = {
    "r1_prices": fig_r1_prices,
    "r1_fill_test": fig_r1_fill_test,
    "r3_iv_smile": fig_r3_iv_smile,
    "r3_live_pnl": fig_r3_live_pnl,
    "r3_sweep": fig_r3_sweep,
    "r4_backtest": fig_r4_backtest,
    "r5_correlation": fig_r5_correlation,
}


def main(argv):
    rerun = "--rerun-backtests" in argv
    wanted = [a for a in argv if not a.startswith("--")]
    for name, fn in FIGURES.items():
        if wanted and not any(w in name for w in wanted):
            continue
        fn(rerun) if name == "r4_backtest" else fn()


if __name__ == "__main__":
    main(sys.argv[1:])
