"""
Phase 34.2 Test Suite -- Pre-Trade Risk Governor

The three properties this suite exists to protect:

  1. BOUNDARY PRECISION -- every control fires exactly at its documented threshold and
     NOT one tick inside it. A control that fires early or late is not enforcing the
     limit that was documented.
  2. NO SILENT RESIZING -- a soft breach is reported, never quietly converted into a
     smaller order. The requested quantity must survive every verdict unchanged.
  3. OVERRIDES CANNOT BYPASS A HARD REJECT -- an override downgrades SOFT_LIMIT and
     nothing else.

Plus: aggregation is most-severe-wins, limits come from the governor rather than the
request, and every evaluation is recorded whether or not it breached.

Every test runs against an isolated throwaway SQLite file where persistence is involved.
"""

from datetime import date
import json

import pytest

from src.data.db import Database
from src.execution.order_model import (
    Actor,
    Market,
    OrderRequest,
    OrderSide,
    RejectionReason,
    build_client_order_id,
)
from src.execution.risk_governor import (
    CONTROL_BY_ID,
    CONTROL_REGISTRY,
    DEFAULT_PROFILE,
    GOVERNOR_LIMITS,
    MAX_DATA_AGE_SESSIONS,
    GovernorContext,
    GovernorOverride,
    PreTradeRiskGovernor,
    RiskGovernorLedger,
    Verdict,
)


@pytest.fixture()
def gov():
    return PreTradeRiskGovernor()


@pytest.fixture()
def ledger(tmp_path):
    return RiskGovernorLedger(db=Database(db_url=f"sqlite:///{tmp_path / 'gov.db'}"))


def make_request(symbol="AAPL", market=Market.US, qty=100.0, profile="MODERATE", nonce=0):
    cid = build_client_order_id(symbol, "BUY", qty, "MARKET", "STRAT_A", date(2026, 10, 2), nonce)
    return OrderRequest(
        client_order_id=cid, symbol=symbol, market=market, side=OrderSide.BUY,
        quantity=qty, risk_profile=profile, signal_date=date(2026, 10, 2),
    )


def base_context(**over):
    """A context in which a 100-share $150 order ($15,000) passes every control."""
    defaults = dict(
        book_value_usd=100_000.0,
        free_cash_usd=100_000.0,
        reference_price=150.0,
        fx_cad_usd=1.0,
        symbol_sector="TECH",
        target_adapter_id="INTERNAL_SIMULATOR",
        target_adapter_is_external=False,
    )
    defaults.update(over)
    return GovernorContext(**defaults)


def outcome_for(decision, control_id):
    return next(o for o in decision.outcomes if o.control_id == control_id)


# ======================================================================================
# 1. Registry integrity
# ======================================================================================

class TestControlRegistry:

    def test_exactly_twelve_controls_registered(self):
        assert len(CONTROL_REGISTRY) == 12

    def test_control_ids_are_c1_through_c12(self):
        assert [c.control_id for c in CONTROL_REGISTRY] == [f"C{i}" for i in range(1, 13)]

    def test_every_control_has_a_distinct_reason_code(self):
        codes = [c.reason_code for c in CONTROL_REGISTRY]
        assert len(set(codes)) == 12
        for c in codes:
            assert c.value.startswith("RISK_C"), f"{c.value} is not a risk reason code"

    def test_reason_code_number_matches_control_id(self):
        """C7 must map to RISK_C7_*, or a breach is attributed to the wrong control."""
        for spec in CONTROL_REGISTRY:
            n = spec.control_id[1:]
            assert spec.reason_code.value.startswith(f"RISK_C{n}_"), (
                f"{spec.control_id} maps to {spec.reason_code.value}"
            )

    def test_verdict_split_is_eight_hard_four_soft(self):
        """
        The split is principled: the four SOFT_LIMIT controls are portfolio-shape concerns
        where an informed override is legitimate; the eight HARD_REJECT controls are
        capital, regulatory, or data-integrity constraints.
        """
        soft = {c.control_id for c in CONTROL_REGISTRY if c.verdict_on_breach == Verdict.SOFT_LIMIT}
        hard = {c.control_id for c in CONTROL_REGISTRY if c.verdict_on_breach == Verdict.HARD_REJECT}
        assert soft == {"C5", "C7", "C8", "C9"}
        assert hard == {"C1", "C2", "C3", "C4", "C6", "C10", "C11", "C12"}

    def test_c12_is_hard_and_unoverridable_by_construction(self):
        assert CONTROL_BY_ID["C12"].verdict_on_breach == Verdict.HARD_REJECT
        assert CONTROL_BY_ID["C12"].limit_key is None, "a regulatory firewall has no threshold"

    def test_every_profile_defines_every_limit_key(self):
        """A missing key would raise at evaluation time, in production, on one control."""
        for profile, limits in GOVERNOR_LIMITS.items():
            for spec in CONTROL_REGISTRY:
                if spec.limit_key is None:
                    continue
                assert spec.limit_key in limits, f"{profile} missing {spec.limit_key}"

    def test_three_profiles_exist(self):
        assert set(GOVERNOR_LIMITS) == {"CONSERVATIVE", "MODERATE", "AGGRESSIVE"}


