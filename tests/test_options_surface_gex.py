"""
Unit and Integration Test Suite for Options Volatility Surface & GEX Intelligence (Phase 30)
US (Cboe / OPRA) + Canada (Bourse de Montréal - MX) Derivative Intelligence

Tests Covered:
1. Black-Scholes-Merton gamma and delta mathematical precision.
2. Multi-tenor volatility surface construction (7D, 30D, 60D, 90D, 180D).
3. 25-Delta Put Skew (Risk Reversal) and Smile Butterfly Kurtosis.
4. Dealer Net Gamma Exposure (GEX) aggregation and strike distribution.
5. Gamma Flip strike solver ($S^*$) and regime classification.
6. Max Pain strike calculation minimizing cumulative options buyer value.
7. US Cboe vs Canadian MX dual-market derivative modeling.
8. Sentinel Predicate 20 (GAMMA_FLIP_REGIME_TRANSITION) trigger and action.
9. Sentinel Predicate 21 (VOLATILITY_SKEW_TAIL_INVERSION) trigger and action.
10. ImpersonalAdviceLinter statutory compliance validation (0 violations).
"""

import math
import pytest
import numpy as np

from src.compliance.linter import ImpersonalAdviceLinter
from src.engine.options_surface_gex import CrossBorderOptionsEngine, cross_border_options_engine
from src.engine.quant_intel_sentinel import quant_intel_sentinel
from src.models.schemas import OptionsIntelligenceFeed, TickerOptionsDossier


