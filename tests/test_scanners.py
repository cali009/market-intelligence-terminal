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


def test_all_14_definitions_exist():
    defs = market_scanners.get_scanner_definitions_with_expectancy("STRONG_BULL")
    assert len(defs) == 14
    for d in defs:
        assert "scanner_id" in d
        assert "name" in d
        assert "category" in d
        assert "rule_summary" in d
        assert "historical_win_rate_pct" in d
        assert d["historical_win_rate_pct"] > 40.0
        assert "forward_5d_return_pct" in d
        assert "expectancy_r" in d
        assert d["regime_status"] in ("PASS", "GATED")


def test_regime_gating_behavior():
    # MOMENTUM_LEADERS should be PASS in STRONG_BULL, but GATED in CRISIS
    bull_defs = {d["scanner_id"]: d for d in market_scanners.get_scanner_definitions_with_expectancy("STRONG_BULL")}
    crisis_defs = {d["scanner_id"]: d for d in market_scanners.get_scanner_definitions_with_expectancy("CRISIS")}

    assert bull_defs["MOMENTUM_LEADERS"]["regime_status"] == "PASS"
    assert crisis_defs["MOMENTUM_LEADERS"]["regime_status"] == "GATED"
    assert crisis_defs["MEAN_REVERSION_OVERSOLD"]["regime_status"] == "GATED" or crisis_defs["MEAN_REVERSION_OVERSOLD"]["regime_status"] == "PASS"


def test_scanner_database_persistence():
    inserted = market_scanners.init_scanner_definitions_in_db()
    assert inserted == 14

    sec_info = {"symbol": "NVDA", "exchange": "NASDAQ", "country": "US"}
    metrics = {
        "close": 125.0,
        "sma_20": 120.0,
        "sma_50": 110.0,
        "sma_200": 95.0,
        "sma_200_slope_20d": 15.0,
        "return_3m": 0.25,
        "proximity_52w_high": -0.015,
        "rvol_20": 1.45,
        "rsi_14": 62.0,
        "bb_bandwidth": 0.07,
        "atr_pct": 2.1,
    }
    matches = market_scanners.scan_all(sec_info, metrics, regime_state="WEAK_BULL")
    run_id = market_scanners.persist_scanner_run(
        as_of_date="2026-09-26",
        universe_size=1,
        matches=matches,
        regime_state="WEAK_BULL"
    )
    assert run_id > 0
