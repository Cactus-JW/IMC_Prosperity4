"""
Parameter sweep for VelvetMM-MRv3.py (Round 3).

Sweeps two products independently:
  - VELVETFRUIT_EXTRACT: mr_z_threshold x mr_agg_z x mm_half_spread
  - HYDROGEL_PACK:       mr_z_threshold x mr_agg_z x mm_half_spread

All other parameters are held at their baseline values during each product's
sweep. The sweep patches the CONFIGS dict in-place for each combination.

Saves results to sweeps/<timestamp>.csv.
"""

import copy
import csv
import itertools
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
STRATEGY_SRC = REPO_ROOT / "B-Strategy and Testing" / "Round 3" / "VelvetMM-MRv3.py"
STRATEGY_DIR = STRATEGY_SRC.parent   # temp files land here so imports resolve
SWEEPS_DIR   = REPO_ROOT / "sweeps"

# ---------------------------------------------------------------------------
# Baselines (must match the CONFIGS dict in the strategy file)
# ---------------------------------------------------------------------------
VFE_BASELINE = dict(
    pos_limit=200, initial_ema=5250.0, sigma=15.63, ema_span=500,
    fair_mode="ema_micro", micro_weight=0.20,
    mm_half_spread=2, mm_volume=15, inv_skew_price=0.02,
    mr_z_threshold=0.75, mr_agg_z=2.0, mr_volume=20, mr_agg_volume=10,
    min_inner_vol=10, mr_buy=True, mr_sell=True,
)

HGP_BASELINE = dict(
    pos_limit=200, initial_ema=10000.0, sigma=31.94, ema_span=2000,
    fair_mode="l2_vwap", micro_weight=0.0,
    mm_half_spread=7, mm_volume=12, inv_skew_price=0.04,
    mr_z_threshold=0.50, mr_agg_z=0.94, mr_volume=15, mr_agg_volume=10,
    min_inner_vol=4, mr_buy=True, mr_sell=True,
)

# ---------------------------------------------------------------------------
# Sweep grids
# ---------------------------------------------------------------------------
VFE_GRID_SPEC = {
    "mr_z_threshold": [0.50, 0.75, 1.00, 1.25],
    "mr_agg_z":       [1.5,  2.0,  2.5],
    "mm_half_spread": [1,    2,    3],
}

HGP_GRID_SPEC = {
    "mr_z_threshold": [0.40, 0.50, 0.65, 0.80],
    "mr_agg_z":       [0.75, 0.94, 1.10, 1.25],
    "mm_half_spread": [6,    7,    8],
}

# Only combos where mr_z_threshold < mr_agg_z are valid; filter later.

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_combos(spec: dict, baseline: dict):
    """Return list of (product_cfg_dict, swept_params_dict)."""
    keys   = list(spec.keys())
    values = [spec[k] for k in keys]
    combos = []
    for vals in itertools.product(*values):
        params = dict(zip(keys, vals))
        # Skip invalid: mr_z_threshold must be strictly below mr_agg_z
        mrt = params.get("mr_z_threshold", baseline["mr_z_threshold"])
        mra = params.get("mr_agg_z",       baseline["mr_agg_z"])
        if mrt >= mra:
            continue
        cfg = dict(baseline)
        cfg.update(params)
        combos.append((cfg, params))
    return combos


def _cfg_to_src(product: str, cfg: dict) -> str:
    """Render a single product's config dict as source text."""
    def _fmt(v):
        if isinstance(v, bool):
            return "True" if v else "False"
        if isinstance(v, str):
            return f'"{v}"'
        return repr(v)

    lines = [f'    "{product}": dict(']
    for k, v in cfg.items():
        lines.append(f"        {k}={_fmt(v)},")
    lines.append("    ),")
    return "\n".join(lines)


def patch_strategy(src_text: str, product: str, cfg: dict) -> str:
    """
    Replace the CONFIGS entry for `product` in the source text.
    Matches the block from '    "<product>": dict(' up to the closing '),'.
    """
    pattern = re.compile(
        r'    "' + re.escape(product) + r'": dict\(.*?\n    \),',
        re.DOTALL
    )
    replacement = _cfg_to_src(product, cfg)
    patched, n = pattern.subn(replacement, src_text)
    if n == 0:
        raise ValueError(f"Could not find CONFIGS entry for {product}")
    return patched


