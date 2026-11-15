"""
Honesty Overhaul Phase 1 — tests for the strategy validation gate engine.

These tests exist to make the §7 checks executable. The three that matter most:

  * test_recomputation_matches_published_feed  (check #2)
  * test_no_two_identical_scorecards            (check #3)
  * test_pbo_never_claims_low_risk_when_all_lose_oos (check #8)
"""

import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.engine.strategy_validation import (  # noqa: E402
    DROPPED_BEST_TRADES,
    MIN_OOS_TRADES,
    PBO_NOT_INFORMATIVE,
    VERDICT_FAILED,
    VERDICT_INSUFFICIENT,
    VERDICT_NOT_VALIDATED,
    VERDICT_PASSED,
    StrategyValidationEngine,
    equity_drawdown_pct,
    t_statistic,
)

REPO = Path(__file__).resolve().parents[1]
FEEDS = REPO / "data" / "feeds"

VERDICTS = {VERDICT_PASSED, VERDICT_FAILED, VERDICT_INSUFFICIENT, VERDICT_NOT_VALIDATED}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _strategy(sid, r_multiples, start_equity=100_000.0, val_cutoff="2026-06-02"):
    """A minimal but structurally faithful strategy payload.

    Dates are real ISO calendar dates starting the day after the cutoff, so the
    string comparison the engine performs is meaningful.
    """
    from datetime import date, timedelta

    start = date.fromisoformat(val_cutoff) + timedelta(days=1)
    trades = []
    for i, r in enumerate(r_multiples):
        d = (start + timedelta(days=i)).isoformat()
        trades.append({
            "entry_date": d, "exit_date": d, "r_multiple": r,
            "return_pct": r, "strategy_id": sid,
        })
    curve = []
    for i, t in enumerate(trades):
        curve.append({
            "date": t["entry_date"], "partition": "TEST",
            "portfolio_equity": start_equity * (1.0 + t["return_pct"] / 100.0) ** (i + 1),
            "benchmark_price_spy": 500.0 + i, "benchmark_price_xiu": 50.0 + i * 0.1,
        })
    return {
        "strategy_id": sid, "strategy_name": sid,
        "all_trades": trades, "equity_curve": curve,
        "config": {"val_cutoff": val_cutoff},
        "metrics": {"deflated_sharpe_ratio": 0.10, "max_drawdown_pct": 5.0},
    }


@pytest.fixture
def engine():
    return StrategyValidationEngine(REPO)


# ---------------------------------------------------------------------------
# statistics primitives
# ---------------------------------------------------------------------------
def test_t_statistic_matches_hand_calculation():
    xs = [1.0, 2.0, 3.0, 4.0]
    # mean 2.5, sample sd sqrt(5/3)=1.29099, se 0.6455, t = 3.873
    assert t_statistic(xs) == pytest.approx(3.8729833, abs=1e-6)


def test_t_statistic_is_none_below_two_observations():
    assert t_statistic([]) is None
    assert t_statistic([1.0]) is None


def test_t_statistic_is_none_when_variance_is_zero():
    assert t_statistic([2.0, 2.0, 2.0]) is None


def test_drawdown_measures_peak_to_trough():
    # 100 -> 120 -> 60 -> 90 : worst trough is 60 against peak 120 = 50%
    assert equity_drawdown_pct([100.0, 120.0, 60.0, 90.0]) == pytest.approx(50.0)


def test_drawdown_of_a_monotonic_series_is_zero():
    assert equity_drawdown_pct([100.0, 110.0, 120.0]) == 0.0


def test_drawdown_of_empty_series_is_none():
    assert equity_drawdown_pct([]) is None


# ---------------------------------------------------------------------------
# partition selection — the bug this test locks in
# ---------------------------------------------------------------------------
def test_test_partition_uses_the_validation_cutoff_not_curve_dates(engine):
    """Intersecting entry dates against equity-curve dates silently drops trades.

    On live data that lost 63 of 98 test trades, because the curve has one row
    per trading day while several trades can share a day.
    """
    # Two trades on the SAME day, plus one on a day absent from the curve.
    strat = _strategy("X", [1.0, 1.0, 1.0])
    strat["all_trades"][1]["entry_date"] = strat["all_trades"][0]["entry_date"]
    strat["equity_curve"] = [c for c in strat["equity_curve"]
                             if c["date"] != strat["all_trades"][2]["entry_date"]]

    picked = engine.test_partition(strat)
    assert len(picked) == 3, "every trade entered after the cutoff must be counted"


def test_test_partition_returns_nothing_when_no_cutoff_is_declared(engine):
    strat = _strategy("X", [1.0, 2.0])
    strat["config"] = {}
    strat["equity_curve"] = []
    assert engine.test_partition(strat) == []


