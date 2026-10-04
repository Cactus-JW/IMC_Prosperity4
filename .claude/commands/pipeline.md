---
name: pipeline
description: Runs the full strategy development pipeline for a product. Invoke as /pipeline <PRODUCT> [round]. Orchestrates: pre-flight check → data-explorer/critic loop → strategy-writer → backtest → discrepancy check → param-sweep → experiment-logger.
---

You are executing the full strategy development pipeline. Follow every phase in order. Do not skip steps. Do not proceed past a STOP instruction.

## Parse arguments and define all file paths

The user invoked this skill with `/pipeline <PRODUCT> [round]`.

Set these variables once — every agent prompt below must use these exact values:

```
PRODUCT        = <product name, e.g. SQUID_INK>
ROUND          = <round number, e.g. 1>   — if not provided, ask the user before continuing
DATA_DIR       = A-Historical Data/Round <ROUND>
ANALYSIS_FILE  = A-Historical Data/analysis/<PRODUCT>.md
VERDICT_FILE   = A-Historical Data/analysis/<PRODUCT>_verdict.md
STRATEGY_FILE  = B-Strategy and Testing/Round <ROUND>/StrategyRound<ROUND>Final.py
MAX_ITER       = 3
iter           = 0
prior_feedback = ""
backtest_log   = ""   (will be filled in after Phase 3)
```

---

## Phase 0: Pre-flight check

Before spawning any agents, verify that data exists for this product and round. Do this yourself using your tools — do not spawn an agent for this.

**Step 0a — check round data exists:**

Use Glob to search for `A-Historical Data/Round <ROUND>/prices_round_*.csv`.

- If no files are found: **STOP.** Tell the user:
  ```
  Pre-flight failed: no price data found for Round <ROUND> in A-Historical Data/Round <ROUND>/.
  Check that the data files have been added before running the pipeline.
  ```

**Step 0b — check product appears in the data:**

Use Grep to search for `<PRODUCT>` (case-sensitive) in `A-Historical Data/Round <ROUND>/`.

- If no matches are found: **STOP.** Tell the user:
  ```
  Pre-flight failed: product <PRODUCT> was not found in any Round <ROUND> data file.
  Available products can be found by searching A-Historical Data/Round <ROUND>/.
  ```

**If both checks pass**, announce:
```
Pre-flight passed for <PRODUCT> (Round <ROUND>).
ANALYSIS_FILE  : <ANALYSIS_FILE>
STRATEGY_FILE  : <STRATEGY_FILE>
Starting Phase 1: Explorer–Critic loop (up to <MAX_ITER> iterations).
```

---

## Phase 1: Explorer–Critic Loop

### Step 1a — Run data-explorer

Spawn the **data-explorer** agent.

First-iteration prompt:
> Analyze product <PRODUCT> for Round <ROUND>.
> Write your findings to `<ANALYSIS_FILE>`.

Subsequent-iteration prompt (when `prior_feedback` is non-empty):
> Analyze product <PRODUCT> for Round <ROUND>.
> Write your findings to `<ANALYSIS_FILE>`.
>
> IMPORTANT — the previous analysis was rejected. You must address every gap below before this analysis will pass review. Be specific: provide numbers, concrete model names, and exact formulas — not vague descriptions.
>
> Gaps to address:
> <prior_feedback>

Wait for the agent to complete before continuing.

### Step 1b — Run strategy-critic in ANALYSIS_REVIEW mode

Spawn the **strategy-critic** agent:

> ANALYSIS_REVIEW mode for <PRODUCT>.
> Read `<ANALYSIS_FILE>`.
> Evaluate whether the analysis is sufficient quality to write a production-quality strategy.
> Write your verdict to `<VERDICT_FILE>`.

Wait for the agent to complete before continuing.

### Step 1c — Read verdict and decide

Read `<VERDICT_FILE>`.

