"""
Phase 34.5 Test Suite -- Execution Telemetry and Predicate 28

The properties this suite exists to protect:

  1. TELEMETRY COUNTS EVALUATIONS, NOT ROWS. `risk_governor_decision` stores one row per
     control -- twelve per governor run. Counting rows overstates everything by 12x. The
     same order_id can also be evaluated several times, because order_id is derived
     deterministically from client_order_id and a retry reproduces it.

  2. PREDICATE 28 BOUNDARIES ARE EXCLUSIVE, consistent with Predicates 26 and 27. Sitting
     exactly on a threshold must not fire. Each boundary is tested from both sides.

  3. A QUIET SESSION IS NOT A BREACH. Zero submitted notional must not divide into a
     spurious 100% rejection share, and a missing ledger table must not raise.

  4. THE ALERT IS CLASSIFIED AND PHRASED CORRECTLY. It is a portfolio-level risk-envelope
     finding, not a technical breakout, and it makes no claim about expected returns.
"""

import tempfile
from datetime import date, datetime, timezone

import pytest

from src.data.db import Database
from src.engine.quant_intel_sentinel import (
    P28_CRITICAL_REJECTIONS,
    P28_HARD_REJECTION_TRIGGER,
    P28_REJECTED_NOTIONAL_SHARE,
    quant_intel_sentinel,
)
from src.execution.execution_telemetry import (
    ExecutionTelemetry,
    compute_execution_telemetry,
)
from src.execution.gateway import ExecutionGateway
from src.execution.order_model import (
    Market,
    OrderRequest,
    OrderSide,
    OrderType,
    build_client_order_id,
    order_id_from_client,
)
from src.execution.risk_governor import GovernorContext, RiskGovernorLedger
from src.execution.state_machine import ExecutionLedger

SIGNAL_DATE = date(2026, 9, 20)


# ======================================================================================
# Helpers
# ======================================================================================

@pytest.fixture()
def db():
    return Database(db_url=f"sqlite:///{tempfile.mktemp(suffix='.db')}")


@pytest.fixture()
def gateway(db):
    return ExecutionGateway(
        ledger=ExecutionLedger(db=db),
        risk_ledger=RiskGovernorLedger(db=db),
    )


def make_request(symbol="AAPL", market=Market.US, qty=100.0, nonce=0, price=150.0):
    return OrderRequest(
        client_order_id=build_client_order_id(symbol, "BUY", qty, "MARKET", "S",
                                              SIGNAL_DATE, nonce),
        symbol=symbol, market=market, side=OrderSide.BUY, quantity=qty,
        order_type=OrderType.MARKET, signal_date=SIGNAL_DATE,
    )


def base_context(**over):
    d = dict(
        book_value_usd=100_000.0, free_cash_usd=100_000.0, reference_price=150.0,
        fx_cad_usd=0.73, symbol_sector="TECH",
        target_adapter_id="INTERNAL_SIMULATOR", target_adapter_is_external=False,
    )
    d.update(over)
    return GovernorContext(**d)


def session_of(db):
    """The UTC session the ledger rows were actually written in."""
    row = db.execute_query("SELECT MAX(decided_at) d FROM risk_governor_decision;")[0]
    return row["d"][:10] if row["d"] else datetime.now(timezone.utc).strftime("%Y-%m-%d")


class StubTelemetry:
    """Exposes the attributes Predicate 28 reads, without needing a populated ledger."""

    def __init__(self, hard_rejections=0, evaluations=0, rejected_share=0.0,
                 submitted=1_000.0, session="2026-10-03", controls=None):
        self.hard_rejections = hard_rejections
        self.evaluations = evaluations
        self.rejected_share = rejected_share
        self.submitted_notional_usd = submitted
        self.rejected_notional_usd = submitted * rejected_share
        self.session = session
        self.breached_controls = controls or {}


# ======================================================================================
# 1. Telemetry aggregation
# ======================================================================================

