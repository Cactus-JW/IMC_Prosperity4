# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a trading strategy repository for the **IMC Prosperity 2026 Challenge**. The goal is to implement algorithmic trading strategies that run on the IMC exchange simulator.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Mac/Linux
# .venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

## Running Code

```bash
# Syntax-check a strategy file
python -m py_compile "B-Strategy and Testing/Round 1/StrategyRound1Final.py"

# Run backtest (round 1 all days)
prosperity4btx "B-Strategy and Testing/Round 1/StrategyRound1Final.py" 1

# Compute risk metrics from a backtest log
python "B-Strategy and Testing/compute_bt_metrics.py" "backtests/<timestamp>.log"
```

## Architecture

### Competition Interface

The IMC exchange calls `Trader.run(state: TradingState)` on every tick. The method must return a tuple of:
- `dict[Symbol, list[Order]]` — orders to place per product
- `int` — number of conversions requested
- `str` — arbitrary trader state string (persisted across ticks via `state.traderData`)

Strategy files must only import from `datamodel.py` and the standard library — no third-party packages are available on the exchange.

### Key Files

- [B-Strategy and Testing/datamodel.py](B-Strategy%20and%20Testing/datamodel.py) — Official competition data model (from Appendix B of the competition wiki). Contains `TradingState`, `OrderDepth`, `Order`, `Trade`, `Observation`, `ConversionObservation`, etc. Do not modify this file; it must match what the exchange provides.
- [B-Strategy and Testing/Round 1/StrategyRound1Final.py](B-Strategy%20and%20Testing/Round%201/StrategyRound1Final.py) — Current strategy submission. The `Trader` class here is the submission entrypoint.
- [B-Strategy and Testing/compute_bt_metrics.py](B-Strategy%20and%20Testing/compute_bt_metrics.py) — Computes Sharpe, Sortino, Calmar, drawdown from a backtest log file.

### TradingState fields

```
traderData     # str — persisted from previous tick's return value
timestamp      # int — current market time
listings       # dict[Symbol, Listing]
order_depths   # dict[Symbol, OrderDepth] — live bids/asks
own_trades     # dict[Symbol, list[Trade]] — your trades this tick
market_trades  # dict[Symbol, list[Trade]] — all market trades this tick
position       # dict[Product, int] — current holdings
observations   # Observation — market observations including ConversionObservations
```

### OrderDepth sign convention

`order_depth.buy_orders` maps `price → quantity` where quantity is **positive** (e.g. `{9: 5}` = someone wants to buy 5 at price 9).  
`order_depth.sell_orders` maps `price → quantity` where quantity is **negative** (e.g. `{11: -3}` = someone wants to sell 3 at price 11).

When placing your own orders, use a **positive** quantity for a buy order and a **negative** quantity for a sell order.

### ConversionObservation fields

Available via `state.observations.conversionObservations[product]`:
```
bidPrice, askPrice     # external market prices
transportFees          # cost per unit to convert
exportTariff           # tariff applied when exporting
importTariff           # tariff applied when importing
sugarPrice             # commodity input price
sunlightIndex          # environmental factor
```

### Strategy pattern

Each product is handled by an `elif product == "<PRODUCT>":` branch inside `Trader.run()`. The branch computes a `fair_value` and posts limit orders around it. Products with no defined fair value are skipped via `continue`. Cross-tick state is serialized with `json` (not `jsonpickle` — unavailable on the exchange).

### Data Files

`A-Historical Data/Round N/` contains historical CSV data (semicolon-delimited). Files follow the naming convention `prices_round_N_day_M.csv` and `trades_round_N_day_M.csv`. The prices CSVs include columns `bid_price_1..3`, `bid_volume_1..3`, `ask_price_1..3`, `ask_volume_1..3`, and `mid_price`.

### Output Log Files

Live submission logs are saved under `D-Output logs/Round N/` as a `.log` and a `.json` file per run.

**`.log` file keys:**

| Key | Contents |
|---|---|
| `submissionId` | Unique ID for the run |
| `activitiesLog` | Semicolon-delimited CSV — order book snapshots per tick (bid/ask prices & volumes, mid price, running P&L) |
| `logs` | Array of per-tick objects: `{timestamp, sandboxLog, lambdaLog}` — `lambdaLog` contains all `print()` output from your strategy |
| `tradeHistory` | Array of every trade that executed during the run |

**`.json` file keys:**

| Key | Contents |
|---|---|
| `activitiesLog` | Same as in `.log` |
| `graphLog` | Semicolon-delimited `timestamp;value` — cumulative P&L over time |
| `positions` | Final position per product at end of run, e.g. `[{"symbol": "EMERALDS", "quantity": -16}]` |
| `profit` | Total profit for the run |
| `status` | `"FINISHED"` when complete |

