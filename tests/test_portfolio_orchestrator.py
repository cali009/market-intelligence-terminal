"""
Unit and Integration Tests for QUANT INTEL Portfolio Orchestrator & Live Risk Sentinels (Phase 26.3)
US + Canada Dual-Market Intelligence Platform
"""

import math
import pytest

from src.compliance.linter import linter
from src.engine.portfolio_orchestrator import portfolio_orchestrator_engine, ORCHESTRATOR_DISCLAIMERS
from src.engine.quant_intel_sentinel import quant_intel_sentinel


class TestPortfolioOrchestrator:
    """Test suite validating conviction-HRP blending, cash reserve gating, and portfolio-level risk sentinels."""

    @pytest.fixture(scope="class", autouse=True)
    def setup_orchestrator(self):
        type(self).result = portfolio_orchestrator_engine.evaluate_orchestration(total_capital=100000.0, force_refresh=True)

    def test_capital_conservation_and_cash_reserve(self):
        """Asserts that deployed capital plus cash reserve strictly conserves total capital."""
        r = self.result
        assert math.isclose(r.deployed_capital + r.cash_reserve, r.total_capital, rel_tol=1e-3)
        assert math.isclose(r.deployed_pct + r.cash_reserve_pct, 100.0, rel_tol=1e-2)
        assert r.cash_reserve >= 0.0
        assert r.deployed_capital > 0.0

    def test_conviction_scalar_modulation(self):
        """Asserts that conviction grades modulate weights (C-grade receives 50% discount vs B-grade)."""
        pos = self.result.positions
        for sym, p in pos.items():
            if p.conviction_grade == "B":
                assert p.conviction_scalar >= 0.80
            elif p.conviction_grade == "C":
                assert p.conviction_scalar <= 0.55
            elif p.conviction_grade == "SKIP":
                assert p.final_weight == 0.0

    def test_dual_market_balance(self):
        """Asserts healthy dual-market capital distribution between US and Canadian venues."""
        r = self.result
        assert r.us_weight_pct > 15.0
        assert r.ca_weight_pct > 15.0
        assert r.us_weight_pct + r.ca_weight_pct <= 100.0

    def test_position_shares_and_execution_strategy(self):
        """Asserts target shares align with effective fill prices and valid execution strategies."""
        for sym, p in self.result.positions.items():
            assert p.target_shares >= 0
            assert p.effective_execution_price > 0.0
            assert p.execution_strategy in ("DIRECT_MARKET", "LIMIT_PASSIVE", "TWAP", "VWAP", "POV_10")
            # Deployed cost of shares cannot exceed allocated dollars
            assert p.target_shares * p.effective_execution_price <= p.dollar_allocation + 1.0

    def test_sentinel_portfolio_concentration_breach_detection(self):
        """Asserts Sentinel triggers PORTFOLIO_CONCENTRATION_BREACH when an asset exceeds 18% weight."""
        class MockPos:
            final_weight = 0.22  # Exceeds 18% ceiling
            country = "US"
            effective_execution_price = 150.0

        mock_positions = {"OVERWEIGHT_SYM": MockPos()}
        mock_stress = {"cvar_99_1d_pct": 2.1}

        alerts = quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            positions=mock_positions,
            stress_metrics=mock_stress,
            avg_pairwise_corr=0.45,
        )

        types = [a.predicate_type for a in alerts]
        assert "PORTFOLIO_CONCENTRATION_BREACH" in types
        alert = next(a for a in alerts if a.predicate_type == "PORTFOLIO_CONCENTRATION_BREACH")
        assert alert.action_required in ("REBALANCE_CONCENTRATION_CAP", "REBALANCE_CROSS_BORDER")

    def test_sentinel_correlation_spike_warning_detection(self):
        """Asserts Sentinel triggers CORRELATION_SPIKE_WARNING when average pairwise correlation exceeds 0.70."""
        mock_positions = {"SPY": {"final_weight": 0.10, "country": "US"}}
        mock_stress = {"cvar_99_1d_pct": 2.1}

        alerts = quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            positions=mock_positions,
            stress_metrics=mock_stress,
            avg_pairwise_corr=0.78,  # Spike > 0.70
        )

        types = [a.predicate_type for a in alerts]
        assert "CORRELATION_SPIKE_WARNING" in types
        alert = next(a for a in alerts if a.predicate_type == "CORRELATION_SPIKE_WARNING")
        assert alert.action_required == "DE_RISK_SYSTEMIC_CORRELATION"

    def test_sentinel_cvar_tail_risk_breach_detection(self):
        """Asserts Sentinel triggers CVAR_TAIL_RISK_BREACH when 1d 99% CVaR exceeds 3.50%."""
        mock_positions = {"SPY": {"final_weight": 0.10, "country": "US"}}
        mock_stress = {"cvar_99_1d_pct": 4.15}  # Breach > 3.50%

        alerts = quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            positions=mock_positions,
            stress_metrics=mock_stress,
            avg_pairwise_corr=0.45,
        )

        types = [a.predicate_type for a in alerts]
        assert "CVAR_TAIL_RISK_BREACH" in types
        alert = next(a for a in alerts if a.predicate_type == "CVAR_TAIL_RISK_BREACH")
        assert alert.action_required == "TRIM_HIGH_BETA_RISK_BUDGET"

    def test_compliance_linter_zero_violations(self):
        """Asserts all portfolio orchestration outputs adhere to CSA 31-369 and SEC publisher rules."""
        for disc in ORCHESTRATOR_DISCLAIMERS:
            assert len(linter.lint_text(disc)) == 0

        for alert in self.result.portfolio_alerts:
            assert len(linter.lint_text(alert["headline"])) == 0
            assert len(linter.lint_text(alert["body"])) == 0
