import csv
import io
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_DIR = os.path.join(BASE_DIR, "Round 1")

TRADES_COLUMNS = ["timestamp", "symbol", "buyer", "seller", "currency", "price", "quantity"]


def parse_market_data(json_path: str, out_path: str) -> int:
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)

    activities = data["activitiesLog"]
    rows = [row for row in activities.strip().split("\n") if row.strip()]

    reader = csv.reader(io.StringIO("\n".join(rows)), delimiter=";")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        written = 0
        for row in reader:
            writer.writerow(row)
            written += 1

    return written - 1  # exclude header


def parse_trades(log_path: str, out_path: str) -> int:
    with open(log_path, encoding="utf-8") as f:
        data = json.load(f)

    trade_history = data["tradeHistory"]

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TRADES_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for trade in trade_history:
            writer.writerow(trade)

    return len(trade_history)


if __name__ == "__main__":
    total_market = 0
    total_trades = 0

    for dirpath, _, filenames in os.walk(INPUT_DIR):
        stems = {
            os.path.splitext(f)[0]
            for f in filenames
            if f.endswith(".json")
        } & {
            os.path.splitext(f)[0]
            for f in filenames
            if f.endswith(".log")
        }

        for stem in sorted(stems):
            json_path  = os.path.join(dirpath, stem + ".json")
            log_path   = os.path.join(dirpath, stem + ".log")
            market_csv = os.path.join(dirpath, f"market_data_{stem}.csv")
            trades_csv = os.path.join(dirpath, f"trades_{stem}.csv")

            market_rows = parse_market_data(json_path, market_csv)
            print(f"  {market_csv}: {market_rows} rows")
            total_market += market_rows

            trade_rows = parse_trades(log_path, trades_csv)
            print(f"  {trades_csv}: {trade_rows} rows")
            total_trades += trade_rows

    print(f"\nDone. {total_market} market rows, {total_trades} trade rows total.")
