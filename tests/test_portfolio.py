"""
Unit & Integration Tests: Portfolio Intelligence & Risk Management (Phase 9)
US + Canada Market Intelligence Platform

Verifies:
- Dual-market portfolio valuation (USD / CAD) using Bank of Canada Valet FX rate
- Dual-market beta engine: Local Beta (SPY / XIU) and Cross-Market Beta
- Mathematical reconciliation of Risk Attribution: sum(% Risk Contribution) == 100.0%
- Correlation clustering & Effective Number of Independent Bets (N_eff via eigenvalue decomposition)
- Unhedged FX Exposure and currency sensitivity (+/- 5% CAD shock)
- Historical macro stress replays (2008 GFC, 2020 COVID, 2022 Rate Shock, 2014 Oil Collapse, 2018 Vol Spike)
- Systematic Drawdown Circuit Breakers (§9.4) customized by risk profile
- Per-holding Exit Signal Engine integration (HOLD / WATCH / REDUCE / EXIT)
- CSV parser functionality for custom portfolio importing
- Strict impersonal advice compliance (CSA Staff Notice 31-369 & SEC Publisher Exclusion)
"""

import json
import pytest
import numpy as np

from src.engine.portfolio import portfolio_engine
from src.compliance.linter import linter
from src.models.schemas import PortfolioAnalyticsReport, PortfolioHoldingRecord


def test_portfolio_default_bundle_and_valuation():
    """Verify that default model portfolios are generated with valid valuations and weights."""
    bundle = portfolio_engine.get_default_portfolios()
    assert "portfolios" in bundle
    assert "PORT_CROSS_BORDER" in bundle["portfolios"]
    assert "PORT_US_ALPHA" in bundle["portfolios"]
    assert "PORT_TSX_INCOME" in bundle["portfolios"]

    p_cb = bundle["portfolios"]["PORT_CROSS_BORDER"]
    assert p_cb["total_market_value_base"] > 100000.0
    assert p_cb["cash_base"] > 0
    assert p_cb["total_cost_basis_base"] > 0
    assert len(p_cb["holdings"]) == 8

    # Verify weight sum equals approximately 100% (accounting for cash)
    holding_weights = sum(h["weight_pct"] for h in p_cb["holdings"])
    cash_weight = (p_cb["cash_base"] / p_cb["total_market_value_base"]) * 100.0
    assert abs((holding_weights + cash_weight) - 100.0) < 0.5


def test_risk_attribution_sums_to_100_percent():
    """DoD: Verify that percentage risk contribution (% RC) reconciles and sums to 100.0%."""
    bundle = portfolio_engine.get_default_portfolios()
    for pid, p in bundle["portfolios"].items():
        holdings = p["holdings"]
        rc_sum = sum(h["risk_contribution_pct"] for h in holdings)
        assert abs(rc_sum - 100.0) < 0.2, f"Portfolio {pid} %RC sum was {rc_sum}, expected 100.0%"


def test_dual_market_betas_local_and_cross():
    """Verify that local beta and cross beta are computed for each holding and portfolio level."""
    bundle = portfolio_engine.get_default_portfolios()
    p_cb = bundle["portfolios"]["PORT_CROSS_BORDER"]

    assert p_cb["portfolio_beta_local"] > 0
    assert p_cb["portfolio_beta_cross"] > 0

    # For US names (e.g. AAPL), local beta should be vs SPY
    # For CA names (e.g. RY), local beta should be vs XIU
    for h in p_cb["holdings"]:
        assert "beta_local" in h
        assert "beta_cross" in h
        assert -2.0 <= h["beta_local"] <= 4.0
        assert -2.0 <= h["beta_cross"] <= 4.0


def test_correlation_clustering_and_effective_bets():
    """Verify that effective independent bets (N_eff) and correlation clusters are identified."""
    bundle = portfolio_engine.get_default_portfolios()
    p_cb = bundle["portfolios"]["PORT_CROSS_BORDER"]

    neff = p_cb["effective_bets"]
    assert 1.0 <= neff <= len(p_cb["holdings"])
    # 8 correlated tech/bank holdings should yield around 3.5 to 6.5 effective bets
    assert 3.0 <= neff <= 7.0

    clusters = p_cb["correlation_clusters"]
    assert len(clusters) >= 2
    for c in clusters:
        assert "cluster_name" in c
        assert "symbols" in c
        assert "avg_correlation" in c
        assert -1.0 <= c["avg_correlation"] <= 1.0


