"""
Tests for the closed-market / staleness determination.

Every case here is a situation the dashboard has to get right, including the two ways it
can be wrong in opposite directions: calling a closed market stale, and excusing a real
ingestion gap as a holiday.
"""

from datetime import date, datetime, timezone


from src.data.market_calendar import (
    AFTER_HOURS,
    CLOSED_HOLIDAY,
    CLOSED_WEEKEND,
    OPEN,
    PRE_MARKET,
    UNKNOWN_CALENDAR,
    closed_market_reason,
    market_state,
    modelled_holiday,
    sessions_completed_since,
)

# The badge closed_market_reason() returns when a weekday is missing and no holiday
# rule explains it. A literal here rather than an imported constant, matching how
# the module publishes it.
UNVERIFIED_GAP = "UNVERIFIED_GAP"


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


def test_market_state_always_declares_what_it_does_and_does_not_model():
    """
    OPEN must never be read as "the exchange is definitely trading today".

    This used to assert holiday_modelled is False, which was true when the module
    deliberately modelled no holidays at all. It now derives the published rule for
    each exchange, so the contract changed rather than lapsed: a known calendar says
    it models holidays and names the one it lands on, an unknown calendar still says
    it models none, and either way the note keeps warning that ad-hoc closures are
    not derivable from a rule.
    """
    for st in (market_state("NYSE", utc(2026, 11, 18, 15, 0)),
               market_state("TSX", utc(2026, 11, 18, 15, 0))):
        assert st["holiday_modelled"] is True
        # The caveat is the point of the field: a rule cannot predict a closure the
        # exchange calls ad hoc, so the note must keep saying so.
        assert "ad-hoc" in st["note"].lower()

    unknown = market_state("XLON", utc(2026, 11, 18, 15, 0))
    assert unknown["holiday_modelled"] is False


def test_a_weekday_holiday_reads_as_closed_not_open():
    """2026-11-26 15:00 UTC is inside NYSE session hours and is Thanksgiving."""
    st = market_state("NYSE", utc(2026, 11, 26, 15, 0))
    assert st["state"] == CLOSED_HOLIDAY
    assert st["holiday"] == "Thanksgiving Day"
    # The same instant in Toronto is an ordinary trading session.
    tsx = market_state("TSX", utc(2026, 11, 26, 15, 0))
    assert tsx["state"] == OPEN
    assert tsx["holiday"] is None


def test_naive_datetime_is_treated_as_utc():
    naive = datetime(2026, 11, 18, 15, 0)
    assert market_state("NYSE", naive)["state"] == OPEN


def test_sessions_completed_since_reports_none_without_a_baseline():
    out = sessions_completed_since("XNYS", None)
    assert out["completed"] is None
    assert "No bars" in out["reason"]


def test_sessions_completed_since_rejects_an_unknown_calendar():
    assert sessions_completed_since("XJSE", date(2026, 11, 20))["completed"] is None


# ---------------------------------------------------------------------------
# Holiday rules
#
# The model may only ever *excuse* a day it knows was a holiday. It must never turn
# an unexplained missing close into a trading day, so every test below that could
# pass by the model being too generous is paired with a negative control.
# ---------------------------------------------------------------------------


def test_thanksgiving_is_modelled_for_the_us_but_not_the_tsx():
    """2026-11-26 is the 4th Thursday of November: US shut, TSX open."""
    assert modelled_holiday("XNYS", date(2026, 11, 26)) == "Thanksgiving Day"
    assert modelled_holiday("XTSE", date(2026, 11, 26)) is None


def test_holiday_rules_track_their_published_definition_across_years():
    """US Thanksgiving is the 4th Thursday; Canada's is the 2nd Monday of October."""
    assert modelled_holiday("XNYS", date(2024, 11, 28)) == "Thanksgiving Day"
    assert modelled_holiday("XNYS", date(2025, 11, 27)) == "Thanksgiving Day"
    assert modelled_holiday("XTSE", date(2026, 10, 12)) == "Thanksgiving Day"


