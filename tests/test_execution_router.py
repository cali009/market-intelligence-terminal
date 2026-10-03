"""
Phase 34.4 Test Suite -- Adapter Routing and Market Firewall

The properties this suite exists to protect:

  1. THE CANADIAN FIREWALL IS ABSOLUTE. A Canadian symbol never reaches an adapter marked
     external -- not by automatic selection, not by explicit demand, and not because a
     caller asserted the adapter was internal. This is CIRO DMR 3200, not a preference.
  2. THE LICENSE GATE FAILS CLOSED. `license_cleared` defaults to False, so an adapter that
     forgot to declare clearance is refused rather than silently usable.
  3. THE KILL SWITCH IS HONOURABLE AND REVERSIBLE. EXTERNAL_ONLY must fall through to the
     internal simulator -- the operator's intent is "stop using the broker", not "stop".
  4. NOTHING IS SILENTLY SUBSTITUTED. An unsupported order type is refused, never rewritten
     into a market order; an explicitly named adapter is never rerouted behind the caller's
     back.
  5. ROUTING PRECEDES THE GOVERNOR and is authoritative about the route, so the governor's
     own Canadian firewall (C12) is fed from what the router resolved.

Each test uses an isolated temp database so the kill switch event log cannot leak between
tests or into the platform database.
"""

import tempfile
from datetime import date

import pytest

from src.data.db import Database
from src.execution.adapters.base import AdapterAck, AdapterCapabilities, BrokerAdapter
from src.execution.adapters.internal_simulator import InternalSimulatorAdapter
from src.execution.gateway import ExecutionGateway
from src.execution.order_model import (
    Actor,
    Market,
    OrderRequest,
    OrderSide,
    OrderState,
    OrderType,
    build_client_order_id,
)
from src.execution.risk_governor import GovernorContext
from src.execution.router import (
    KILL_ALL,
    KILL_EXTERNAL_ONLY,
    STRATEGY_PREFIX,
    SYMBOL_PREFIX,
    AdapterRegistry,
    AdapterRouter,
    KillSwitch,
    is_valid_scope,
)
from src.execution.state_machine import ExecutionLedger

SIGNAL_DATE = date(2026, 9, 20)


# ======================================================================================
# Test doubles
# ======================================================================================

class StubBroker(BrokerAdapter):
    """An external broker that is licensed and configured, but supports only MARKET/LIMIT."""
    adapter_id = "STUB_BROKER"
    markets = frozenset({Market.US, Market.CA})
    capabilities = AdapterCapabilities(market=True, limit=True)
    is_external = True
    license_cleared = True

    def is_configured(self) -> bool:
        return True

    def validate_order(self, request) -> AdapterAck:
        return AdapterAck(True)


class UnclearedBroker(StubBroker):
    """Licensed terms never cleared. Must be refused outright."""
    adapter_id = "UNCLEARED_BROKER"
    license_cleared = False


class UnconfiguredBroker(StubBroker):
    """Licensed but missing credentials."""
    adapter_id = "UNCONFIGURED_BROKER"

    def is_configured(self) -> bool:
        return False


class USOnlyBroker(StubBroker):
    """Serves the US only."""
    adapter_id = "US_ONLY_BROKER"
    markets = frozenset({Market.US})


class NaiveAdapter(BrokerAdapter):
    """
    Overrides nothing. Exercises the base-class defaults: external and not license-cleared.
    A new adapter written this way must fail closed, not become quietly usable.
    """
    adapter_id = "NAIVE_ADAPTER"
    markets = frozenset({Market.US})

    def is_configured(self) -> bool:
        return True

    def validate_order(self, request) -> AdapterAck:
        return AdapterAck(True)


@pytest.fixture()
def db():
    return Database(db_url=f"sqlite:///{tempfile.mktemp(suffix='.db')}")


@pytest.fixture()
def kill_switch(db):
    return KillSwitch(db=db)


@pytest.fixture()
def registry(db):
    reg = AdapterRegistry()
    reg.register(InternalSimulatorAdapter(ledger=ExecutionLedger(db=db), db=db))
    reg.register(StubBroker())
    return reg