# ======================================================================================
# 2. Baseline behaviour
# ======================================================================================

class TestBaseline:

    def test_clean_order_is_approved(self, gov):
        d = gov.evaluate(make_request(), base_context())
        assert d.overall == Verdict.APPROVE
        assert d.approved is True
        assert d.breaches == []
        assert d.rejection_reason is None

    def test_every_control_produces_an_outcome(self, gov):
        """A silent control is worse than a missing one -- it looks like it ran."""
        d = gov.evaluate(make_request(), base_context())
        assert len(d.outcomes) == 12
        assert {o.control_id for o in d.outcomes} == {c.control_id for c in CONTROL_REGISTRY}

    def test_limits_snapshot_is_recorded(self, gov):
        d = gov.evaluate(make_request(), base_context())
        assert d.limits_snapshot == GOVERNOR_LIMITS["MODERATE"]
        json.dumps(d.limits_snapshot)  # must serialize

    def test_decision_is_json_serializable(self, gov):
        d = gov.evaluate(make_request(), base_context())
        blob = json.dumps(d.to_dict())
        assert "C12" in blob


# ======================================================================================
# 3. Boundary precision -- each control fires AT its threshold, not inside it
# ======================================================================================

class TestBoundaryPrecision:
    """
    For each numeric control: an order sized exactly at the limit must APPROVE, and one
    tick over must breach. This is the difference between a documented limit and an
    approximate one.
    """

    def test_c1_buying_power_boundary(self, gov):
        """Cap = free_cash * 90%. Notional exactly at cap passes; one cent over fails."""
        free = 20_000.0                      # cap = 18,000.0
        # exactly at the cap
        at = gov.evaluate(make_request(qty=120.0), base_context(free_cash_usd=free,
                                                                reference_price=150.0))
        assert outcome_for(at, "C1").breach is False, outcome_for(at, "C1").detail
        # one cent over
        over = gov.evaluate(make_request(qty=120.0001),
                            base_context(free_cash_usd=free, reference_price=150.0))
        assert outcome_for(over, "C1").breach is True
        assert over.overall == Verdict.HARD_REJECT
        assert over.rejection_reason == "RISK_C1_BUYING_POWER"

    def test_c2_position_weight_boundary(self, gov):
        """Cap = 18% of book. $100k book -> $18,000 projected weight passes."""
        at = gov.evaluate(make_request(qty=120.0), base_context())   # 18,000 / 100,000
        assert outcome_for(at, "C2").breach is False
        over = gov.evaluate(make_request(qty=120.01), base_context())
        assert outcome_for(over, "C2").breach is True
        assert over.rejection_reason == "RISK_C2_POSITION_WEIGHT"

    def test_c2_counts_existing_position(self, gov):
        """The cap is on the PROJECTED weight, not the order alone."""
        ctx = base_context(position_value_usd={"AAPL": 15_000.0})
        d = gov.evaluate(make_request(qty=21.0), ctx)   # 15,000 + 3,150 = 18,150 > 18,000
        assert outcome_for(d, "C2").breach is True

    def test_c3_country_exposure_boundary(self, gov):
        """
        Isolated by pre-loading the US bucket to $60k and adding a $15k order, so the
        projected exposure lands exactly on the 75% cap. Sizing a single order to $75k
        instead would trip C2, C4, C6 and C7 first and test the wrong control.
        """
        ctx = base_context(country_exposure_usd={"US": 60_000.0})
        at = gov.evaluate(make_request(qty=100.0), ctx)      # 60,000 + 15,000 = 75,000 = 75%
        assert outcome_for(at, "C3").breach is False
        assert at.overall == Verdict.APPROVE, [b.control_id for b in at.breaches]

        over = gov.evaluate(make_request(qty=100.001), ctx)
        assert outcome_for(over, "C3").breach is True
        assert over.rejection_reason == "RISK_C3_COUNTRY_EXPOSURE"

    def test_c3_is_per_market(self, gov):
        """Canadian exposure must not be measured against the US bucket."""
        ctx = base_context(country_exposure_usd={"US": 70_000.0}, reference_price=150.0)
        d = gov.evaluate(make_request(symbol="SHOP", market=Market.CA, qty=100.0), ctx)
        # CA bucket is empty, so a $15,000 CA order is 15% -- well inside 75%.
        assert outcome_for(d, "C3").breach is False

    def test_c4_unexecuted_value_boundary(self, gov):
        """Cap = 25% of book = $25,000."""
        ctx = base_context(unexecuted_value_usd={"AAPL": 10_000.0})
        at = gov.evaluate(make_request(qty=100.0), ctx)      # 10,000 + 15,000 = 25,000
        assert outcome_for(at, "C4").breach is False
        over = gov.evaluate(make_request(qty=100.01), ctx)
        assert outcome_for(over, "C4").breach is True
        assert over.rejection_reason == "RISK_C4_UNEXECUTED_VALUE"

    def test_c5_open_order_count_boundary(self, gov):
        """Cap = 25 concurrent; this order would be the 26th."""
        at = gov.evaluate(make_request(), base_context(open_order_count=24))
        assert outcome_for(at, "C5").breach is False
        over = gov.evaluate(make_request(), base_context(open_order_count=25))
        assert outcome_for(over, "C5").breach is True
        assert over.overall == Verdict.SOFT_LIMIT, "C5 is a soft control"

    def test_c6_per_order_ceiling_boundary(self, gov):
        """
        C6 is an absolute USD ceiling, so the book is enlarged to $1M -- otherwise a
        $25,000 order is 25% of a $100k book and C2 fires first. Price $125 x 200 shares
        lands the notional exactly on the $25,000 cap.
        """
        ctx = base_context(book_value_usd=1_000_000.0, free_cash_usd=1_000_000.0,
                           reference_price=125.0)
        at = gov.evaluate(make_request(qty=200.0), ctx)      # exactly 25,000
        assert outcome_for(at, "C6").breach is False
        assert at.overall == Verdict.APPROVE, [b.control_id for b in at.breaches]

        over = gov.evaluate(make_request(qty=200.001), ctx)  # 25,000.125
        assert outcome_for(over, "C6").breach is True
        assert over.rejection_reason == "RISK_C6_PER_ORDER_CEILING"

    def test_c7_sector_concentration_boundary(self, gov):
        """
        Isolated by pre-loading TECH to $15k and adding a $15k order, so projected sector
        weight lands exactly on the 30% cap while the position itself stays at 15% and
        does not trip C2. C7 is soft, so the overall verdict must be SOFT_LIMIT.
        """
        ctx = base_context(sector_exposure_usd={"TECH": 15_000.0})
        at = gov.evaluate(make_request(qty=100.0), ctx)      # 15,000 + 15,000 = 30,000 = 30%
        assert outcome_for(at, "C7").breach is False
        assert at.overall == Verdict.APPROVE, [b.control_id for b in at.breaches]

        over = gov.evaluate(make_request(qty=100.001), ctx)
        assert outcome_for(over, "C7").breach is True
        assert over.overall == Verdict.SOFT_LIMIT

    def test_c7_is_not_applicable_when_sector_unknown(self, gov):
        """No sector means no measurement; the control must say so, not silently pass."""
        d = gov.evaluate(make_request(), base_context(symbol_sector=None))
        o = outcome_for(d, "C7")
        assert o.breach is False
        assert "not applicable" in o.detail

    def test_c8_correlation_boundary(self, gov):
        at = gov.evaluate(make_request(), base_context(correlation_to_book=0.85))
        assert outcome_for(at, "C8").breach is False
        over = gov.evaluate(make_request(), base_context(correlation_to_book=0.8501))
        assert outcome_for(over, "C8").breach is True

    def test_c9_beta_boundary(self, gov):
        at = gov.evaluate(make_request(), base_context(portfolio_beta=1.30))
        assert outcome_for(at, "C9").breach is False
        over = gov.evaluate(make_request(), base_context(portfolio_beta=1.3001))
        assert outcome_for(over, "C9").breach is True

    def test_c10_daily_gross_boundary(self, gov):
        """Cap = 200% of book = $200,000 gross in a session."""
        ctx = base_context(daily_gross_traded_usd=185_000.0)
        at = gov.evaluate(make_request(qty=100.0), ctx)      # +15,000 = 200,000
        assert outcome_for(at, "C10").breach is False
        over = gov.evaluate(make_request(qty=100.01), ctx)
        assert outcome_for(over, "C10").breach is True
        assert over.rejection_reason == "RISK_C10_DAILY_GROSS_TRADED"

    def test_c11_data_age_boundary(self, gov):
        at = gov.evaluate(make_request(), base_context(data_age_sessions=MAX_DATA_AGE_SESSIONS))
        assert outcome_for(at, "C11").breach is False
        over = gov.evaluate(make_request(),
                            base_context(data_age_sessions=MAX_DATA_AGE_SESSIONS + 0.5))
        assert outcome_for(over, "C11").breach is True
        assert over.overall == Verdict.HARD_REJECT

    def test_c11_restricted_list_blocks(self, gov):
        ctx = base_context(restricted_symbols=frozenset({"AAPL"}))
        d = gov.evaluate(make_request(), ctx)
        assert outcome_for(d, "C11").breach is True
        assert "restricted list" in outcome_for(d, "C11").detail

    def test_c11_restricted_list_is_symbol_specific(self, gov):
        ctx = base_context(restricted_symbols=frozenset({"MSFT"}))
        d = gov.evaluate(make_request(symbol="AAPL"), ctx)
        assert outcome_for(d, "C11").breach is False

    def test_c12_blocks_canadian_external_route(self, gov):
        ctx = base_context(target_adapter_id="EXTERNAL_BROKER",
                           target_adapter_is_external=True)
        d = gov.evaluate(make_request(symbol="SHOP", market=Market.CA), ctx)
        assert outcome_for(d, "C12").breach is True
        assert d.overall == Verdict.HARD_REJECT
        assert d.rejection_reason == "RISK_C12_CA_EXTERNAL_FIREWALL"
        assert "CIRO DMR 3200" in outcome_for(d, "C12").detail

    def test_c12_allows_canadian_internal_route(self, gov):
        """The firewall targets EXTERNAL routing; the internal simulator must still work."""
        d = gov.evaluate(make_request(symbol="SHOP", market=Market.CA), base_context())
        assert outcome_for(d, "C12").breach is False
        assert d.overall == Verdict.APPROVE

    def test_c12_allows_us_external_route(self, gov):
        """The firewall is Canadian-specific, not a blanket ban on external adapters."""
        ctx = base_context(target_adapter_id="EXTERNAL_BROKER",
                           target_adapter_is_external=True)
        d = gov.evaluate(make_request(market=Market.US), ctx)
        assert outcome_for(d, "C12").breach is False