def test_good_friday_follows_the_computus_not_a_typed_date():
    """Easter moves, so Good Friday must be computed. Known dates used as controls."""
    assert modelled_holiday("XNYS", date(2024, 3, 29)) == "Good Friday"
    assert modelled_holiday("XTSE", date(2026, 4, 3)) == "Good Friday"
    assert modelled_holiday("XNYS", date(2025, 4, 18)) == "Good Friday"
    # The day before is never Good Friday; if the computus drifted it would land here.
    assert modelled_holiday("XNYS", date(2024, 3, 28)) is None


def test_weekend_fixed_holidays_shift_to_the_nearest_weekday():
    """Saturday is observed Friday, Sunday Monday, per both exchanges."""
    assert modelled_holiday("XNYS", date(2026, 7, 3)) == "Independence Day"   # 4th is a Sat
    assert modelled_holiday("XNYS", date(2021, 12, 24)) == "Christmas Day"    # 25th is a Sat
    assert modelled_holiday("XTSE", date(2027, 12, 27)) == "Boxing Day"       # 26th is a Sun


def test_victoria_day_is_the_monday_before_25_may_even_when_the_25th_is_a_monday():
    """Regression: subtracting weekday()+1 landed on the Sunday before in 2026."""
    assert modelled_holiday("XTSE", date(2026, 5, 18)) == "Victoria Day"
    assert modelled_holiday("XTSE", date(2026, 5, 24)) is None
    assert modelled_holiday("XTSE", date(2024, 5, 20)) == "Victoria Day"


def test_a_modelled_holiday_excuses_a_missing_close():
    """
    Thanksgiving 2026 with no bar is a closed market, not an unverified gap.

    The instant matters and is the real one: 2026-11-27 00:43 UTC is still Thursday
    evening in New York, so the only date in the window is the holiday itself.
    """
    out = sessions_completed_since(
        "XNYS", date(2026, 11, 25),
        now_utc=datetime(2026, 11, 27, 0, 43, tzinfo=timezone.utc),
        observed={date(2026, 11, 25)},
    )
    assert out["holidays"] == 1
    assert out["holiday_dates"] == [{"date": "2026-11-26", "holiday": "Thanksgiving Day"}]
    assert out["unverified"] == 0
    assert out["completed"] == 0
    assert out["holiday_modelled"] is True


def test_negative_control_an_unexplained_weekday_is_still_a_gap():
    """
    2026-12-14 is an ordinary Monday that no rule explains. Missing a close there is
    either an ad-hoc exchange closure this module cannot derive, or an ingestion
    failure. Excusing it as a holiday would be the exact dishonesty check 11 exists
    to catch, so it must stay UNVERIFIED_GAP.
    """
    assert modelled_holiday("XNYS", date(2026, 12, 14)) is None
    out = sessions_completed_since(
        "XNYS", date(2026, 12, 11),
        now_utc=datetime(2026, 12, 15, 12, 0, tzinfo=timezone.utc),
        observed={date(2026, 12, 11)},
    )
    assert out["unverified"] == 1
    assert out["unverified_dates"] == ["2026-12-14"]
    assert out["holidays"] == 0
    reason = closed_market_reason(
        "NYSE", date(2026, 12, 11),
        now_utc=datetime(2026, 12, 15, 12, 0, tzinfo=timezone.utc),
        observed={date(2026, 12, 11)},
    )
    assert reason["market_closed"] is False
    assert reason["badge"] == UNVERIFIED_GAP


def test_negative_control_a_modelled_holiday_does_not_excuse_a_real_gap():
    """
    If a session was missed *and* a holiday fell in the same window, the missed session
    must still be reported. The holiday explains itself only.
    """
    out = sessions_completed_since(
        "XNYS", date(2026, 11, 24),
        now_utc=datetime(2026, 11, 28, 12, 0, tzinfo=timezone.utc),
        observed={date(2026, 11, 24)},
    )
    assert out["holidays"] == 1            # 11-26 Thanksgiving
    # Both 11-25 and 11-27 are ordinary weekdays with no bar and no rule to excuse them.
    assert out["unverified"] == 2
    assert out["unverified_dates"] == ["2026-11-25", "2026-11-27"]


def test_unknown_calendar_never_claims_a_holiday():
    assert modelled_holiday("XLON", date(2026, 12, 25)) is None
