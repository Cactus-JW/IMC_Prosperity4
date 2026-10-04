"""
Computes risk metrics from a prosperity4btx output log file.

Usage:
    python compute_bt_metrics.py <path_to_log_file>

Metrics reported:
    Sharpe Ratio            mean(returns) / std(returns), scaled to daily-equivalent
                            via sqrt(TICKS_PER_DAY). Comparable across strategies;
                            do not interpret as a calendar-day Sharpe.
    Deflated Sharpe Ratio   Probability that Sharpe > 0 after adjusting for
                            return distribution shape (Bailey & Lopez de Prado 2012).
                            Computed on raw tick Sharpe before scaling.
    Sortino Ratio           Like Sharpe but denominator uses only downside deviations,
                            scaled by the same sqrt(TICKS_PER_DAY) factor.
    Calmar Ratio            total_pnl / max_drawdown
    Max Drawdown            peak-to-trough decline in cumulative portfolio PnL
    Max Drawdown Duration   longest consecutive run of ticks spent below the prior peak
"""

import csv
import json
import math
import sys
from collections import defaultdict
from io import StringIO

# Each competition day has timestamps 0..999900 in steps of 100 → 10,000 ticks.
# Sharpe/Sortino are scaled by sqrt(TICKS_PER_DAY) so they read as daily-equivalent
# ratios. This is the standard sqrt(N) annualization transform, not a cosmetic scaling.
TICKS_PER_DAY = 10_000


def _extract_activities_csv(log_path: str) -> str:
    """Return the activities CSV string from either a JSON live log or a backtester text log."""
    with open(log_path, "r", encoding="utf-8") as f:
        raw = f.read()

    # Live submission logs are JSON objects with an "activitiesLog" key.
    try:
        data = json.loads(raw)
        return data["activitiesLog"]
    except (json.JSONDecodeError, KeyError):
        pass

    # Backtester text logs have an "Activities log:" section header.
    marker = "Activities log:\n"
    start = raw.find(marker)
    if start == -1:
        raise ValueError(f"Cannot parse log file: no JSON and no 'Activities log:' section in {log_path}")

    section = raw[start + len(marker):]
    # Section ends at the next blank line block (two consecutive newlines after data).
    end = section.find("\n\n")
    return section if end == -1 else section[:end]


