"""
Parameter sweep for INTARIAN_PEPPER_ROOT strategy.

Sweeps (primary phase):
  INTARIAN_MAKE_SPREAD  x  INTARIAN_BASE_SIZE  x  INTARIAN_SNIPE_CUSHION

Runs prosperity4btx for all 3 days of round 1 on each combination.
Saves results to sweeps/<timestamp>.csv.

The patched strategy is written into the same directory as the original
(B-Strategy and Testing/Round 1/) so that datamodel.py is co-located
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
STRATEGY_SRC = REPO_ROOT / "B-Strategy and Testing" / "Round 1" / "StrategyRound1Final.py"
STRATEGY_DIR = STRATEGY_SRC.parent          # temp files go here so datamodel.py is found
SWEEPS_DIR   = REPO_ROOT / "sweeps"
METRICS_SCRIPT = REPO_ROOT / "B-Strategy and Testing" / "compute_bt_metrics.py"

# ---------------------------------------------------------------------------
# Sweep grid
# ---------------------------------------------------------------------------
MAKE_SPREAD_VALS    = [1, 2, 3]
BASE_SIZE_VALS      = [10, 15, 20, 25]
SNIPE_CUSHION_VALS  = [0, 1, 2]

# Secondary params held at baseline during primary sweep
INV_SKEW_VALS       = [0.3]
FADE_THRESHOLD_VALS = [40]
FADE_SIZE_VALS      = [20]

GRID = list(itertools.product(
    MAKE_SPREAD_VALS,
    BASE_SIZE_VALS,
    SNIPE_CUSHION_VALS,
    INV_SKEW_VALS,
    FADE_THRESHOLD_VALS,
    FADE_SIZE_VALS,
))

PARAM_NAMES = [
    "make_spread",
    "base_size",
    "snipe_cushion",
    "inv_skew",
    "fade_threshold",
    "fade_size",
]

REPLACEMENT_MAP = {
    "make_spread":      "INTARIAN_MAKE_SPREAD",
    "base_size":        "INTARIAN_BASE_SIZE",
    "snipe_cushion":    "INTARIAN_SNIPE_CUSHION",
    "inv_skew":         "INTARIAN_INV_SKEW",
    "fade_threshold":   "INTARIAN_FADE_THRESHOLD",
    "fade_size":        "INTARIAN_FADE_SIZE",
}

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
    """Run prosperity4btx for round 1 (all 3 days). Raises on failure."""
    result = subprocess.run(
        ["prosperity4btx", strategy_path, "1", "--out", log_path],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=180,
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

    # Per-product final PnL from activities log
    with open(log_path, "r", encoding="utf-8") as f:
        raw = f.read()
    data = json.loads(raw)
    csv_text = data["activitiesLog"]

    import csv as csv_mod
    from io import StringIO
    reader = csv_mod.DictReader(StringIO(csv_text), delimiter=";")
    last_pnl = {}
    for row in reader:
        product = row["product"]
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
        "ipr_pnl":      last_pnl.get("INTARIAN_PEPPER_ROOT", float("nan")),
        "ash_pnl":      last_pnl.get("ASH_COATED_OSMIUM",    float("nan")),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"Strategy source : {STRATEGY_SRC}")
    print(f"Sweep dimensions: {PARAM_NAMES[:3]}  (secondary held at baseline)")
    print(f"Grid size       : {len(GRID)} combinations")
    print()

    src_text = STRATEGY_SRC.read_text(encoding="utf-8")
    metrics_mod = load_metrics_module()

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_csv = SWEEPS_DIR / f"{timestamp}.csv"

    fieldnames = PARAM_NAMES + [
        "total_pnl", "ipr_pnl", "ash_pnl",
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

            # Write patched strategy alongside datamodel.py
            tmp_strategy = None
            log_path = str(SWEEPS_DIR / f"_tmp_{i}.log")
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
                row["ipr_pnl"]      = round(result["ipr_pnl"],      2)
                row["ash_pnl"]      = round(result["ash_pnl"],      2)
                row["sharpe"]       = round(result["sharpe"],        4)
                row["sortino"]      = round(result["sortino"],       4)
                row["calmar"]       = round(result["calmar"],        4)
                row["max_drawdown"] = round(result["max_drawdown"],  2)
                row["dsr"]          = round(result["dsr"],           4)
                row["n_ticks"]      = result["n_ticks"]

                print(
                    f"         total={row['total_pnl']:>10,.0f}  "
                    f"ipr={row['ipr_pnl']:>9,.0f}  "
                    f"sharpe={row['sharpe']:>7.4f}  "
                    f"dsr={row['dsr']:.4f}"
                )

            except Exception as e:
                row["error"]        = str(e)[:300]
                row["total_pnl"]    = ""
                row["ipr_pnl"]      = ""
                row["ash_pnl"]      = ""
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

            writer.writerow(row)
            csvfile.flush()

    print()
    print(f"Sweep complete. Results: {out_csv}")
    return str(out_csv)


if __name__ == "__main__":
    main()