# ======================================================================================
# 4. No silent resizing
# ======================================================================================

class TestNoSilentResizing:

    def test_quantity_survives_an_approve(self, gov):
        req = make_request(qty=100.0)
        d = gov.evaluate(req, base_context())
        assert d.quantity == 100.0
        assert req.quantity == 100.0

    @pytest.mark.parametrize("ctx_kwargs,control", [
        (dict(open_order_count=99), "C5"),
        (dict(correlation_to_book=0.99), "C8"),
        (dict(portfolio_beta=1.95), "C9"),
        (dict(symbol_sector="TECH",
              sector_exposure_usd={"TECH": 45_000.0}), "C7"),
    ])
    def test_quantity_survives_every_soft_breach(self, gov, ctx_kwargs, control):
        """
        The core anti-theatre guarantee. A soft breach must be reported with its limit and
        observed value -- never converted into a smaller, quieter order.
        """
        req = make_request(qty=100.0)
        d = gov.evaluate(req, base_context(**ctx_kwargs))
        o = outcome_for(d, control)
        assert o.breach is True, f"{control} did not breach"
        assert d.overall == Verdict.SOFT_LIMIT
        # The requested size is untouched, on both the request and the decision.
        assert d.quantity == 100.0
        assert req.quantity == 100.0
        # And the breach is fully explained.
        assert o.limit_value is not None
        assert o.observed_value is not None
        assert o.reason_code is not None
        assert o.detail

    def test_hard_rejection_also_does_not_resize(self, gov):
        """A rejected order is rejected, not trimmed to something that would pass."""
        req = make_request(qty=1000.0)
        d = gov.evaluate(req, base_context())
        assert d.overall == Verdict.HARD_REJECT
        assert d.quantity == 1000.0
        assert req.quantity == 1000.0

    def test_governor_does_not_mutate_the_request_object(self, gov):
        req = make_request(qty=100.0)
        before = req.model_dump()
        gov.evaluate(req, base_context(open_order_count=99, portfolio_beta=1.99))
        assert req.model_dump() == before


