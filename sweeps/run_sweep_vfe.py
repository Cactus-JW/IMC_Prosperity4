"""
Parameter sweep for VELVETFRUIT_EXTRACT strategy (Round 3).

Primary sweep axes (per user spec):
  VFE_MAKE_SPREAD   x  VFE_MAKE_SIZE  x  VFE_INV_SKEW  x  VFE_TAKER_THRESH

Runs prosperity4btx for all days of round 3 on each combination.
Saves results to sweeps/vfe_<timestamp>.csv.

The patched strategy is written into the same directory as the original
(B-Strategy and Testing/Round 3/) so that datamodel.py is co-located
and the backtester can resolve imports.
"""

import csv
import importlib.util
import itertools
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT    = Path(__file__).resolve().parent.parent
STRATEGY_SRC = REPO_ROOT / "B-Strategy and Testing" / "Round 3" / "StrategyRound3Final.py"
STRATEGY_DIR = STRATEGY_SRC.parent
SWEEPS_DIR   = REPO_ROOT / "sweeps"
METRICS_SCRIPT = REPO_ROOT / "B-Strategy and Testing" / "compute_bt_metrics.py"

# ---------------------------------------------------------------------------
# Sweep grid — user-specified
# ---------------------------------------------------------------------------
MAKE_SPREAD_VALS   = [1, 2, 3]
MAKE_SIZE_VALS     = [10, 15, 20, 25]
INV_SKEW_VALS      = [0.02, 0.03, 0.05]
TAKER_THRESH_VALS  = [0, 1, 2]

# Secondary params held at baseline
INV_CUT_THRESH_VALS       = [100]
INV_SUPPRESS_THRESH_VALS  = [180]
TAKER_MAX_VALS            = [8]
EMA_BLEND_VALS            = [0.3]
MICROPRICE_ALPHA_VALS     = [2.0]

GRID = list(itertools.product(
    MAKE_SPREAD_VALS,
    MAKE_SIZE_VALS,
    INV_SKEW_VALS,
    TAKER_THRESH_VALS,
    INV_CUT_THRESH_VALS,
    INV_SUPPRESS_THRESH_VALS,
    TAKER_MAX_VALS,
    EMA_BLEND_VALS,
    MICROPRICE_ALPHA_VALS,
))

PARAM_NAMES = [
    "make_spread",
    "make_size",
    "inv_skew",
    "taker_thresh",
    "inv_cut_thresh",
    "inv_suppress_thresh",
    "taker_max",
    "ema_blend",
    "microprice_alpha",
]

