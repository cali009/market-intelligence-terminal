"""
Tests for Vectorized Technical Analysis Engine
Verifies mathematical correctness of indicators, moving averages, and oscillators.
"""

import numpy as np
import pandas as pd
from datetime import date, timedelta
from src.engine.technicals import TechnicalAnalysisEngine


def generate_synthetic_bars(n: int = 100, trend: float = 0.5) -> pd.DataFrame:
    """Generate synthetic consistent OHLCV bars."""
    start_date = date(2025, 1, 1)
    dates = [start_date + timedelta(days=i) for i in range(n)]

    # Steady upward trending price series
    base = 100.0 + np.cumsum(np.full(n, trend) + np.sin(np.linspace(0, 10, n)))
    close = base
    high = base + 1.5
    low = base - 1.2
    open_p = (high + low) / 2.0
    volume = np.full(n, 1_000_000.0)

    return pd.DataFrame({
        "trading_date": dates,
        "open": open_p,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "adjusted_close": close,
    })


def test_technical_indicators_calculation():
    df = generate_synthetic_bars(n=60, trend=0.5)
    features = TechnicalAnalysisEngine.compute_all_features(df)

    assert "sma_20" in features.columns
    assert "sma_50" in features.columns
    assert "rsi_14" in features.columns
    assert "macd_line" in features.columns
    assert "atr_14" in features.columns
    assert "bb_upper" in features.columns
    assert "rvol_20" in features.columns

    # Verify RSI is within bounds [0, 100]
    valid_rsi = features["rsi_14"].dropna()
    assert (valid_rsi >= 0.0).all() and (valid_rsi <= 100.0).all()

    # In upward trending series, SMA20 should be below close
    assert features["close"].iloc[-1] > features["sma_20"].iloc[-1]

    # Verify Bollinger Bands ordering: lower <= sma_20 <= upper
    last_row = features.iloc[-1]
    assert last_row["bb_lower"] <= last_row["sma_20"] <= last_row["bb_upper"]

    # Verify ATR is positive
    assert last_row["atr_14"] > 0
    assert last_row["atr_pct"] > 0


def test_latest_feature_snapshot():
    df = generate_synthetic_bars(n=40)
    snapshot = TechnicalAnalysisEngine.get_latest_feature_snapshot(df)
    assert isinstance(snapshot, dict)
    assert "rsi_14" in snapshot
    assert "rvol_20" in snapshot
    assert snapshot["close"] > 0