# ======================================================================================
# 5. Aggregation
# ======================================================================================

class TestAggregation:

    def test_most_severe_verdict_wins(self, gov):
        """A hard breach must never be outvoted by clean soft controls."""
        ctx = base_context(
            free_cash_usd=1_000.0,          # C1 hard breach
            open_order_count=99,            # C5 soft breach
            portfolio_beta=1.99,            # C9 soft breach
        )
        d = gov.evaluate(make_request(), ctx)
        assert d.overall == Verdict.HARD_REJECT

    def test_soft_beats_approve(self, gov):
        ctx = base_context(open_order_count=99)
        d = gov.evaluate(make_request(), ctx)
        assert d.overall == Verdict.SOFT_LIMIT

    def test_rejection_reason_is_the_first_hard_breach_in_registry_order(self, gov):
        """Attribution must be stable, not dependent on dict ordering."""
        ctx = base_context(
            free_cash_usd=1_000.0,          # C1
            data_age_sessions=9.0,          # C11
        )
        d = gov.evaluate(make_request(), ctx)
        assert d.rejection_reason == "RISK_C1_BUYING_POWER"

    def test_no_reason_when_approved(self, gov):
        d = gov.evaluate(make_request(), base_context())
        assert d.rejection_reason is None

    def test_all_breaches_are_listed_not_just_the_worst(self, gov):
        ctx = base_context(free_cash_usd=1_000.0, open_order_count=99,
                           portfolio_beta=1.99, data_age_sessions=9.0)
        d = gov.evaluate(make_request(), ctx)
        ids = {b.control_id for b in d.breaches}
        assert {"C1", "C5", "C9", "C11"} <= ids


