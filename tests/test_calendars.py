"""
Tests for Dual-Market Calendar Engine
Verifies trading sessions, holiday asymmetric handling, and weekend detection.
"""

from datetime import date
from config.calendars import is_trading_day, get_venue_status


def test_weekend_detection():
    # 2026-09-26 is Saturday, 2026-09-27 is Sunday
    assert not is_trading_day("US", date(2026, 9, 26))
    assert not is_trading_day("CA", date(2026, 9, 27))


def test_regular_trading_day():
    # 2026-09-24 is Thursday
    assert is_trading_day("US", date(2026, 9, 24))
    assert is_trading_day("CA", date(2026, 9, 24))


def test_asymmetric_holiday_civic_holiday_canada():
    # 2026-08-03 is Civic Holiday in Canada (TSX Closed, US Open)
    civic_day = date(2026, 8, 3)
    status = get_venue_status(civic_day)
    assert status["US"] is True
    assert status["CA"] is False
    assert status["is_asymmetric"] is True


def test_asymmetric_holiday_presidents_day_us():
    # 2026-02-16 is Presidents' Day in US (US Closed, Family Day in Canada so both closed in 2026)
    # Juneteenth 2026-06-19: US Closed, TSX Open
    juneteenth = date(2026, 6, 19)
    status = get_venue_status(juneteenth)
    assert status["US"] is False
    assert status["CA"] is True
    assert status["is_asymmetric"] is True
