"""
Unit & Integration Tests for Phase 24: Cross-Asset Macro Regime Transition Engine & Bayesian Markov Modeling
US + Canada Market Intelligence Platform
"""

import json
from pathlib import Path
import pytest
import numpy as np

from config.settings import DATA_DIR
from src.compliance.linter import linter
from src.engine.macro_regime import macro_regime_engine, DISCLAIMERS, REGIME_STATES, ADVERSE_REGIMES
from src.models.schemas import MarkovTransitionMatrix, MacroRegimeFeed, ForwardRegimeProjection


class TestMacroRegimeTransition:
    """Test suite covering Markov transition matrices, Dirichlet smoothing, hazard rates, and cross-border divergence."""

    def test_markov_matrix_row_stochasticity(self):
        """Verifies that every row in US and Canada Markov transition matrices sums to exactly 1.0."""
        for mkt in ["US", "CA"]:
            matrix = macro_regime_engine.build_markov_matrix(mkt)
            assert isinstance(matrix, MarkovTransitionMatrix)
            assert matrix.market == mkt
            assert matrix.sample_sessions_count >= 500
            assert len(matrix.rows) == len(REGIME_STATES)

            for row in matrix.rows:
                probs_sum = sum(row.probabilities.values())
                assert probs_sum == pytest.approx(1.0, abs=1e-3)

    def test_bayesian_dirichlet_smoothing(self):
        """Verifies that Dirichlet prior smoothing eliminates zero-probability transition artifacts."""
        for mkt in ["US", "CA"]:
            matrix = macro_regime_engine.build_markov_matrix(mkt)
            for row in matrix.rows:
                for target_st, p in row.probabilities.items():
                    # Every transition must be strictly positive
                    assert p > 0.0
                    assert p <= 1.0

    def test_expected_regime_duration_bounds(self):
        """Verifies expected regime duration math E[D] = 1 / (1 - P_ii) is positive and bounded."""
        for mkt in ["US", "CA"]:
            matrix = macro_regime_engine.build_markov_matrix(mkt)
            for row in matrix.rows:
                assert row.expected_duration_days >= 1.0
                assert row.expected_duration_days <= 100.0

            # Strong Bull and Bearish regimes should show persistent behavior
            sb_row = next(r for r in matrix.rows if r.from_state == "STRONG_BULL")
            assert sb_row.expected_duration_days >= 5.0

    def test_stationary_distribution_sum(self):
        """Verifies that the stationary steady-state distribution is valid and sums to 1.0."""
        for mkt in ["US", "CA"]:
            matrix = macro_regime_engine.build_markov_matrix(mkt)
            stat_dist = matrix.stationary_distribution
            assert len(stat_dist) == len(REGIME_STATES)
            assert sum(stat_dist.values()) == pytest.approx(1.0, abs=1e-3)
            for p in stat_dist.values():
                assert 0.0 <= p <= 1.0

    def test_chapman_kolmogorov_multi_horizon_projections(self):
        """Verifies Chapman-Kolmogorov forward projections P^h and adverse hazard rate calculations."""
        matrix = macro_regime_engine.build_markov_matrix("US")
        horizons = [1, 5, 20]
        projections = macro_regime_engine.compute_forward_projections(matrix, horizons=horizons)

        assert len(projections) == len(horizons)
        for proj, h in zip(projections, horizons):
            assert isinstance(proj, ForwardRegimeProjection)
            assert proj.horizon_days == h
            assert sum(proj.projected_distribution.values()) == pytest.approx(1.0, abs=1e-3)
            assert 0.0 <= proj.adverse_hazard_rate <= 1.0
            assert proj.hazard_tier in ["LOW_HAZARD", "MODERATE_HAZARD", "ELEVATED_HAZARD", "SEVERE_HAZARD"]

    def test_cross_border_macro_divergence_metrics(self):
        """Verifies cross-border yield spread divergence, CAD/USD FX velocity, and synchronization score."""
        us_mat = macro_regime_engine.build_markov_matrix("US")
        ca_mat = macro_regime_engine.build_markov_matrix("CA")
        div = macro_regime_engine.compute_cross_border_divergence(us_mat, ca_mat)

        assert div.us_regime in REGIME_STATES
        assert div.ca_regime in REGIME_STATES
        assert 0.0 <= div.regime_synchronization_score <= 1.0
        assert div.cad_usd_rate >= 1.0
        assert div.macro_divergence_posture in [
            "SYNCHRONIZED_EXPANSION",
            "ASYMMETRIC_POLICY_CYCLE",
            "CANADIAN_MACRO_LAG",
            "US_OVERHEATING_DIVERGENCE",
            "CROSS_BORDER_STRESS",
        ]

    def test_macro_regime_feed_export_and_schema(self, tmp_path):
        """Verifies feed serialization, directory creation, and JSON schema completeness."""
        out_path = macro_regime_engine.export_feed(tmp_path, force_refresh=True)
        assert out_path.exists()

        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert "us_matrix" in data
        assert "ca_matrix" in data
        assert "us_forward_projections" in data
        assert "ca_forward_projections" in data
        assert "cross_border_divergence" in data
        assert "disclaimers" in data
        assert len(data["disclaimers"]) >= 3

    def test_compliance_linter_zero_violations(self):
        """Asserts zero promissory or regulatory violations in disclaimers."""
        for disc in DISCLAIMERS:
            linter.assert_clean(disc)
            assert "guarantee" not in disc.lower() or "do not guarantee" in disc.lower()

    def test_quant_intel_layer1_macro_regime_integration(self):
        """Verifies that Layer 1 Regime Synthesis integrates Bayesian Markov hazard metrics."""
        from src.engine.quant_intel import QuantIntelEngine
        engine = QuantIntelEngine()

        for ctry in ["US", "CA"]:
            l1 = engine.evaluate_layer1_regime(country=ctry)
            assert hasattr(l1, "adverse_hazard_rate_5d")
            assert hasattr(l1, "adverse_hazard_rate_20d")
            assert hasattr(l1, "expected_regime_duration_days")
            assert hasattr(l1, "hazard_tier")
            assert hasattr(l1, "cross_border_macro_divergence_posture")

            assert 0.0 <= l1.adverse_hazard_rate_5d <= 1.0
            assert 0.0 <= l1.adverse_hazard_rate_20d <= 1.0
            assert l1.expected_regime_duration_days >= 1.0
            assert l1.hazard_tier in ["LOW_HAZARD", "MODERATE_HAZARD", "ELEVATED_HAZARD", "SEVERE_HAZARD"]
            assert l1.cross_border_macro_divergence_posture in [
                "SYNCHRONIZED_EXPANSION",
                "ASYMMETRIC_POLICY_CYCLE",
                "CANADIAN_MACRO_LAG",
                "US_OVERHEATING_DIVERGENCE",
                "CROSS_BORDER_STRESS",
            ]

    def test_quant_intel_macro_hazard_governor_downweighting(self):
        """Verifies that elevated adverse regime hazard triggers conservative position size scalar."""
        from src.engine.quant_intel import (
            QuantIntelEngine,
            Layer1Regime,
            Layer2Fingerprint,
            Layer3Technical,
            Layer4Fundamental,
            Layer5Sentiment,
            Layer6Memory,
        )
        engine = QuantIntelEngine()

        tech = Layer3Technical(
            status="BULLISH",
            key_reason="Clear breakout",
            price_structure="Uptrend",
            ma_stack="Bullish Stack",
            rsi_14=58.0,
            macd_hist=0.45,
            roc_10=3.2,
            obv_trend="Accumulation",
            rvol_20=1.5,
            cmf_20=0.12,
            atr_14=2.0,
            atr_pct=1.5,
            bb_squeeze=False,
            bb_bandwidth=5.0,
            support_1=100.0,
            resistance_1=115.0,
            resistance_2=125.0,
            volume_poc=103.0,
        )
        fp = Layer2Fingerprint(
            symbol="TEST",
            dominant_pattern="Momentum Breakout",
            hurst_exponent=0.62,
            hurst_class="Persistent Trend",
            archetype_label="MOMENTUM_RUNNER",
            amihud_illiquidity=0.001,
            pattern_logic="Strong trend persistence",
        )
        fund = Layer4Fundamental(
            score=8.5,
            status="BULLISH",
            key_reason="Strong ROIC",
            quality_score=8.0,
            valuation_score=7.0,
        )
        sent = Layer5Sentiment(
            net_sentiment_score=0.45,
            status="BULLISH",
            key_headline="Positive earnings preview",
            catalyst_count=3,
            risk_count=0,
            weighted_catalysts=2.5,
            weighted_risks=0.0,
        )
        mem = Layer6Memory(
            win_rate_pct=68.0,
            sample_size=35,
            avg_move_pct=8.5,
            false_breakout_rate_pct=15.0,
            median_bars_to_target=14,
            expectancy_r=0.60,
            status="BULLISH",
        )

        # 1. Benign regime (Low Hazard)
        regime_benign = Layer1Regime(
            trend_type="Bull",
            volatility_regime="Low (VIX < 15)",
            liquidity_env="Risk-ON",
            rate_context="Paused",
            regime_state="STRONG_BULL",
            regime_fit="YES",
            status="BULLISH",
            conviction_weight=1.1,
            adverse_hazard_rate_5d=0.01,
            adverse_hazard_rate_20d=0.05,
            expected_regime_duration_days=20.0,
            hazard_tier="LOW_HAZARD",
            cross_border_macro_divergence_posture="SYNCHRONIZED_EXPANSION",
        )
        plan_benign, grade_b, _ = engine.compute_trade_plan(
            "TEST", tech, fp, regime_benign, fund, sent, mem, portfolio_size=100000.0
        )

        # 2. Elevated hazard regime (Hazard >= 0.20)
        regime_elevated = Layer1Regime(
            trend_type="Bull",
            volatility_regime="Medium (15–25)",
            liquidity_env="Risk-ON",
            rate_context="Paused",
            regime_state="STRONG_BULL",
            regime_fit="YES",
            status="BULLISH",
            conviction_weight=1.0,
            adverse_hazard_rate_5d=0.10,
            adverse_hazard_rate_20d=0.25,
            expected_regime_duration_days=8.0,
            hazard_tier="ELEVATED_HAZARD",
            cross_border_macro_divergence_posture="ASYMMETRIC_POLICY_CYCLE",
        )
        plan_elevated, grade_e, _ = engine.compute_trade_plan(
            "TEST", tech, fp, regime_elevated, fund, sent, mem, portfolio_size=100000.0
        )

        # Assert governor applied conservative downweighting (0.80x)
        assert plan_benign.recommended_shares > 0
        assert plan_elevated.recommended_shares < plan_benign.recommended_shares
        assert plan_elevated.recommended_shares == int(plan_benign.recommended_shares * 0.80)
        assert "Elevated forward macro hazard" in plan_elevated.invalidation_rule

    def test_quant_intel_sentinel_adverse_regime_hazard_predicate(self):
        """Verifies that QuantIntelSentinel triggers ADVERSE_REGIME_HAZARD_SPIKE on elevated Markov hazard."""
        from src.engine.quant_intel_sentinel import quant_intel_sentinel
        from src.engine.quant_intel import QuantIntelDossier, Layer1Regime, QuantIntelTradePlan, Layer3Technical, Layer5Sentiment

        mock_regime = Layer1Regime(
            trend_type="Bull",
            volatility_regime="Medium (15–25)",
            liquidity_env="Risk-ON",
            rate_context="Paused",
            regime_state="STRONG_BULL",
            regime_fit="YES",
            status="BULLISH",
            conviction_weight=1.0,
            adverse_hazard_rate_5d=0.15,
            adverse_hazard_rate_20d=0.32,
            expected_regime_duration_days=6.0,
            hazard_tier="ELEVATED_HAZARD",
            cross_border_macro_divergence_posture="CROSS_BORDER_STRESS",
        )
        mock_plan = QuantIntelTradePlan(
            entry_zone_ideal=100.0,
            entry_zone_max=101.5,
            entry_trigger="Breakout",
            stop_loss=95.0,
            stop_distance_pct=5.0,
            stop_distance_dollars=5.0,
            atr_stop_multiplier=1.5,
            target_1=108.0,
            target_1_days=6,
            target_1_rr=1.6,
            target_2=116.0,
            target_2_days=20,
            target_2_rr=3.2,
            target_3=125.0,
            target_3_days=45,
            target_3_rr=5.0,
            risk_reward_ratio=3.0,
            portfolio_size=100000.0,
            recommended_shares=160,
            capital_at_risk=800.0,
            position_value=16000.0,
            portfolio_risk_pct=0.8,
            invalidation_rule="Stop below $95.00",
            trade_thesis="Strong continuation",
        )

        class MockDossier:
            def __init__(self):
                self.trade_plan = mock_plan
                self.layer_1_regime = mock_regime
                self.layer_3_technical = None
                self.layer_5_sentiment = type("S", (), {"skip_triggered": False, "net_sentiment_score": 0.20, "key_headline": "Solid"})()

        dossier = MockDossier()
        alerts = quant_intel_sentinel.evaluate_ticker_sentinel(
            symbol="TEST",
            dossier=dossier,
            recent_bars=[{"trading_date": "2026-09-25", "open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 1000000}]
        )

        # Check for ADVERSE_REGIME_HAZARD_SPIKE
        hazard_alert = next((a for a in alerts if a.predicate_type == "ADVERSE_REGIME_HAZARD_SPIKE"), None)
        assert hazard_alert is not None
        assert hazard_alert.severity == "WARNING"
        assert hazard_alert.action_required == "DE_RISK_OR_TIGHTEN_STOP"

        # Check conversion to formal system AlertRecord & compliance
        sys_records = quant_intel_sentinel.convert_to_system_alerts([hazard_alert])
        assert len(sys_records) == 1
        assert sys_records[0].taxonomy == "REGIME_SHIFT"
        assert "ELEVATED_HAZARD" in sys_records[0].headline