# ======================================================================================
# 6. Overrides
# ======================================================================================

class TestOverrides:

    def test_override_requires_a_written_justification(self):
        with pytest.raises(ValueError):
            GovernorOverride(actor=Actor.USER, justification="")
        with pytest.raises(ValueError):
            GovernorOverride(actor=Actor.USER, justification="   ")

    def test_override_downgrades_a_soft_breach(self, gov):
        ctx = base_context(open_order_count=99)
        ov = GovernorOverride(actor=Actor.USER, justification="Intentional scale-in")
        d = gov.evaluate(make_request(), ctx, override=ov)
        assert d.overall == Verdict.APPROVE
        assert d.override_applied is True
        o = outcome_for(d, "C5")
        assert o.verdict == Verdict.APPROVE
        assert o.overridden is True
        assert o.breach is True, "the breach is still recorded, only the verdict changes"

    @pytest.mark.parametrize("hard_ctx,control,req_kwargs", [
        # C12 is Canadian-specific, so its case must actually request a CA symbol.
        (dict(free_cash_usd=1_000.0), "C1", {}),
        (dict(data_age_sessions=9.0), "C11", {}),
        (dict(target_adapter_id="EXT", target_adapter_is_external=True), "C12",
         dict(symbol="SHOP", market=Market.CA)),
    ])
    def test_override_cannot_bypass_a_hard_reject(self, gov, hard_ctx, control, req_kwargs):
        """
        The single most important safety property in this module. Capital, data-integrity
        and regulatory controls are not negotiable by the caller.
        """
        ov = GovernorOverride(actor=Actor.USER, justification="I really want this trade")
        d = gov.evaluate(make_request(**req_kwargs), base_context(**hard_ctx), override=ov)
        assert outcome_for(d, control).breach is True, (
            f"{control} did not breach -- this case would be vacuous"
        )
        assert d.overall == Verdict.HARD_REJECT
        assert outcome_for(d, control).verdict == Verdict.HARD_REJECT
        assert outcome_for(d, control).overridden is False

    def test_override_of_c12_specifically_fails(self, gov):
        """Named separately because it is the regulatory control."""
        ctx = base_context(target_adapter_id="EXT", target_adapter_is_external=True)
        ov = GovernorOverride(actor=Actor.USER, justification="override everything",
                              control_ids=frozenset({"C12"}))
        d = gov.evaluate(make_request(symbol="SHOP", market=Market.CA), ctx, override=ov)
        assert d.overall == Verdict.HARD_REJECT
        assert d.rejection_reason == "RISK_C12_CA_EXTERNAL_FIREWALL"

    def test_scoped_override_leaves_other_soft_breaches(self, gov):
        ctx = base_context(open_order_count=99, portfolio_beta=1.99)
        ov = GovernorOverride(actor=Actor.USER, justification="Accept C5 only",
                              control_ids=frozenset({"C5"}))
        d = gov.evaluate(make_request(), ctx, override=ov)
        assert outcome_for(d, "C5").overridden is True
        assert outcome_for(d, "C9").overridden is False
        assert d.overall == Verdict.SOFT_LIMIT, "C9 still stands"

    def test_no_op_override_is_not_recorded_as_applied(self, gov):
        """An override that changes nothing must not claim it changed the decision."""
        ov = GovernorOverride(actor=Actor.USER, justification="nothing to override")
        d = gov.evaluate(make_request(), base_context(), override=ov)
        assert d.override_applied is False

    def test_override_without_breaches_is_not_applied(self, gov):
        ov = GovernorOverride(actor=Actor.USER, justification="pre-emptive")
        d = gov.evaluate(make_request(), base_context(), override=ov)
        assert d.override_applied is False
        assert d.overall == Verdict.APPROVE


