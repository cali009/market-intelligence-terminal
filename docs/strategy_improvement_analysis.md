# Can the strategies be made to beat the benchmark?

Analysis date: 2026-11-27. All figures measured from `data/market_intel.db` and
`data/feeds/backtests.json`, not estimated.

## The decisive finding

The best surviving strategy, `ADAPTIVE_DUAL_REGIME`, returns **+60.4% gross** over the
five-year window. SPY buy-and-hold over the identical window returns **+56.2%**.

```
edge at ZERO transaction cost, 5 years : +4.2 pp   (+0.85 pp/year)
```

That is the ceiling. Costs can be reduced, position sizing can be improved, exits can be
tuned — none of it can create return that the gross signal does not contain. **+0.85 pp/year
before costs is inside the noise band for a 460-trade sample**, and it would not survive the
t >= 2.0 gate.

Consequence for the most popular proposed fix:

```
current fee drag                : 23.3 pp
if turnover HALVED              : net ~+48.8%   -> still 7.4 pp short of SPY
if turnover ELIMINATED entirely : net ~+60.4%   -> beats SPY by only 4.2 pp
```

**Cutting turnover cannot close the gap.** It recovers roughly half of it and no further.

## Risk-adjusted comparison

| | ADAPTIVE_DUAL_REGIME | SPY |
|---|---|---|
| total return (5y) | +37.1% | +56.2% |
| CAGR | +6.67% | +9.4% |
| Sharpe (rf=0) | 0.24 | **0.60** |
| Sortino | 0.33 | — |
| Calmar | 0.39 | 0.37 |
| max drawdown | 16.9% | 25.4% |
| exposure | 81.1% | 100% |

The strategy's lower drawdown is real but is largely explained by sitting 19% in cash. On
Calmar the two are a wash (0.39 vs 0.37); on Sharpe the benchmark is **2.5x better**. There
is no risk-adjusted case for the strategy over the index.

## Full strategy table (5-year, 20 bps round-trip, next-day-open fills)

| strategy | gross | net | trades | win% | expR | PF | MaxDD |
|---|---|---|---|---|---|---|---|
| ADAPTIVE_DUAL_REGIME | +60.4 | +37.1 | 460 | 45.2 | +0.08 | 1.13 | 16.9 |
| PO3_LIQUIDITY_SWEEP | +41.9 | +21.9 | 404 | 41.6 | +0.04 | 1.05 | 12.4 |
| PO3_META_GATED | +34.3 | +22.6 | 306 | 45.8 | +0.06 | 1.06 | 6.9 |
| TAC02_SQUEEZE | -4.7 | -33.1 | 1285 | 36.1 | -0.10 | 0.86 | 37.9 |
| TAC03_BREAKOUT_CHASE | -4.1 | -24.5 | 757 | 37.3 | -0.07 | 0.87 | 37.5 |
| TAC01_PULLBACK | -32.1 | -56.5 | 1293 | 36.5 | -0.10 | 0.79 | 59.0 |
| TAC04_MOMENTUM | -37.1 | -60.2 | 1211 | 35.7 | -0.14 | 0.76 | 61.2 |
| ENSEMBLE_8F | -52.0 | -72.8 | 1230 | 35.1 | -0.17 | 0.70 | 74.7 |

Benchmark over the same window: SPY +56.2%, XIU +60.5%, blended +59.7%.

Five strategies have **negative gross** return. They lose before costs are charged, so no
cost model rescues them. `ENSEMBLE_8F` at -52% gross and PF 0.70 is combining losing
signals, which cannot produce a winning sum.

## Harness defect that undermines every number above

`src/engine/backtester.py:315-329` implements a **single fixed 60/20/20 chronological
holdout** and labels it walk-forward:

```
TRAIN       2021-11-26 .. 2024-11-26   (769 sessions)
VALIDATION  2024-11-26 .. 2025-11-26   (256 sessions)
TEST        2025-11-26 .. 2026-11-26   (257 sessions)  <- the entire "OOS" sample
```

Three of five years are never used out-of-sample. Every OOS statistic — including the
+37.1% — rests on one 12-month window. **The harness must be fixed for credibility, not for
performance.** A rolling walk-forward is more likely to lower these numbers than raise them.

## Gate suite: one structural contradiction