class TestTelemetryAggregation:

    def test_a_missing_table_returns_zeros_without_raising(self, db):
        """A platform that has never run the governor must not blow up the sentinel."""
        t = compute_execution_telemetry(db=db, session="2026-10-03")
        assert t.evaluations == 0
        assert t.hard_rejections == 0
        assert t.rejected_share == 0.0

    def test_an_empty_session_returns_zeros(self, gateway, db):
        gateway.submit(make_request(nonce=1), base_context())
        t = compute_execution_telemetry(db=db, session="1999-01-01")
        assert t.evaluations == 0
        assert t.submitted_notional_usd == 0.0

    def test_evaluations_are_counted_not_ledger_rows(self, gateway, db):
        """
        The load-bearing property. One governor run writes twelve rows (one per control);
        telemetry must report one evaluation, not twelve.
        """
        gateway.submit(make_request(nonce=2), base_context())
        rows = db.execute_query("SELECT COUNT(*) n FROM risk_governor_decision;")[0]["n"]
        t = compute_execution_telemetry(db=db, session=session_of(db))
        assert rows == 12, f"expected one control row per control, got {rows}"
        assert t.evaluations == 1
        assert t.orders == 1

    def test_hard_rejections_are_counted_once_per_evaluation(self, gateway, db):
        """A hard rejection writes a breach row for the offending control only."""
        gateway.submit(make_request(nonce=3), base_context(free_cash_usd=10.0))
        t = compute_execution_telemetry(db=db, session=session_of(db))
        assert t.hard_rejections == 1
        assert t.approvals == 0

    def test_the_gateway_prevents_re_governing_the_same_order(self, gateway):
        """
        The duplicate guard from Phase 34.1 means a resubmitted request is refused, so in
        normal operation evaluations and orders stay 1:1. Recorded here because it is the
        reason the next test has to write to the ledger directly.
        """
        from src.execution.state_machine import DuplicateOrderError
        gateway.submit(make_request(nonce=4), base_context(free_cash_usd=10.0))
        with pytest.raises(DuplicateOrderError):
            gateway.submit(make_request(nonce=4), base_context(free_cash_usd=10.0))

    def test_repeated_evaluations_of_one_order_each_count(self, db):
        """
        order_id is deterministic from client_order_id, so the ledger CAN hold several
        evaluations for one order -- historical data does. Each evaluation is an attempt
        that happened, so each is counted; that is what "rejections in the session" means.
        Written directly because the gateway's duplicate guard blocks this path.
        """
        from src.execution.risk_governor import PreTradeRiskGovernor
        gov = PreTradeRiskGovernor()
        risk_ledger = RiskGovernorLedger(db=db)
        req = make_request(nonce=4)
        order_id = order_id_from_client(req.client_order_id)
        for _ in range(3):
            risk_ledger.record(gov.evaluate(req, base_context(free_cash_usd=10.0)),
                               order_id=order_id)
        t = compute_execution_telemetry(db=db, session=session_of(db))
        assert t.orders == 1, "the same client order id is the same order"
        assert t.evaluations == 3, "but it was evaluated three times"
        assert t.hard_rejections == 3

    def test_overall_verdict_is_the_max_severity_across_controls(self, gateway, db):
        """
        A single evaluation writes APPROVE for eleven controls and HARD_REJECT for one.
        The evaluation is a hard rejection, not eleven approvals.
        """
        gateway.submit(make_request(nonce=5), base_context(free_cash_usd=10.0))
        t = compute_execution_telemetry(db=db, session=session_of(db))
        assert t.hard_rejections == 1
        assert t.approvals == 0
        assert t.soft_limits == 0

    def test_soft_limits_are_counted_separately(self, gateway, db):
        gateway.submit(make_request(nonce=6), base_context(portfolio_beta=1.45))
        t = compute_execution_telemetry(db=db, session=session_of(db))
        assert t.soft_limits == 1
        assert t.hard_rejections == 0

    def test_mixed_session_totals_reconcile(self, gateway, db):
        """Approvals + soft + hard must equal the evaluation count exactly."""
        for i in range(3):
            gateway.submit(make_request(nonce=10 + i), base_context())
        for i in range(2):
            gateway.submit(make_request(nonce=20 + i), base_context(free_cash_usd=10.0))
        gateway.submit(make_request(nonce=30), base_context(portfolio_beta=1.45))
        t = compute_execution_telemetry(db=db, session=session_of(db))
        assert t.evaluations == 6
        assert t.approvals + t.soft_limits + t.hard_rejections == t.evaluations
        assert t.hard_rejections == 2

    def test_notional_is_summed_per_evaluation(self, gateway, db):
        gateway.submit(make_request(qty=100.0, nonce=41), base_context())
        t = compute_execution_telemetry(db=db, session=session_of(db))
        assert t.submitted_notional_usd == pytest.approx(100.0 * 150.0)

    def test_rejected_notional_only_counts_hard_rejections(self, gateway, db):
        gateway.submit(make_request(qty=100.0, nonce=42), base_context())
        gateway.submit(make_request(qty=100.0, nonce=43), base_context(free_cash_usd=10.0))
        t = compute_execution_telemetry(db=db, session=session_of(db))
        assert t.submitted_notional_usd == pytest.approx(2 * 100.0 * 150.0)
        assert t.rejected_notional_usd == pytest.approx(100.0 * 150.0)
        assert t.rejected_share == pytest.approx(0.5)

    def test_breached_controls_are_counted_per_evaluation(self, gateway, db):
        """Same 12x hazard: a control that breaches must count once per evaluation."""
        for i in range(3):
            gateway.submit(make_request(nonce=50 + i), base_context(free_cash_usd=10.0))
        t = compute_execution_telemetry(db=db, session=session_of(db))
        assert t.breached_controls.get("C1") == 3

    def test_top_rejected_symbols_are_ranked(self, gateway, db):
        for i in range(2):
            gateway.submit(make_request(symbol="SHOP", market=Market.CA, nonce=60 + i),
                           base_context(free_cash_usd=10.0))
        gateway.submit(make_request(symbol="AAPL", nonce=70),
                       base_context(free_cash_usd=10.0))
        t = compute_execution_telemetry(db=db, session=session_of(db))
        assert t.top_rejected_symbols[0]["symbol"] == "SHOP"
        assert len(t.top_rejected_symbols) <= 5

    def test_to_dict_round_trips_the_figures(self, gateway, db):
        gateway.submit(make_request(nonce=80), base_context(free_cash_usd=10.0))
        d = compute_execution_telemetry(db=db, session=session_of(db)).to_dict()
        assert d["hard_rejections"] == 1
        assert d["generated_at"]
        assert "rejected_share" in d