# ======================================================================================
# 7. Risk profile parameterization
# ======================================================================================

class TestRiskProfiles:

    def test_unknown_profile_falls_back_to_default(self, gov):
        assert gov.resolve_profile("ULTRA_AGGRESSIVE") == DEFAULT_PROFILE
        assert gov.resolve_profile("") == DEFAULT_PROFILE
        assert gov.resolve_profile(None) == DEFAULT_PROFILE

    def test_profile_is_case_insensitive(self, gov):
        assert gov.resolve_profile("aggressive") == "AGGRESSIVE"
        assert gov.resolve_profile(" Conservative ") == "CONSERVATIVE"

    def test_wider_profile_widens_the_envelope(self, gov):
        """Aggressive permits a larger order than Conservative under identical inputs."""
        results = {}
        for profile in ("CONSERVATIVE", "MODERATE", "AGGRESSIVE"):
            d = gov.evaluate(make_request(qty=150.0, profile=profile), base_context())
            results[profile] = outcome_for(d, "C6").breach
        assert results["CONSERVATIVE"] is True      # 22,500 > 10,000 ceiling
        assert results["MODERATE"] is False          # 22,500 < 25,000
        assert results["AGGRESSIVE"] is False        # 22,500 < 40,000

    def test_limits_are_monotonic_across_profiles(self):
        """No control may be TIGHTER on a more aggressive profile."""
        order = ["CONSERVATIVE", "MODERATE", "AGGRESSIVE"]
        for key in GOVERNOR_LIMITS["MODERATE"]:
            vals = [GOVERNOR_LIMITS[p][key] for p in order]
            assert vals == sorted(vals), f"{key} is not monotonic: {vals}"

    def test_decision_records_the_resolved_profile(self, gov):
        d = gov.evaluate(make_request(profile="aggressive"), base_context())
        assert d.risk_profile == "AGGRESSIVE"
        assert d.limits_snapshot == GOVERNOR_LIMITS["AGGRESSIVE"]

    def test_unknown_profile_records_the_fallback_not_the_request(self, gov):
        """The snapshot must show what was actually enforced."""
        d = gov.evaluate(make_request(profile="YOLO"), base_context())
        assert d.risk_profile == "MODERATE"
        assert d.limits_snapshot == GOVERNOR_LIMITS["MODERATE"]

    def test_limits_cannot_be_supplied_by_the_request(self, gov):
        """
        A request selects a profile NAME; it cannot carry limit values. Verified by the
        absence of any limit field on OrderRequest.
        """
        fields = set(OrderRequest.model_fields)
        for key in GOVERNOR_LIMITS["MODERATE"]:
            assert key not in fields, f"{key} is settable from the request"
        assert "limits" not in fields
        assert "buying_power_pct" not in fields


# ======================================================================================
# 8. FX normalization
# ======================================================================================

class TestFxNormalization:

    def test_canadian_notional_is_converted_to_usd(self, gov):
        """100 shares at C$50 with CAD/USD 0.73 must be $3,650, not $5,000."""
        d = gov.evaluate(make_request(symbol="SHOP", market=Market.CA),
                         base_context(reference_price=50.0, fx_cad_usd=0.73))
        assert d.order_notional_usd == pytest.approx(3_650.0)

    def test_us_notional_is_not_converted(self, gov):
        d = gov.evaluate(make_request(), base_context(reference_price=150.0, fx_cad_usd=0.73))
        assert d.order_notional_usd == pytest.approx(15_000.0)

    def test_fx_affects_the_buying_power_check(self, gov):
        """A weaker CAD must reduce the USD notional tested against the capital cap."""
        ctx_strong = base_context(reference_price=50.0, fx_cad_usd=1.00, free_cash_usd=4_000.0)
        ctx_weak = base_context(reference_price=50.0, fx_cad_usd=0.60, free_cash_usd=4_000.0)
        req = make_request(symbol="SHOP", market=Market.CA)
        assert outcome_for(gov.evaluate(req, ctx_strong), "C1").breach is True   # 5,000 > 3,600
        assert outcome_for(gov.evaluate(req, ctx_weak), "C1").breach is False    # 3,000 < 3,600


# ======================================================================================
# 9. Degenerate inputs
# ======================================================================================