@pytest.fixture()
def router(registry, kill_switch):
    return AdapterRouter(registry=registry, kill_switch=kill_switch)


def make_request(symbol="AAPL", market=Market.US, order_type=OrderType.MARKET,
                 strategy="momentum", side=OrderSide.BUY, qty=10.0, nonce=0,
                 adapter_hint=None):
    """
    OrderRequest validates its own price requirements, so LIMIT needs limit_price and STOP
    needs stop_price. Supplying them here keeps the routing tests about routing.
    """
    limit_price = 100.0 if order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) else None
    stop_price = 90.0 if order_type in (OrderType.STOP, OrderType.STOP_LIMIT) else None
    return OrderRequest(
        client_order_id=build_client_order_id(symbol, side.value, qty, order_type.value,
                                              strategy, SIGNAL_DATE, nonce),
        symbol=symbol, market=market, side=side, quantity=qty, order_type=order_type,
        limit_price=limit_price, stop_price=stop_price, adapter_hint=adapter_hint,
        strategy_id=strategy, signal_date=SIGNAL_DATE,
    )


def base_context(**over):
    d = dict(
        book_value_usd=100_000.0, free_cash_usd=100_000.0, reference_price=150.0,
        fx_cad_usd=0.73, symbol_sector="TECH",
        target_adapter_id="INTERNAL_SIMULATOR", target_adapter_is_external=False,
    )
    d.update(over)
    return GovernorContext(**d)


def outcome_for(decision, control_id):
    return next(o for o in decision.outcomes if o.control_id == control_id)


# ======================================================================================
# 1. Scope parsing
# ======================================================================================

class TestScopeValidation:

    @pytest.mark.parametrize("scope", [KILL_ALL, KILL_EXTERNAL_ONLY, "SYMBOL:SHOP",
                                       "STRATEGY:momentum", "SYMBOL:X", "STRATEGY:x"])
    def test_valid_scopes(self, scope):
        assert is_valid_scope(scope) is True

    @pytest.mark.parametrize("scope", ["SYMBOL:", "STRATEGY:", "EVERYTHING", "", "symbol:SHOP",
                                       "SYMBOL", "EXTERNAL"])
    def test_invalid_scopes(self, scope):
        """Prefixes without a value, and unknown scopes, must be refused."""
        assert is_valid_scope(scope) is False

    def test_engage_rejects_an_invalid_scope(self, kill_switch):
        with pytest.raises(ValueError):
            kill_switch.engage("NOT_A_SCOPE")

    def test_disengage_rejects_an_invalid_scope(self, kill_switch):
        with pytest.raises(ValueError):
            kill_switch.disengage("SYMBOL:")


# ======================================================================================
# 2. Kill switch
# ======================================================================================