def run_backtest(strategy_path: str) -> str:
    """Run prosperity4btx for round 3 (all 3 days). Returns combined stdout+stderr."""
    result = subprocess.run(
        ["prosperity4btx", strategy_path, "3"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=300,
    )
    output = result.stdout + "\n" + result.stderr
    if result.returncode != 0:
        raise RuntimeError(
            f"Backtest failed (rc={result.returncode}): {output[-600:]}"
        )
    return output


def parse_total_profit(output: str) -> float:
    """
    Parse 'Total profit: X,XXX' from the backtest output.
    Returns the final 3-day aggregate.
    """
    # "Total profit: 378,854" – find the LAST occurrence (the combined summary)
    matches = re.findall(r"Total profit:\s*([\-\d,]+)", output)
    if not matches:
        raise ValueError("Could not find 'Total profit' in backtest output")
    return float(matches[-1].replace(",", ""))


def parse_per_day_profit(output: str) -> dict:
    """
    Extract per-day profits from lines like:
        Round 3 day 0: 178,013
        Round 3 day 1: 201,806
        Round 3 day 2: -964
    Returns {0: float, 1: float, 2: float}.
    """
    result = {}
    for m in re.finditer(r"Round 3 day (\d):\s*([\-\d,]+)", output):
        day = int(m.group(1))
        pnl = float(m.group(2).replace(",", ""))
        result[day] = pnl
    return result


def parse_per_product_profit(output: str, product: str) -> float:
    """
    Extract per-product profit from lines like '  VELVETFRUIT_EXTRACT: 12,345'.
    Returns the LAST occurrence (final day block, which has cumulative per-day).
    """
    pattern = re.compile(
        r"^" + re.escape(product) + r":\s*([\-\d,]+)",
        re.MULTILINE
    )
    matches = pattern.findall(output)
    if not matches:
        return float("nan")
    # Sum across all day-blocks? Actually each day block shows that day's profit.
    # Sum them.
    return sum(float(v.replace(",", "")) for v in matches)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    src_text = STRATEGY_SRC.read_text(encoding="utf-8")

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_csv   = SWEEPS_DIR / f"r3v3_{timestamp}.csv"

    # Build combo lists
    vfe_combos = _build_combos(VFE_GRID_SPEC, VFE_BASELINE)
    hgp_combos = _build_combos(HGP_GRID_SPEC, HGP_BASELINE)

    total_combos = len(vfe_combos) + len(hgp_combos)

    print(f"Strategy       : {STRATEGY_SRC}")
    print(f"VFE combos     : {len(vfe_combos)}")
    print(f"HGP combos     : {len(hgp_combos)}")
    print(f"Total combos   : {total_combos}")
    print(f"Output CSV     : {out_csv}")
    print()

    fieldnames = [
        "combo_id", "product_swept",
        "mr_z_threshold", "mr_agg_z", "mm_half_spread",
        "total_pnl", "day0_pnl", "day1_pnl", "day2_pnl",
        "vfe_pnl", "hgp_pnl",
        "error",
    ]

    with open(out_csv, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        combo_id = 0
        all_sweeps = [
            ("VELVETFRUIT_EXTRACT", vfe_combos),
            ("HYDROGEL_PACK",       hgp_combos),
        ]

        for product, combos in all_sweeps:
            print(f"=== Sweeping {product} ({len(combos)} combos) ===")
            for cfg, swept in combos:
                combo_id += 1
                label = "  ".join(f"{k}={v}" for k, v in swept.items())
                print(f"[{combo_id:3d}/{total_combos}] {product[:4]}  {label}", end="  ", flush=True)

                row = {
                    "combo_id":        combo_id,
                    "product_swept":   product,
                    "mr_z_threshold":  swept.get("mr_z_threshold"),
                    "mr_agg_z":        swept.get("mr_agg_z"),
                    "mm_half_spread":  swept.get("mm_half_spread"),
                    "error":           "",
                }

                try:
                    patched = patch_strategy(src_text, product, cfg)

                    with tempfile.NamedTemporaryFile(
                        mode="w",
                        suffix=".py",
                        delete=False,
                        dir=str(STRATEGY_DIR),
                        encoding="utf-8",
                    ) as tmp:
                        tmp.write(patched)
                        tmp_path = tmp.name

                    try:
                        output = run_backtest(tmp_path)
                    finally:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass

                    total_pnl = parse_total_profit(output)
                    day_pnls  = parse_per_day_profit(output)
                    vfe_pnl   = parse_per_product_profit(output, "VELVETFRUIT_EXTRACT")
                    hgp_pnl   = parse_per_product_profit(output, "HYDROGEL_PACK")

                    row["total_pnl"] = total_pnl
                    row["day0_pnl"]  = day_pnls.get(0, float("nan"))
                    row["day1_pnl"]  = day_pnls.get(1, float("nan"))
                    row["day2_pnl"]  = day_pnls.get(2, float("nan"))
                    row["vfe_pnl"]   = vfe_pnl
                    row["hgp_pnl"]   = hgp_pnl

                    print(f"total={total_pnl:>10,.0f}  vfe={vfe_pnl:>8,.0f}  hgp={hgp_pnl:>8,.0f}")

                except Exception as e:
                    row["error"]     = str(e)[:400]
                    row["total_pnl"] = ""
                    row["day0_pnl"]  = ""
                    row["day1_pnl"]  = ""
                    row["day2_pnl"]  = ""
                    row["vfe_pnl"]   = ""
                    row["hgp_pnl"]   = ""
                    print(f"ERROR: {str(e)[:100]}")

                writer.writerow(row)
                csvfile.flush()

    print()
    print(f"Sweep complete. Results saved to: {out_csv}")
    return str(out_csv)


if __name__ == "__main__":
    main()