# ---------------------------------------------------------------------------
# gate mechanics
# ---------------------------------------------------------------------------
def test_gate8_fails_when_no_forward_log_exists(engine, tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "repo_root", tmp_path)
    status = engine.forward_log_status()
    assert status["exists"] is False
    assert status["entries"] == 0

    res = engine.evaluate(_strategy("X", [1.0] * 10), None, 0.0, 12, status)
    g8 = next(g for g in res.gates if g.gate_id == 8)
    assert g8.passed is False
    assert "No forward paper record" in g8.detail


def test_forward_log_entries_without_recorded_at_are_excluded(engine, tmp_path, monkeypatch):
    """An untimestamped entry is a replay, not a forward record."""
    monkeypatch.setattr(engine, "repo_root", tmp_path)
    log = tmp_path / StrategyValidationEngine.FORWARD_LOG_PATH
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(
        json.dumps({"strategy_id": "X", "r_multiple": 1.0}) + "\n"          # no recorded_at
        + json.dumps({"strategy_id": "X", "r_multiple": 1.0,
                      "recorded_at": "2026-11-15T00:00:00Z"}) + "\n"
    )
    status = engine.forward_log_status()
    assert status["timestamped_entries"] == 1
    assert status["untimestamped_excluded"] == 1


def test_gate7_removes_the_best_five_trades(engine):
    """A strategy carried by five winners must not pass gate 7."""
    carried = [5.0] * DROPPED_BEST_TRADES + [-1.0] * 20
    res = engine.evaluate(_strategy("CARRIED", carried), None, 0.0, 12,
                          engine.forward_log_status())
    g7 = next(g for g in res.gates if g.gate_id == 7)
    assert g7.passed is False, "removing the five winners leaves only losses"


def test_gate7_is_unevaluable_on_a_tiny_sample(engine):
    res = engine.evaluate(_strategy("TINY", [1.0, 2.0, 3.0]), None, 0.0, 12,
                          engine.forward_log_status())
    g7 = next(g for g in res.gates if g.gate_id == 7)
    assert g7.passed is None


def test_verdict_vocabulary_is_closed(engine):
    res = engine.evaluate(_strategy("X", [1.0] * 10), None, 0.0, 12,
                          engine.forward_log_status())
    assert res.verdict in VERDICTS


def test_no_verdict_can_be_passed_while_gate8_is_red(engine):
    """Gate 8 needs a real forward record; nothing here has one."""
    res = engine.evaluate(_strategy("X", [1.0] * 200), None, 0.99, 12,
                          engine.forward_log_status())
    assert res.verdict != VERDICT_PASSED


def test_conclusive_loss_fails_even_on_a_short_sample(engine):
    """A statistically significant loss is a failure, not 'insufficient sample'."""
    # 20 varied losses around -0.5%: mean < 0 with a large negative t.
    # (Values must vary — identical values have zero variance, so t is undefined.)
    losses = [-0.5 + 0.05 * (i % 5) for i in range(20)]
    res = engine.evaluate(_strategy("LOSING", losses), None, 0.0, 12,
                          engine.forward_log_status())
    assert res.oos_mean_return_pct < 0
    assert res.oos_t_stat <= -2.0
    assert res.verdict == VERDICT_FAILED


def test_inconclusive_small_sample_is_insufficient_not_failed(engine):
    # Mixed signs, tiny sample: no verdict is defensible either way.
    res = engine.evaluate(_strategy("MIXED", [1.0, -1.0, 0.5, -0.5]), None, 0.0, 12,
                          engine.forward_log_status())
    assert len(res.gates[3].observed.split()[0:1]) == 1  # sanity: g4 observed present
    assert res.verdict == VERDICT_INSUFFICIENT


# ---------------------------------------------------------------------------
# PBO — check #8
# ---------------------------------------------------------------------------
def test_pbo_never_claims_low_risk_when_all_lose_oos():
    """The exact misleading claim the overhaul forbids.

    A CPCV PBO of 0.0 is arithmetically possible when every strategy loses
    out-of-sample — there is no profitable 'best' to invert. Reporting that as
    LOW_OVERFITTING_RISK would be reassurance built on an artefact.
    """
    results = [
        StrategyValidationEngine(REPO).evaluate(
            _strategy("L%d" % i, [-0.5] * (MIN_OOS_TRADES + 10)), None, 0.0, 12,
            StrategyValidationEngine(REPO).forward_log_status())
        for i in range(4)
    ]
    assessment = StrategyValidationEngine.pbo_assessment(0.0, results)
    assert assessment["informative"] is False
    assert assessment["assessment"] == PBO_NOT_INFORMATIVE
    assert "LOW_OVERFITTING_RISK" not in assessment["assessment"]