Gate G2 requires deflated Sharpe >= 0.95 **counting all variants with N >= 12 trials**, but
only 8 strategies exist. `BacktestConfig.num_trials_tested` is set to 12 while 8 are run, so
the reported trial count (8) and the configured one disagree. G2 is currently unpassable by
construction. This is the gate working as intended — it is saying there has not been enough
independent research to claim anything — but the config constant is misleading and should
either match reality or the research programme should genuinely reach 12 variants.

## Model consensus

Four models (`openai/gpt-oss-120b`, `claude-sonnet-4.5`, `deepseek-3.2`,
`gemini-3-flash-preview`) were given the measured table and asked for falsifiable proposals.
They agreed on three, in the same order:

1. **Delete TAC01-04 and ENSEMBLE_8F.** Unanimous. Negative gross expectancy, PF < 0.9,
   MaxDD > 35%. Not borderline — systematically wrong.
2. **Replace the single holdout with a real rolling walk-forward.** Unanimous, and all four
   flagged that current "OOS" results are unreliable without it.
3. **Cut turnover / lengthen holding period.** Unanimous.

They disagreed on which survivor to back: `gpt-oss-120b` picked ADAPTIVE_DUAL_REGIME on
return; `claude-sonnet-4.5` picked PO3_META_GATED on fee efficiency (11.7pp drag vs 23.3pp),
lowest drawdown (6.9%) and highest win rate (45.8%).

**Correction to the consensus:** proposal 3 is worth about 11 pp and the gap is 19 pp, with a
zero-cost ceiling only 4 pp above the benchmark. Turnover reduction is worth doing for cost
hygiene but is not a path to outperformance.

## What is actually worth doing, in order

**1. Fix the harness before believing anything (highest value, no performance upside).**
Anchored rolling walk-forward — e.g. 24-month train, 3-month test, rolled quarterly, with a
purge gap between train and test to stop leakage from overlapping indicators. Produces ~10
non-overlapping OOS windows from the data already held instead of 1. Expect the numbers to
get worse. That is the point.

**2. Delete the five losers.** Reduces the multiple-testing penalty that deflates the
survivors' Sharpe, and removes 5,700 trades of pure noise. Falsifiable: if deleting them
changes nothing about the survivors' risk-adjusted results, they were not contaminating
anything.

**3. Exploit the breadth Phase B created.** Every strategy above was designed for a
19-symbol universe. The universe is now 163 names with real GICS sectors. Time-series
technical rules on individual names are the weakest documented edge; **cross-sectional
ranking** (long the top decile on a composite, rebalanced monthly) uses breadth, and monthly
rebalancing cuts cost by roughly an order of magnitude versus 460 trades. This is the only
proposal here with a genuinely different mechanism rather than a retune.

**4. Close the fundamentals gap.** Only 7 of 103 US issuers have an EDGAR CIK, so 6.9%
fundamental coverage against a 95% target. Any value or quality factor is currently computed
on almost no data. This blocks proposal 3 from being done properly.

**5. Treat the benchmark as the bar, not the enemy.** On the evidence, a core index position
with a small, gated research sleeve beats every strategy currently in the lab on both return
and Sharpe.

## What should not be done

- **Do not tune exits or holding periods to improve the backtest.** All four models flagged
  this as the highest curve-fit risk, correctly. With one 12-month OOS window, any parameter
  chosen by looking at results is fitted to that window.
- **Do not weaken a gate to let something through.** In particular G2's N >= 12.
- **Do not add leverage to close a 19 pp gap.** It scales the drawdown that already fails G6.
- **Do not read the +37.1% as achieved.** It is a single-window figure on a harness that does
  not do what its name says.

---

# Addendum: the strategies DO make money — the combination is the finding

The analysis above answered "does it beat SPY". The question "does it make a profit" has a
different and more favourable answer.

## Equity-curve sampling caveat

`equity_curve` in `backtests.json` is sampled roughly **every other trading day** (616 points
over 4.79 years = 51% of expected sessions; dominant gap 2 calendar days). Annualising those
returns with sqrt(252) inflates Sharpe by ~1.41x and CAGR by ~2x. All figures below use the
measured frequency of **128.3 observations/year**.

## Diversifying the three profitable strategies

Daily-return correlations between the three winners are moderate, not redundant:

```
ADAPTIVE_DUAL_REGIME  <->  PO3_META_GATED     0.474
ADAPTIVE_DUAL_REGIME  <->  PO3_LIQUIDITY_SWEEP 0.455
PO3_META_GATED        <->  PO3_LIQUIDITY_SWEEP 0.703
```

An equal-weight combination, with no parameter tuning of any kind:

| | total% | CAGR% | Sharpe | MaxDD% | Calmar |
|---|---|---|---|---|---|
| ADAPTIVE_DUAL_REGIME | 36.8 | 6.76 | 0.65 | 16.9 | 0.40 |
| PO3_META_GATED | 22.6 | 4.34 | 0.74 | 6.8 | 0.64 |
| PO3_LIQUIDITY_SWEEP | 21.9 | 4.21 | 0.61 | 12.2 | 0.34 |
| **EQUAL-WEIGHT COMBO** | **27.7** | **5.23** | **0.80** | **8.6** | **0.61** |
| SPY buy & hold | 58.9 | 10.15 | 0.68 | 22.5 | 0.45 |

**The combination has the highest Sharpe of anything measured in this system, including the
benchmark (0.80 vs 0.68), at 38% of the benchmark's drawdown.** That is diversification
arithmetic across three different mechanisms, not a fitted result.

## Where the value actually is: down years

| year | COMBO | ADAPTIVE | PO3_META | PO3_LIQ | SPY |
|---|---|---|---|---|---|
| 2022 | **-0.6** | -1.3 | -1.2 | +0.5 | **-15.0** |
| 2023 | +12.0 | +14.9 | +10.2 | +10.5 | +24.8 |
| 2024 | +8.4 | +10.6 | +8.8 | +5.2 | +25.5 |
| 2025 | +0.4 | -0.2 | +1.1 | +0.1 | +17.5 |
| 2026 | +5.1 | +7.3 | +3.1 | +4.7 | +4.9 |

The combination was **flat in 2022 while SPY fell 15.0%** — a 14.4 pp relative gain in the
one down year of the sample. It then gave back 12-17 pp per year through the 2023-2025 bull.
That is the whole story: it protects capital in downturns and lags in strong bull markets.
Because this particular five-year window was a powerful bull market (SPY +58.9%), the
protection never got to pay for itself in the total.

## Honest conclusion

- **Profitable in absolute terms: yes.** The combination returned +27.7% over 4.8 years,
  positive or flat in 4 of 5 years, maximum drawdown 8.6%.
- **Beats the benchmark on absolute return: no.** 5.23% vs 10.15% CAGR, and the gap is
  structural — it holds cash and does not participate fully in rallies.
- **Beats the benchmark risk-adjusted: yes, on Sharpe and Calmar.** 0.80 vs 0.68, 0.61 vs 0.45.

## Caveats that must not be skipped

1. **None of this is out-of-sample.** The harness produces a single 12-month OOS window, so
   the 0.80 Sharpe is a full-period figure. It is a hypothesis to validate, not a result.
2. The equity-curve sampling means these Sharpe estimates carry more uncertainty than a true
   daily series would.
3. The gate suite still fails, and should still fail: the DSR gate cannot pass at 8 trials,
   and the forward-paper gate has never run (`data/forward_paper_log.jsonl` does not exist).

---

# The sleeve, run through the gate suite

`scripts/build_strategy_sleeve.py` builds `SLEEVE_3WAY_EQUAL` as a first-class strategy
record — one third of capital in each constituent, never rebalanced, 1,170 pooled trades,
256 of them in the locked TEST window — so `strategy_validation.py` judges it on identical
terms.

## Verdict: FAILED

| gate | | observed |
|---|---|---|
| G1 OOS profitability | FAIL | mean **+0.060%**, t = **0.70** (n=256). Positive, but indistinguishable from zero. |
| G2 Deflated Sharpe | N/A | not computed for this strategy |
| G3 FDR control | N/A | absent from the CPCV/FDR table |
| G4 OOS evidence | PASS | 256 trades, 12 months |
| G5 Beats benchmark | FAIL | +4.11% vs SPY +5.36% / XIU +10.89% |
| G6 Drawdown | PASS | **7.28%**, well inside 35% |
| G7 Not winner-dependent | FAIL | trimmed mean -0.002R after removing the top 5 |
| G8 Forward paper | FAIL | no forward log exists — fails for every strategy |

## The part that matters: out-of-sample, risk-adjusted, it beat the index

| over the locked OOS window | Sharpe | MaxDD | total |
|---|---|---|---|
| SLEEVE_3WAY_EQUAL | **0.62** | **-7.3%** | +4.1% |
| SPY | 0.45 | -9.1% | +5.4% |

