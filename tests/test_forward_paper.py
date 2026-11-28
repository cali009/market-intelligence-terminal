"""
Tests for the forward paper ledger.

The important test here is not the hash chain, it is `test_forward_predicate_agrees_with_backtest`.
Gate 8 compares a forward record against a backtest. That comparison is meaningless unless
both paths fire on exactly the same conditions, so that agreement is asserted against real
historical signals taken from the published backtest ledger rather than against a mock.

The integrity tests deliberately tamper with a temporary copy of the log, never the real one.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engine.forward_paper import (  # noqa: E402
    ForwardPaperLedger, _hash, strategy_version_hash,
)
from src.data.db import db  # noqa: E402


@pytest.fixture
def ledger(tmp_path):
    return ForwardPaperLedger(path=tmp_path / "log.jsonl")


def _rec(rt, sid="S", **kw):
    return {"record_type": rt, "strategy_id": sid, "strategy_version_hash": "v1",
            "as_of_date": kw.pop("as_of_date", "2026-11-25"), **kw}


# -- integrity -------------------------------------------------------------
def test_empty_log_verifies_clean(ledger):
    v = ledger.verify()
    assert v["ok"] is True and v["records"] == 0
    assert "tamper-EVIDENT, not tamper-PROOF" in v["integrity_statement"]


def test_chain_extends_and_verifies(ledger):
    ledger.append([_rec("CENSUS"), _rec("CENSUS", as_of_date="2026-11-26")])
    v = ledger.verify()
    assert v["ok"] is True, v["violations"]
    assert v["records"] == 2
    recs = ledger.read()
    assert recs[0]["prev_hash"] is None
    assert recs[1]["prev_hash"] == recs[0]["record_hash"]


def test_editing_one_line_breaks_the_chain(ledger):
    ledger.append([_rec("CENSUS"), _rec("CENSUS", as_of_date="2026-11-26"),
                   _rec("CENSUS", as_of_date="2026-11-27")])
    assert ledger.verify()["ok"] is True

    lines = ledger.path.read_text().splitlines()
    tampered = json.loads(lines[1])
    tampered["signals_fired"] = 999
    lines[1] = json.dumps(tampered, sort_keys=True)
    ledger.path.write_text("\n".join(lines) + "\n")

    v = ledger.verify()
    assert v["ok"] is False
    # Both the edited line's own hash and the next line's prev_hash must fail.
    assert any("record_hash does not match" in x for x in v["violations"])
    assert any("chain broken" in x for x in v["violations"])


def test_unsettled_decision_is_flagged_only_once_a_later_session_exists(ledger):
    """A DECISION made today cannot be filled until tomorrow's open prints."""
    ledger.append([_rec("DECISION", decision_hash="d1", as_of_date="2026-11-27")])
    v = ledger.verify()
    assert v["ok"] is True, "a decision on the newest date is legitimately pending"

    ledger.append([_rec("CENSUS", as_of_date="2026-11-28")])
    v = ledger.verify()
    assert v["ok"] is False
    assert any("have no FILL" in x for x in v["violations"])

    ledger.append([_rec("FILL", decision_hash="d1", entered=True, as_of_date="2026-11-28")])
    assert ledger.verify()["ok"] is True


def test_strategy_version_drift_is_flagged(ledger):
    """Quietly retuning mid-stream must not blend into one flattering record."""
    ledger.append([_rec("CENSUS")])
    ledger.append([_rec("CENSUS", as_of_date="2026-11-26")])
    assert ledger.verify()["ok"] is True

    ledger.append([_rec("CENSUS", as_of_date="2026-11-27", strategy_version_hash="v2")])
    v = ledger.verify()
    assert v["ok"] is False
    assert any("changed mid-stream" in x for x in v["violations"])


def test_untimestamped_record_is_flagged(ledger):
    ledger.append([_rec("CENSUS")])
    lines = ledger.path.read_text().splitlines()
    rec = json.loads(lines[0])
    rec.pop("recorded_at")
    rec["record_hash"] = _hash(rec)
    ledger.path.write_text(json.dumps(rec, sort_keys=True) + "\n")
    assert any("missing recorded_at" in x for x in ledger.verify()["violations"])