class TestDegenerateInputs:

    def test_zero_book_value_is_rejected_not_divided_by(self, gov):
        """Every ratio control must fail closed rather than raise ZeroDivisionError."""
        ctx = base_context(book_value_usd=0.0)
        d = gov.evaluate(make_request(), ctx)
        assert d.overall == Verdict.HARD_REJECT
        for cid in ("C2", "C3", "C4", "C10"):
            assert outcome_for(d, cid).breach is True, f"{cid} did not fail closed"

    def test_zero_free_cash_blocks_every_purchase(self, gov):
        d = gov.evaluate(make_request(), base_context(free_cash_usd=0.0))
        assert outcome_for(d, "C1").breach is True

    def test_zero_reference_price_produces_zero_notional(self, gov):
        d = gov.evaluate(make_request(), base_context(reference_price=0.0))
        assert d.order_notional_usd == 0.0


# ======================================================================================
# 10. Persistence
# ======================================================================================

class TestLedgerPersistence:

    def test_every_control_is_recorded(self, gov, ledger):
        d = gov.evaluate(make_request(), base_context())
        assert ledger.record(d) == 12

    def test_clean_evaluation_is_recorded_too(self, gov, ledger):
        """
        'The governor looked and approved' must be distinguishable from 'the governor
        never ran'. A ledger that only stores breaches cannot prove either.
        """
        d = gov.evaluate(make_request(), base_context())
        ledger.record(d)
        rows = ledger.db.execute_query(
            "SELECT COUNT(*) AS n FROM risk_governor_decision WHERE breach = 0;",
        )
        assert rows[0]["n"] == 12
        assert ledger.breaches_for(d.client_order_id) == []

    def test_breaches_are_queryable_by_order(self, gov, ledger):
        d = gov.evaluate(make_request(), base_context(open_order_count=99, portfolio_beta=1.99))
        ledger.record(d)
        rows = ledger.breaches_for(d.client_order_id)
        assert {r["control_id"] for r in rows} == {"C5", "C9"}

    def test_override_actor_is_recorded(self, gov, ledger):
        ov = GovernorOverride(actor=Actor.USER, justification="scale-in")
        d = gov.evaluate(make_request(), base_context(open_order_count=99), override=ov)
        ledger.record(d, override=ov)
        rows = ledger.db.execute_query(
            "SELECT overridden, override_actor FROM risk_governor_decision "
            "WHERE control_id = 'C5';",
        )
        assert rows[0]["overridden"] == 1
        assert rows[0]["override_actor"] == "USER"

    def test_limits_snapshot_is_persisted_as_json(self, gov, ledger):
        d = gov.evaluate(make_request(profile="AGGRESSIVE"), base_context())
        ledger.record(d)
        rows = ledger.db.execute_query(
            "SELECT limits_snapshot, risk_profile FROM risk_governor_decision LIMIT 1;",
        )
        assert json.loads(rows[0]["limits_snapshot"]) == GOVERNOR_LIMITS["AGGRESSIVE"]
        assert rows[0]["risk_profile"] == "AGGRESSIVE"

    def test_hard_rejection_count_for_predicate_28(self, gov, ledger):
        for i in range(3):
            d = gov.evaluate(make_request(nonce=8000 + i), base_context(free_cash_usd=1_000.0))
            ledger.record(d)
        assert ledger.hard_rejection_count() == 3

    def test_hard_rejection_count_is_distinct_by_order(self, gov, ledger):
        """One order breaching four hard controls is still ONE rejected order."""
        ctx = base_context(free_cash_usd=0.0, book_value_usd=0.0, data_age_sessions=9.0)
        ledger.record(gov.evaluate(make_request(), ctx))
        assert ledger.hard_rejection_count() == 1

    def test_account_snapshot_computes_buying_power(self, ledger):
        ledger.snapshot_account("INTERNAL_SIMULATOR", 100_000.0, 25_000.0, 120_000.0)
        rows = ledger.db.execute_query("SELECT * FROM execution_account;")
        assert rows[0]["buying_power_usd"] == 75_000.0

    def test_tables_are_created_idempotently(self, tmp_path):
        db = Database(db_url=f"sqlite:///{tmp_path / 'idem.db'}")
        RiskGovernorLedger(db=db)
        RiskGovernorLedger(db=db)


# ======================================================================================
# 11. Governed submission path
# ======================================================================================

from src.execution.gateway import ExecutionGateway, SubmissionResult
from src.execution.order_model import OrderState
from src.execution.state_machine import ExecutionLedger


@pytest.fixture()
def gateway(tmp_path):
    db = Database(db_url=f"sqlite:///{tmp_path / 'gw.db'}")
    return ExecutionGateway(
        ledger=ExecutionLedger(db=db),
        governor=PreTradeRiskGovernor(),
        risk_ledger=RiskGovernorLedger(db=db),
    )