class TestKillSwitch:

    def test_starts_disengaged(self, kill_switch):
        assert kill_switch.active_scopes() == []
        assert kill_switch.is_halted() is False

    def test_engage_makes_a_scope_active(self, kill_switch):
        kill_switch.engage(KILL_ALL, reason="incident")
        assert kill_switch.active_scopes() == [KILL_ALL]
        assert kill_switch.is_halted() is True

    def test_disengage_clears_it(self, kill_switch):
        kill_switch.engage(KILL_ALL)
        kill_switch.disengage(KILL_ALL)
        assert kill_switch.active_scopes() == []
        assert kill_switch.is_halted() is False

    def test_engage_is_idempotent(self, kill_switch):
        kill_switch.engage(KILL_ALL, reason="first")
        kill_switch.engage(KILL_ALL, reason="second")
        assert len(kill_switch.events()) == 1, "re-engaging must not append a second event"

    def test_disengage_is_idempotent(self, kill_switch):
        kill_switch.disengage(KILL_ALL)
        assert kill_switch.events() == []

    def test_reengage_after_disengage_records_both(self, kill_switch):
        kill_switch.engage(KILL_ALL)
        kill_switch.disengage(KILL_ALL)
        kill_switch.engage(KILL_ALL)
        assert [e["action"] for e in kill_switch.events()] == ["ENGAGE", "DISENGAGE", "ENGAGE"]
        assert kill_switch.is_halted() is True

    def test_multiple_scopes_coexist(self, kill_switch):
        kill_switch.engage("SYMBOL:SHOP")
        kill_switch.engage("STRATEGY:momentum")
        assert set(kill_switch.active_scopes()) == {"SYMBOL:SHOP", "STRATEGY:momentum"}

    def test_state_survives_a_new_instance(self, db):
        """
        State is derived from the event log, so a fresh instance sees the same active set.
        There is no in-memory flag that could drift from the audit record.
        """
        KillSwitch(db=db).engage(KILL_ALL, reason="incident")
        assert KillSwitch(db=db).is_halted() is True

    def test_events_are_audited_with_actor_and_reason(self, kill_switch):
        kill_switch.engage(KILL_ALL, reason="runaway algo", actor=Actor.USER)
        ev = kill_switch.events()[0]
        assert ev["scope"] == KILL_ALL
        assert ev["action"] == "ENGAGE"
        assert ev["actor"] == Actor.USER.value
        assert ev["reason"] == "runaway algo"
        assert ev["recorded_at"]

    def test_default_actor_is_kill_switch(self, kill_switch):
        kill_switch.engage(KILL_ALL)
        assert kill_switch.events()[0]["actor"] == Actor.KILL_SWITCH.value

    def test_reporting_precedence_is_deterministic(self, kill_switch):
        """Engaged out of precedence order, reported in precedence order."""
        kill_switch.engage("STRATEGY:momentum")
        kill_switch.engage("SYMBOL:SHOP")
        kill_switch.engage(KILL_EXTERNAL_ONLY)
        kill_switch.engage(KILL_ALL)
        assert kill_switch.active_scopes() == [
            KILL_ALL, KILL_EXTERNAL_ONLY, "SYMBOL:SHOP", "STRATEGY:momentum",
        ]


class TestKillSwitchMatching:

    def test_all_blocks_everything(self, router, kill_switch):
        kill_switch.engage(KILL_ALL)
        for req in [make_request(), make_request("SHOP", Market.CA, nonce=1)]:
            d = router.route(req)
            assert d.allowed is False
            assert d.kill_switch_scope == KILL_ALL

    def test_symbol_scope_blocks_only_that_symbol(self, router, kill_switch):
        kill_switch.engage("SYMBOL:SHOP")
        assert router.route(make_request("SHOP", Market.CA)).allowed is False
        assert router.route(make_request("AAPL", Market.US, nonce=1)).allowed is True

    def test_symbol_scope_is_case_and_value_exact(self, router, kill_switch):
        kill_switch.engage("SYMBOL:SHOP")
        assert router.route(make_request("SHOPX", Market.US, nonce=2)).allowed is True

    def test_strategy_scope_blocks_only_that_strategy(self, router, kill_switch):
        kill_switch.engage("STRATEGY:momentum")
        assert router.route(make_request(strategy="momentum")).allowed is False
        assert router.route(make_request(strategy="meanrev", nonce=3)).allowed is True

    def test_external_only_blocks_an_external_adapter(self, router, kill_switch):
        kill_switch.engage(KILL_EXTERNAL_ONLY)
        d = router.route(make_request(nonce=4), preferred_adapter_id="STUB_BROKER")
        assert d.allowed is False
        assert d.kill_switch_scope == KILL_EXTERNAL_ONLY

    def test_external_only_falls_through_to_internal(self, router, kill_switch):
        """
        The load-bearing property: EXTERNAL_ONLY means "stop using the broker", not "stop
        working". Automatic selection must land on the internal simulator.
        """
        kill_switch.engage(KILL_EXTERNAL_ONLY)
        d = router.route(make_request("SHOP", Market.CA, nonce=5))
        assert d.allowed is True
        assert d.adapter_id == "INTERNAL_SIMULATOR"

    def test_external_only_does_not_block_an_internal_route(self, router, kill_switch):
        kill_switch.engage(KILL_EXTERNAL_ONLY)
        assert router.route(make_request(nonce=6)).is_external is False

    def test_disengaging_restores_routing(self, router, kill_switch):
        kill_switch.engage(KILL_ALL)
        assert router.route(make_request(nonce=7)).allowed is False
        kill_switch.disengage(KILL_ALL)
        assert router.route(make_request(nonce=7)).allowed is True


