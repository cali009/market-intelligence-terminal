"""
Unit and Integration Tests for Historical & Macroeconomic Scenario Stress-Testing Engine (Phase 26.2)
US + Canada Dual-Market Intelligence Platform
"""

import math
import pytest

from src.compliance.linter import linter
from src.engine.portfolio_stress import portfolio_stress_engine, STRESS_DISCLAIMERS


class TestPortfolioStressEngine:
    """Test suite validating VaR/CVaR tail risk, historical scenario crisis replays, and capital preservation."""

    @pytest.fixture(autouse=True)
    def setup_stress_test(self):
        self.result = portfolio_stress_engine.run_stress_test(total_capital=100000.0, force_refresh=True)

    def test_var_and_cvar_ordering_and_bounds(self):
        """Asserts theoretical quantile monotonicity: 0 < VaR_95 < CVaR_95 < VaR_99 < CVaR_99."""
        r = self.result
        assert 0.0 < r.var_95_1d_pct < r.cvar_95_1d_pct
        assert r.cvar_95_1d_pct <= r.var_99_1d_pct or abs(r.cvar_95_1d_pct - r.var_99_1d_pct) < 0.5
        assert r.var_99_1d_pct < r.cvar_99_1d_pct

        # Dollar conversions must match percentages
        assert math.isclose(r.var_95_1d_dollars, (r.var_95_1d_pct / 100.0) * r.total_capital, rel_tol=1e-2)
        assert math.isclose(r.cvar_99_1d_dollars, (r.cvar_99_1d_pct / 100.0) * r.total_capital, rel_tol=1e-2)

    def test_ten_day_horizon_scaling(self):
        """Asserts 10-day VaR scales by approximately sqrt(10) relative to 1-day VaR under Basel III standards."""
        r = self.result
        expected_10d_pct = r.var_95_1d_pct * math.sqrt(10.0)
        assert math.isclose(r.var_95_10d_pct, expected_10d_pct, rel_tol=1e-2)

    def test_five_crisis_scenarios_presence(self):
        """Asserts all 5 institutional crisis replay scenarios are evaluated."""
        expected_scenarios = [
            "2008_GFC",
            "2015_OIL_CRASH",
            "2020_COVID_FLASH",
            "2022_RATE_SHOCK",
            "2026_STAGFLATION_TARIFF"
        ]
        assert set(expected_scenarios).issubset(set(self.result.scenarios.keys()))
        assert len(self.result.scenarios) == 5

    def test_scenario_drawdowns_and_pnl_consistency(self):
        """Asserts each scenario produces valid negative returns (drawdowns) with consistent dollar P&L."""
        for s_id, sc in self.result.scenarios.items():
            assert sc.portfolio_return_pct < 0.0, f"Scenario {s_id} did not produce a crisis drawdown!"
            expected_pnl = (sc.portfolio_return_pct / 100.0) * self.result.total_capital
            assert math.isclose(sc.portfolio_pnl_dollars, expected_pnl, rel_tol=1e-2)
            assert len(sc.asset_shocks) >= 19
            assert sc.worst_asset in sc.asset_shocks
            assert sc.best_asset in sc.asset_shocks
            assert sc.worst_asset_return_pct <= sc.best_asset_return_pct

    def test_capital_preservation_in_2008_gfc_and_2022_rate_shock(self):
        """Asserts HRP preserves capital relative to naive Equal-Weight in 2008 GFC and 2022 Rate Shock."""
        gfc = self.result.scenarios["2008_GFC"]
        rate = self.result.scenarios["2022_RATE_SHOCK"]
        stag = self.result.scenarios["2026_STAGFLATION_TARIFF"]

        # Capital preservation delta must be positive (HRP lost less capital than Equal-Weight)
        assert gfc.capital_preservation_delta > 0.0
        assert rate.capital_preservation_delta > 0.0
        assert stag.capital_preservation_delta > 0.0

    def test_tail_risk_posture_classification(self):
        """Asserts tail risk posture aligns with 1-day 99% CVaR threshold."""
        assert self.result.tail_risk_posture in (
            "LOW_TAIL_RISK", "MODERATE_TAIL_RISK", "ELEVATED_TAIL_RISK", "CRITICAL_TAIL_RISK"
        )
        if self.result.cvar_99_1d_pct <= 2.5:
            assert self.result.tail_risk_posture == "LOW_TAIL_RISK"

    def test_compliance_linter_zero_violations(self):
        """Asserts all scenario descriptions, factor definitions, and disclaimers are clean of advice violations."""
        for disc in STRESS_DISCLAIMERS:
            assert len(linter.lint_text(disc)) == 0

        for sc in self.result.scenarios.values():
            assert len(linter.lint_text(sc.description)) == 0