Full-period Sharpe 0.78 degraded to 0.62 out-of-sample — a 21% haircut, which is mild by the
standards of this lab. It beat SPY on both Sharpe and drawdown while giving up 1.3 pp of
return.

## What the failure actually says

The sleeve is not a bad strategy. It fails because:

1. **G1 is a significance test, and t = 0.70 means the OOS sample cannot distinguish its
   return from zero.** Twelve months and 256 trades is not enough evidence, not proof of
   absence of edge.
2. **G5 compares absolute return only.** A portfolio that beats the benchmark on Sharpe and
   drawdown but not on total return will always fail it. That is a defensible design choice
   for a retail investor who cannot lever up, but it should be understood as a choice.
3. **G8 has never been satisfiable by anything.** `data/forward_paper_log.jsonl` does not
   exist, so every strategy in the lab fails it automatically.

## The one gate that time can fix

G8 is the only gate that converts a backtest into evidence, and it is the only one currently
blocked by an absent mechanism rather than by a weak strategy. Starting a timestamped
forward paper log for the sleeve costs nothing and is the honest next step: three months of
live-recorded decisions with no ability to revise them afterwards.

Until that exists, the sleeve's correct status is **EXPERIMENTAL / NOT VALIDATED**, which is
what the record says.

---

# Building the forward paper record

Gate 8 was the only gate no strategy could pass, because `data/forward_paper_log.jsonl`
never existed. It is also the only gate that time rather than tuning can fix, and the only
one that produces evidence a backtest cannot. So it was built.

## The design review

Three router models (`auto/best-reasoning`, `auto/claude-sonnet`, `auto/deepseek`) reviewed
the design before it was written. All three independently reached the same conclusion:

> **The hash chain is tamper-EVIDENT, not tamper-PROOF.** The operator controls the file, the
> git history and the clock. A chain cannot stop someone rewriting the whole file and
> recomputing every hash.

That is correct and it is now stated in the module docstring, in `INTEGRITY_STATEMENT`, and
in the published feed. A gate-8 pass from this file means the process was followed, not that
an edge exists. The only real upgrade is an anchor outside the operator's control.

Five specific holes were found and closed:

| hole | fix |
|---|---|
| Write ten DECISIONs, peek at the opens, FILL only the five good ones | Every DECISION gets exactly one FILL, entered or not. `verify()` reports unsettled decisions. |
| Backfill only the strategies that happened to work | A CENSUS record is written for every strategy every run, including zero-signal days. |
| Quietly retune mid-stream and keep the same `strategy_id` | `strategy_version_hash` on every record; drift is reported so the log is segmented, not blended. |
| Record a stop price before the entry is known | DECISION records the stop **rule** and the ATR, never a price. The FILL computes it. |
| Forward fires on different conditions than the backtest it is compared to | The predicate is imported from `backtest_engine`, not re-implemented. |

That last one is tested, not asserted: `test_forward_predicate_agrees_with_backtest` replays
real historical signals from the published backtest ledger and requires the forward path to
reproduce them. Measured on the current data: **PO3_LIQUIDITY_SWEEP 21/21,
ADAPTIVE_DUAL_REGIME 18/18, PO3_META_GATED 21/21.** Gate 8 is comparing the same strategy on
both sides.

## What was broken and found while building it

1. **`run_all_backtests()` rebuilds `results` from scratch**, so the sleeve added last turn
   would have been silently erased on the next pipeline run. It is now rebuilt as a derived
   record inside `run_backtests.py`, and degrades gracefully if a constituent disappears.
2. **The local `.git` reverted a fourth time**, back to `85fc7d6` with an 8.9 MB database
   holding 10,024 bars. The remote was untouched at `3ec54e5` with the correct 84.4 MB /
   203,078-bar database — the push script's behind-remote guard did its job. Recovered with
   `git fetch` + `git reset --mixed origin/main`.
3. **The CI workflow had no commit step**, so a forward log written by the cron was discarded
   with the runner and gate 8 could never accumulate the three months it requires. Added a
   commit step that stages `data/forward_paper_log.jsonl` only — never the 84 MB database —
   and refuses to commit if the log shrank. Also added the missing `timeout-minutes: 60`.

## State as of writing

The log holds 124 records across three sessions, the chain verifies, and gate 8 reads
**1/3 months for all three strategies**. Two more months of daily runs are required before
gate 8 can produce a verdict. Until then it fails for the honest reason: not enough time has
passed, not because anything is wrong.

Nothing about this changes the verdict on the sleeve. It changes whether a verdict will
eventually be possible.