# ======================================================================================
# 3. Registry
# ======================================================================================

class TestRegistry:

    def test_register_and_retrieve(self, registry):
        assert registry.get("STUB_BROKER").adapter_id == "STUB_BROKER"
        assert registry.get("NOPE") is None
        assert registry.get(None) is None

    def test_duplicate_registration_is_refused(self, registry):
        with pytest.raises(ValueError):
            registry.register(StubBroker())

    def test_replace_is_opt_in(self, registry):
        registry.register(StubBroker(), replace=True)
        assert len(registry.all()) == 2

    def test_for_market_filters_by_market(self, db):
        reg = AdapterRegistry()
        reg.register(USOnlyBroker())
        reg.register(StubBroker())
        assert [a.adapter_id for a in reg.for_market(Market.CA)] == ["STUB_BROKER"]
        assert len(reg.for_market(Market.US)) == 2

    def test_exclude_external_removes_external_adapters(self, registry):
        """This is how the Canadian firewall is applied during automatic selection."""
        all_ca = [a.adapter_id for a in registry.for_market(Market.CA)]
        internal = [a.adapter_id for a in registry.for_market(Market.CA, include_external=False)]
        assert "STUB_BROKER" in all_ca
        assert "STUB_BROKER" not in internal
        assert internal == ["INTERNAL_SIMULATOR"]

    def test_registration_order_is_preference_order(self, db):
        reg = AdapterRegistry()
        reg.register(StubBroker())
        reg.register(InternalSimulatorAdapter(ledger=ExecutionLedger(db=db), db=db))
        assert [a.adapter_id for a in reg.for_market(Market.US)][0] == "STUB_BROKER"


# ======================================================================================
# 4. The Canadian firewall
# ======================================================================================

class TestCanadianFirewall:

    """CIRO / IIROC Dealer Member Rule 3200 A.1.(b)(i) and NI 23-103 §1.2(1)."""

    def test_canadian_symbol_never_auto_routes_to_an_external_adapter(self, router):
        """The simulator is registered second, so an unchecked router would prefer it."""
        d = router.route(make_request("SHOP", Market.CA))
        assert d.allowed is True
        assert d.is_external is False
        assert d.adapter_id == "INTERNAL_SIMULATOR"

    def test_explicit_demand_for_an_external_adapter_is_refused(self, router):
        d = router.route(make_request("SHOP", Market.CA, nonce=1),
                         preferred_adapter_id="STUB_BROKER")
        assert d.allowed is False
        from src.execution.order_model import RejectionReason
        assert d.reason_code == RejectionReason.CANADIAN_EXTERNAL_ROUTING_BLOCKED

    def test_the_block_names_the_regulation(self, router):
        d = router.route(make_request("SHOP", Market.CA, nonce=2),
                         preferred_adapter_id="STUB_BROKER")
        assert "CIRO" in d.detail
        assert "SHOP" in d.detail

    def test_the_firewall_precedes_the_license_gate(self, router, db):
        """
        An adapter that is both external AND unlicensed must be reported as a regulatory
        block for a Canadian symbol, because that is the more fundamental reason.
        """
        router.registry.register(UnclearedBroker())
        d = router.route(make_request("SHOP", Market.CA, nonce=3),
                         preferred_adapter_id="UNCLEARED_BROKER")
        from src.execution.order_model import RejectionReason
        assert d.reason_code == RejectionReason.CANADIAN_EXTERNAL_ROUTING_BLOCKED

    def test_us_symbols_may_route_externally(self, router):
        """The firewall is jurisdictional, not a blanket ban on external adapters."""
        d = router.route(make_request("AAPL", Market.US, nonce=4),
                         preferred_adapter_id="STUB_BROKER")
        assert d.allowed is True
        assert d.is_external is True

    @pytest.mark.parametrize("symbol", ["SHOP", "XIU", "RY", "ENB", "CNR"])
    def test_holds_for_every_canadian_symbol(self, router, symbol):
        d = router.route(make_request(symbol, Market.CA, nonce=hash(symbol) % 1000),
                         preferred_adapter_id="STUB_BROKER")
        assert d.allowed is False, f"{symbol} must not route externally"

    def test_an_explicit_block_is_not_rerouted_behind_the_callers_back(self, router):
        """
        A caller that names an external adapter for a Canadian symbol must be told it was
        blocked. Silently rerouting to the simulator would hide the block.
        """
        d = router.route(make_request("SHOP", Market.CA, nonce=5),
                         preferred_adapter_id="STUB_BROKER")
        assert d.allowed is False
        assert d.adapter_id == "STUB_BROKER", "the refused adapter must be reported"