- If it starts with `VERDICT: PASS` → tell the user "Critic accepted after <iter+1> iteration(s)." Proceed to Phase 2.
- If it starts with `VERDICT: REVISE` and `iter < MAX_ITER - 1`:
  - Extract the FEEDBACK section. Store it in `prior_feedback`.
  - Increment `iter`.
  - Tell the user: "Critic returned REVISE (iteration <iter>/<MAX_ITER>). Feeding back:\n<prior_feedback>"
  - Return to Step 1a.
- If `iter >= MAX_ITER - 1`:
  - Tell the user: "Reached <MAX_ITER> iterations without PASS. Proceeding with best available analysis — review `<VERDICT_FILE>` manually if results look weak."
  - Proceed to Phase 2.

---

## Phase 2: Strategy Writer

Announce: "Phase 2: Writing strategy → `<STRATEGY_FILE>`"

Spawn the **strategy-writer** agent:

> Write the strategy for product <PRODUCT> based on `<ANALYSIS_FILE>`.
> STRATEGY_FILE: <STRATEGY_FILE>
>
> Read `<STRATEGY_FILE>` to understand the existing product structure, then add or update the <PRODUCT> logic in that same file. Do not create a new file.

Wait for completion before continuing.

---

## Phase 3: Backtest

Announce: "Phase 3: Backtesting `<STRATEGY_FILE>` for Round <ROUND>."

Spawn the **backtest-runner** agent:

> Run the backtest for round <ROUND> using this strategy file:
> `<STRATEGY_FILE>`

Wait for completion. From the agent's output:
- Read the `Backtest log:` bullet and store the path as `backtest_log`
- Store the full summary as `backtest_summary`

**Evaluate for red flags** — any of the following counts:
- Runtime errors or Python exceptions
- Position limit violations on any product > 5 occurrences
- Total PnL is strongly negative

**If red flags exist:**

Announce: "Phase 3: red flags detected — running discrepancy-investigator. Pipeline will halt."

Spawn the **discrepancy-investigator** agent:

> Investigate backtest anomalies for a pre-submission sanity check (no live log yet — focus on internal backtest consistency and strategy bugs).
> STRATEGY_FILE: <STRATEGY_FILE>
> BACKTEST_LOG: <backtest_log>
>
> Red flags to investigate:
> <description of each red flag from backtest_summary>

Wait for completion. Report all findings to the user.

**STOP.** Tell the user:
```
Pipeline halted at Phase 3.
Fix the issues above, then re-run: /pipeline <PRODUCT> <ROUND>
```

Do not proceed to Phase 4.

**If no red flags:**

Tell the user: "Backtest healthy. Proceeding to Phase 4."

---

## Phase 4: Parameter Sweep

Announce: "Phase 4: Running param-sweeper (waiting for results before logging)."

Spawn the **param-sweeper** agent:

> Sweep parameters for the <PRODUCT> strategy.
> STRATEGY_FILE: <STRATEGY_FILE>
>
> Focus on any spread, sizing, or lookback parameters flagged as tunable by the strategy-writer. Identify stable regions, not just the PnL peak. Save results to sweeps/.

**Wait for this agent to complete** before proceeding to Phase 5. Store its output as `sweep_summary`.

---

## Phase 5: Experiment Logger

Announce: "Phase 5: Logging experiment."

Spawn the **experiment-logger** agent:

> Log this pipeline run.
> - Product: <PRODUCT>, Round: <ROUND>
> - Strategy file: <STRATEGY_FILE>
> - Explorer–critic iterations: <iter + 1>
> - Final critic verdict: <first line of VERDICT_FILE>
> - Backtest summary: <backtest_summary>
> - Param sweep summary: <sweep_summary>

Wait for completion.

---

## Final Report

Output this summary:

```
Pipeline complete — <PRODUCT> (Round <ROUND>)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Analysis file   : <ANALYSIS_FILE>
Strategy file   : <STRATEGY_FILE>
Backtest log    : <backtest_log>
─────────────────────────────────────────────
Explorer iterations : <iter + 1> / <MAX_ITER>
Critic verdict      : <PASS or REVISE — max iter>
Backtest PnL        : <total from backtest_summary>
Sweep results       : sweeps/
Experiment logged   : yes
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```
