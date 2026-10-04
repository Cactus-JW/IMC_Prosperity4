import json
import sys
import uuid
from collections import defaultdict
from datetime import datetime
from functools import reduce
from importlib import import_module, metadata, reload
from pathlib import Path
from typing import Annotated, Any, Optional

from typer import Argument, Option, Typer

from prosperity4bt.data import has_day_data
from prosperity4bt.file_reader import FileReader, FileSystemReader, PackageResourcesReader
from prosperity4bt.datamodel import Trade
from prosperity4bt.models import BacktestResult, TradeMatchingMode, TradeRow
from prosperity4bt.open import open_visualizer
from prosperity4bt.runner import run_backtest


def parse_algorithm(algorithm: Path) -> Any:
    sys.path.append(str(algorithm.parent))
    return import_module(algorithm.stem)


def parse_data(data_root: Optional[Path]) -> FileReader:
    if data_root is not None:
        return FileSystemReader(data_root)
    else:
        return PackageResourcesReader()


def parse_days(file_reader: FileReader, days: list[str]) -> list[tuple[int, int]]:
    parsed_days = []

    for arg in days:
        if "-" in arg:
            round_num, day_num = map(int, arg.split("-", 1))

            if not has_day_data(file_reader, round_num, day_num):
                print(f"Warning: no data found for round {round_num} day {day_num}")
                continue

            parsed_days.append((round_num, day_num))
        else:
            round_num = int(arg)

            parsed_days_in_round = []
            for day_num in range(-5, 100):
                if has_day_data(file_reader, round_num, day_num):
                    parsed_days_in_round.append((round_num, day_num))

            if len(parsed_days_in_round) == 0:
                print(f"Warning: no data found for round {round_num}")
                continue

            parsed_days.extend(parsed_days_in_round)

    if len(parsed_days) == 0:
        print("Error: did not find data for any requested round/day")
        sys.exit(1)

    return parsed_days


def parse_out(out: Optional[Path], no_out: bool) -> Optional[Path]:
    if out is not None:
        return out

    if no_out:
        return None

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return Path.cwd() / "backtests" / f"{timestamp}.log"


def print_day_summary(result: BacktestResult) -> None:
    last_timestamp = result.activity_logs[-1].timestamp

    product_lines = []
    total_profit = 0

    for row in reversed(result.activity_logs):
        if row.timestamp != last_timestamp:
            break

        product = row.columns[2]
        profit = row.columns[-1]

        product_lines.append(f"{product}: {profit:,.0f}")
        total_profit += profit

    print(*reversed(product_lines), sep="\n")
    print(f"Total profit: {total_profit:,.0f}")


def compute_final_positions(trades: list[TradeRow]) -> dict[str, int]:
    """Compute net SUBMISSION position from a day's trade list."""
    positions: dict[str, int] = defaultdict(int)
    for row in trades:
        t = row.trade
        if t.buyer == "SUBMISSION":
            positions[t.symbol] += t.quantity
        elif t.seller == "SUBMISSION":
            positions[t.symbol] -= t.quantity
    return dict(positions)


def merge_results(
    a: BacktestResult, b: BacktestResult, merge_profit_loss: bool
) -> BacktestResult:
    sandbox_logs = a.sandbox_logs[:]
    activity_logs = a.activity_logs[:]
    trades = a.trades[:]

    # Sandbox logs and trades are already pre-offset by day_num * 1_000_000 before
    # this function is called, so no additional timestamp shifting is needed here.
    sandbox_logs.extend(b.sandbox_logs)
    trades.extend(b.trades)

    if merge_profit_loss:
        # Identify the last (day, timestamp) pair in a to read PnL offsets from.
        # Both day and timestamp are needed because all days share timestamps 0-999_900.
        last_row = a.activity_logs[-1]
        last_day = last_row.columns[0]
        last_ts = last_row.columns[1]
        profit_loss_offsets: dict[str, float] = defaultdict(float)
        for row in reversed(a.activity_logs):
            if row.columns[0] != last_day or row.columns[1] != last_ts:
                break
            profit_loss_offsets[row.columns[2]] = row.columns[-1]

        activity_logs.extend(
            [row.with_offset(0, profit_loss_offsets[row.columns[2]]) for row in b.activity_logs]
        )
    else:
        activity_logs.extend(b.activity_logs)

    return BacktestResult(a.round_num, a.day_num, sandbox_logs, activity_logs, trades)