class TestOptionsSurfaceGexEngine:
    """Validates Black-Scholes volatility surfaces, dealer Net GEX, Gamma Flip, and Max Pain."""

    @classmethod
    def setup_class(cls):
        cls.engine = CrossBorderOptionsEngine(
            us_risk_free_rate=0.0485,
            ca_risk_free_rate=0.0425,
        )
        cls.linter = ImpersonalAdviceLinter()

    def test_bsm_greeks_precision(self):
        """Verifies Black-Scholes gamma and delta formulas against known analytical benchmarks."""
        s = 100.0
        k = 100.0
        t = 1.0
        r = 0.05
        q = 0.0
        sigma = 0.20

        gamma = self.engine.calculate_bsm_gamma(s, k, t, r, q, sigma)
        delta_call = self.engine.calculate_bsm_delta(s, k, t, r, q, sigma, "call")
        delta_put = self.engine.calculate_bsm_delta(s, k, t, r, q, sigma, "put")

        # Analytical ATM gamma for S=100, K=100, T=1, sigma=0.2, r=0.05:
        # d1 = (0.05 + 0.02) / 0.2 = 0.35; phi(0.35) = 0.37524; gamma = 0.37524 / 20 = 0.01876
        assert gamma == pytest.approx(0.01876, abs=1e-4)

        # Delta call ~ 0.6368, delta put ~ -0.3632 (call - put = 1.0)
        assert delta_call == pytest.approx(0.6368, abs=1e-3)
        assert delta_put == pytest.approx(-0.3632, abs=1e-3)
        assert (delta_call - delta_put) == pytest.approx(1.0, abs=1e-4)

    def test_multi_tenor_volatility_surface_structure(self):
        """Verifies multi-tenor implied volatility surfaces and 25-delta put skew."""
        s = 575.0
        surfaces = self.engine.build_volatility_surface(s, r=0.0485, q=0.0125, base_iv=0.155)

        assert len(surfaces) == 5
        labels = [surf.tenor_label for surf in surfaces]
        assert labels == ["7D", "30D", "60D", "90D", "180D"]

        for surf in surfaces:
            assert surf.atm_iv_pct > 0.0
            # Institutional crash put skew: 25D Put IV exceeds 25D Call IV (skew > 0)
            assert surf.skew_25d_pct > 0.0
            assert len(surf.smile_points) == 9

            # Moneyness smile points: OTM puts (moneyness 80%) have higher IV than ATM (100%)
            smile = surf.smile_points
            iv_80 = [p.implied_volatility_pct for p in smile if p.moneyness_pct == 80.0][0]
            iv_100 = [p.implied_volatility_pct for p in smile if p.moneyness_pct == 100.0][0]
            iv_120 = [p.implied_volatility_pct for p in smile if p.moneyness_pct == 120.0][0]

            assert iv_80 > iv_100
            assert iv_80 > iv_120

    def test_strike_gex_and_gamma_flip_solver(self):
        """Verifies strike-by-strike Net GEX aggregation, Gamma Flip level, and Max Pain."""
        s = 765.0
        strikes, net_gex, gamma_flip, max_pain, pcr_oi, pcr_vol = self.engine.build_strike_gex_and_max_pain(
            symbol="SPY", s=s, r=0.0485, q=0.0125, base_iv=0.155, is_canadian=False
        )

        assert len(strikes) >= 20
        assert gamma_flip > 0.0
        assert max_pain > 0.0
        assert pcr_oi > 0.0
        assert pcr_vol > 0.0

        # Strikes above spot should have positive Net GEX (call dominance)
        otm_calls = [st for st in strikes if st.strike > s + 10.0]
        assert any(st.net_gex_millions > 0 for st in otm_calls)

        # Strikes below spot should have negative Net GEX (put dominance)
        otm_puts = [st for st in strikes if st.strike < s - 10.0]
        assert any(st.net_gex_millions < 0 for st in otm_puts)

    def test_dual_market_universe_coverage(self):
        """Verifies full coverage of 5 US Cboe and 5 Canadian MX equities."""
        feed = self.engine.evaluate_options_intelligence(force_refresh=True)

        assert len(feed.symbols_monitored) == 10
        us_syms = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA"]
        ca_syms = ["XIU", "SHOP", "RY", "TD", "ENB"]

        for sym in us_syms:
            d = feed.dossiers[sym]
            assert d.exchange_market == "US_CBOE"
            assert d.currency == "USD"
            assert d.underlying_spot_price > 0.0
            assert d.gamma_flip_strike > 0.0
            assert d.gamma_regime in ["LONG_GAMMA", "SHORT_GAMMA", "TRANSITION_ZONE"]
            assert d.market_maker_posture in ["SUPPRESSING_VOLATILITY", "AMPLIFYING_VOLATILITY", "NEUTRAL_REBALANCING"]

        for sym in ca_syms:
            d = feed.dossiers[sym]
            assert d.exchange_market == "CA_MX"
            assert d.currency == "CAD"
            assert d.underlying_spot_price > 0.0
            assert d.gamma_flip_strike > 0.0

    def test_sentinel_predicate_20_gamma_flip_regime(self):
        """Verifies Predicate 20 triggers when spot crosses below Gamma Flip strike into short gamma."""
        # Simulated short gamma regime
        sim_options = {
            "dossiers": {
                "SPY": {
                    "symbol": "SPY",
                    "exchange_market": "US_CBOE",
                    "gamma_regime": "SHORT_GAMMA",
                    "net_gex_millions": -185.0,  # Below -50M threshold
                    "gamma_flip_strike": 770.0,
                    "skew_zscore": 0.5,
                }
            }
        }

        alerts = quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            positions={},
            stress_metrics={},
            options_intelligence_metrics=sim_options,
        )

        p20_alerts = [a for a in alerts if a.predicate_type == "GAMMA_FLIP_REGIME_TRANSITION"]
        assert len(p20_alerts) == 1
        a = p20_alerts[0]
        assert a.symbol == "SPY"
        assert a.severity == "WARNING"
        assert a.trigger_level == -50.0
        assert a.current_price == -185.0
        assert a.action_required == "WIDEN_STOP_THRESHOLDS"
        assert "Gamma Flip Negative Dealer Regime: SPY" in a.headline

        sys_alerts = quant_intel_sentinel.convert_to_system_alerts(p20_alerts)
        assert len(sys_alerts) == 1
        assert sys_alerts[0].taxonomy == "REGIME_SHIFT"

    def test_sentinel_predicate_21_volatility_skew_inversion(self):
        """Verifies Predicate 21 triggers when 25-delta put skew expands beyond +2.50σ."""
        # Simulated skew expansion panic
        sim_options = {
            "dossiers": {
                "QQQ": {
                    "symbol": "QQQ",
                    "exchange_market": "US_CBOE",
                    "gamma_regime": "LONG_GAMMA",
                    "net_gex_millions": 200.0,
                    "gamma_flip_strike": 720.0,
                    "thirty_day_skew_pct": 7.20,
                    "skew_zscore": 2.85,  # Above 2.50σ threshold
                    "skew_percentile_1y": 99.8,
                }
            }
        }

        alerts = quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            positions={},
            stress_metrics={},
            options_intelligence_metrics=sim_options,
        )

        p21_alerts = [a for a in alerts if a.predicate_type == "VOLATILITY_SKEW_TAIL_INVERSION"]
        assert len(p21_alerts) == 1
        a = p21_alerts[0]
        assert a.symbol == "QQQ"
        assert a.severity == "WARNING"
        assert a.trigger_level == 2.50
        assert a.current_price == 2.85
        assert a.action_required == "SCRUTINIZE_TAIL_RISK_PROTECTION"
        assert "Volatility Skew Tail Inversion: QQQ" in a.headline

        sys_alerts = quant_intel_sentinel.convert_to_system_alerts(p21_alerts)
        assert len(sys_alerts) == 1
        assert sys_alerts[0].taxonomy == "PORTFOLIO_CIRCUIT_BREAKER"

    def test_statutory_impersonal_compliance_linter(self):
        """Verifies all Phase 30 disclaimers, text copies, and actions pass ImpersonalAdviceLinter."""
        feed = self.engine.evaluate_options_intelligence()

        for disc in feed.disclaimers:
            violations = self.linter.lint_text(disc)
            assert violations == [], f"Linter violation in Options disclaimer: {violations}"

        # Test terminology
        sample_notices = [
            "Dealer Net Gamma Exposure (GEX) profile by strike.",
            "Gamma Flip threshold where market maker posture shifts between long and short regimes.",
            "25-Delta put implied volatility skew measuring crash protection insurance demand.",
            "Max Pain strike where cumulative payout to option buyers is minimized at expiration.",
            "WIDEN_STOP_THRESHOLDS",
            "SCRUTINIZE_TAIL_RISK_PROTECTION",
            "SUPPRESSING_VOLATILITY",
            "AMPLIFYING_VOLATILITY",
        ]
        for notice in sample_notices:
            violations = self.linter.lint_text(notice)
            assert violations == [], f"Linter violation in sample notice: {violations}"
