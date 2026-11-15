"""
HONESTY OVERHAUL — Phase 1: Strategy Validation Gate Engine.

This module is the SINGLE AUTHORITY for two things the terminal previously faked:

  1. The eight promotion gates. A strategy is never called validated, promoted or
     live unless every gate passes. Verdicts are derived only from gate results.
  2. Probability of Backtest Overfitting (PBO). One value, computed once, here.

WHY THIS EXISTS
---------------
The retired paper-trading panel published four identical scorecards (19 trades,
78.9% win, +1.35R, Sharpe 3.78, MaxDD 4.2%) and labelled them LIVE_QUALIFIED with
"PROMOTED TO LIVE: Robust out-of-sample edge confirmed". Every one of those
numbers was fabricated:

  * sharpe_ratio  = round(expectancy_r * 2.8, 2)          <- arithmetic on a fake
  * max_drawdown  = max(4.2, 12.0 - expectancy_r * 10)     <- formula, floored at 4.2
  * backtest_expectancy_r came from a hard-coded dict {0.42, 0.38, 0.44, 0.35}
    while the real walk-forward test partition returns -0.16R to -0.50R.
  * per-strategy "pbo" = clamp(1 - deflated_sharpe_ratio, 0.02, 0.98), i.e. a
    relabelled DSR, not an overfitting probability.

This module computes everything from the walk-forward trade ledger instead.

GROUND RULE (from the overhaul brief): never invent a number. Every figure here is
either computed from real trades by the code below, or emitted as None/"—" with the
reason recorded in `detail`. There are no placeholder statistics in this file.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Gate thresholds — the §3 validation criteria. These are the only constants in
# this module, and each is a policy threshold, not a statistic.
# ---------------------------------------------------------------------------
MIN_T_STAT = 2.0
MIN_DSR = 0.95
MIN_TRIAL_COUNT = 12
FDR_Q = 0.05
MIN_OOS_TRADES = 100
MIN_OOS_MONTHS = 36
MAX_DRAWDOWN_PCT = 35.0
DROPPED_BEST_TRADES = 5
MIN_FORWARD_MONTHS = 3
MAX_FORWARD_DEGRADATION_PCT = 40.0

# Verdict vocabulary — closed set, nothing else may be emitted.
VERDICT_PASSED = "PASSED ALL GATES"
VERDICT_FAILED = "FAILED"
VERDICT_INSUFFICIENT = "INSUFFICIENT SAMPLE"
VERDICT_NOT_VALIDATED = "NOT VALIDATED"

# A strategy is "profitable out-of-sample" only if this holds. Used to decide
# whether PBO is informative at all.
PBO_NOT_INFORMATIVE = "not informative — no strategy profitable out-of-sample"

NOT_AVAILABLE = "—"


@dataclass
class GateResult:
    """One gate. `passed` is None when the data cannot answer the question."""

    gate_id: int
    name: str
    requirement: str
    passed: Optional[bool]
    observed: str
    detail: str

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["symbol"] = "✅" if self.passed else ("❌" if self.passed is False else "⚠️")
        return d


@dataclass
class StrategyValidation:
    strategy_id: str
    strategy_name: str
    verdict: str
    gates: List[GateResult] = field(default_factory=list)
    oos_trades: int = 0
    oos_months: int = 0
    oos_mean_return_pct: Optional[float] = None
    oos_expectancy_r: Optional[float] = None
    oos_t_stat: Optional[float] = None
    verdict_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_name": self.strategy_name,
            "verdict": self.verdict,
            "verdict_reason": self.verdict_reason,
            "oos_trades": self.oos_trades,
            "oos_months": self.oos_months,
            "oos_mean_return_pct": self.oos_mean_return_pct,
            "oos_expectancy_r": self.oos_expectancy_r,
            "oos_t_stat": self.oos_t_stat,
            "gates_passed": sum(1 for g in self.gates if g.passed is True),
            "gates_failed": sum(1 for g in self.gates if g.passed is False),
            "gates_unevaluable": sum(1 for g in self.gates if g.passed is None),
            "gates": [g.to_dict() for g in self.gates],
        }


# ---------------------------------------------------------------------------
# Statistics — plain, auditable arithmetic. No library magic.
# ---------------------------------------------------------------------------
def _mean(xs: List[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def _std(xs: List[float]) -> Optional[float]:
    """Sample standard deviation (n-1). None when fewer than 2 observations."""
    if len(xs) < 2:
        return None
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def t_statistic(xs: List[float]) -> Optional[float]:
    """One-sample t-statistic of the mean against zero."""
    n = len(xs)
    if n < 2:
        return None
    sd = _std(xs)
    if not sd or sd == 0:
        return None
    return _mean(xs) / (sd / math.sqrt(n))


def equity_drawdown_pct(equity: List[float]) -> Optional[float]:
    """Max peak-to-trough drawdown, as a positive percentage."""
    if not equity:
        return None
    peak = equity[0]
    worst = 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, (v - peak) / peak)
    return round(abs(worst) * 100.0, 2)


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------
class StrategyValidationEngine:
    """Computes the §3 gates from the real walk-forward ledger."""

    #: append-only forward record. Nothing may be written here except by an
    #: operator running the pipeline; see `forward_log_status`.
    FORWARD_LOG_PATH = "data/forward_paper_log.jsonl"

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]

    # -- inputs ------------------------------------------------------------
    def _load_json(self, rel: str) -> Optional[Dict[str, Any]]:
        p = self.repo_root / rel
        if not p.exists():
            return None
        with open(p) as f:
            return json.load(f)

    # -- partition handling ------------------------------------------------
    @staticmethod
    def test_partition(strategy: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Trades in the locked out-of-sample TEST window.

        Preference order: (1) the equity_curve `partition` label, which the
        backtester stamps per bar; (2) `config.val_cutoff`, the date the test
        window starts. Falls back to nothing rather than guessing.
        """
        trades = strategy.get("all_trades") or strategy.get("trades") or []
        curve = strategy.get("equity_curve") or []

        # Primary: the declared validation cutoff. A trade belongs to the TEST
        # partition if it was ENTERED after the cutoff. This is complete — every
        # test-window trade is captured.
        #
        # Do not use the equity_curve `partition` dates for this. The curve has
        # one row per trading day, so intersecting entry dates against it
        # silently drops every trade that shares a day with another trade
        # (measured on live data: 35 of 98 test trades survived — a 64% loss).
        cutoff = (strategy.get("config") or {}).get("val_cutoff")
        if cutoff:
            picked = [t for t in trades if (t.get("entry_date") or "") > cutoff]
            if picked:
                return picked

        # Fallback only when no cutoff is declared.
        test_dates = {c["date"] for c in curve if c.get("partition") == "TEST"}
        if test_dates:
            by_date = {t.get("entry_date"): t for t in trades}
            return [by_date[d] for d in sorted(test_dates) if d in by_date]
        return []

    @staticmethod
    def test_partition_curve(strategy: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [c for c in (strategy.get("equity_curve") or []) if c.get("partition") == "TEST"]

    # -- gate 8 input ------------------------------------------------------
    def forward_log_status(self) -> Dict[str, Any]:
        """The genuine forward record. Empty is a valid, honest answer.

        A forward log entry must carry `recorded_at` — the wall-clock time the
        trade was recorded — because without it the record is a historical
        replay, not forward paper trading. Entries lacking it are excluded and
        the reason is reported.
        """
        p = self.repo_root / self.FORWARD_LOG_PATH
        if not p.exists():
            return {
                "exists": False,
                "entries": 0,
                "timestamped_entries": 0,
                "months_covered": 0,
                "reason": "No forward log file exists. No strategy has any forward paper record.",
            }

        entries, untimestamped = [], 0
        with open(p) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("recorded_at"):
                    entries.append(rec)
                else:
                    untimestamped += 1

        months = sorted({(e.get("recorded_at") or "")[:7] for e in entries} - {""})
        reason = (
            "Forward log is empty. No strategy has any forward paper record, so gate 8 "
            "cannot be evaluated for any strategy."
            if not entries
            else "%d timestamped forward entries across %d month(s)." % (len(entries), len(months))
        )
        if untimestamped:
            reason += " %d entr(y/ies) excluded: missing `recorded_at`." % untimestamped

        return {
            "exists": True,
            "entries": len(entries),
            "timestamped_entries": len(entries),
            "untimestamped_excluded": untimestamped,
            "months_covered": len(months),
            "months": months,
            "reason": reason,
        }

    # -- gates -------------------------------------------------------------
    def evaluate(
        self,
        strategy: Dict[str, Any],
        fdr_row: Optional[Dict[str, Any]],
        empirical_pbo: Optional[float],
        num_trials: Optional[int],
        forward: Dict[str, Any],
    ) -> StrategyValidation:
        sid = strategy.get("strategy_id") or "UNKNOWN"
        name = strategy.get("strategy_name") or sid
        metrics = strategy.get("metrics") or {}

        oos = self.test_partition(strategy)
        oos_curve = self.test_partition_curve(strategy)

        # Trade-level statistics, computed here from the ledger.
        r_mults = [t["r_multiple"] for t in oos if t.get("r_multiple") is not None]
        returns = [t["return_pct"] for t in oos if t.get("return_pct") is not None]
        oos_months = len({(t.get("exit_date") or "")[:7] for t in oos} - {""})

        mean_ret = _mean(returns)
        mean_r = _mean(r_mults)
        tstat = t_statistic(returns) if returns else t_statistic(r_mults)

        gates: List[GateResult] = []

        # --- G1: OOS mean return after costs > 0 with t-stat >= 2.0 --------
        if mean_ret is None:
            g1 = GateResult(1, "Out-of-sample profitability",
                            "OOS mean return after costs > 0 with t-statistic ≥ %.1f" % MIN_T_STAT,
                            None, NOT_AVAILABLE,
                            "No out-of-sample trades carry a computed return.")
        else:
            ok = mean_ret > 0 and (tstat is not None and tstat >= MIN_T_STAT)
            g1 = GateResult(
                1, "Out-of-sample profitability",
                "OOS mean return after costs > 0 with t-statistic ≥ %.1f" % MIN_T_STAT,
                ok,
                "mean %.3f%%, t = %s (n=%d)" % (mean_ret, ("%.2f" % tstat) if tstat is not None else NOT_AVAILABLE, len(returns)),
                "Mean is %s zero%s." % ("above" if mean_ret > 0 else "not above",
                                       "" if tstat is None else ("; t-statistic %s the 2.0 threshold" % ("meets" if tstat >= MIN_T_STAT else "is below"))),
            )
        gates.append(g1)

        # --- G2: DSR >= 0.95 over >= 12 trials -----------------------------
        dsr = metrics.get("deflated_sharpe_ratio")
        trials = num_trials if num_trials is not None else metrics.get("num_trials_tested")
        if dsr is None:
            g2 = GateResult(2, "Deflated Sharpe ratio",
                            "DSR ≥ %.2f counting all variants (N ≥ %d)" % (MIN_DSR, MIN_TRIAL_COUNT),
                            None, NOT_AVAILABLE, "No deflated Sharpe ratio was computed for this strategy.")
        else:
            enough = trials is not None and trials >= MIN_TRIAL_COUNT
            ok = dsr >= MIN_DSR and enough
            g2 = GateResult(
                2, "Deflated Sharpe ratio",
                "DSR ≥ %.2f counting all variants (N ≥ %d)" % (MIN_DSR, MIN_TRIAL_COUNT),
                ok, "DSR %.3f, trials %s" % (dsr, trials if trials is not None else NOT_AVAILABLE),
                "DSR %s %.2f%s." % ("≥" if dsr >= MIN_DSR else "is below", MIN_DSR,
                                    "" if enough else "; trial count is below the %d minimum" % MIN_TRIAL_COUNT),
            )
        gates.append(g2)

        # --- G3: passes Benjamini-Hochberg FDR at q = 0.05 -----------------
        if not fdr_row:
            g3 = GateResult(3, "Multiple-testing control",
                            "Passes Benjamini-Hochberg FDR at q = %.2f" % FDR_Q,
                            None, NOT_AVAILABLE, "Strategy is absent from the FDR gating table.")
        else:
            verdict_raw = str(fdr_row.get("fdr_verdict") or "")
            ok = "PASS" in verdict_raw.upper() and "REJECT" not in verdict_raw.upper()
            g3 = GateResult(
                3, "Multiple-testing control",
                "Passes Benjamini-Hochberg FDR at q = %.2f" % FDR_Q,
                ok,
                "p = %s vs critical %s → %s" % (
                    fdr_row.get("raw_p_value", NOT_AVAILABLE),
                    fdr_row.get("fdr_critical_threshold", NOT_AVAILABLE),
                    verdict_raw or NOT_AVAILABLE),
                "Rejected for selection bias." if not ok else "Survives multiple-testing control.",
            )
        gates.append(g3)

        # --- G4: enough out-of-sample evidence -----------------------------
        enough_trades = len(oos) >= MIN_OOS_TRADES
        enough_months = oos_months >= MIN_OOS_MONTHS
        g4 = GateResult(
            4, "Sufficient out-of-sample evidence",
            "≥ %d OOS trades or ≥ %d monthly periods" % (MIN_OOS_TRADES, MIN_OOS_MONTHS),
            enough_trades or enough_months,
            "%d trades, %d month(s)" % (len(oos), oos_months),
            "Below both thresholds — results are not statistically meaningful."
            if not (enough_trades or enough_months)
            else "Sample size is adequate.",
        )
        gates.append(g4)

        # --- G5: beats the relevant index buy-and-hold after costs ---------
        g5 = self._benchmark_gate(strategy, oos_curve, metrics)
        gates.append(g5)

        # --- G6: maximum drawdown <= 35% -----------------------------------
        dd = equity_drawdown_pct([c["portfolio_equity"] for c in oos_curve if c.get("portfolio_equity") is not None])
        if dd is None:
            dd = metrics.get("max_drawdown_pct")
            dd_src = "full-period metric"
        else:
            dd_src = "OOS equity curve"
        if dd is None:
            g6 = GateResult(6, "Drawdown limit", "Max drawdown ≤ %.0f%%" % MAX_DRAWDOWN_PCT,
                            None, NOT_AVAILABLE, "No equity curve available to compute drawdown.")
        else:
            g6 = GateResult(
                6, "Drawdown limit", "Max drawdown ≤ %.0f%%" % MAX_DRAWDOWN_PCT,
                dd <= MAX_DRAWDOWN_PCT, "%.2f%% (%s)" % (dd, dd_src),
                "Within the 35%% limit." if dd <= MAX_DRAWDOWN_PCT else "Exceeds the 35%% limit.",
            )
        gates.append(g6)

        # --- G7: still positive after removing the 5 best trades -----------
        if len(r_mults) <= DROPPED_BEST_TRADES:
            g7 = GateResult(7, "Not dependent on a few winners",
                            "Still positive after removing the %d best trades" % DROPPED_BEST_TRADES,
                            None, NOT_AVAILABLE,
                            "Only %d OOS trades — removing %d leaves no sample." % (len(r_mults), DROPPED_BEST_TRADES))
        else:
            trimmed = sorted(r_mults, reverse=True)[DROPPED_BEST_TRADES:]
            trimmed_mean = _mean(trimmed)
            g7 = GateResult(
                7, "Not dependent on a few winners",
                "Still positive after removing the %d best trades" % DROPPED_BEST_TRADES,
                trimmed_mean > 0, "trimmed mean %+.3fR (n=%d)" % (trimmed_mean, len(trimmed)),
                "Profitability %s the top %d trades." % ("does not depend on" if trimmed_mean > 0 else "depends on", DROPPED_BEST_TRADES),
            )
        gates.append(g7)

        # --- G8: genuine timestamped forward record ------------------------
        g8 = self._forward_gate(sid, forward, metrics, mean_ret)
        gates.append(g8)

        verdict, reason = self._verdict(gates, g4, mean_ret, tstat)

        return StrategyValidation(
            strategy_id=sid,
            strategy_name=name,
            verdict=verdict,
            gates=gates,
            oos_trades=len(oos),
            oos_months=oos_months,
            oos_mean_return_pct=round(mean_ret, 4) if mean_ret is not None else None,
            oos_expectancy_r=round(mean_r, 4) if mean_r is not None else None,
            oos_t_stat=round(tstat, 3) if tstat is not None else None,
            verdict_reason=reason,
        )

    # -- individual gate helpers ------------------------------------------
    def _benchmark_gate(self, strategy, oos_curve, metrics) -> GateResult:
        req = "Beats SPY (US) / XIU (CA) buy-and-hold after costs over the same window"
        if len(oos_curve) < 2:
            return GateResult(5, "Beats the benchmark", req, None, NOT_AVAILABLE,
                              "Fewer than two out-of-sample equity points to compare.")

        strat_ret = self._pct_change([c.get("portfolio_equity") for c in oos_curve])
        spy_ret = self._pct_change([c.get("benchmark_price_spy") for c in oos_curve])
        xiu_ret = self._pct_change([c.get("benchmark_price_xiu") for c in oos_curve])

        if strat_ret is None or (spy_ret is None and xiu_ret is None):
            return GateResult(5, "Beats the benchmark", req, None, NOT_AVAILABLE,
                              "Strategy or benchmark series is missing over the OOS window.")

        # Both benchmarks are tracked for every strategy; a strategy must beat
        # the one that applies to its market. Conservative reading: beat both.
        observed = "strategy %+.2f%% vs SPY %+.2f%% / XIU %+.2f%%" % (
            strat_ret, spy_ret if spy_ret is not None else float("nan"),
            xiu_ret if xiu_ret is not None else float("nan"))
        ok = True
        parts = []
        if spy_ret is not None:
            ok = ok and strat_ret > spy_ret
            parts.append("SPY %s" % ("beaten" if strat_ret > spy_ret else "not beaten"))
        if xiu_ret is not None:
            ok = ok and strat_ret > xiu_ret
            parts.append("XIU %s" % ("beaten" if strat_ret > xiu_ret else "not beaten"))
        return GateResult(5, "Beats the benchmark", req, ok, observed,
                          "Over the OOS window: " + ", ".join(parts) + ".")

    @staticmethod
    def _pct_change(series: List[Optional[float]]) -> Optional[float]:
        vals = [v for v in series if v is not None]
        if len(vals) < 2 or not vals[0]:
            return None
        return (vals[-1] / vals[0] - 1.0) * 100.0

    def _forward_gate(self, sid, forward, metrics, oos_mean_ret) -> GateResult:
        req = ("≥ %d months of genuine timestamped forward paper record, degrading ≤ %.0f%% "
               "vs backtest" % (MIN_FORWARD_MONTHS, MAX_FORWARD_DEGRADATION_PCT))
        if not forward.get("exists") or not forward.get("entries"):
            return GateResult(
                8, "Forward paper record", req, False, "0 forward trades, 0 month(s)",
                "No forward paper record exists for any strategy. " + forward.get("reason", ""),
            )

        mine = [e for e in forward.get("_entries", []) if e.get("strategy_id") == sid]
        if not mine:
            return GateResult(8, "Forward paper record", req, False,
                              "0 forward trades for this strategy",
                              "The forward log holds no entries for this strategy.")

        months = len({(e.get("recorded_at") or "")[:7] for e in mine} - {""})
        fwd = _mean([e["r_multiple"] for e in mine if e.get("r_multiple") is not None])
        bt = metrics.get("expectancy_r")

        if months < MIN_FORWARD_MONTHS:
            return GateResult(8, "Forward paper record", req, False,
                              "%d forward trade(s) over %d month(s)" % (len(mine), months),
                              "Fewer than %d months of forward record." % MIN_FORWARD_MONTHS)

        if fwd is None or bt is None or bt <= 0:
            return GateResult(8, "Forward paper record", req, None,
                              "forward %s vs backtest %s" % (fwd, bt),
                              "n/a — backtest not profitable, so degradation cannot be measured.")

        deg = ((bt - fwd) / bt) * 100.0
        return GateResult(8, "Forward paper record", req, deg <= MAX_FORWARD_DEGRADATION_PCT,
                          "forward %+.3fR vs backtest %+.3fR → %.1f%% degradation" % (fwd, bt, deg),
                          "Degradation %s the 40%% tolerance." % ("within" if deg <= MAX_FORWARD_DEGRADATION_PCT else "exceeds"))

    # -- verdict -----------------------------------------------------------
    @staticmethod
    def _verdict(gates: List[GateResult], g4: GateResult, oos_mean_ret: Optional[float],
                 oos_t: Optional[float]):
        """Verdicts come only from gate results. Never from a narrative.

        Order of precedence, chosen so the label never overstates the evidence:

          1. PASSED ALL GATES  — every gate green.
          2. FAILED            — the sample is small but shows a statistically
                                 significant LOSS (mean < 0 at t <= -2.0). A clear
                                 loss is a failure even on a short sample; calling
                                 it "insufficient" would hide a real result.
          3. INSUFFICIENT SAMPLE — too few trades/months AND no conclusive result.
                                 You cannot fail a strategy on statistics you do
                                 not have enough data to compute.
          4. FAILED            — adequate sample, at least one gate red.
          5. NOT VALIDATED     — anything else. The safe default.
        """
        if all(g.passed is True for g in gates):
            return VERDICT_PASSED, "All eight gates passed."

        conclusive_loss = (oos_mean_ret is not None and oos_mean_ret < 0
                           and oos_t is not None and oos_t <= -MIN_T_STAT)
        hard_failed = [g for g in gates if g.passed is False and g.gate_id != 8]

        if conclusive_loss:
            return (VERDICT_FAILED,
                    "Out-of-sample result is a statistically significant loss "
                    "(mean %+.3f%%, t = %.2f). The sample is short but the direction "
                    "is not in doubt." % (oos_mean_ret, oos_t))

        if g4.passed is False:
            return (VERDICT_INSUFFICIENT,
                    "Only %s — too little out-of-sample evidence to reach a verdict. "
                    "This is not a pass, and not yet a fail." % g4.observed)

        if hard_failed:
            names = ", ".join("G%d %s" % (g.gate_id, g.name) for g in hard_failed)
            return VERDICT_FAILED, "Failed: %s." % names

        return (VERDICT_NOT_VALIDATED,
                "Not every gate passed. No strategy may be called validated, promoted or live.")

    # -- PBO ---------------------------------------------------------------
    @staticmethod
    def pbo_assessment(empirical_pbo: Optional[float], results: List["StrategyValidation"]) -> Dict[str, Any]:
        """One PBO for the whole terminal, plus an honesty guard.

        PBO measures rank inversion across CPCV paths — whether the strategy that
        looked best in-sample turns out worst out-of-sample. When EVERY strategy
        loses out-of-sample there is no meaningful "best" to invert, so a low PBO
        is an artefact, not reassurance. Reporting "low overfitting risk" in that
        state would be misleading, so it is suppressed here.

        Profitability is judged on the COMPUTED out-of-sample expectancy, not on
        the full-period metric — using the latter would wrongly count strategies
        whose in-sample gains mask an out-of-sample loss.

        A strategy only counts if its out-of-sample sample is also large enough to
        mean anything (gate 4). Without that, a handful of lucky trades would be
        enough to unlock a "low overfitting risk" label — which is precisely the
        misleading reassurance this guard exists to prevent.
        """
        profitable = [r.strategy_id for r in results
                      if r.oos_expectancy_r is not None and r.oos_expectancy_r > 0]
        credible = [r.strategy_id for r in results
                    if r.oos_expectancy_r is not None and r.oos_expectancy_r > 0
                    and any(g.gate_id == 4 and g.passed for g in r.gates)]
        n = len(results)

        if not credible:
            detail = ("No strategy has a positive out-of-sample expectancy."
                      if not profitable else
                      "%d strateg%s show%s a positive out-of-sample mean (%s) but none on a "
                      "sample large enough to mean anything — all fall short of the %d-trade / "
                      "%d-month threshold."
                      % (len(profitable), "y" if len(profitable) == 1 else "ies",
                         "" if len(profitable) == 1 else "", ", ".join(profitable),
                         MIN_OOS_TRADES, MIN_OOS_MONTHS))
            return {
                "value": empirical_pbo,
                "informative": False,
                "assessment": PBO_NOT_INFORMATIVE,
                "reason": detail + " There is no credible profitable strategy whose ranking "
                                   "could be inverted, so a low PBO here would be an artefact "
                                   "and must not be read as reassurance about overfitting.",
                "profitable_strategies": profitable,
                "credible_profitable_strategies": [],
            }
        if empirical_pbo is None:
            return {"value": None, "informative": False, "assessment": NOT_AVAILABLE,
                    "reason": "CPCV paths were not computed.",
                    "profitable_strategies": profitable,
                    "credible_profitable_strategies": credible}

        if empirical_pbo < 0.25:
            band = "LOW_OVERFITTING_RISK"
        elif empirical_pbo <= 0.50:
            band = "MODERATE_OVERFITTING_RISK"
        else:
            band = "HIGH_OVERFITTING_RISK"
        return {
            "value": empirical_pbo,
            "informative": True,
            "assessment": band,
            "reason": "%d of %d strategies are profitable out-of-sample on an adequate sample, "
                      "so rank inversion is meaningful." % (len(credible), n),
            "profitable_strategies": profitable,
            "credible_profitable_strategies": credible,
        }

    # -- feed --------------------------------------------------------------
    def generate_feed(self, feeds_dir: Optional[Path] = None) -> Dict[str, Any]:
        """Build strategy_validation.json from the real backtest + CPCV outputs."""
        root = feeds_dir or (self.repo_root / "data" / "feeds")
        backtests = self._load_json(str(Path(root) / "backtests.json"))
        cpcv = self._load_json(str(Path(root) / "cpcv_validation.json"))

        if not backtests:
            raise FileNotFoundError("backtests.json not found in %s — run the backtest lab first." % root)

        strategies = backtests.get("strategies") or {}
        fdr_table = ((cpcv or {}).get("fdr_gating_table")) or []
        fdr_by_id = {r.get("strategy_id"): r for r in fdr_table}
        summary = (cpcv or {}).get("summary") or {}

        empirical_pbo = summary.get("empirical_pbo")
        num_trials = summary.get("total_strategies_evaluated") or len(strategies)

        forward = self.forward_log_status()
        if forward.get("entries"):
            with open(self.repo_root / self.FORWARD_LOG_PATH) as f:
                forward["_entries"] = [json.loads(l) for l in f if l.strip()]

        results = [
            self.evaluate(s, fdr_by_id.get(sid), empirical_pbo, num_trials, forward)
            for sid, s in strategies.items()
        ]

        pbo = self.pbo_assessment(empirical_pbo, results)
        passed = [r for r in results if r.verdict == VERDICT_PASSED]

        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "as_of_date": backtests.get("as_of_date"),
            "source_of_truth": "Computed by src/engine/strategy_validation.py from the "
                               "walk-forward trade ledger in backtests.json and the FDR "
                               "table in cpcv_validation.json. No figure on this panel is "
                               "hard-coded.",
            "validation_criteria": {
                "min_t_stat": MIN_T_STAT, "min_dsr": MIN_DSR, "min_trial_count": MIN_TRIAL_COUNT,
                "fdr_q": FDR_Q, "min_oos_trades": MIN_OOS_TRADES, "min_oos_months": MIN_OOS_MONTHS,
                "max_drawdown_pct": MAX_DRAWDOWN_PCT, "dropped_best_trades": DROPPED_BEST_TRADES,
                "min_forward_months": MIN_FORWARD_MONTHS,
                "max_forward_degradation_pct": MAX_FORWARD_DEGRADATION_PCT,
            },
            "summary": {
                "strategies_evaluated": len(results),
                "passed_all_gates": len(passed),
                "failed": sum(1 for r in results if r.verdict == VERDICT_FAILED),
                "insufficient_sample": sum(1 for r in results if r.verdict == VERDICT_INSUFFICIENT),
                "not_validated": sum(1 for r in results if r.verdict == VERDICT_NOT_VALIDATED),
                "promoted_to_live": 0,
                "promotion_statement": (
                    "No strategy is promoted to live. %d of %d strategies pass all eight gates."
                    % (len(passed), len(results))
                ),
            },
            "pbo": pbo,
            "forward_record": {k: v for k, v in forward.items() if k != "_entries"},
            "strategies": [r.to_dict() for r in results],
            "disclaimers": [
                "No strategy shown here is validated, promoted or live.",
                "This is research output, not investment advice or a trade recommendation.",
                "Backtest results are historical simulations and do not predict future returns.",
            ],
        }
        return payload


strategy_validation_engine = StrategyValidationEngine()