#### Distinguishing your trades from bot trades

Each entry in `tradeHistory` has `buyer` and `seller` fields:
- **Your trade** → `buyer` or `seller` is `"SUBMISSION"`
- **Bot-only trade** → both `buyer` and `seller` are `""` (empty string)

```json
{"timestamp": 5900, "buyer": "", "seller": "SUBMISSION", "symbol": "EMERALDS", "price": 10006.0, "quantity": 8}
// You sold 8 EMERALDS at 10006 (your ask was hit)

{"timestamp": 2900, "buyer": "", "seller": "", "symbol": "TOMATOES", "price": 4997.0, "quantity": 3}
// Bot-to-bot trade — you were not involved
```

#### Position tracking

There is no per-tick position in the log files. The `.json` file only has the **final** position under `positions`. To track position per tick, either:
- Reconstruct it by accumulating your trades from `tradeHistory`, or
- Add `print(state.position)` in your strategy so it appears in each tick's `lambdaLog`

## Team Workflow

Each team member works on their own branch (named by initials). Do not push directly to `main` without discussing with the team first.

## Agent pipeline

Run the full pipeline with:
```
/pipeline <PRODUCT> <ROUND>
```
Example: `/pipeline SQUID_INK 1`

The pipeline orchestrates all agents automatically. You can also invoke agents individually for targeted tasks.

### Agents and what they produce

| Agent | Invoke when | Output |
|---|---|---|
| **data-explorer** | Investigating a product before strategy design, or after surprising results | `A-Historical Data/analysis/<PRODUCT>.md` |
| **strategy-critic** | Runs automatically in the pipeline (ANALYSIS_REVIEW mode) to gate data-explorer output quality | `A-Historical Data/analysis/<PRODUCT>_verdict.md` |
| **strategy-writer** | After data-explorer has produced an analysis file | Edits `B-Strategy and Testing/Round N/StrategyRound<N>Final.py` |
| **backtest-runner** | After any non-trivial strategy change | `backtests/<timestamp>.log` |
| **param-sweeper** | Once a strategy is directionally right and needs tuning | `sweeps/<timestamp>.csv` |
| **discrepancy-investigator** | When backtest has red flags, or when live PnL diverges meaningfully from backtest | Report only |
| **experiment-logger** | Automatically at the end of each pipeline run | Appends to `A-Historical Data/analysis/experiments.md` |

### Pipeline phases

```
Phase 1 : data-explorer → strategy-critic loop (up to 3 iterations, ANALYSIS_REVIEW mode)
Phase 2 : strategy-writer  →  B-Strategy and Testing/Round N/StrategyRound<N>Final.py
Phase 3 : backtest-runner  →  backtests/<timestamp>.log
          └─ if red flags: discrepancy-investigator → HALT
Phase 4 : param-sweeper    →  sweeps/<timestamp>.csv
Phase 5 : experiment-logger → A-Historical Data/analysis/experiments.md
```

### Manual loop (without the pipeline skill)

1. `@data-explorer` to analyze the product → `A-Historical Data/analysis/<PRODUCT>.md`
2. `@strategy-writer` reads the analysis → edits the strategy file
3. `@backtest-runner` to verify
4. If results surprise: `@data-explorer` again to investigate
5. If tuning needed: `@param-sweeper` (background)
6. `@experiment-logger` to record the outcome
7. If live ≠ backtest: `@discrepancy-investigator`

## File map

| Path | Purpose |
|---|---|
| `B-Strategy and Testing/Round N/StrategyRound<N>Final.py` | Strategy submission — edited by strategy-writer |
| `B-Strategy and Testing/datamodel.py` | Competition data model — do not modify |
| `A-Historical Data/Round N/` | Historical price/trade CSVs for data-explorer |
| `D-Output logs/Round N/` | Live submission logs (.log and .json) |
| `backtests/` | Backtest logs, one timestamped file per run |
| `A-Historical Data/analysis/<PRODUCT>.md` | data-explorer output, strategy-writer input |
| `A-Historical Data/analysis/<PRODUCT>_verdict.md` | strategy-critic ANALYSIS_REVIEW verdict |
| `sweeps/` | Param sweep CSVs, one timestamped file per run |
| `A-Historical Data/analysis/experiments.md` | Running log of all pipeline runs |
| `MEMORY.md` | Shared agent memory — patterns and findings across runs |
| `.claude/agents/` | Agent definitions |
| `.claude/commands/pipeline.md` | Pipeline orchestrator skill (custom slash command) |
