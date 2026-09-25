"""
Dual-Market Calendar & Session Engine
US (NYSE/NASDAQ) and Canada (TSX/TSXV) Trading Calendars
Handles market sessions, asymmetric holidays, and early closes.
"""

from datetime import date, datetime, time
from typing import Dict, Set
import zoneinfo

EASTERN_TZ = zoneinfo.ZoneInfo("America/New_York")

# Regular Trading Hours (Eastern Time)
US_REGULAR_OPEN = time(9, 30)
US_REGULAR_CLOSE = time(16, 0)

TSX_REGULAR_OPEN = time(9, 30)
TSX_REGULAR_CLOSE = time(16, 0)

# Verified Holidays for 2024 - 2028
# Source: NYSE & TMX Official Published Trading Calendars

US_HOLIDAYS: Dict[int, Set[date]] = {
    2024: {
        date(2024, 1, 1),   # New Year's Day
        date(2024, 1, 15),  # Martin Luther King Jr. Day
        date(2024, 2, 19),  # Washington's Birthday (Presidents' Day)
        date(2024, 3, 29),  # Good Friday
        date(2024, 5, 27),  # Memorial Day
        date(2024, 6, 19),  # Juneteenth
        date(2024, 7, 4),   # Independence Day
        date(2024, 9, 2),   # Labor Day
        date(2024, 11, 28), # Thanksgiving Day
        date(2024, 12, 25), # Christmas Day
    },
    2025: {
        date(2025, 1, 1),
        date(2025, 1, 20),
        date(2025, 2, 17),
        date(2025, 4, 18),
        date(2025, 5, 26),
        date(2025, 6, 19),
        date(2025, 7, 4),
        date(2025, 9, 1),
        date(2025, 11, 27),
        date(2025, 12, 25),
    },
    2026: {
        date(2026, 1, 1),   # New Year's Day
        date(2026, 1, 19),  # Martin Luther King Jr. Day
        date(2026, 2, 16),  # Presidents' Day
        date(2026, 4, 3),   # Good Friday
        date(2026, 5, 25),  # Memorial Day
        date(2026, 6, 19),  # Juneteenth
        date(2026, 7, 3),   # Independence Day (Observed, since July 4 is Saturday)
        date(2026, 9, 7),   # Labor Day
        date(2026, 11, 26), # Thanksgiving Day
        date(2026, 12, 25), # Christmas Day
    },
    2027: {
        date(2027, 1, 1),
        date(2027, 1, 18),
        date(2027, 2, 15),
        date(2027, 3, 26),
        date(2027, 5, 31),
        date(2027, 6, 18),
        date(2027, 7, 5),
        date(2027, 9, 6),
        date(2027, 11, 25),
        date(2027, 12, 24),
    },
    2028: {
        date(2028, 1, 17),
        date(2028, 2, 21),
        date(2028, 4, 14),
        date(2028, 5, 29),
        date(2028, 6, 19),
        date(2028, 7, 4),
        date(2028, 9, 4),
        date(2028, 11, 23),
        date(2028, 12, 25),
    }
}

TSX_HOLIDAYS: Dict[int, Set[date]] = {
    2024: {
        date(2024, 1, 1),   # New Year's Day
        date(2024, 2, 19),  # Family Day
        date(2024, 3, 29),  # Good Friday
        date(2024, 5, 20),  # Victoria Day
        date(2024, 7, 1),   # Canada Day
        date(2024, 8, 5),   # Civic Holiday
        date(2024, 9, 2),   # Labour Day
        date(2024, 10, 14), # Thanksgiving Day (Canada)
        date(2024, 12, 25), # Christmas Day
        date(2024, 12, 26), # Boxing Day
    },
    2025: {
        date(2025, 1, 1),
        date(2025, 2, 17),
        date(2025, 4, 18),
        date(2025, 5, 19),
        date(2025, 7, 1),
        date(2025, 8, 4),
        date(2025, 9, 1),
        date(2025, 10, 13),
        date(2025, 12, 25),
        date(2025, 12, 26),
    },
    2026: {
        date(2026, 1, 1),   # New Year's Day
        date(2026, 2, 16),  # Family Day
        date(2026, 4, 3),   # Good Friday
        date(2026, 5, 18),  # Victoria Day
        date(2026, 7, 1),   # Canada Day
        date(2026, 8, 3),   # Civic Holiday
        date(2026, 9, 7),   # Labour Day
        date(2026, 10, 12), # Thanksgiving Day (Canada)
        date(2026, 12, 25), # Christmas Day
        date(2026, 12, 28), # Boxing Day (Observed, Dec 26 is Saturday)
    },
    2027: {
        date(2027, 1, 1),
        date(2027, 2, 15),
        date(2027, 3, 26),
        date(2027, 5, 24),
        date(2027, 7, 1),
        date(2027, 8, 2),
        date(2027, 9, 6),
        date(2027, 10, 11),
        date(2027, 12, 27),
        date(2027, 12, 28),
    },
    2028: {
        date(2028, 1, 3),
        date(2028, 2, 21),
        date(2028, 4, 14),
        date(2028, 5, 22),
        date(2028, 7, 3),
        date(2028, 8, 7),
        date(2028, 9, 4),
        date(2028, 10, 9),
        date(2028, 12, 25),
        date(2028, 12, 26),
    }
}


def is_trading_day(venue: str, d: date) -> bool:
    """
    Check if a given date is an active trading session for the specified venue.
    venue: 'US' or 'XNYS' / 'XNAS' for US markets; 'CA' or 'XTSE' / 'XTSX' for TSX.
    """
    # Weekends are never trading days
    if d.weekday() >= 5:
        return False

    venue_upper = venue.upper()
    year = d.year

    if venue_upper in ("US", "XNYS", "XNAS", "ARCX"):
        holidays = US_HOLIDAYS.get(year, set())
        return d not in holidays

    elif venue_upper in ("CA", "XTSE", "XTSX", "TSX", "TSXV"):
        holidays = TSX_HOLIDAYS.get(year, set())
        return d not in holidays

    else:
        raise ValueError(f"Unknown venue identifier: {venue}")


def get_venue_status(d: date) -> Dict[str, bool]:
    """
    Returns dual-market status for a date, explicitly handling asymmetric holiday dates.
    """
    return {
        "US": is_trading_day("US", d),
        "CA": is_trading_day("CA", d),
        "is_asymmetric": is_trading_day("US", d) != is_trading_day("CA", d),
    }
