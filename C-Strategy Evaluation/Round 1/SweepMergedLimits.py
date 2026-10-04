from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
ROOT_DIR = THIS_DIR.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from prosperity3bt.file_reader import PackageResourcesReader
from prosperity3bt.runner import run_backtest
from prosperity3bt.models import TradeMatchingMode


STRATEGY_PATH = ROOT_DIR / "Strategy and Testing" / "Round 1" / "StrategyMerged3.py"


@dataclass(frozen=True)
class SweepResult:
    market_gap: float
    total_profit: float
    ash_profit: float
    intarian_profit: float


def _load_strategy_module(strategy_path: Path):
    strategy_dir = str(strategy_path.parent)
    if strategy_dir not in sys.path:
        sys.path.insert(0, strategy_dir)

    module_name = f"_sweep_strategy_{strategy_path.stem}_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, strategy_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load strategy module from {strategy_path}")

    module = importlib.util.module_from_spec(spec)
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(module)
    return module


def _iter_gaps(start: float, stop: float, step: float):
    start_d = Decimal(str(start))
    stop_d = Decimal(str(stop))
    step_d = Decimal(str(step))

    current = start_d
    while current <= stop_d:
        yield float(current)
        current += step_d


def _run_single(market_gap: float, days: list[int]) -> SweepResult:
    module = _load_strategy_module(STRATEGY_PATH)

    module.ASH_MARKET_GAP = market_gap
    reader = PackageResourcesReader()

    total_profit = 0.0
    ash_profit = 0.0
    intarian_profit = 0.0

    for day in days:
        trader = module.Trader()
        result = run_backtest(
            trader,
            reader,
            round_num=1,
            day_num=day,
            print_output=False,
            trade_matching_mode=TradeMatchingMode.all,
            no_names=True,
            show_progress_bar=False,
        )

        day_profit = 0.0
        for row in reversed(result.activity_logs):
            if row.timestamp != result.activity_logs[-1].timestamp:
                break
            day_profit += float(row.columns[-1])

        total_profit += day_profit

        day_ash = 0.0
        day_intarian = 0.0
        for row in reversed(result.activity_logs):
            if row.timestamp != result.activity_logs[-1].timestamp:
                break
            product = row.columns[2]
            profit = float(row.columns[-1])
            if product == "ASH_COATED_OSMIUM":
                day_ash += profit
            elif product == "INTARIAN_PEPPER_ROOT":
                day_intarian += profit

        ash_profit += day_ash
        intarian_profit += day_intarian

    return SweepResult(
        market_gap=market_gap,
        total_profit=total_profit,
        ash_profit=ash_profit,
        intarian_profit=intarian_profit,
    )


def main() -> None:
    if not STRATEGY_PATH.exists():
        raise FileNotFoundError(f"Strategy file not found: {STRATEGY_PATH}")

    market_gaps = list(_iter_gaps(0.2, 10.0, 0.01))

    days = [-2, -1, 0]

    results: list[SweepResult] = []
    best: SweepResult | None = None

    print("Sweeping merged strategy market-making gap...")
    print("Gap range      : 0.20 to 10.00")
    print("Gap step       : 0.01")
    print(f"Sweep points   : {len(market_gaps)}")
    print(f"Days           : {days}")

    for market_gap in market_gaps:
        result = _run_single(market_gap, days)
        results.append(result)

        if best is None or result.total_profit > best.total_profit:
            best = result

        print(
            f"GAP={market_gap:>5.2f}  "
            f"profit={result.total_profit:>10.2f}  "
            f"ash={result.ash_profit:>10.2f}  intarian={result.intarian_profit:>10.2f}"
        )

    if best is None:
        print("No valid results produced.")
        return

    print()
    print("Best result:")
    print(f"  Market gap     : {best.market_gap:.2f}")
    print(f"  Total profit   : {best.total_profit:.2f}")
    print(f"  ASH profit     : {best.ash_profit:.2f}")
    print(f"  Intarian profit: {best.intarian_profit:.2f}")

    print()
    print("Top 10 results:")
    top_results = sorted(results, key=lambda r: r.total_profit, reverse=True)[:10]
    for idx, row in enumerate(top_results, start=1):
        print(
            f"  {idx:>2}. gap={row.market_gap:.2f} "
            f"total={row.total_profit:.2f} "
            f"ash={row.ash_profit:.2f} intarian={row.intarian_profit:.2f}"
        )


if __name__ == "__main__":
    main()