# Maps sweep param key -> constant name in strategy file
REPLACEMENT_MAP = {
    "make_spread":          "VFE_MAKE_SPREAD",
    "make_size":            "VFE_MAKE_SIZE",
    "inv_skew":             "VFE_INV_SKEW",
    "taker_thresh":         "VFE_TAKER_THRESH",
    "inv_cut_thresh":       "VFE_INV_CUT_THRESH",
    "inv_suppress_thresh":  "VFE_INV_SUPPRESS_THRESH",
    "taker_max":            "VFE_TAKER_MAX",
    "ema_blend":            "VFE_EMA_BLEND",
    "microprice_alpha":     "VFE_MICROPRICE_ALPHA",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def patch_strategy(src_text: str, params: dict) -> str:
    """Replace constant assignments in source text with sweep param values."""
    lines = src_text.splitlines()
    out = []
    for line in lines:
        for param_key, const_name in REPLACEMENT_MAP.items():
            if param_key in params:
                pattern = re.compile(
                    r'^(' + re.escape(const_name) + r'\s*=\s*)([^\s#]+)(.*)'
                )
                m = pattern.match(line)
                if m:
                    line = f"{m.group(1)}{params[param_key]}{m.group(3)}"
                    break
        out.append(line)
    return "\n".join(out)


def run_backtest(strategy_path: str, log_path: str) -> None:
    """Run prosperity4btx for round 3 (all days). Raises on failure."""
    result = subprocess.run(
        ["prosperity4btx", strategy_path, "3", "--out", log_path],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Backtest failed (rc={result.returncode}): "
            f"{result.stdout[-400:]} | {result.stderr[-400:]}"
        )


def load_metrics_module():
    spec = importlib.util.spec_from_file_location("compute_bt_metrics", str(METRICS_SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_log(log_path: str, metrics_mod) -> dict:
    """Extract total/per-product PnL and risk metrics from a backtest log."""
    metrics = metrics_mod.compute_metrics(log_path)

    with open(log_path, "r", encoding="utf-8") as f:
        raw = f.read()

    # Try JSON (live log format)
    try:
        data = json.loads(raw)
        csv_text = data["activitiesLog"]
    except (json.JSONDecodeError, KeyError):
        # Backtester text log format
        marker = "Activities log:\n"
        start = raw.find(marker)
        if start == -1:
            csv_text = ""
        else:
            section = raw[start + len(marker):]
            end = section.find("\n\n")
            csv_text = section if end == -1 else section[:end]

    import csv as csv_mod
    from io import StringIO
    last_pnl = {}
    if csv_text:
        reader = csv_mod.DictReader(StringIO(csv_text), delimiter=";")
        for row in reader:
            product = row.get("product", "")
            try:
                last_pnl[product] = float(row["profit_and_loss"])
            except (ValueError, KeyError):
                pass

    return {
        "total_pnl":    metrics.get("total_pnl",    float("nan")),
        "sharpe":       metrics.get("sharpe",        float("nan")),
        "sortino":      metrics.get("sortino",       float("nan")),
        "calmar":       metrics.get("calmar",        float("nan")),
        "max_drawdown": metrics.get("max_drawdown",  float("nan")),
        "dsr":          metrics.get("dsr",           float("nan")),
        "n_ticks":      metrics.get("n_ticks",       0),
        "vfe_pnl":      last_pnl.get("VELVETFRUIT_EXTRACT", float("nan")),
    }


# ---------------------------------------------------------------------------
# Stability helpers
# ---------------------------------------------------------------------------

def build_lookup(results: list) -> dict:
    """Map (spread, size, skew, taker) -> total_pnl."""
    lookup = {}
    for row in results:
        if row.get("error"):
            continue
        try:
            key = (
                float(row["make_spread"]),
                float(row["make_size"]),
                float(row["inv_skew"]),
                float(row["taker_thresh"]),
            )
            lookup[key] = float(row["total_pnl"])
        except (ValueError, KeyError):
            pass
    return lookup


def neighbor_stability(row: dict, lookup: dict) -> float:
    """
    Mean absolute deviation of axis-adjacent neighbors from the focal PnL.
    Lower = flatter plateau = more stable.
    """
    try:
        ms  = float(row["make_spread"])
        msz = float(row["make_size"])
        sk  = float(row["inv_skew"])
        tt  = float(row["taker_thresh"])
        focal_pnl = float(row["total_pnl"])
    except (ValueError, KeyError):
        return float("nan")

    ms_grid  = sorted(set(MAKE_SPREAD_VALS))
    msz_grid = sorted(set(MAKE_SIZE_VALS))
    sk_grid  = sorted(set(INV_SKEW_VALS))
    tt_grid  = sorted(set(TAKER_THRESH_VALS))

    def adjacent(val, grid):
        try:
            idx = grid.index(val)
        except ValueError:
            return []
        neighbors = []
        if idx > 0:
            neighbors.append(grid[idx - 1])
        if idx < len(grid) - 1:
            neighbors.append(grid[idx + 1])
        return neighbors

    neighbor_pnls = []
    for nms in adjacent(ms, ms_grid):
        key = (nms, msz, sk, tt)
        if key in lookup:
            neighbor_pnls.append(lookup[key])
    for nmsz in adjacent(msz, msz_grid):
        key = (ms, nmsz, sk, tt)
        if key in lookup:
            neighbor_pnls.append(lookup[key])
    for nsk in adjacent(sk, sk_grid):
        key = (ms, msz, nsk, tt)
        if key in lookup:
            neighbor_pnls.append(lookup[key])
    for ntt in adjacent(tt, tt_grid):
        key = (ms, msz, sk, ntt)
        if key in lookup:
            neighbor_pnls.append(lookup[key])

    if not neighbor_pnls:
        return float("nan")

    mad = sum(abs(p - focal_pnl) for p in neighbor_pnls) / len(neighbor_pnls)
    return mad


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"Strategy source : {STRATEGY_SRC}")
    print(f"Primary sweep   : make_spread x make_size x inv_skew x taker_thresh")
    print(f"Grid size       : {len(GRID)} combinations")
    print()

    src_text = STRATEGY_SRC.read_text(encoding="utf-8")
    metrics_mod = load_metrics_module()

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_csv = SWEEPS_DIR / f"vfe_{timestamp}.csv"

    fieldnames = PARAM_NAMES + [
        "total_pnl", "vfe_pnl",
        "sharpe", "sortino", "calmar",
        "max_drawdown", "dsr", "n_ticks", "error",
    ]

    results_buffer = []

    with open(out_csv, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        for i, combo in enumerate(GRID):
            params = dict(zip(PARAM_NAMES, combo))
            label = " | ".join(f"{k}={v}" for k, v in list(params.items())[:4])
            print(f"[{i+1:3d}/{len(GRID)}] {label}")

            patched_text = patch_strategy(src_text, params)

            tmp_strategy = None
            log_path = str(SWEEPS_DIR / f"_vfe_tmp_{i}.log")
            row = {k: v for k, v in params.items()}
            row["error"] = ""

            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=".py",
                    delete=False,
                    dir=str(STRATEGY_DIR),
                    encoding="utf-8",
                ) as tmp:
                    tmp.write(patched_text)
                    tmp_strategy = tmp.name

                run_backtest(tmp_strategy, log_path)
                result = parse_log(log_path, metrics_mod)

                row["total_pnl"]    = round(result["total_pnl"],    2)
                vfe_pnl_val = result["vfe_pnl"]
                row["vfe_pnl"]      = round(vfe_pnl_val, 2) if vfe_pnl_val == vfe_pnl_val else ""
                row["sharpe"]       = round(result["sharpe"],        4)
                row["sortino"]      = round(result["sortino"],       4)
                row["calmar"]       = round(result["calmar"],        4)
                row["max_drawdown"] = round(result["max_drawdown"],  2)
                row["dsr"]          = round(result["dsr"],           4)
                row["n_ticks"]      = result["n_ticks"]

                vfe_display = str(row["vfe_pnl"]) if row["vfe_pnl"] != "" else "N/A"
                print(
                    f"         total={row['total_pnl']:>10,.0f}  "
                    f"vfe={vfe_display:>9}  "
                    f"sharpe={row['sharpe']:>7.4f}  "
                    f"max_dd={row['max_drawdown']:>10,.0f}"
                )

            except Exception as e:
                row["error"]        = str(e)[:300]
                row["total_pnl"]    = ""
                row["vfe_pnl"]      = ""
                row["sharpe"]       = ""
                row["sortino"]      = ""
                row["calmar"]       = ""
                row["max_drawdown"] = ""
                row["dsr"]          = ""
                row["n_ticks"]      = ""
                print(f"  ERROR: {row['error'][:120]}")

            finally:
                if tmp_strategy:
                    try:
                        os.unlink(tmp_strategy)
                    except OSError:
                        pass
                try:
                    os.unlink(log_path)
                except OSError:
                    pass

            results_buffer.append(dict(row))
            writer.writerow(row)
            csvfile.flush()

    print()
    print(f"Sweep complete. Results: {out_csv}")
    return str(out_csv), results_buffer


if __name__ == "__main__":
    csv_path, results = main()

    # Post-sweep analysis
    valid = []
    for r in results:
        if r.get("error") != "" and r.get("error") is not None:
            continue
        try:
            pnl = float(str(r["total_pnl"]))
            if pnl == pnl:  # not NaN
                valid.append(r)
        except (ValueError, KeyError, TypeError):
            pass

    if not valid:
        print("No valid results to analyze.")
        raise SystemExit(0)

    valid.sort(key=lambda r: float(str(r["total_pnl"])), reverse=True)

    print("\n=== TOP 10 BY TOTAL PNL ===")
    print(f"{'Rank':>4}  {'spread':>6}  {'size':>4}  {'skew':>6}  {'taker':>5}  {'total_pnl':>10}  {'sharpe':>8}  {'max_dd':>10}")
    for rank, r in enumerate(valid[:10], 1):
        print(
            f"{rank:>4}  {r['make_spread']:>6}  {r['make_size']:>4}  {r['inv_skew']:>6}  "
            f"{r['taker_thresh']:>5}  {float(str(r['total_pnl'])):>10,.0f}  "
            f"{float(str(r['sharpe'])):>8.4f}  {float(str(r['max_drawdown'])):>10,.0f}"
        )

    # Stability analysis
    lookup = build_lookup(results)
    for r in valid:
        r["_stability_mad"] = neighbor_stability(r, lookup)

    stable_valid = [
        r for r in valid
        if isinstance(r.get("_stability_mad"), float) and r["_stability_mad"] == r["_stability_mad"]
    ]
    stable_valid.sort(key=lambda r: r["_stability_mad"])

    print("\n=== TOP 10 BY STABILITY (lowest neighbor MAD = flattest plateau) ===")
    print(f"{'Rank':>4}  {'spread':>6}  {'size':>4}  {'skew':>6}  {'taker':>5}  {'total_pnl':>10}  {'neigh_MAD':>10}")
    for rank, r in enumerate(stable_valid[:10], 1):
        print(
            f"{rank:>4}  {r['make_spread']:>6}  {r['make_size']:>4}  {r['inv_skew']:>6}  "
            f"{r['taker_thresh']:>5}  {float(str(r['total_pnl'])):>10,.0f}  "
            f"{r['_stability_mad']:>10,.1f}"
        )

    # Overfit flags: top-20 PnL combos with MAD > 2x median of all valid
    all_mads = sorted(
        r["_stability_mad"]
        for r in valid
        if isinstance(r.get("_stability_mad"), float) and r["_stability_mad"] == r["_stability_mad"]
    )
    if all_mads:
        median_mad = all_mads[len(all_mads) // 2]
        overfit_thresh = 2.0 * median_mad
        overfit = [
            r for r in valid[:20]
            if isinstance(r.get("_stability_mad"), float) and r["_stability_mad"] > overfit_thresh
        ]
        if overfit:
            print(f"\n=== OVERFIT FLAGS (top-20 PnL, neighbor MAD > 2x median={median_mad:.0f}) ===")
            for r in overfit:
                print(
                    f"  spread={r['make_spread']}  size={r['make_size']}  skew={r['inv_skew']}  "
                    f"taker={r['taker_thresh']}  pnl={float(str(r['total_pnl'])):,.0f}  MAD={r['_stability_mad']:,.0f}"
                )
        else:
            print(f"\nNo overfit flags in top-20 (median MAD={median_mad:.0f}, threshold={overfit_thresh:.0f}).")

    print(f"\nFull results CSV: {csv_path}")
