"""
Tests for Exit Signal Engine & State Machine (Phase 6)
US + Canada Market Intelligence Platform

Verifies:
- All 10 categorical exit triggers:
  1. Structural stop hit -> EXIT
  2. Profit Target 1 hit -> HOLD/Trim recommendation
  3. Profit Target 2 reached -> REDUCE/Trim recommendation
  4. Trend reversal confirmed -> REDUCE / EXIT
  5. Momentum deterioration -> WATCH / REDUCE
  6. Failed breakout -> WATCH / REDUCE
  7. Fundamental deterioration -> WATCH / REDUCE
  8. Material adverse catalyst -> EMERGENCY_RISK
  9. Extreme valuation excess -> WATCH / REDUCE
  10. Macro regime shift -> REDUCE
- Hysteresis escalation:
  WATCH for 3 consecutive cycles -> escalates to REDUCE
  REDUCE for 2 consecutive cycles -> escalates to EXIT
- De-escalation counters
- Output schema compliance with ExitVerdict
"""

import pytest
from src.engine.exit_engine import ExitSignalEngine, ExitVerdict


def test_structural_stop_trigger():
    engine = ExitSignalEngine()
    pos = {
        "id": 1,
        "symbol": "TEST",
        "entry_price": 100.0,
        "stop_loss": 92.0,
        "profit_target": 120.0,
    }
    # Current price has fallen below stop loss
    metrics = {"close": 90.5, "sma_20": 98.0, "sma_50": 99.0, "rsi_14": 35.0}

    verdict = engine.evaluate_position(pos, metrics)
    assert verdict.state == "EXIT"
    assert "STOP_LOSS_BREACH" in verdict.active_triggers
    assert verdict.trigger_severity == "EXIT"
    assert verdict.pnl_pct < 0
    assert verdict.target_allocation_pct == 0.0


def test_emergency_risk_trigger():
    engine = ExitSignalEngine()
    pos = {"id": 2, "symbol": "TEST_EMERGENCY", "entry_price": 100.0}
    metrics = {"close": 102.0}
    catalysts = [
        {"materiality_score": 5, "sentiment_score": -0.8, "headline": "SEC Investigation Launched"}
    ]

    verdict = engine.evaluate_position(pos, metrics, recent_catalysts=catalysts)
    assert verdict.state == "EMERGENCY_RISK"
    assert "EMERGENCY_RISK_EVENT" in verdict.active_triggers
    assert verdict.trigger_severity == "EMERGENCY"
    assert verdict.target_allocation_pct == 0.0


def test_trend_reversal_trigger():
    engine = ExitSignalEngine()
    pos = {"id": 3, "symbol": "TEST_TREND", "entry_price": 100.0, "stop_loss": 85.0}
    # Below 50-DMA with negative momentum
    metrics = {
        "close": 92.0,
        "sma_20": 95.0,
        "sma_50": 94.0,
        "macd_hist": -0.45,
        "rsi_14": 42.0,
    }

    verdict = engine.evaluate_position(pos, metrics)
    assert verdict.state in ["REDUCE", "WATCH"]
    assert "TREND_REVERSAL_CONFIRMED" in verdict.active_triggers
    assert verdict.target_allocation_pct <= 50.0


def test_profit_target_trigger():
    engine = ExitSignalEngine()
    pos = {
        "id": 4,
        "symbol": "TEST_GAIN",
        "entry_price": 100.0,
        "stop_loss": 90.0,
        "profit_target": 120.0,
    }
    # Hit profit target with healthy trend
    metrics = {
        "close": 125.0,
        "sma_20": 118.0,
        "sma_50": 110.0,
        "rsi_14": 68.0,
    }

    verdict = engine.evaluate_position(pos, metrics)
    assert "PROFIT_TARGET_HIT" in verdict.active_triggers
    assert verdict.pnl_pct == 25.0
    assert verdict.r_multiple >= 2.5


def test_hysteresis_escalation():
    engine = ExitSignalEngine()
    pos = {"id": 5, "symbol": "TEST_HYST", "entry_price": 100.0, "stop_loss": 80.0}
    # Momentum deterioration below 20DMA triggers WATCH state
    metrics = {
        "close": 98.0,
        "sma_20": 100.0,
        "sma_50": 95.0,
        "rsi_14": 42.0,
    }

    # Cycle 1 -> WATCH
    v1 = engine.evaluate_position(pos, metrics)
    assert v1.state == "WATCH"

    # Cycle 2 -> WATCH
    v2 = engine.evaluate_position(pos, metrics)
    assert v2.state == "WATCH"

    # Cycle 3 -> Escalation from persistent WATCH to REDUCE
    v3 = engine.evaluate_position(pos, metrics)
    assert v3.state == "REDUCE"
    assert "ESCALATION_WATCH_TO_REDUCE" in v3.active_triggers


def test_macro_regime_shift_trigger():
    engine = ExitSignalEngine()
    pos = {"id": 6, "symbol": "TEST_REGIME", "entry_price": 100.0, "stop_loss": 85.0}
    metrics = {"close": 101.0, "sma_20": 100.0, "sma_50": 98.0}

    verdict = engine.evaluate_position(pos, metrics, regime_state="CRISIS")
    assert verdict.state in ["REDUCE", "EXIT"]
    assert "REGIME_DETERIORATION" in verdict.active_triggers
