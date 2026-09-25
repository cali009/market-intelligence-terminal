"""
Tests for 14 Market Scanners
Verifies deterministic pattern triggers and explanation strings.
"""

from src.engine.scanners import market_scanners


def test_momentum_leaders_and_52w_breakout():
    sec_info = {"symbol": "NVDA", "exchange": "NASDAQ", "country": "US"}
    metrics = {
        "close": 125.0,
        "sma_20": 120.0,
        "sma_50": 110.0,
        "sma_200": 95.0,
        "sma_200_slope_20d": 15.0,
        "return_3m": 0.25,            # +25%
        "proximity_52w_high": -0.015, # 1.5% from 52w high
        "rvol_20": 1.45,
        "rsi_14": 62.0,
        "bb_bandwidth": 0.07,
        "atr_pct": 2.1,
    }

    matches = market_scanners.scan_all(sec_info, metrics)
    matched_ids = [m.scanner_id for m in matches]

    assert "MOMENTUM_LEADERS" in matched_ids
    assert "BREAKOUT_52W_HIGH" in matched_ids
    assert "VOLATILITY_SQUEEZE" in matched_ids

    for m in matches:
        assert m.symbol == "NVDA"
        assert len(m.why_matched) > 20
        assert m.price == 125.0


def test_pullback_uptrend_scanner():
    sec_info = {"symbol": "SHOP", "exchange": "TSX", "country": "CA"}
    metrics = {
        "close": 102.0,
        "sma_20": 103.5,              # Pulled back near 20DMA (1.4% distance)
        "sma_50": 95.0,
        "sma_200": 85.0,
        "sma_200_slope_20d": 10.0,
        "rsi_14": 49.0,               # Digestion zone
        "atr_pct": 2.4,
    }

    matches = market_scanners.scan_all(sec_info, metrics)
    matched_ids = [m.scanner_id for m in matches]
    assert "PULLBACK_UPTREND" in matched_ids


def test_mean_reversion_oversold_scanner():
    sec_info = {"symbol": "ENB", "exchange": "TSX", "country": "CA"}
    metrics = {
        "close": 45.0,
        "sma_20": 50.0,
        "sma_50": 52.0,
        "sma_200": 53.0,
        "bb_lower": 45.5,             # Under lower Bollinger Band
        "rsi_14": 28.5,               # Heavily oversold
    }

    matches = market_scanners.scan_all(sec_info, metrics)
    matched_ids = [m.scanner_id for m in matches]
    assert "MEAN_REVERSION_OVERSOLD" in matched_ids