# ======================================================================================
# 5. The license gate
# ======================================================================================

class TestLicenseGate:

    def test_an_uncleared_adapter_is_refused(self, router):
        router.registry.register(UnclearedBroker())
        from src.execution.order_model import RejectionReason
        d = router.route(make_request(nonce=11), preferred_adapter_id="UNCLEARED_BROKER")
        assert d.allowed is False
        assert d.reason_code == RejectionReason.ADAPTER_LICENSE_NOT_CLEARED

    def test_the_base_class_fails_closed(self):
        """
        A new adapter that overrides nothing inherits is_external=True and
        license_cleared=False. Forgetting the flags must make it unusable, not usable.
        """
        naive = NaiveAdapter()
        assert naive.is_external is True
        assert naive.license_cleared is False

    def test_a_naive_adapter_is_refused_by_the_license_gate(self, router):
        router.registry.register(NaiveAdapter())
        from src.execution.order_model import RejectionReason
        d = router.route(make_request(nonce=12), preferred_adapter_id="NAIVE_ADAPTER")
        assert d.allowed is False
        assert d.reason_code == RejectionReason.ADAPTER_LICENSE_NOT_CLEARED

    def test_a_cleared_adapter_passes_the_gate(self, router):
        d = router.route(make_request(nonce=13), preferred_adapter_id="STUB_BROKER")
        assert d.allowed is True


# ======================================================================================
# 6. Capability negotiation
# ======================================================================================

class TestCapabilityNegotiation:

    def test_an_unsupported_order_type_is_refused(self, router):
        from src.execution.order_model import RejectionReason
        d = router.route(make_request(order_type=OrderType.VWAP, nonce=21),
                         preferred_adapter_id="STUB_BROKER")
        assert d.allowed is False
        assert d.reason_code == RejectionReason.CAPABILITY_UNSUPPORTED

    @pytest.mark.parametrize("order_type", [OrderType.BRACKET, OrderType.OCO, OrderType.TWAP,
                                            OrderType.VWAP, OrderType.POV])
    def test_no_silent_substitution(self, router, order_type):
        """
        The refusal detail must state that nothing was substituted. IBKR's paper environment
        has no VWAP/Auction/RFQ/Pegged support while execution_algo simulates VWAP, so a
        silent downgrade would misrepresent what the platform can route.
        """
        d = router.route(make_request(order_type=order_type,
                                      nonce=abs(hash(order_type)) % 900 + 30),
                         preferred_adapter_id="STUB_BROKER")
        assert d.allowed is False
        assert "refusing to substitute" in d.detail

    def test_a_supported_type_passes(self, router):
        for ot in (OrderType.MARKET, OrderType.LIMIT):
            d = router.route(make_request(order_type=ot, nonce=abs(hash(ot)) % 900 + 40),
                             preferred_adapter_id="STUB_BROKER")
            assert d.allowed is True, ot.value

    def test_the_simulator_accepts_the_algos_it_simulates(self, router):
        for ot in (OrderType.VWAP, OrderType.TWAP, OrderType.POV, OrderType.STOP_LIMIT):
            d = router.route(make_request(order_type=ot, nonce=abs(hash(ot)) % 900 + 50))
            assert d.allowed is True, f"simulator should accept {ot.value}"