class TestRejectedShare:

    def test_zero_submitted_does_not_divide_by_zero(self):
        t = ExecutionTelemetry(session="2026-10-03", submitted_notional_usd=0.0,
                               rejected_notional_usd=0.0)
        assert t.rejected_share == 0.0

    def test_zero_submitted_with_rejected_notional_stays_finite(self):
        """
        Degenerate but must not raise or produce inf. Guarding only the denominator being
        exactly zero is what prevents a quiet session from looking like a 100% breach.
        """
        t = ExecutionTelemetry(session="2026-10-03", submitted_notional_usd=0.0,
                               rejected_notional_usd=500.0)
        assert t.rejected_share == 0.0

    def test_share_is_a_ratio_not_a_percentage(self):
        t = ExecutionTelemetry(session="x", submitted_notional_usd=1000.0,
                               rejected_notional_usd=250.0)
        assert t.rejected_share == pytest.approx(0.25)


# ======================================================================================
# 2. Predicate 28 boundaries -- exclusive
# ======================================================================================

class TestPredicate28Boundaries:

    def test_exactly_at_the_count_boundary_does_not_fire(self):
        """3 rejections sits ON the boundary. Exclusive means it does not fire."""
        alerts = quant_intel_sentinel.evaluate_execution_sentinel(
            StubTelemetry(hard_rejections=P28_HARD_REJECTION_TRIGGER, evaluations=10))
        assert alerts == []

    def test_one_past_the_count_boundary_fires(self):
        alerts = quant_intel_sentinel.evaluate_execution_sentinel(
            StubTelemetry(hard_rejections=P28_HARD_REJECTION_TRIGGER + 1, evaluations=10))
        assert len(alerts) == 1
        assert alerts[0].severity == "WARNING"

    def test_exactly_at_the_share_boundary_does_not_fire(self):
        alerts = quant_intel_sentinel.evaluate_execution_sentinel(
            StubTelemetry(rejected_share=P28_REJECTED_NOTIONAL_SHARE, evaluations=10))
        assert alerts == []

    def test_just_past_the_share_boundary_fires(self):
        alerts = quant_intel_sentinel.evaluate_execution_sentinel(
            StubTelemetry(rejected_share=P28_REJECTED_NOTIONAL_SHARE + 0.0001, evaluations=10))
        assert len(alerts) == 1

    def test_exactly_at_the_critical_boundary_is_still_a_warning(self):
        alerts = quant_intel_sentinel.evaluate_execution_sentinel(
            StubTelemetry(hard_rejections=P28_CRITICAL_REJECTIONS, evaluations=20))
        assert len(alerts) == 1
        assert alerts[0].severity == "WARNING"

    def test_past_the_critical_boundary_escalates(self):
        alerts = quant_intel_sentinel.evaluate_execution_sentinel(
            StubTelemetry(hard_rejections=P28_CRITICAL_REJECTIONS + 1, evaluations=20))
        assert alerts[0].severity == "CRITICAL"

    def test_a_clean_session_produces_no_alert(self):
        assert quant_intel_sentinel.evaluate_execution_sentinel(StubTelemetry()) == []

    def test_below_both_boundaries_produces_no_alert(self):
        alerts = quant_intel_sentinel.evaluate_execution_sentinel(
            StubTelemetry(hard_rejections=2, rejected_share=0.24, evaluations=10))
        assert alerts == []

    @pytest.mark.parametrize("hard,share", [
        (4, 0.0), (0, 0.26), (4, 0.26), (11, 0.9),
    ])
    def test_either_trigger_alone_is_sufficient(self, hard, share):
        """The triggers are OR-ed, so either one on its own must fire."""
        alerts = quant_intel_sentinel.evaluate_execution_sentinel(
            StubTelemetry(hard_rejections=hard, rejected_share=share, evaluations=20))
        assert len(alerts) == 1


