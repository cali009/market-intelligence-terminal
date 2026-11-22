"""
Tests for the closed-market / staleness determination.

Every case here is a situation the dashboard has to get right, including the two ways it
can be wrong in opposite directions: calling a closed market stale, and excusing a real
ingestion gap as a holiday.
"""

from datetime import date, datetime, timezone


from src.data.market_calendar import (
    AFTER_HOURS,
    CLOSED_WEEKEND,
    OPEN,
    PRE_MARKET,
    UNKNOWN_CALENDAR,
    closed_market_reason,
    market_state,
    sessions_completed_since,
)


def utc(y, m, d, hh=0, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


# Mon 2026-11-16 .. Fri 2026-11-20, a normal full week with no holidays.
FULL_WEEK = {date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 18),
             date(2026, 11, 19), date(2026, 11, 20)}


def test_weekend_after_a_friday_close_is_not_stale():
    """The exact live situation: Sunday morning, newest bar Friday. Not stale."""
    r = closed_market_reason("NYSE", date(2026, 11, 20), utc(2026, 11, 22, 1, 34), FULL_WEEK)
    assert r["badge"] == "CLOSED_MARKET"
    assert r["market_closed"] is True
    assert "weekend" in r["reason"]
    assert r["market_state"]["state"] == CLOSED_WEEKEND


def test_both_exchanges_resolve_through_their_own_calendar():
    for exch in ("NYSE", "NASDAQ", "AMEX", "TSX", "TSXV"):
        r = closed_market_reason(exch, date(2026, 11, 20), utc(2026, 11, 22, 1, 34), FULL_WEEK)
        assert r["badge"] == "CLOSED_MARKET", exch


def test_missing_a_friday_close_is_stale_not_closed():
    """If we never captured Friday, Friday IS observed elsewhere, so it is a real gap."""
    r = closed_market_reason("NYSE", date(2026, 11, 19), utc(2026, 11, 22, 1, 34), FULL_WEEK)
    assert r["badge"] == "STALE"
    assert r["market_closed"] is False
    assert "2026-11-20" in r["reason"]
    assert r["sessions"]["completed"] == 1


def test_holiday_inside_the_observed_span_is_not_a_gap():
    """A weekday with no bar anywhere, inside the ingested range, is a holiday."""
    observed = FULL_WEEK - {date(2026, 11, 19)}  # Thursday absent everywhere
    r = closed_market_reason("NYSE", date(2026, 11, 20), utc(2026, 11, 22, 1, 34), observed)
    assert r["badge"] == "CLOSED_MARKET"
    assert r["sessions"]["completed"] == 0


def test_weekday_beyond_the_ingested_history_is_unverified_not_excused():
    """
    The failure mode this module exists to avoid: ingestion silently stopped on the 16th,
    four weekdays passed, and the observed set has no bars for any of them. Reporting
    CLOSED_MARKET here would excuse a real outage. Reporting STALE would assert a session
    closed on a day that may have been a holiday. Neither is honest; UNVERIFIED_GAP is.
    """
    truncated = {date(2026, 11, 13), date(2026, 11, 16)}
    r = closed_market_reason("NYSE", date(2026, 11, 16), utc(2026, 11, 22, 1, 34), truncated)
    assert r["badge"] == "UNVERIFIED_GAP"
    assert r["market_closed"] is False
    assert r["sessions"]["unverified"] == 4
    assert r["sessions"]["completed"] == 0
    assert "cannot be established" in r["reason"]


def test_todays_session_is_not_due_until_it_ends():
    """Mid-session on a weekday: nothing is owed yet, so nothing is stale."""
    mid = utc(2026, 11, 18, 19, 0)  # 14:00 ET Wednesday
    assert market_state("NYSE", mid)["state"] == OPEN
    r = closed_market_reason("NYSE", date(2026, 11, 17), mid, FULL_WEEK)
    assert r["badge"] == "CLOSED_MARKET"
    assert r["sessions"]["completed"] == 0


def test_todays_session_becomes_due_once_it_ends():
    after = utc(2026, 11, 18, 22, 0)  # 17:00 ET Wednesday
    assert market_state("NYSE", after)["state"] == AFTER_HOURS
    r = closed_market_reason("NYSE", date(2026, 11, 17), after, FULL_WEEK)
    assert r["badge"] == "STALE"
    assert r["sessions"]["completed_dates"] == ["2026-11-18"]


def test_session_windows():
    assert market_state("NYSE", utc(2026, 11, 18, 12, 0))["state"] == PRE_MARKET   # 07:00 ET
    assert market_state("NYSE", utc(2026, 11, 18, 14, 30))["state"] == OPEN        # 09:30 ET
    assert market_state("NYSE", utc(2026, 11, 18, 20, 59))["state"] == OPEN        # 15:59 ET
    assert market_state("NYSE", utc(2026, 11, 18, 21, 0))["state"] == AFTER_HOURS  # 16:00 ET
    assert market_state("NYSE", utc(2026, 11, 21, 15, 0))["state"] == CLOSED_WEEKEND


def test_unknown_exchange_is_unknown_not_closed():
    r = closed_market_reason("LSE", date(2026, 11, 20))
    assert r["badge"] == "UNKNOWN"
    assert r["market_closed"] is False
    assert market_state("LSE")["state"] == UNKNOWN_CALENDAR


def test_no_bars_at_all_is_unknown():
    r = closed_market_reason("NYSE", None, utc(2026, 11, 22, 1, 34), FULL_WEEK)
    assert r["badge"] == "UNKNOWN"
    assert r["market_closed"] is False


def test_market_state_always_declares_that_holidays_are_not_modelled():
    """OPEN must never be read as 'the exchange is definitely trading today'."""
    for st in (market_state("NYSE", utc(2026, 11, 18, 15, 0)),
               market_state("TSX", utc(2026, 11, 18, 15, 0))):
        assert st["holiday_modelled"] is False
        assert "NOT modelled" in st["note"]


def test_naive_datetime_is_treated_as_utc():
    naive = datetime(2026, 11, 18, 15, 0)
    assert market_state("NYSE", naive)["state"] == OPEN


def test_sessions_completed_since_reports_none_without_a_baseline():
    out = sessions_completed_since("XNYS", None)
    assert out["completed"] is None
    assert "No bars" in out["reason"]


def test_sessions_completed_since_rejects_an_unknown_calendar():
    assert sessions_completed_since("XJSE", date(2026, 11, 20))["completed"] is None