# ======================================================================================
# 7. Other routing failures
# ======================================================================================

class TestRoutingFailures:

    def test_unknown_adapter_id(self, router):
        from src.execution.order_model import RejectionReason
        d = router.route(make_request(nonce=31), preferred_adapter_id="NO_SUCH_ADAPTER")
        assert d.allowed is False
        assert d.reason_code == RejectionReason.NO_ADAPTER_AVAILABLE

    def test_no_adapter_serves_the_market(self, db, kill_switch):
        from src.execution.order_model import RejectionReason
        reg = AdapterRegistry()
        reg.register(USOnlyBroker())
        rt = AdapterRouter(registry=reg, kill_switch=kill_switch)
        d = rt.route(make_request("SHOP", Market.CA, nonce=32))
        assert d.allowed is False
        assert d.reason_code == RejectionReason.NO_ADAPTER_AVAILABLE

    def test_the_no_adapter_detail_explains_the_canadian_exclusion(self, db, kill_switch):
        """When the only CA-capable adapter is external, say so rather than 'nothing found'."""
        from src.execution.order_model import RejectionReason
        reg = AdapterRegistry()
        reg.register(USOnlyBroker())
        reg.register(StubBroker())          # serves CA, but is external
        rt = AdapterRouter(registry=reg, kill_switch=kill_switch)
        d = rt.route(make_request("SHOP", Market.CA, nonce=33))
        assert d.reason_code == RejectionReason.NO_ADAPTER_AVAILABLE
        assert "CIRO" in d.detail

    def test_an_unconfigured_adapter_is_refused(self, router):
        from src.execution.order_model import RejectionReason
        router.registry.register(UnconfiguredBroker())
        d = router.route(make_request(nonce=34), preferred_adapter_id="UNCONFIGURED_BROKER")
        assert d.allowed is False
        assert d.reason_code == RejectionReason.ADAPTER_NOT_CONFIGURED

    def test_automatic_selection_skips_past_a_broken_adapter(self, db, kill_switch):
        """
        Automatic routing should land on a working adapter, not fail because the preferred
        one is unconfigured. Explicit routing must NOT do this -- see the firewall tests.
        """
        reg = AdapterRegistry()
        reg.register(UnconfiguredBroker())
        reg.register(InternalSimulatorAdapter(ledger=ExecutionLedger(db=db), db=db))
        rt = AdapterRouter(registry=reg, kill_switch=kill_switch)
        d = rt.route(make_request(nonce=35))
        assert d.allowed is True
        assert d.adapter_id == "INTERNAL_SIMULATOR"
        assert d.attempted == ["UNCONFIGURED_BROKER", "INTERNAL_SIMULATOR"]

    def test_the_failure_lists_what_was_tried(self, db, kill_switch):
        reg = AdapterRegistry()
        reg.register(UnconfiguredBroker())
        reg.register(UnclearedBroker())
        rt = AdapterRouter(registry=reg, kill_switch=kill_switch)
        d = rt.route(make_request(nonce=36))
        assert d.allowed is False
        assert "also tried" in d.detail

    def test_explicit_routing_has_no_fallback(self, router):
        """A named adapter that is unconfigured is refused, not silently rerouted."""
        router.registry.register(UnconfiguredBroker())
        d = router.route(make_request(nonce=37), preferred_adapter_id="UNCONFIGURED_BROKER")
        assert d.allowed is False
        assert d.attempted == ["UNCONFIGURED_BROKER"]


# ======================================================================================
# 8. adapter_hint -- the caller's preference carried on the order
# ======================================================================================

