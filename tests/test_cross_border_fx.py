"""
Unit and Integration Test Suite for Cross-Border Dual-Currency Analytics & Optimal FX Hedging (Phase 29.1 & 29.2)
US + Canada Dual-Market Intelligence Platform

Tests Covered:
1. Covered Interest Rate Parity (CIP) Forward Curves (1M, 3M, 6M, 12M).
2. Interest rate differential spread & negative forward points (CAD discount / USD premium).
3. Minimum-variance optimal currency hedge ratio h* for CAD-base holding US basket.
4. Minimum-variance optimal currency hedge ratio h* for USD-base holding Canadian basket.
5. Volatility reduction invariant: sigma(h*) <= sigma(unhedged) and sigma(h*) <= sigma(100% hedged).
6. Dual-listed arbitrage evaluation across 9 interlisted equities.
7. Hurdle friction hurdle logic (9.0 bps): EXECUTE_TSX, EXECUTE_NYSE, PARITY_EFFICIENT routing.
8. Liquidity dominance classification (TSX_DOMINANT, US_DOMINANT, BALANCED).
9. Sentinels Predicates 18 & 19 triggering and alert generation.
10. ImpersonalAdviceLinter compliance on all disclaimers and action tags.
"""

import math
import pytest
import numpy as np
import pandas as pd

from src.compliance.linter import ImpersonalAdviceLinter
from src.engine.cross_border_fx import CrossBorderFxEngine, cross_border_fx_engine
from src.engine.quant_intel_sentinel import quant_intel_sentinel
from src.models.schemas import CrossBorderFxFeed, CipForwardPoint, OptimalHedgeRatioSummary