def test_decision_records_no_entry_price(ledger, monkeypatch):
    """The entry is next session's open, unknowable now. A price here would be invented."""
    import src.engine.forward_paper as fp
    monkeypatch.setattr(fp, "TRACKED_STRATEGIES", ["PO3_LIQUIDITY_SWEEP"])
    ledger.capture("2026-11-25")

    census = [r for r in ledger.read() if r["record_type"] == "CENSUS"]
    assert census, "a CENSUS record must be written even when nothing fires"
    assert census[0]["securities_evaluated"] > 0

    decs = [r for r in ledger.read() if r["record_type"] == "DECISION"]
    if not decs:
        pytest.skip("no signal fired on this date")
    for d in decs:
        assert d["planned_entry"] == "NEXT_SESSION_OPEN"
        assert "entry_price" not in d and "stop_loss" not in d
        assert d["stop_rule"] and d["atr_at_signal"] > 0
        assert d["r_multiple"] is None


# -- the test that actually matters ---------------------------------------
@pytest.mark.parametrize("strategy_id", ["PO3_LIQUIDITY_SWEEP", "ADAPTIVE_DUAL_REGIME"])
def test_forward_predicate_agrees_with_backtest(strategy_id):
    """Every historical backtest signal must also fire on the forward path.

    The backtester fires on the bar PRIOR to entry and fills at the next open, so a trade
    whose signal_date is D must reproduce when the same predicate is run on D's bar. If
    the forward path ever diverges from the backtest, gate 8 would be comparing two
    different strategies and its verdict would be worthless.

    Only this direction is asserted. The forward path deliberately omits the backtester's
    position-limit, quarantine and per-day entry caps, so it may fire where the backtest
    did not trade; that is expected and not a disagreement.
    """
    from src.engine.backtester import backtest_engine
    from src.engine.technicals import TechnicalAnalysisEngine
    import pandas as pd

    bundle = json.load(open(Path(__file__).resolve().parent.parent / "data" / "feeds" / "backtests.json"))
    strat = bundle["strategies"].get(strategy_id)
    if not strat:
        pytest.skip(f"{strategy_id} not in backtests.json")
    trades = (strat.get("all_trades") or strat.get("trades") or [])[:12]
    if not trades:
        pytest.skip("no trades to check")

    checked = missed = 0
    for t in trades:
        sym, sig_date = t.get("symbol"), t.get("signal_date")
        if not sym or not sig_date:
            continue
        rows = db.execute_query(
            "SELECT b.trading_date, b.open, b.high, b.low, b.close, b.volume FROM bar_1d b "
            "JOIN security s ON s.security_id = b.security_id WHERE s.symbol = ? "
            "AND b.trading_date <= ? ORDER BY b.trading_date ASC;", (sym, sig_date))
        if len(rows) < 60:
            continue
        df = TechnicalAnalysisEngine.compute_all_features(pd.DataFrame(rows))
        bar = df.iloc[-1]
        if str(bar["trading_date"]) != str(sig_date):
            continue
        checked += 1
        if not backtest_engine._evaluate_signal_predicate(strategy_id, bar):
            missed += 1

    assert checked > 0, "no historical signals could be reproduced from bar_1d"
    assert missed == 0, (
        f"{missed}/{checked} backtest signals for {strategy_id} did NOT reproduce on the "
        f"forward path -- the two paths have diverged and gate 8 would compare different "
        f"strategies")


def test_version_hash_changes_when_the_predicate_changes():
    """The drift detector must actually detect drift, or it is decoration."""
    import src.engine.forward_paper as fp
    from src.engine.backtester import backtest_engine

    original = type(backtest_engine)._evaluate_signal_predicate
    h1 = strategy_version_hash("ADAPTIVE_DUAL_REGIME")
    try:
        def mutated(self, sid, bar):
            return False
        type(backtest_engine)._evaluate_signal_predicate = mutated
        h2 = strategy_version_hash("ADAPTIVE_DUAL_REGIME")
    finally:
        type(backtest_engine)._evaluate_signal_predicate = original
    assert h1 != h2, "version hash did not change when the predicate source changed"


def test_version_hash_is_stable_across_calls():
    assert strategy_version_hash("PO3_META_GATED") == strategy_version_hash("PO3_META_GATED")
    assert strategy_version_hash("PO3_META_GATED") != strategy_version_hash("PO3_LIQUIDITY_SWEEP")