class TestAdapterHint:

    """
    `OrderRequest.adapter_hint` was declared and read by nothing before Phase 34.4. The
    router now honours it, so it needs the same guarantees as an explicit argument.
    """

    def test_a_hint_is_honoured(self, router):
        d = router.route(make_request(nonce=71, adapter_hint="STUB_BROKER"))
        assert d.allowed is True
        assert d.adapter_id == "STUB_BROKER"

    def test_no_hint_means_automatic_selection(self, router):
        d = router.route(make_request(nonce=72))
        assert d.adapter_id == "INTERNAL_SIMULATOR"

    def test_the_explicit_argument_outranks_the_hint(self, router):
        d = router.route(make_request(nonce=73, adapter_hint="INTERNAL_SIMULATOR"),
                         preferred_adapter_id="STUB_BROKER")
        assert d.adapter_id == "STUB_BROKER"

    def test_a_hint_cannot_smuggle_a_canadian_order_to_an_external_adapter(self, router):
        """
        The hint is a preference, not a permission. A Canadian symbol hinted at an external
        broker must be refused exactly as if the caller had demanded it explicitly.
        """
        from src.execution.order_model import RejectionReason
        d = router.route(make_request("SHOP", Market.CA, nonce=74,
                                      adapter_hint="STUB_BROKER"))
        assert d.allowed is False
        assert d.reason_code == RejectionReason.CANADIAN_EXTERNAL_ROUTING_BLOCKED

    def test_a_hint_at_an_unknown_adapter_is_reported(self, router):
        from src.execution.order_model import RejectionReason
        d = router.route(make_request(nonce=75, adapter_hint="NO_SUCH_ADAPTER"))
        assert d.allowed is False
        assert d.reason_code == RejectionReason.NO_ADAPTER_AVAILABLE

    def test_a_hint_respects_the_kill_switch(self, router, kill_switch):
        kill_switch.engage("SYMBOL:SHOP")
        d = router.route(make_request("SHOP", Market.CA, nonce=76,
                                      adapter_hint="INTERNAL_SIMULATOR"))
        assert d.allowed is False
        assert d.kill_switch_scope == "SYMBOL:SHOP"


# ======================================================================================
# 9. RoutingDecision semantics
# ======================================================================================

class TestRoutingDecision:

    def test_unroutable_order_reports_not_external(self, router):
        """
        An order that resolved to no adapter is not an externally-routed order. Reporting
        True here would make the governor's C12 fire on a route that does not exist.
        """
        d = router.route(make_request(nonce=41), preferred_adapter_id="NO_SUCH_ADAPTER")
        assert d.adapter is None
        assert d.is_external is False

    def test_adapter_id_is_none_when_nothing_resolved(self, router):
        d = router.route(make_request(nonce=42), preferred_adapter_id="NO_SUCH_ADAPTER")
        assert d.adapter_id is None

    def test_allowed_decision_exposes_the_adapter(self, router):
        d = router.route(make_request(nonce=43))
        assert d.allowed is True
        assert d.adapter_id == "INTERNAL_SIMULATOR"
        assert d.blocked_by_kill_switch is False


# ======================================================================================
# 10. Gateway integration
# ======================================================================================