def test_pbo_is_not_unlocked_by_a_handful_of_lucky_trades():
    """Positive mean on 10 trades is noise, not a profitable strategy."""
    eng = StrategyValidationEngine(REPO)
    fwd = eng.forward_log_status()
    losers = [eng.evaluate(_strategy("L%d" % i, [-0.5 + 0.05 * (j % 5) for j in range(150)]),
                           None, 0.0, 12, fwd)
              for i in range(3)]
    lucky = [eng.evaluate(_strategy("LUCKY", [3.0] * 10), None, 0.0, 12, fwd)]

    assessment = StrategyValidationEngine.pbo_assessment(0.0, losers + lucky)
    assert assessment["informative"] is False
    assert "LUCKY" in assessment["profitable_strategies"]
    assert assessment["credible_profitable_strategies"] == []


def test_pbo_reports_a_band_when_a_credible_winner_exists():
    eng = StrategyValidationEngine(REPO)
    fwd = eng.forward_log_status()
    winner = eng.evaluate(_strategy("WINNER", [0.5] * (MIN_OOS_TRADES + 10)), None, 0.0, 12, fwd)
    assessment = StrategyValidationEngine.pbo_assessment(0.9, [winner])
    assert assessment["informative"] is True
    assert assessment["assessment"] == "HIGH_OVERFITTING_RISK"
    assert assessment["value"] == 0.9


# ---------------------------------------------------------------------------
# published-feed checks (require data/feeds to be present)
# ---------------------------------------------------------------------------
requires_feed = pytest.mark.skipif(
    not (FEEDS / "strategy_validation.json").exists(),
    reason="data/feeds/strategy_validation.json not generated yet",
)


@requires_feed
def test_published_feed_matches_a_fresh_recomputation():
    """Check #2: the shipped numbers must equal what the code recomputes."""
    published = json.loads((FEEDS / "strategy_validation.json").read_text())
    fresh = StrategyValidationEngine(REPO).generate_feed(FEEDS)

    assert published["summary"]["strategies_evaluated"] == fresh["summary"]["strategies_evaluated"]
    assert published["pbo"]["value"] == fresh["pbo"]["value"]
    assert published["pbo"]["assessment"] == fresh["pbo"]["assessment"]

    by_id = {s["strategy_id"]: s for s in fresh["strategies"]}
    for pub in published["strategies"]:
        new = by_id[pub["strategy_id"]]
        assert pub["verdict"] == new["verdict"]
        assert pub["oos_trades"] == new["oos_trades"]
        for field in ("oos_mean_return_pct", "oos_expectancy_r", "oos_t_stat"):
            a, b = pub[field], new[field]
            if a is None or b is None:
                assert a == b
            else:
                assert abs(a - b) <= 0.01, "%s drifts for %s" % (field, pub["strategy_id"])


@requires_feed
def test_no_two_identical_scorecards():
    """Check #3: the defect that started this overhaul."""
    published = json.loads((FEEDS / "strategy_validation.json").read_text())
    seen = {}
    for s in published["strategies"]:
        key = (s["oos_trades"], s["oos_months"], s["oos_mean_return_pct"],
               s["oos_expectancy_r"], s["oos_t_stat"])
        assert key not in seen, (
            "%s and %s publish an identical record (%s) — this is the duplicated-"
            "scorecard defect the overhaul removed." % (seen.get(key), s["strategy_id"], key)
        )
        seen[key] = s["strategy_id"]


@requires_feed
def test_no_strategy_is_promoted_to_live():
    published = json.loads((FEEDS / "strategy_validation.json").read_text())
    assert published["summary"]["promoted_to_live"] == 0
    for s in published["strategies"]:
        assert s["verdict"] in VERDICTS
        assert s["verdict"] != VERDICT_PASSED or s["gates_failed"] == 0


@requires_feed
def test_every_gate_carries_a_requirement_and_an_observed_value():
    published = json.loads((FEEDS / "strategy_validation.json").read_text())
    for s in published["strategies"]:
        assert len(s["gates"]) == 8
        for g in s["gates"]:
            assert g["requirement"], "gate %d has no stated requirement" % g["gate_id"]
            assert g["observed"], "gate %d reports no observed value" % g["gate_id"]
            assert g["passed"] in (True, False, None)


@requires_feed
def test_pbo_in_published_feed_is_not_a_relabelled_dsr():
    """The retired code set pbo = clamp(1 - dsr, 0.02, 0.98). Guard against a regression."""
    published = json.loads((FEEDS / "strategy_validation.json").read_text())
    backtests = json.loads((FEEDS / "backtests.json").read_text())
    pbo = published["pbo"]["value"]
    if pbo is None:
        pytest.skip("no PBO published")
    for sid, strat in backtests["strategies"].items():
        dsr = strat["metrics"].get("deflated_sharpe_ratio")
        if dsr is None:
            continue
        fake = round(max(0.02, min(0.98, 1.0 - dsr)), 3)
        assert pbo != fake or published["pbo"]["informative"] is False, (
            "%s: published PBO %.3f equals clamp(1-DSR) — that is a relabelled DSR." % (sid, pbo)
        )