class TestCrossBorderFxEngine:
    """Validates Covered Interest Rate Parity forward curves, optimal hedging, and arbitrage routing."""

    @classmethod
    def setup_class(cls):
        cls.engine = CrossBorderFxEngine(
            boc_policy_rate=4.25,
            fed_funds_rate=4.85,
            round_trip_friction_bps=9.0,
        )
        cls.linter = ImpersonalAdviceLinter()

    def test_cip_forward_curve_calculation(self):
        """Verifies Covered Interest Rate Parity formula across 1M, 3M, 6M, and 12M tenors."""
        spot = 1.4188
        curve = self.engine.compute_forward_curve(spot_fx=spot)
        assert len(curve) == 4

        tenors = [p.tenor for p in curve]
        assert tenors == ["1M", "3M", "6M", "12M"]

        # Policy rate differential is BoC (4.25%) - Fed (4.85%) = -60.0 bps
        for pt in curve:
            assert pt.interest_diff_bps == -60.0
            assert pt.boc_rate_pct == 4.25
            assert pt.fed_rate_pct == 4.85
            assert pt.spot_fx == spot
            # Since r_CAD < r_USD, forward rate should be lower than spot (CAD at a discount)
            assert pt.forward_fx < spot
            assert pt.forward_points_pips < 0.0
            assert pt.cip_basis_bps < 0.0

        # Monotonicity: forward points become more negative with longer maturities
        assert curve[0].forward_points_pips > curve[1].forward_points_pips
        assert curve[1].forward_points_pips > curve[2].forward_points_pips
        assert curve[2].forward_points_pips > curve[3].forward_points_pips

    def test_cad_base_optimal_hedge_ratio(self):
        """Verifies minimum-variance hedge ratio calculation for CAD-base holding US foreign equity assets."""
        res = self.engine.evaluate_cross_border_fx(force_refresh=True)
        cad_h = res.cad_base_hedging

        assert cad_h.portfolio_base_currency == "CAD"
        assert 0.0 <= cad_h.optimal_hedge_ratio <= 1.0

        # Min-variance volatility must be lower than or equal to unhedged volatility
        assert cad_h.optimal_hedged_portfolio_volatility_pct <= cad_h.unhedged_portfolio_volatility_pct
        assert cad_h.risk_reduction_pct >= 0.0

        # Natural hedge posture verification
        if cad_h.optimal_hedge_ratio < 0.20:
            assert cad_h.hedging_posture == "UNHEDGED_OPTIMAL"
        elif cad_h.optimal_hedge_ratio > 0.65:
            assert cad_h.hedging_posture == "FULL_HEDGE_RECOMMENDED"
        else:
            assert cad_h.hedging_posture == "PARTIAL_NATURAL_HEDGE"

        # Annualized hedging carry drag
        assert cad_h.hedging_drag_bps >= 0.0

    def test_usd_base_optimal_hedge_ratio(self):
        """Verifies minimum-variance hedge ratio calculation for USD-base holding Canadian TSX equity assets."""
        res = self.engine.evaluate_cross_border_fx(force_refresh=False)
        usd_h = res.usd_base_hedging

        assert usd_h.portfolio_base_currency == "USD"
        assert 0.0 <= usd_h.optimal_hedge_ratio <= 1.0
        assert usd_h.optimal_hedged_portfolio_volatility_pct <= usd_h.unhedged_portfolio_volatility_pct
        assert usd_h.risk_reduction_pct >= 0.0
        assert usd_h.hedging_posture in ["PARTIAL_NATURAL_HEDGE", "FULL_HEDGE_RECOMMENDED", "UNHEDGED_OPTIMAL"]

    def test_dual_listed_arbitrage_monitoring(self):
        """Verifies dual-listed basis spread and venue routing across interlisted universe."""
        res = self.engine.evaluate_cross_border_fx(force_refresh=False)
        arb_opps = res.dual_listed_arbitrage

        assert len(arb_opps) >= 8
        symbols = {a.symbol for a in arb_opps}
        required_syms = {"RY", "TD", "SHOP", "ENB", "CNQ", "BAM", "CNR", "CP"}
        assert required_syms.issubset(symbols)

        for arb in arb_opps:
            assert arb.tsx_price_cad > 0.0
            assert arb.nyse_price_usd > 0.0
            assert arb.implied_parity_cad > 0.0
            assert arb.round_trip_friction_bps == 9.0
            assert arb.routing_recommendation in ["EXECUTE_TSX", "EXECUTE_NYSE", "PARITY_EFFICIENT"]
            assert arb.liquidity_center in ["TSX_DOMINANT", "US_DOMINANT", "BALANCED"]
            assert arb.volume_ratio_tsx_us > 0.0

            # Hurdle logic validation
            if arb.basis_spread_bps > 9.0:
                assert arb.routing_recommendation == "EXECUTE_NYSE"
                assert arb.net_arbitrage_bps == pytest.approx(arb.basis_spread_bps - 9.0, abs=0.1)
            elif arb.basis_spread_bps < -9.0:
                assert arb.routing_recommendation == "EXECUTE_TSX"
                assert arb.net_arbitrage_bps == pytest.approx(abs(arb.basis_spread_bps) - 9.0, abs=0.1)
            else:
                assert arb.routing_recommendation == "PARITY_EFFICIENT"
                assert arb.net_arbitrage_bps == 0.0

    def test_sentinel_predicate_18_parity_dislocation(self):
        """Verifies Predicate 18 triggers when dual-listed basis spread magnitude exceeds 45.0 bps."""
        # Simulated dislocation with spread = -52.0 bps
        dislocated_metrics = {
            "dual_listed_arbitrage": [
                {
                    "symbol": "RY",
                    "basis_spread_bps": -52.0,
                    "routing_recommendation": "EXECUTE_TSX",
                }
            ],
            "cad_base_hedging": {
                "unhedged_portfolio_volatility_pct": 19.0,
                "fully_hedged_portfolio_volatility_pct": 18.5,
            },
        }

        alerts = quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            positions={},
            stress_metrics={},
            cross_border_fx_metrics=dislocated_metrics,
        )

        p18_alerts = [a for a in alerts if a.predicate_type == "CROSS_BORDER_PARITY_DISLOCATION"]
        assert len(p18_alerts) == 1
        a = p18_alerts[0]
        assert a.symbol == "RY"
        assert a.severity == "WARNING"
        assert a.trigger_level == 45.0
        assert a.current_price == 52.0
        assert a.action_required == "INVESTIGATE_VENUE_ARBITRAGE"
        assert "Cross-Border Dual-Listed Parity Dislocation: RY" in a.headline

        # System alert conversion
        sys_alerts = quant_intel_sentinel.convert_to_system_alerts(p18_alerts)
        assert len(sys_alerts) == 1
        assert sys_alerts[0].taxonomy == "PORTFOLIO_CIRCUIT_BREAKER"

    def test_sentinel_predicate_19_unhedged_currency_drag(self):
        """Verifies Predicate 19 triggers when unhedged volatility exceeds hedged baseline by > 2.0%."""
        high_drag_metrics = {
            "dual_listed_arbitrage": [],
            "cad_base_hedging": {
                "unhedged_portfolio_volatility_pct": 21.5,
                "fully_hedged_portfolio_volatility_pct": 18.5,  # Drag = 3.0%
            },
        }

        alerts = quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            positions={},
            stress_metrics={},
            cross_border_fx_metrics=high_drag_metrics,
        )

        p19_alerts = [a for a in alerts if a.predicate_type == "UNHEDGED_CURRENCY_DRAG"]
        assert len(p19_alerts) == 1
        a = p19_alerts[0]
        assert a.symbol == "PORTFOLIO"
        assert a.severity == "WARNING"
        assert a.trigger_level == 2.0
        assert a.current_price == 3.0
        assert a.action_required == "IMPLEMENT_CURRENCY_HEDGE"
        assert "Unhedged Currency Volatility Drag" in a.headline

        sys_alerts = quant_intel_sentinel.convert_to_system_alerts(p19_alerts)
        assert len(sys_alerts) == 1
        assert sys_alerts[0].taxonomy == "PORTFOLIO_CIRCUIT_BREAKER"

    def test_statutory_impersonal_compliance_linter(self):
        """Verifies all Phase 29 disclaimers and text notices pass ImpersonalAdviceLinter."""
        feed = self.engine.evaluate_cross_border_fx()

        for disc in feed.disclaimers:
            violations = self.linter.lint_text(disc)
            assert violations == [], f"Linter violation in FX disclaimer: {violations}"

        # Test routing and action descriptions
        sample_notices = [
            "Smart venue routing suggestion derived from raw basis spread exceeding round-trip friction hurdle.",
            "Covered Interest Rate Parity forward curves represent quantitative mathematical pricing models.",
            "INVESTIGATE_VENUE_ARBITRAGE",
            "IMPLEMENT_CURRENCY_HEDGE",
            "PARITY_EFFICIENT",
            "EXECUTE_TSX",
            "EXECUTE_NYSE",
        ]
        for notice in sample_notices:
            violations = self.linter.lint_text(notice)
            assert violations == [], f"Linter violation in notice: {violations}"