# ======================================================================================
# 3. Alert content
# ======================================================================================

class TestPredicate28Content:

    @pytest.fixture()
    def alert(self):
        alerts = quant_intel_sentinel.evaluate_execution_sentinel(
            StubTelemetry(hard_rejections=5, evaluations=20, rejected_share=0.4,
                          controls={"C1": 3, "C6": 2}))
        assert len(alerts) == 1
        return alerts[0]

    def test_attributed_to_the_portfolio(self, alert):
        """Session-level finding, so it uses the same convention as P10/P11."""
        assert alert.symbol == "PORTFOLIO"

    def test_predicate_type_and_action(self, alert):
        assert alert.predicate_type == "PRE_TRADE_RISK_BREACH"
        assert alert.action_required == "REVIEW_POSITION_SIZING"

    def test_trigger_level_is_the_count_boundary(self, alert):
        assert alert.trigger_level == float(P28_HARD_REJECTION_TRIGGER)
        assert alert.current_price == 5.0

    def test_body_reports_which_trigger_fired(self, alert):
        """Both criteria are stated, and the one that fired is named."""
        assert "hard rejections against a boundary of 3" in alert.body
        assert "rejected notional at 40.0% of submitted" in alert.body

    def test_body_names_the_breached_controls(self, alert):
        assert "C1 (3)" in alert.body
        assert "C6 (2)" in alert.body

    def test_body_makes_no_return_claim(self, alert):
        """
        This is decision-support. The alert must say explicitly that it is not a statement
        about expected returns.
        """
        assert "not a statement about expected returns" in alert.body

    def test_body_does_not_present_anything_as_certain(self, alert):
        for banned in ("guarantee", "will profit", "certain", "risk-free"):
            assert banned not in alert.body.lower()

    def test_alert_passes_the_compliance_linter(self, alert):
        """convert_to_system_alerts calls linter.assert_clean; this must not raise."""
        records = quant_intel_sentinel.convert_to_system_alerts([alert])
        assert len(records) == 1

    def test_taxonomy_is_portfolio_circuit_breaker(self, alert):
        """
        PRE_TRADE_RISK_BREACH matches none of the substring rules in
        convert_to_system_alerts, so without an explicit branch it would fall through to
        TECHNICAL_BREAKOUT and misdescribe a portfolio-level finding.
        """
        rec = quant_intel_sentinel.convert_to_system_alerts([alert])[0]
        assert rec.taxonomy == "PORTFOLIO_CIRCUIT_BREAKER"

    def test_critical_alert_escalates_channels(self):
        alerts = quant_intel_sentinel.evaluate_execution_sentinel(
            StubTelemetry(hard_rejections=12, evaluations=20))
        rec = quant_intel_sentinel.convert_to_system_alerts(alerts)[0]
        assert rec.severity == "CRITICAL"
        assert "SMS" in rec.channels

    def test_dedupe_key_is_stable_within_a_session(self, alert):
        rec = quant_intel_sentinel.convert_to_system_alerts([alert])[0]
        assert rec.dedupe_key.startswith("PORTFOLIO:PRE_TRADE_RISK_BREACH:")


