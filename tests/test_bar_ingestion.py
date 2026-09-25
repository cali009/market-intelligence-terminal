"""
Tests for Dual-Market Bar Ingestion Engine
Verifies symbol formatting, bounds checking, and dataframe parsing.
"""

import pandas as pd
from datetime import date
from src.data.bar_ingestion import bar_engine


def test_format_yfinance_symbol():
    # TSX and TSXV symbol formatting
    assert bar_engine.format_yfinance_symbol("SU", "TSX") == "SU.TO"
    assert bar_engine.format_yfinance_symbol("RY.TO", "TSX") == "RY.TO"
    assert bar_engine.format_yfinance_symbol("NOVA", "TSXV") == "NOVA.V"

    # US symbols remain plain
    assert bar_engine.format_yfinance_symbol("AAPL", "NASDAQ") == "AAPL"
    assert bar_engine.format_yfinance_symbol("SPY", "AMEX") == "SPY"


def test_validate_and_parse_bars():
    df = pd.DataFrame({
        "trading_date": [date(2026, 9, 23), date(2026, 9, 24)],
        "Open": [100.0, 102.0],
        "High": [105.0, 106.0],
        "Low": [98.0, 101.0],
        "Close": [103.0, 105.5],
        "Volume": [10000.0, 15000.0],
        "adjusted_close": [103.0, 105.5],
    })

    bars = bar_engine.validate_and_parse_bars(security_id=1, df=df)
    assert len(bars) == 2
    assert bars[0].close == 103.0
    assert bars[1].close == 105.5
    assert bars[0].low <= bars[0].open <= bars[0].high
    assert bars[0].low <= bars[0].close <= bars[0].high