def test_dual_currency_fx_exposure():
    """Verify Bank of Canada Valet FX rate conversion and unhedged currency sensitivity."""
    bundle = portfolio_engine.get_default_portfolios()
    p_cb = bundle["portfolios"]["PORT_CROSS_BORDER"]
    fx = p_cb["fx_exposure"]

    assert "cad_usd_rate" in fx
    assert fx["cad_usd_rate"] > 1.2
    assert fx["usd_weight_pct"] > 0
    assert fx["cad_weight_pct"] > 0
    assert abs(fx["usd_weight_pct"] + fx["cad_weight_pct"] - 100.0) < 0.5
    assert "cad_depreciate_5pct_impact_pct" in fx
    assert "cad_appreciate_5pct_impact_pct" in fx
    assert "sensitivity_analysis" in fx


def test_macro_stress_replays():
    """Verify that 5 historical stress scenarios are replayed with simulated drawdowns."""
    bundle = portfolio_engine.get_default_portfolios()
    p_cb = bundle["portfolios"]["PORT_CROSS_BORDER"]
    replays = p_cb["stress_replays"]

    assert len(replays) == 5
    scenario_ids = [r["scenario_id"] for r in replays]
    assert "GFC_2008" in scenario_ids
    assert "COVID_2020" in scenario_ids
    assert "RATE_SHOCK_2022" in scenario_ids
    assert "OIL_CRASH_2014" in scenario_ids
    assert "VOL_SPIKE_2018" in scenario_ids

    for r in replays:
        assert "simulated_portfolio_drawdown_pct" in r
        assert "simulated_dollar_loss" in r
        assert "recovery_timeline_months" in r
        # 2008 GFC should produce severe negative simulated return
        if r["scenario_id"] == "GFC_2008":
            assert r["simulated_portfolio_drawdown_pct"] < -35.0


def test_drawdown_circuit_breakers_by_risk_profile():
    """Verify systematic drawdown circuit breakers (§9.4) customized by risk profile."""
    # Conservative profile
    breakers_cons = portfolio_engine.evaluate_circuit_breakers(current_drawdown_pct=-5.0, risk_profile="CONSERVATIVE")
    assert len(breakers_cons) == 3
    # L1 (-4%) should be triggered at -5%
    assert breakers_cons[0]["is_triggered"] is True
    assert breakers_cons[0]["status"] == "TRIGGERED"
    # L2 (-7%) should be normal at -5%
    assert breakers_cons[1]["is_triggered"] is False
    assert breakers_cons[1]["status"] == "NORMAL"

    # Aggressive profile (L1 is -8%)
    breakers_agg = portfolio_engine.evaluate_circuit_breakers(current_drawdown_pct=-5.0, risk_profile="AGGRESSIVE")
    # L1 (-8%) should NOT be triggered at -5%
    assert breakers_agg[0]["is_triggered"] is False
    assert breakers_agg[0]["status"] == "NORMAL"


def test_exit_signal_engine_integration_per_holding():
    """Verify that every portfolio holding receives an Exit Engine verdict (HOLD/WATCH/REDUCE/EXIT)."""
    bundle = portfolio_engine.get_default_portfolios()
    p_cb = bundle["portfolios"]["PORT_CROSS_BORDER"]

    for h in p_cb["holdings"]:
        assert h["exit_verdict"] in ["HOLD", "WATCH", "REDUCE", "EXIT"]
        assert "exit_score" in h
        assert "days_to_liquidate" in h
        assert h["days_to_liquidate"] >= 0.01


def test_csv_holdings_parser():
    """Verify CSV parser accepts pasted multi-line input and returns structured holdings."""
    csv_sample = """
    # My Sample Dual-Market Portfolio
    symbol, shares, cost_basis, currency
    AAPL, 50, 215.50, USD
    RY, 100, 155.00, CAD
    NVDA, 30, 110.00, USD
    ENB, 200, 48.50, CAD
    """
    holdings = portfolio_engine.parse_csv_holdings(csv_sample)
    assert len(holdings) == 4
    assert holdings[0]["symbol"] == "AAPL"
    assert holdings[0]["shares"] == 50.0
    assert holdings[0]["cost_basis"] == 215.50
    assert holdings[0]["currency"] == "USD"
    assert holdings[1]["symbol"] == "RY"
    assert holdings[1]["currency"] == "CAD"


def test_compliance_linter_on_portfolio_copy():
    """Verify that all portfolio disclaimers and summaries are strictly free of advisory violations."""
    bundle = portfolio_engine.get_default_portfolios()
    for pid, p in bundle["portfolios"].items():
        disclaimers = p.get("disclaimers", [])
        assert len(disclaimers) >= 2
        for d in disclaimers:
            linter.assert_clean(d)

        # Check sensitivity text
        fx_text = p["fx_exposure"].get("sensitivity_analysis", "")
        if fx_text:
            linter.assert_clean(fx_text)