# ======================================================================================
# 4. End to end through the real ledger
# ======================================================================================

class TestEndToEnd:

    def test_a_breaching_session_produces_an_alert(self, gateway, db):
        """Drive the real governor enough times to trip Predicate 28, then evaluate it."""
        for i in range(4):
            gateway.submit(make_request(nonce=100 + i), base_context(free_cash_usd=10.0))
        t = compute_execution_telemetry(db=db, session=session_of(db))
        assert t.hard_rejections == 4
        alerts = quant_intel_sentinel.evaluate_execution_sentinel(t)
        assert len(alerts) == 1
        assert alerts[0].severity == "WARNING"
        assert "4 hard rejections" in alerts[0].headline

    def test_a_healthy_session_produces_no_alert(self, gateway, db):
        for i in range(5):
            gateway.submit(make_request(nonce=200 + i), base_context())
        t = compute_execution_telemetry(db=db, session=session_of(db))
        assert t.hard_rejections == 0
        assert quant_intel_sentinel.evaluate_execution_sentinel(t) == []

    def test_a_share_only_breach_produces_an_alert(self, gateway, db):
        """One large rejection among few submissions trips the share trigger alone."""
        gateway.submit(make_request(qty=10.0, nonce=300), base_context())
        gateway.submit(make_request(qty=1000.0, nonce=301), base_context(free_cash_usd=10.0))
        t = compute_execution_telemetry(db=db, session=session_of(db))
        assert t.hard_rejections == 1, "below the count trigger"
        assert t.rejected_share > P28_REJECTED_NOTIONAL_SHARE
        alerts = quant_intel_sentinel.evaluate_execution_sentinel(t)
        assert len(alerts) == 1
        assert "rejected notional at" in alerts[0].body


# ======================================================================================
# 5. Integration through the portfolio-level sentinel path
# ======================================================================================

class TestPortfolioLevelIntegration:

    """
    Predicate 28 is reachable two ways: directly, and through
    evaluate_portfolio_level_sentinels, which is how the orchestrator reaches it. Both must
    behave identically, and the orchestrator path must tolerate a missing ledger.
    """

    def _minimal_kwargs(self):
        """The other predicates need real metric surfaces; empty dicts keep them inert."""
        return dict(positions={}, stress_metrics={}, avg_pairwise_corr=0.45)

    def test_telemetry_none_produces_no_predicate_28_alert(self):
        alerts = quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            **self._minimal_kwargs(), execution_telemetry=None)
        assert not any(a.predicate_type == "PRE_TRADE_RISK_BREACH" for a in alerts)

    def test_a_breaching_session_reaches_the_alert_list(self):
        tel = StubTelemetry(hard_rejections=6, evaluations=20, rejected_share=0.5)
        alerts = quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            **self._minimal_kwargs(), execution_telemetry=tel)
        p28 = [a for a in alerts if a.predicate_type == "PRE_TRADE_RISK_BREACH"]
        assert len(p28) == 1
        assert p28[0].symbol == "PORTFOLIO"

    def test_both_paths_produce_the_same_alert(self):
        """Delegation, not a second implementation -- the two must not diverge."""
        tel = StubTelemetry(hard_rejections=7, evaluations=20, rejected_share=0.6)
        direct = quant_intel_sentinel.evaluate_execution_sentinel(tel)
        via = [a for a in quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            **self._minimal_kwargs(), execution_telemetry=tel)
            if a.predicate_type == "PRE_TRADE_RISK_BREACH"]
        assert len(direct) == len(via) == 1
        assert direct[0].headline == via[0].headline
        assert direct[0].body == via[0].body
        assert direct[0].severity == via[0].severity

    def test_a_clean_session_adds_nothing(self):
        before = quant_intel_sentinel.evaluate_portfolio_level_sentinels(**self._minimal_kwargs())
        after = quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            **self._minimal_kwargs(), execution_telemetry=StubTelemetry())
        assert len(after) == len(before)

    def test_the_orchestrator_helper_returns_telemetry_not_raises(self):
        """The helper must degrade to None rather than take down orchestration."""
        from src.engine.portfolio_orchestrator import _safe_execution_telemetry
        t = _safe_execution_telemetry()
        assert t is None or hasattr(t, "hard_rejections")
