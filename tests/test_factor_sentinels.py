"""
Unit and Integration Tests for QUANT INTEL Factor Risk Sentinels (Phase 27.3)
US + Canada Dual-Market Intelligence Platform

Validates:
- Predicate 13: FACTOR_CROWDING_BREACH (>45% non-market systematic risk)
- Predicate 14: FX_COMMODITY_OVEREXPOSURE (crude tilt > 0.40 or FX tilt > 0.35)
- Predicate 15: ALPHA_EROSION_WARNING (specific alpha < -1.50%)
- Nominal portfolio health (zero breaches)
- Compliance linter verification (CSA Staff Notice 31-369 & SEC Publisher Exclusion)
"""

import pytest
from src.compliance.linter import linter
from src.engine.quant_intel_sentinel import quant_intel_sentinel
from src.engine.portfolio_orchestrator import portfolio_orchestrator_engine
from src.engine.factor_risk import factor_risk_engine
from src.engine.factor_attribution import factor_attribution_engine


class TestFactorSentinels:
    """Test suite validating factor risk sentinels and portfolio orchestrator factor governance."""

    @pytest.fixture(scope="class", autouse=True)
    def setup_data(self):
        type(self).orch_res = portfolio_orchestrator_engine.evaluate_orchestration(
            total_capital=100000.0, force_refresh=True
        )
        type(self).factor_risk_res = factor_risk_engine.evaluate_factor_risk(
            total_capital=100000.0, force_refresh=True
        )
        type(self).attrib_res = factor_attribution_engine.evaluate_attribution(
            total_capital=100000.0, force_refresh=True
        )

    def test_nominal_portfolio_zero_factor_sentinel_breaches(self):
        """Asserts that under live nominal portfolio allocations, all 6 portfolio sentinels are nominal."""
        alerts = quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            positions=self.orch_res.positions,
            stress_metrics={"cvar_99_1d_pct": 2.09},
            avg_pairwise_corr=0.28,
            factor_risk_metrics=self.factor_risk_res.model_dump(),
            factor_attribution_metrics=self.attrib_res.model_dump(),
        )
        assert len(alerts) == 0, f"Expected 0 alerts under nominal conditions, got: {[a.predicate_type for a in alerts]}"

    def test_factor_crowding_breach_sentinel_trigger(self):
        """Asserts that Predicate 13 triggers when a non-market factor exceeds 45% systematic risk."""
        mock_factor_risk = self.factor_risk_res.model_dump()
        # Simulate extreme value crowding (52.0% of systematic risk)
        mock_factor_risk["factor_risk_summaries"]["value_hml"]["percent_of_systematic_risk"] = 52.0

        alerts = quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            positions=self.orch_res.positions,
            stress_metrics={"cvar_99_1d_pct": 2.09},
            avg_pairwise_corr=0.28,
            factor_risk_metrics=mock_factor_risk,
            factor_attribution_metrics=self.attrib_res.model_dump(),
        )

        crowding_alerts = [a for a in alerts if a.predicate_type == "FACTOR_CROWDING_BREACH"]
        assert len(crowding_alerts) == 1
        alert = crowding_alerts[0]
        assert "Factor Risk Crowding Detected" in alert.headline
        assert alert.severity == "WARNING"
        assert alert.action_required == "REBALANCE_FACTOR_TILT"
        assert alert.current_price == 52.0
        assert linter.lint_text(alert.headline) == []
        assert linter.lint_text(alert.body) == []

    def test_fx_commodity_overexposure_sentinel_trigger(self):
        """Asserts that Predicate 14 triggers when crude oil active tilt exceeds 0.40."""
        mock_attrib = self.attrib_res.model_dump()
        # Simulate extreme crude oil active tilt (+0.48)
        mock_attrib["benchmarks"]["BLENDED_CROSS_BORDER"]["factor_tilts"]["crude_oil_beta"]["active_tilt"] = 0.48

        alerts = quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            positions=self.orch_res.positions,
            stress_metrics={"cvar_99_1d_pct": 2.09},
            avg_pairwise_corr=0.28,
            factor_risk_metrics=self.factor_risk_res.model_dump(),
            factor_attribution_metrics=mock_attrib,
        )

        overexp_alerts = [a for a in alerts if a.predicate_type == "FX_COMMODITY_OVEREXPOSURE"]
        assert len(overexp_alerts) == 1
        alert = overexp_alerts[0]
        assert "Commodity or FX Overexposure" in alert.headline
        assert alert.severity == "WARNING"
        assert alert.action_required == "REDUCE_COMMODITY_EXPOSURE"
        assert alert.current_price == 0.48
        assert linter.lint_text(alert.headline) == []
        assert linter.lint_text(alert.body) == []

    def test_alpha_erosion_warning_sentinel_trigger(self):
        """Asserts that Predicate 15 triggers when idiosyncratic selection alpha drops below -1.50%."""
        mock_attrib = self.attrib_res.model_dump()
        # Simulate negative stock selection alpha (-1.85%)
        mock_attrib["benchmarks"]["BLENDED_CROSS_BORDER"]["specific_alpha_pct"] = -1.85

        alerts = quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            positions=self.orch_res.positions,
            stress_metrics={"cvar_99_1d_pct": 2.09},
            avg_pairwise_corr=0.28,
            factor_risk_metrics=self.factor_risk_res.model_dump(),
            factor_attribution_metrics=mock_attrib,
        )

        alpha_alerts = [a for a in alerts if a.predicate_type == "ALPHA_EROSION_WARNING"]
        assert len(alpha_alerts) == 1
        alert = alpha_alerts[0]
        assert "Alpha Erosion" in alert.headline
        assert alert.severity == "WARNING"
        assert alert.action_required == "AUDIT_SECURITY_SELECTION"
        assert alert.current_price == -1.85
        assert linter.lint_text(alert.headline) == []
        assert linter.lint_text(alert.body) == []

    def test_convert_to_system_alerts_taxonomy_mapping(self):
        """Asserts that factor sentinel alerts properly map to PORTFOLIO_RISK_LIMIT taxonomy."""
        mock_factor_risk = self.factor_risk_res.model_dump()
        mock_factor_risk["factor_risk_summaries"]["value_hml"]["percent_of_systematic_risk"] = 52.0

        mock_attrib = self.attrib_res.model_dump()
        mock_attrib["benchmarks"]["BLENDED_CROSS_BORDER"]["specific_alpha_pct"] = -2.10

        alerts = quant_intel_sentinel.evaluate_portfolio_level_sentinels(
            positions=self.orch_res.positions,
            stress_metrics={"cvar_99_1d_pct": 2.09},
            avg_pairwise_corr=0.28,
            factor_risk_metrics=mock_factor_risk,
            factor_attribution_metrics=mock_attrib,
        )

        records = quant_intel_sentinel.convert_to_system_alerts(alerts)
        assert len(records) == 2
        for r in records:
            assert r.taxonomy == "PORTFOLIO_CIRCUIT_BREAKER"
            assert r.symbol == "PORTFOLIO"
            assert len(r.channels) > 0
            assert "Impersonal decision-support research" in r.disclaimer

    def test_compliance_linter_zero_violations(self):
        """Asserts all factor sentinel texts have zero regulatory violations."""
        for phrase in [
            "Factor Risk Crowding Detected: Crude Oil (48.5% of Systematic Risk)",
            "Systematic risk concentration in Crude Oil reached 48.5%, exceeding the 45.0% factor crowding limit.",
            "Cross-Border Commodity or FX Overexposure: Crude Oil (+0.42) / CAD/USD (-0.38)",
            "Specific Stock Selection Alpha Erosion (-1.85%)",
        ]:
            assert linter.lint_text(phrase) == []