def compute_metrics(log_path: str) -> dict:
    activities_csv = _extract_activities_csv(log_path)
    reader = csv.DictReader(StringIO(activities_csv), delimiter=";")
    rows = list(reader)

    # Collect all unique (day, timestamp) keys and per-product PnL updates
    ts_order = []
    seen_keys = set()
    product_updates: dict = defaultdict(dict)  # {(day, ts): {product: pnl}}
    products: set = set()

    for row in rows:
        day = int(row["day"])
        ts = int(row["timestamp"])
        product = row["product"]
        pnl = float(row["profit_and_loss"])
        key = (day, ts)
        product_updates[key][product] = pnl
        products.add(product)
        if key not in seen_keys:
            seen_keys.add(key)
            ts_order.append(key)

    ts_order.sort()

    # Build portfolio PnL series by carrying forward last known per-product PnL
    last_pnl: dict = {p: 0.0 for p in products}
    portfolio_pnl: list = []

    for key in ts_order:
        last_pnl.update(product_updates[key])
        portfolio_pnl.append(sum(last_pnl.values()))

    n_ticks = len(portfolio_pnl)
    if n_ticks < 4:
        return {"error": f"Too few ticks ({n_ticks}) to compute metrics"}

    # Tick-to-tick returns
    returns = [portfolio_pnl[i] - portfolio_pnl[i - 1] for i in range(1, n_ticks)]
    n = len(returns)

    mean_r = sum(returns) / n
    # Unbiased variance
    var_r = sum((r - mean_r) ** 2 for r in returns) / max(n - 1, 1)
    std_r = math.sqrt(var_r) if var_r > 1e-20 else 1e-10

    # Sharpe scaled to daily-equivalent: multiply raw tick Sharpe by sqrt(TICKS_PER_DAY).
    # This is the standard sqrt(N) annualization: mean scales linearly with N but
    # std scales with sqrt(N), so Sharpe scales with sqrt(N).
    sharpe_raw = mean_r / std_r
    sharpe = sharpe_raw * math.sqrt(TICKS_PER_DAY)

    # Sortino: same sqrt(TICKS_PER_DAY) scaling applied for consistency.
    downside_sq = sum(r ** 2 for r in returns if r < 0)
    downside_std = math.sqrt(downside_sq / n) if downside_sq > 0 else 1e-10
    sortino = (mean_r / downside_std) * math.sqrt(TICKS_PER_DAY)

    # Max Drawdown and Max Drawdown Duration
    peak = portfolio_pnl[0]
    max_dd = 0.0
    current_dd_start = None
    dd_durations = []

    for i, p in enumerate(portfolio_pnl):
        if p >= peak:
            if current_dd_start is not None:
                dd_durations.append(i - current_dd_start)
                current_dd_start = None
            peak = p
        else:
            dd = peak - p
            if dd > max_dd:
                max_dd = dd
            if current_dd_start is None:
                current_dd_start = i

    # Still in drawdown at end of series
    if current_dd_start is not None:
        dd_durations.append(n_ticks - current_dd_start)

    max_dd_duration = max(dd_durations) if dd_durations else 0

    # Calmar: total PnL / max drawdown
    total_pnl = portfolio_pnl[-1]
    calmar = total_pnl / max_dd if max_dd > 1e-10 else float("inf")

    # Deflated Sharpe Ratio (Bailey & Lopez de Prado 2012)
    # DSR = Phi( SR * sqrt(T-1) / sqrt(1 - skew*SR + (kurt-1)/4 * SR^2) )
    # Uses raw tick-frequency Sharpe — the formula already accounts for sample size T.
    skew = (sum((r - mean_r) ** 3 for r in returns) / n) / (std_r ** 3)
    kurt = (sum((r - mean_r) ** 4 for r in returns) / n) / (std_r ** 4)
    denom_inner = max(1e-20, 1.0 - skew * sharpe_raw + (kurt - 1.0) / 4.0 * sharpe_raw ** 2)
    z_dsr = (sharpe_raw * math.sqrt(n - 1)) / math.sqrt(denom_inner)
    dsr = 0.5 * math.erfc(-z_dsr / math.sqrt(2))

    return {
        "sharpe": sharpe,
        "dsr": dsr,
        "sortino": sortino,
        "calmar": calmar,
        "max_drawdown": max_dd,
        "max_dd_duration_ticks": max_dd_duration,
        "total_pnl": total_pnl,
        "n_ticks": n_ticks,
        "mean_tick_pnl": mean_r,
        "ticks_per_day": TICKS_PER_DAY,
    }


def format_metrics(m: dict) -> str:
    if "error" in m:
        return f"[metrics] {m['error']}"

    def fmt_ratio(v, precision=6):
        if v == float("inf"):
            return "inf"
        return f"{v:.{precision}f}"

    scale = int(math.sqrt(m["ticks_per_day"]))
    lines = [
        f"--- Risk Metrics (daily-equivalent, scaled by sqrt({m['ticks_per_day']})={scale}) ---",
        f"  Sharpe Ratio:           {fmt_ratio(m['sharpe'], 4)}",
        f"  Deflated Sharpe Ratio:  {m['dsr']:.4f}  (prob. Sharpe > 0)",
        f"  Sortino Ratio:          {fmt_ratio(m['sortino'], 4)}",
        f"  Calmar Ratio:           {fmt_ratio(m['calmar'], 4)}",
        f"  Max Drawdown:           {m['max_drawdown']:.2f}",
        f"  Max DD Duration:        {m['max_dd_duration_ticks']} ticks",
        f"  (N ticks: {m['n_ticks']}, mean tick PnL: {m['mean_tick_pnl']:.4f})",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python compute_bt_metrics.py <log_file>", file=sys.stderr)
        sys.exit(1)

    metrics = compute_metrics(sys.argv[1])
    print(format_metrics(metrics))