class TestGatewayIntegration:

    @pytest.fixture()
    def gateway(self, db, registry, kill_switch):
        return ExecutionGateway(
            ledger=ExecutionLedger(db=db),
            router=AdapterRouter(registry=registry, kill_switch=kill_switch),
        )

    def test_a_canadian_order_routes_internal_and_is_governed(self, gateway):
        r = gateway.submit(make_request("SHOP", Market.CA), base_context())
        assert r.approved is True
        assert r.routing.adapter_id == "INTERNAL_SIMULATOR"
        assert outcome_for(r.decision, "C12").breach is False

    def test_the_order_row_records_the_resolved_adapter(self, gateway):
        r = gateway.submit(make_request(nonce=51), base_context())
        assert gateway.ledger.get_order(r.order_id)["adapter_id"] == "INTERNAL_SIMULATOR"

    def test_a_routing_block_is_recorded_as_invalid(self, gateway):
        r = gateway.submit(make_request("SHOP", Market.CA, nonce=52), base_context(),
                           adapter_id="STUB_BROKER")
        assert r.state == OrderState.INVALID
        assert r.routing_blocked is True
        assert r.decision is None, "the governor must not run on an unroutable order"

    def test_the_blocked_order_row_names_the_refused_adapter(self, gateway):
        """An operator reading the audit trail needs to see which adapter was refused."""
        r = gateway.submit(make_request("SHOP", Market.CA, nonce=53), base_context(),
                           adapter_id="STUB_BROKER")
        assert gateway.ledger.get_order(r.order_id)["adapter_id"] == "STUB_BROKER"

    def test_a_kill_switch_halt_is_recorded_as_cancelled(self, gateway, kill_switch):
        """
        CANCELLED rather than INVALID: the order was perfectly serviceable, the system
        stopped it. The distinction matters when reading the audit trail later.
        """
        kill_switch.engage(KILL_ALL, reason="incident")
        r = gateway.submit(make_request(nonce=54), base_context())
        assert r.state == OrderState.CANCELLED
        assert r.routing_blocked is True

    def test_the_halt_transition_is_attributed_to_the_kill_switch_actor(self, gateway, kill_switch):
        kill_switch.engage(KILL_ALL, reason="incident")
        r = gateway.submit(make_request(nonce=55), base_context())
        last = gateway.ledger.get_history(r.order_id)[-1]
        assert last["actor"] == Actor.KILL_SWITCH.value
        assert last["to_state"] == OrderState.CANCELLED.value

    def test_the_rejection_reason_is_persisted_on_the_transition(self, gateway, kill_switch):
        kill_switch.engage("SYMBOL:SHOP", reason="halt")
        r = gateway.submit(make_request("SHOP", Market.CA, nonce=56), base_context())
        last = gateway.ledger.get_history(r.order_id)[-1]
        assert last["reason_code"] == "KILL_SWITCH_ACTIVE"

    def test_the_router_overrides_a_caller_that_claims_internal(self, gateway):
        """
        The caller asserts the adapter is internal; the router knows it is external. The
        router wins, so C12 cannot be talked out of firing by a mislabelled context.
        """
        r = gateway.submit(make_request("SHOP", Market.CA, nonce=57),
                           base_context(target_adapter_is_external=False),
                           adapter_id="STUB_BROKER")
        assert r.routing_blocked is True
        assert r.state == OrderState.INVALID

    def test_the_governor_sees_the_resolved_route(self, gateway):
        r = gateway.submit(make_request(nonce=58), base_context(), adapter_id="STUB_BROKER")
        assert r.approved is True
        assert outcome_for(r.decision, "C12").detail.find("STUB_BROKER") >= 0

    def test_us_orders_still_route_externally_through_the_gateway(self, gateway):
        r = gateway.submit(make_request(nonce=59), base_context(), adapter_id="STUB_BROKER")
        assert r.approved is True
        assert r.routing.is_external is True

    def test_without_a_router_the_34_2_behaviour_is_unchanged(self, db):
        """
        The router is optional. A gateway with no router must behave exactly as Phase 34.2,
        including C12 firing on a Canadian order the context labels external.
        """
        gw = ExecutionGateway(ledger=ExecutionLedger(db=db))
        r = gw.submit(make_request("SHOP", Market.CA, nonce=60),
                      base_context(target_adapter_is_external=True,
                                   target_adapter_id="STUB_BROKER"))
        assert r.routing is None
        assert r.state == OrderState.RISK_REJECTED
        assert r.decision.rejection_reason == "RISK_C12_CA_EXTERNAL_FIREWALL"

    def test_without_a_router_the_result_has_no_routing(self, db):
        gw = ExecutionGateway(ledger=ExecutionLedger(db=db))
        r = gw.submit(make_request(nonce=61), base_context())
        assert r.routing is None
        assert r.routing_blocked is False
        assert r.approved is True

    def test_soft_limit_still_reports_through_the_routed_path(self, gateway):
        """Routing must not disturb the soft-limit surfacing established in 34.2."""
        r = gateway.submit(make_request(nonce=62), base_context(portfolio_beta=1.45))
        assert r.approved is True
        assert r.soft_limited is True
        assert outcome_for(r.decision, "C9").breach is True

    def test_hard_reject_still_reports_through_the_routed_path(self, gateway):
        r = gateway.submit(make_request(nonce=63), base_context(free_cash_usd=10.0))
        assert r.state == OrderState.RISK_REJECTED
        assert r.routing is not None and r.routing.allowed is True
