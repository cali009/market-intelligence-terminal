"""
Unit & Integration Tests: Walk-Forward Backtesting Engine
US + Canada Market Intelligence Platform
Verifies event-driven simulation, anti-lookahead controls, metric calculation, and statutory compliance.
"""

import pytest
from src.engine.backtester import backtest_engine, BacktestConfig


def test_backtest_execution_ensemble():
    """Verify that the 8-Factor Ensemble backtest executes and calculates full metric set."""
    res = backtest_engine.run_strategy_backtest("ENSEMBLE_8F")
    assert res["strategy_id"] == "ENSEMBLE_8F"
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


def test_backtest_disclaimer_attached():
    """Verify statutory hypothetical performance disclosure is present."""
    res = backtest_engine.run_strategy_backtest("TAC04_MOMENTUM")
    assert "statutory_disclaimer" in res
    assert "HYPOTHETICAL" in res["statutory_disclaimer"].upper()
    assert "LIMITATIONS" in res["statutory_disclaimer"].upper()