def write_output(output_file: Path, merged_results: BacktestResult) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)

    csv_header = "day;timestamp;product;bid_price_1;bid_volume_1;bid_price_2;bid_volume_2;bid_price_3;bid_volume_3;ask_price_1;ask_volume_1;ask_price_2;ask_volume_2;ask_price_3;ask_volume_3;mid_price;profit_and_loss"
    activities_log = csv_header + "\n" + "\n".join(map(str, merged_results.activity_logs))

    logs = [
        {"timestamp": row.timestamp, "sandboxLog": row.sandbox_log, "lambdaLog": row.lambda_log}
        for row in merged_results.sandbox_logs
    ]

    trade_history = [
        {
            "timestamp": row.trade.timestamp,
            "buyer": row.trade.buyer,
            "seller": row.trade.seller,
            "symbol": row.trade.symbol,
            "currency": "XIRECS",
            "price": row.trade.price,
            "quantity": row.trade.quantity,
        }
        for row in merged_results.trades
    ]

    output = {
        "submissionId": str(uuid.uuid4()),
        "activitiesLog": activities_log,
        "logs": logs,
        "tradeHistory": trade_history,
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w+", encoding="utf-8") as file:
        json.dump(output, file)


def print_overall_summary(results: list[BacktestResult]) -> None:
    print("Profit summary:")

    total_profit = 0
    for result in results:
        last_timestamp = result.activity_logs[-1].timestamp

        profit = 0
        for row in reversed(result.activity_logs):
            if row.timestamp != last_timestamp:
                break

            profit += row.columns[-1]

        print(f"Round {result.round_num} day {result.day_num}: {profit:,.0f}")
        total_profit += profit

    print(f"Total profit: {total_profit:,.0f}")


def format_path(path: Path) -> str:
    cwd = Path.cwd()
    if path.is_relative_to(cwd):
        return str(path.relative_to(cwd))
    else:
        return str(path)


def version_callback(value: bool) -> None:
    if value:
        print(f"prosperity4btx {metadata.version('prosperity4btx')}")
        sys.exit(0)


app = Typer(context_settings={"help_option_names": ["--help", "-h"]})


@app.command()
def cli(
    algorithm: Annotated[Path, Argument(help="Path to the Python file containing the algorithm to backtest.", show_default=False, exists=True, file_okay=True, dir_okay=False, resolve_path=True)],
    days: Annotated[list[str], Argument(help="The days to backtest on. <round>-<day> for a single day, <round> for all days in a round.", show_default=False)],
    merge_pnl: Annotated[bool, Option("--merge-pnl/--no-merge-pnl", help="Merge profit and loss across days.")] = False,
    vis: Annotated[bool, Option("--vis", help="Open backtest results in https://jmerle.github.io/imc-prosperity-3-visualizer/ when done.")] = False,
    out: Annotated[Optional[Path], Option(help="File to save output log to (defaults to backtests/<timestamp>.log).", show_default=False, dir_okay=False, resolve_path=True)] = None,
    no_out: Annotated[bool, Option("--no-out", help="Skip saving output log.")] = False,
    data: Annotated[Optional[Path], Option(help="Path to data directory. Must look similar in structure to https://github.com/Xeeshan85/imc-prosperity-4-backtester/tree/main/prosperity4bt/resources.", show_default=False, exists=True, file_okay=False, dir_okay=True, resolve_path=True)] = None,
    print_output: Annotated[bool, Option("--print", help="Print the trader's output to stdout while it's running.")] = False,
    match_trades: Annotated[TradeMatchingMode, Option(help="How to match orders against market trades. 'all' matches trades with prices equal to or worse than your quotes, 'worse' matches trades with prices worse than your quotes, 'none' does not match trades against orders at all.")] = TradeMatchingMode.all,
    no_progress: Annotated[bool, Option("--no-progress", help="Don't show progress bars.")] = False,
    original_timestamps: Annotated[bool, Option("--original-timestamps", help="Preserve original timestamps in output log rather than making them increase across days.")] = False,
    version: Annotated[bool, Option("--version", "-v", help="Show the program's version number and exit.", is_eager=True, callback=version_callback)] = False,
) -> None:  # fmt: skip
    if out is not None and no_out:
        print("Error: --out and --no-out are mutually exclusive")
        sys.exit(1)

    try:
        trader_module = parse_algorithm(algorithm)
    except ModuleNotFoundError as e:
        print(f"{algorithm} is not a valid algorithm file: {e}")
        sys.exit(1)

    if not hasattr(trader_module, "Trader"):
        print(f"{algorithm} does not expose a Trader class")
        sys.exit(1)

    file_reader = parse_data(data)
    parsed_days = parse_days(file_reader, days)
    output_file = parse_out(out, no_out)

    show_progress_bars = not no_progress and not print_output

    results = []
    for round_num, day_num in parsed_days:
        print(f"Backtesting {algorithm} on round {round_num} day {day_num}")

        reload(trader_module)

        result = run_backtest(
            trader_module.Trader(),
            file_reader,
            round_num,
            day_num,
            print_output,
            match_trades,
            True,
            show_progress_bars,
        )

        print_day_summary(result)
        if len(parsed_days) > 1:
            print()

        if not original_timestamps:
            # Offset sandbox logs and trades to global timestamps (day_num * 1_000_000 + tick).
            # Activity logs keep their original per-day timestamps because the visualiser
            # already computes global x as day * 1_000_000 + timestamp using the day column.
            day_offset = day_num * 1_000_000
            result.sandbox_logs = [row.with_offset(day_offset) for row in result.sandbox_logs]
            result.trades = [row.with_offset(day_offset) for row in result.trades]

        results.append(result)

    if len(parsed_days) > 1:
        print_overall_summary(results)

    if output_file is not None:
        # Inject position-reset trades at day boundaries so the visualiser's position
        # chart reflects the per-day reset rather than accumulating across days.
        if not original_timestamps and len(results) > 1:
            for i in range(len(results) - 1):
                final_pos = compute_final_positions(results[i].trades)
                reset_ts = results[i].day_num * 1_000_000 + 999_950
                for symbol, pos in final_pos.items():
                    if pos > 0:
                        results[i].trades.append(
                            TradeRow(Trade(symbol, 0, pos, "", "SUBMISSION", reset_ts))
                        )
                    elif pos < 0:
                        results[i].trades.append(
                            TradeRow(Trade(symbol, 0, abs(pos), "SUBMISSION", "", reset_ts))
                        )

        merged_results = reduce(lambda a, b: merge_results(a, b, merge_pnl), results)
        write_output(output_file, merged_results)
        print(f"\nSuccessfully saved backtest results to {format_path(output_file)}")

    if vis and output_file is not None:
        open_visualizer(output_file)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
