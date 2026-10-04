"""
Parameter sweep for HYDROGEL_PACK strategy (Round 3).

Sweeps:
  HGP_MAKE_SPREAD         x  HGP_MAKE_SPREAD_WIDE  x  HGP_MAKE_SIZE
  x  HGP_INV_SKEW_COEFF   x  HGP_IMB_COEFF         x  HGP_TAKER_DEV_THRESH

Runs prosperity4btx for all 3 days of round 3 on each combination.
Saves results to sweeps/<timestamp>.csv.

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
from io import StringIO
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT    = Path(__file__).resolve().parent.parent
STRATEGY_SRC = REPO_ROOT / "B-Strategy and Testing" / "Round 3" / "StrategyRound3Final.py"
STRATEGY_DIR = STRATEGY_SRC.parent          # temp files go here so datamodel.py is found
SWEEPS_DIR   = REPO_ROOT / "sweeps"
METRICS_SCRIPT = REPO_ROOT / "B-Strategy and Testing" / "compute_bt_metrics.py"

# ---------------------------------------------------------------------------
# Sweep grid — requested values
# ---------------------------------------------------------------------------
HGP_MAKE_SPREAD_VALS       = [6, 7, 8]
HGP_MAKE_SPREAD_WIDE_VALS  = [8, 9, 10]
HGP_MAKE_SIZE_VALS         = [15, 20, 25]
HGP_INV_SKEW_COEFF_VALS    = [0.02, 0.04, 0.06]
HGP_IMB_COEFF_VALS         = [1, 2, 3, 4]
HGP_TAKER_DEV_THRESH_VALS  = [20, 25, 30, 35]

PARAM_NAMES = [
    "make_spread",
    "make_spread_wide",
    "make_size",
    "inv_skew_coeff",
    "imb_coeff",
    "taker_dev_thresh",
]

# Constant name in the source file
REPLACEMENT_MAP = {
    "make_spread":       "HGP_MAKE_SPREAD",
    "make_spread_wide":  "HGP_MAKE_SPREAD_WIDE",
    "make_size":         "HGP_MAKE_SIZE",
    "inv_skew_coeff":    "HGP_INV_SKEW_COEFF",
    "imb_coeff":         "HGP_IMB_COEFF",
    "taker_dev_thresh":  "HGP_TAKER_DEV_THRESH",
}

# To keep runtime manageable we do a structured sweep:
# Primary 3-way: make_spread x make_spread_wide x make_size  (3x3x3 = 27)
# Secondary 4-way: inv_skew_coeff x imb_coeff x taker_dev_thresh at baseline make params (3x4x4 = 48)
# Overlap: baseline secondary, all primary combos

BASELINE = {
    "make_spread":       7,
    "make_spread_wide":  8,
    "make_size":         20,
    "inv_skew_coeff":    0.04,
    "imb_coeff":         3,
    "taker_dev_thresh":  25,
}

def build_grid():
    combos = set()

    # Primary sweep: make_spread x make_spread_wide x make_size (secondary at baseline)
    for ms, msw, mz in itertools.product(
        HGP_MAKE_SPREAD_VALS, HGP_MAKE_SPREAD_WIDE_VALS, HGP_MAKE_SIZE_VALS
    ):
        combo = (
            ms, msw, mz,
            BASELINE["inv_skew_coeff"],
            BASELINE["imb_coeff"],
            BASELINE["taker_dev_thresh"],
        )
        combos.add(combo)

    # Secondary sweep: inv_skew x imb x taker_dev_thresh (spread/size at baseline)
    for isk, imb, tdt in itertools.product(
        HGP_INV_SKEW_COEFF_VALS, HGP_IMB_COEFF_VALS, HGP_TAKER_DEV_THRESH_VALS
    ):
        combo = (
            BASELINE["make_spread"],
            BASELINE["make_spread_wide"],
            BASELINE["make_size"],
            isk, imb, tdt,
        )
        combos.add(combo)

    return sorted(combos)

GRID = build_grid()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def patch_strategy(src_text: str, params: dict) -> str:
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
    """Run prosperity4btx for round 3 (all 3 days). Raises on failure."""
    venv_btx = REPO_ROOT / ".venv" / "Scripts" / "prosperity4btx"
    cmd = [str(venv_btx), strategy_path, "3", "--out", log_path]
    result = subprocess.run(
        cmd,
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
    metrics = metrics_mod.compute_metrics(log_path)

    with open(log_path, "r", encoding="utf-8") as f:
        raw = f.read()

    # Try JSON first (live log format), else text backtester format
    activities_csv = None
    try:
        data = json.loads(raw)
        activities_csv = data["activitiesLog"]
    except (json.JSONDecodeError, KeyError):
        marker = "Activities log:\n"
        start = raw.find(marker)
        if start != -1:
            section = raw[start + len(marker):]
            end = section.find("\n\n")
            activities_csv = section if end == -1 else section[:end]

    last_pnl = {}
    if activities_csv:
        reader = csv.DictReader(StringIO(activities_csv), delimiter=";")
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
        "hgp_pnl":      last_pnl.get("HYDROGEL_PACK", float("nan")),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"Strategy source  : {STRATEGY_SRC}")
    print(f"Backtester round : 3  (days 0, 1, 2)")
    print(f"Grid size        : {len(GRID)} combinations")
    print(f"  Primary phase  : make_spread x make_spread_wide x make_size  (3x3x3=27, secondary=baseline)")
    print(f"  Secondary phase: inv_skew x imb_coeff x taker_dev_thresh  (3x4x4=48, spread/size=baseline)")
    print()

    src_text = STRATEGY_SRC.read_text(encoding="utf-8")
    metrics_mod = load_metrics_module()

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_csv = SWEEPS_DIR / f"{timestamp}.csv"

    fieldnames = PARAM_NAMES + [
        "total_pnl", "hgp_pnl",
        "sharpe", "sortino", "calmar",
        "max_drawdown", "dsr", "n_ticks", "error",
    ]

    with open(out_csv, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        for i, combo in enumerate(GRID):
            params = dict(zip(PARAM_NAMES, combo))
            label = " | ".join(f"{k}={v}" for k, v in params.items())
            print(f"[{i+1:3d}/{len(GRID)}] {label}")

            patched_text = patch_strategy(src_text, params)

            tmp_strategy = None
            log_path = str(SWEEPS_DIR / f"_tmp_hgp_{i}.log")
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
                row["hgp_pnl"]      = round(result["hgp_pnl"],      2)
                row["sharpe"]       = round(result["sharpe"],        4)
                row["sortino"]      = round(result["sortino"],       4)
                row["calmar"]       = round(result["calmar"],        4)
                row["max_drawdown"] = round(result["max_drawdown"],  2)
                row["dsr"]          = round(result["dsr"],           4)
                row["n_ticks"]      = result["n_ticks"]

                print(
                    f"         total={row['total_pnl']:>10,.0f}  "
                    f"hgp={row['hgp_pnl']:>9,.0f}  "
                    f"sharpe={row['sharpe']:>7.4f}  "
                    f"dsr={row['dsr']:.4f}"
                )

            except Exception as e:
                row["error"]        = str(e)[:300]
                row["total_pnl"]    = ""
                row["hgp_pnl"]      = ""
                row["sharpe"]       = ""
                row["sortino"]      = ""
                row["calmar"]       = ""
                row["max_drawdown"] = ""
                row["dsr"]          = ""
                row["n_ticks"]      = ""
                print(f"  ERROR: {row['error'][:200]}")

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

            writer.writerow(row)
            csvfile.flush()

    print()
    print(f"Sweep complete. Results: {out_csv}")
    return str(out_csv)


if __name__ == "__main__":
    main()
