"""
Unit & Integration Tests: Walk-Forward Backtesting Engine (Phase 7)
US + Canada Market Intelligence Platform

Verifies:
- Event-driven simulation & strict anti-lookahead execution (orders fill T+1 Open)
- Walk-forward partitioning (TRAIN 60%, VALIDATION 20%, TEST 20%)
- Deflated Sharpe Ratio (DSR) & Probability of Backtest Overfitting (PBO)
- Cost sensitivity analysis (1x, 2x, 3x) and break-even cost
- Strategy Registry & Pre-Registration Lifecycle:
  - At least 3 active strategies
  - At least 1 retired strategy on empirical evidence (TAC-03 Breakout Chase)
- Calendar year-by-year performance breakdown
- Statutory hypothetical disclaimer auto-attachment
"""

import pytest
from src.engine.backtester import backtest_engine, BacktestConfig


def test_backtest_execution_ensemble():
    """Verify that the 8-Factor Ensemble backtest executes and calculates full metric set."""
    res = backtest_engine.run_strategy_backtest("ENSEMBLE_8F")
    assert res["strategy_id"] == "ENSEMBLE_8F"
    assert res["status"] == "ACTIVE"
    assert "metrics" in res
    m = res["metrics"]
    assert "total_trades" in m and m["total_trades"] > 0
    assert "cagr_pct" in m
    assert "sharpe_ratio" in m
    assert "max_drawdown_pct" in m
    assert "win_rate_pct" in m
    assert "profit_factor" in m
    assert "expectancy_r" in m


def test_walk_forward_partitions_exist():
    """Verify that walk-forward partitions (TRAIN, VALIDATION, TEST) are strictly populated."""
    res = backtest_engine.run_strategy_backtest("TAC01_PULLBACK")
    m = res["metrics"]
    partitions = m["walk_forward_partitions"]
    assert "TRAIN" in partitions
    assert "VALIDATION" in partitions
    assert "TEST" in partitions
    assert partitions["TRAIN"]["total_trades"] > 0
    assert partitions["VALIDATION"]["total_trades"] > 0
    assert partitions["TEST"]["total_trades"] > 0


def test_anti_lookahead_execution():
    """Verify that every simulated trade executes strictly AFTER the signal date."""
    res = backtest_engine.run_strategy_backtest("TAC02_SQUEEZE")
    trades = res["recent_trades"]
    for t in trades:
        assert t["entry_date"] > t["signal_date"], f"Lookahead violation detected in trade {t['symbol']}"


def test_deflated_sharpe_ratio_and_pbo():
    """Verify that Deflated Sharpe Ratio (DSR) and PBO are computed accounting for multiple trials."""
    res = backtest_engine.run_strategy_backtest("TAC04_MOMENTUM")
    m = res["metrics"]
    assert "deflated_sharpe_ratio" in m
    assert "probability_backtest_overfitting" in m
    assert 0.0 <= m["deflated_sharpe_ratio"] <= 1.0
    assert 0.0 <= m["probability_backtest_overfitting"] <= 1.0
    assert m["num_trials_tested"] == 12


def test_cost_sensitivity_and_breakeven():
    """Verify that cost sensitivity at 1x, 2x, 3x is calculated with a break-even cost."""
    res = backtest_engine.run_strategy_backtest("ENSEMBLE_8F")
    m = res["metrics"]
    assert "cost_sensitivity" in m
    cs = m["cost_sensitivity"]
    assert "cost_1x" in cs
    assert "cost_2x" in cs
    assert "cost_3x" in cs
    assert "breakeven_cost_bps" in cs
    # 2x fees should be double 1x fees
    assert cs["cost_2x"]["fees_usd"] > cs["cost_1x"]["fees_usd"]


def test_strategy_registry_active_and_retired():
    """Verify that at least 3 strategies are ACTIVE and at least 1 is RETIRED on evidence (DoD)."""
    reg = backtest_engine.get_strategy_registry()
    active = [k for k, v in reg.items() if v["status"] == "ACTIVE"]
    retired = [k for k, v in reg.items() if v["status"] == "RETIRED"]

    assert len(active) >= 3, f"Expected at least 3 active strategies, found {len(active)}"
    assert len(retired) >= 1, f"Expected at least 1 retired strategy on empirical evidence, found {len(retired)}"
    assert "TAC03_BREAKOUT_CHASE" in retired


def test_retired_strategy_autopsy_evidence():
    """Verify that the retired strategy (TAC-03) executes and documents retirement evidence."""
    res = backtest_engine.run_strategy_backtest("TAC03_BREAKOUT_CHASE")
    assert res["status"] == "RETIRED"
    assert res["retirement_reason"] is not None
    assert "empirical evidence" in res["retirement_reason"].lower()


def test_calendar_yearly_breakdown():
    """Verify calendar year-by-year performance breakdown table is generated."""
    res = backtest_engine.run_strategy_backtest("TAC01_PULLBACK")
    m = res["metrics"]
    assert "yearly_breakdown" in m
    yb = m["yearly_breakdown"]
    assert len(yb) >= 2
    for yr in yb:
        assert "year" in yr
        assert "return_pct" in yr
        assert "trades" in yr
        assert "win_rate_pct" in yr


def test_backtest_disclaimer_attached():
    """Verify statutory hypothetical performance disclosure is present."""
    res = backtest_engine.run_strategy_backtest("TAC04_MOMENTUM")
    assert "statutory_disclaimer" in res
    assert "HYPOTHETICAL" in res["statutory_disclaimer"].upper()
    assert "LIMITATIONS" in res["statutory_disclaimer"].upper()