class TestGovernedSubmission:

    def test_clean_order_is_risk_approved_with_a_recorded_decision(self, gateway):
        res = gateway.submit(make_request(), base_context())
        assert res.approved is True
        assert res.soft_limited is False
        assert res.state == OrderState.RISK_APPROVED
        # A governor decision was actually written, not just computed.
        rows = gateway.risk_ledger.db.execute_query(
            "SELECT COUNT(*) AS n FROM risk_governor_decision WHERE order_id = ?;",
            (res.order_id,),
        )
        assert rows[0]["n"] == 12

    def test_hard_reject_concludes_the_order_with_its_reason(self, gateway):
        res = gateway.submit(make_request(), base_context(free_cash_usd=1_000.0))
        assert res.approved is False
        assert res.state == OrderState.RISK_REJECTED
        row = gateway.ledger.get_order(res.order_id)
        assert row["state"] == OrderState.RISK_REJECTED.value
        assert row["rejection_reason"] == "RISK_C1_BUYING_POWER"
        assert row["terminal_at"] is not None

    def test_soft_limit_proceeds_but_stays_recorded(self, gateway):
        """
        The whole point of the soft/hard split: the trade is allowed, and the reason it
        was uncomfortable is preserved rather than erased.
        """
        res = gateway.submit(make_request(), base_context(open_order_count=99))
        assert res.approved is True
        assert res.soft_limited is True
        assert res.state == OrderState.RISK_APPROVED
        breaches = gateway.risk_ledger.breaches_for(res.decision.client_order_id)
        assert [b["control_id"] for b in breaches] == ["C5"]
        # And the order was NOT resized on its way through.
        assert gateway.ledger.get_order(res.order_id)["quantity"] == 100.0

    def test_canadian_external_route_is_blocked_end_to_end(self, gateway):
        ctx = base_context(target_adapter_id="EXTERNAL_BROKER",
                           target_adapter_is_external=True)
        res = gateway.submit(make_request(symbol="SHOP", market=Market.CA), ctx,
                             adapter_id="EXTERNAL_BROKER")
        assert res.state == OrderState.RISK_REJECTED
        assert gateway.ledger.get_order(res.order_id)["rejection_reason"] == \
            "RISK_C12_CA_EXTERNAL_FIREWALL"

    def test_canadian_internal_route_is_allowed_end_to_end(self, gateway):
        res = gateway.submit(make_request(symbol="SHOP", market=Market.CA), base_context())
        assert res.approved is True

    def test_override_flows_through_the_gateway(self, gateway):
        ov = GovernorOverride(actor=Actor.USER, justification="deliberate scale-in")
        res = gateway.submit(make_request(), base_context(open_order_count=99), override=ov)
        assert res.approved is True
        assert res.decision.override_applied is True
        assert res.soft_limited is False, "an overridden soft breach is no longer soft"
        rows = gateway.risk_ledger.db.execute_query(
            "SELECT overridden, override_actor FROM risk_governor_decision "
            "WHERE order_id = ? AND control_id = 'C5';", (res.order_id,),
        )
        assert rows[0]["overridden"] == 1 and rows[0]["override_actor"] == "USER"

    def test_override_still_cannot_force_a_hard_reject_through(self, gateway):
        ov = GovernorOverride(actor=Actor.USER, justification="force it")
        res = gateway.submit(make_request(symbol="SHOP", market=Market.CA),
                             base_context(target_adapter_id="EXT",
                                          target_adapter_is_external=True),
                             override=ov, adapter_id="EXT")
        assert res.state == OrderState.RISK_REJECTED

    def test_history_shows_the_governor_as_the_actor(self, gateway):
        res = gateway.submit(make_request(), base_context())
        states = [(h["from_state"], h["to_state"], h["actor"]) for h in
                  gateway.ledger.get_history(res.order_id)]
        assert states == [(None, "NEW", "SYSTEM"), ("NEW", "RISK_APPROVED", "GOVERNOR")]

    def test_rejected_order_history_is_complete(self, gateway):
        res = gateway.submit(make_request(), base_context(free_cash_usd=1_000.0))
        states = [(h["to_state"], h["reason_code"]) for h in
                  gateway.ledger.get_history(res.order_id)]
        assert states == [("NEW", None),
                          ("RISK_REJECTED", "RISK_C1_BUYING_POWER")]

    def test_quantity_is_unchanged_end_to_end(self, gateway):
        """From request to persisted row, the size the user asked for is the size recorded."""
        req = make_request(qty=137.0)
        res = gateway.submit(req, base_context())
        assert gateway.ledger.get_order(res.order_id)["quantity"] == 137.0

    def test_duplicate_submission_is_still_refused_by_the_gateway(self, gateway):
        from src.execution.state_machine import DuplicateOrderError
        gateway.submit(make_request(), base_context())
        with pytest.raises(DuplicateOrderError):
            gateway.submit(make_request(), base_context())
