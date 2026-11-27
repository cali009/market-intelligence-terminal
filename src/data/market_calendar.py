"""
Session and closed-market determination for the exchanges this platform covers.

Holidays are modelled, but only as computed rules, and only in one direction.

A typed-in list of holiday dates would be exactly the kind of figure this project refuses
to carry: it goes silently wrong the year nobody updates it, and a wrong holiday is
indistinguishable from a missing close. The holidays below are instead derived from the
published rule for each one -- the fourth Thursday of November, the Monday preceding 25
May, Good Friday from the Gregorian computus -- so there is no list to fall out of date.

The asymmetry is the part that matters:

  * A date the rules say is a holiday MAY excuse a missing close, because the exchange
    publishes that it was shut.
  * The absence of a holiday rule NEVER establishes that a day was a trading day. Ad-hoc
    closures are not derivable from any rule -- 11 September 2001, Hurricane Sandy in
    2012, a state funeral -- so an unobserved weekday that the rules do not explain is
    still reported as UNVERIFIED_GAP rather than quietly treated as a session we missed.

Everything else is unchanged:

  * Weekend closure is arithmetic and needs no list.
  * Session times below are the published, stable regular-session hours.
  * Whether a *past* weekday was actually a trading day is answered by ``bar_1d`` itself --
    the observed set of trading dates, not an assumption.
  * For *today*, if the session has not ended yet there is nothing to observe, so the state
    is reported from the clock. If the session has ended and no bar exists, that is a
    genuine gap and is reported as such rather than excused as a holiday.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Set
from zoneinfo import ZoneInfo

# Published regular-session hours. Both exchanges open 09:30 and close 16:00 local;
# they sit in different zones only because Toronto and New York share Eastern Time
# year-round, so the wall-clock window is identical and the zone is kept per exchange
# so a future venue in another zone does not silently inherit New York's clock.
SESSIONS: Dict[str, Dict] = {
    "XNYS": {"tz": "America/New_York", "open": time(9, 30), "close": time(16, 0),
             "label": "NYSE/Nasdaq regular session 09:30-16:00 ET"},
    "XTSE": {"tz": "America/Toronto", "open": time(9, 30), "close": time(16, 0),
             "label": "TSX regular session 09:30-16:00 ET"},
}

# Which stored `exchange` values belong to which calendar.
EXCHANGE_TO_CALENDAR = {
    "NYSE": "XNYS", "NASDAQ": "XNYS", "AMEX": "XNYS", "NYSEARCA": "XNYS", "CBOE": "XNYS",
    "TSX": "XTSE", "TSXV": "XTSE",
}

# States. Kept as a closed set so the UI can map every one of them to a label.
CLOSED_WEEKEND = "CLOSED_WEEKEND"
CLOSED_HOLIDAY = "CLOSED_HOLIDAY"
PRE_MARKET = "PRE_MARKET"
OPEN = "OPEN"
AFTER_HOURS = "AFTER_HOURS"
UNKNOWN_CALENDAR = "UNKNOWN_CALENDAR"


def _calendar_for(exchange: str) -> Optional[str]:
    return EXCHANGE_TO_CALENDAR.get(str(exchange).strip().upper())


# ---------------------------------------------------------------------------
# Holiday rules
#
# Each rule is computed from its published definition, never read from a list of
# dates. A list would need an owner to keep it current every year; a rule does not.
# ---------------------------------------------------------------------------


def _easter(year: int) -> date:
    """Gregorian computus (Meeus/Jones/Butcher). Good Friday is derived from this."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The n-th given weekday of a month (weekday: Mon=0). n=-1 means last."""
    if n > 0:
        first = date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + timedelta(days=offset + 7 * (n - 1))
    last = date(year, month, 31) if month in (1, 3, 5, 7, 8, 10, 12) else (
        date(year, month, 30) if month != 2 else date(year, 2, 29 if _is_leap(year) else 28))
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset)


def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _last_monday_before(year: int, month: int, day: int) -> date:
    """
    The Monday strictly preceding a given date -- TSX Victoria Day (before 25 May).

    Step off the anchor first, then walk back. Subtracting ``weekday() + 1`` directly
    lands on the Sunday before whenever the anchor is itself a Monday, which is exactly
    what happened for 2026: 25 May 2026 is a Monday and Victoria Day that year is
    18 May, not 24 May.
    """
    d = date(year, month, day) - timedelta(days=1)
    return d - timedelta(days=d.weekday())


def _observed(d: date) -> date:
    """
    Shift a fixed-date holiday off a weekend, the way both exchanges do:
    Saturday is observed on the Friday before, Sunday on the Monday after.
    """
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _holiday_rules(calendar_id: str, year: int) -> Dict[date, str]:
    """Every holiday a calendar closes for in a given year, computed from its rule."""
    out: Dict[date, str] = {}
    if calendar_id == "XNYS":
        out[_observed(date(year, 1, 1))] = "New Year's Day"
        out[_nth_weekday(year, 1, 0, 3)] = "Martin Luther King Jr. Day"
        out[_nth_weekday(year, 2, 0, 3)] = "Washington's Birthday"
        out[_easter(year) - timedelta(days=2)] = "Good Friday"
        out[_nth_weekday(year, 5, 0, -1)] = "Memorial Day"
        out[_observed(date(year, 6, 19))] = "Juneteenth"
        out[_observed(date(year, 7, 4))] = "Independence Day"
        out[_nth_weekday(year, 9, 0, 1)] = "Labor Day"
        out[_nth_weekday(year, 11, 3, 4)] = "Thanksgiving Day"
        out[_observed(date(year, 12, 25))] = "Christmas Day"
    elif calendar_id == "XTSE":
        # The TSX does not observe the US Thanksgiving, and Canada Day in July rather
        # than the US Independence Day. Verified against the data on 2026-11-26:
        # all 60 TSX securities traded that day while all 103 US securities were shut.
        out[_observed(date(year, 1, 1))] = "New Year's Day"
        out[_easter(year) - timedelta(days=2)] = "Good Friday"
        out[_last_monday_before(year, 5, 25)] = "Victoria Day"
        out[_observed(date(year, 7, 1))] = "Canada Day"
        out[_nth_weekday(year, 8, 0, 1)] = "Civic Holiday"
        out[_nth_weekday(year, 9, 0, 1)] = "Labour Day"
        out[_nth_weekday(year, 10, 0, 2)] = "Thanksgiving Day"
        out[_observed(date(year, 12, 25))] = "Christmas Day"
        out[_observed(date(year, 12, 26))] = "Boxing Day"
    return out


_HOLIDAY_CACHE: Dict[str, Dict[date, str]] = {}


def modelled_holiday(calendar_id: str, d: date) -> Optional[str]:
    """
    The name of the holiday ``d`` falls on for this calendar, or None.

    None means only "no rule explains this date" -- never "this was a trading day".
    """
    if calendar_id not in SESSIONS:
        return None
    key = f"{calendar_id}:{d.year}"
    if key not in _HOLIDAY_CACHE:
        _HOLIDAY_CACHE[key] = _holiday_rules(calendar_id, d.year)
    return _HOLIDAY_CACHE[key].get(d)


def modelled_holidays(calendar_id: str, start: date, end: date) -> Dict[str, str]:
    """Holiday name for every modelled holiday in an inclusive date range."""
    out = {}
    d = start
    while d <= end:
        name = modelled_holiday(calendar_id, d)
        if name:
            out[d.isoformat()] = name
        d += timedelta(days=1)
    return out


def _local_now(calendar_id: str, now_utc: datetime) -> datetime:
    return now_utc.astimezone(ZoneInfo(SESSIONS[calendar_id]["tz"]))


def market_state(exchange: str, now_utc: Optional[datetime] = None) -> Dict:
    """
    Where the given exchange sits relative to its regular session, right now.

    Returns a dict rather than a bare string because the honest answer carries its own
    caveat. Weekday holidays derived from a published rule are reported as
    ``CLOSED_HOLIDAY``; anything no rule explains still reads from the clock alone, so
    ``OPEN`` means "inside session hours on a weekday no holiday rule covers", not "the
    exchange is definitely trading today" -- an ad-hoc closure would still read OPEN.
    """
    cal = _calendar_for(exchange)
    if cal is None:
        return {
            "calendar": None,
            "state": UNKNOWN_CALENDAR,
            "holiday_modelled": False,
            "note": f"No session defined for exchange '{exchange}'.",
        }

    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    local = _local_now(cal, now_utc)
    sess = SESSIONS[cal]
    weekday = local.weekday()  # Mon=0 .. Sun=6

    holiday_name = modelled_holiday(cal, local.date())

    if weekday >= 5:
        state = CLOSED_WEEKEND
    elif holiday_name:
        state = CLOSED_HOLIDAY
    elif local.time() < sess["open"]:
        state = PRE_MARKET
    elif local.time() < sess["close"]:
        state = OPEN
    else:
        state = AFTER_HOURS

    return {
        "calendar": cal,
        "state": state,
        "holiday": holiday_name,
        "local_time": local.strftime("%Y-%m-%d %H:%M %Z"),
        "session_local": sess["label"],
        "holiday_modelled": True,
        "note": (
            "Weekday holidays derived from each exchange's published rule are reported "
            "as CLOSED_HOLIDAY. Ad-hoc closures are not derivable from any rule, so a "
            "weekday no rule covers still reads from the clock and would show OPEN even "
            "if the exchange were shut."
        ),
    }


def observed_trading_dates(exchange_group: Iterable[str]) -> Set[date]:
    """
    Distinct trading dates actually present in bar_1d for the given stored exchanges.

    This is the honest substitute for a holiday calendar: if every symbol in a market
    has no bar for a weekday, that weekday was not a trading day.
    """
    from src.data.db import db

    exch = [str(e).strip().upper() for e in exchange_group]
    if not exch:
        return set()
    placeholders = ",".join("?" * len(exch))
    rows = db.execute_query(
        f"""
        SELECT DISTINCT b.trading_date
        FROM bar_1d b
        JOIN security s USING (security_id)
        WHERE UPPER(s.exchange) IN ({placeholders})
        """,
        tuple(exch),
    )
    out: Set[date] = set()
    for r in rows:
        raw = r["trading_date"] if isinstance(r, dict) else r[0]
        try:
            out.add(date.fromisoformat(str(raw)[:10]))
        except (TypeError, ValueError):
            continue
    return out


def sessions_completed_since(
    calendar_id: str,
    last_bar_date: Optional[date],
    now_utc: Optional[datetime] = None,
    observed: Optional[Set[date]] = None,
) -> Dict:
    """
    How many regular sessions have *finished* since the newest bar we hold.

    Zero means the market has simply been shut -- the data is as current as it can
    possibly be, and calling that stale is wrong. One or more means a session closed and
    we did not capture it, which is a real gap.

    A weekday in the past is counted as a completed session unless the observed set says
    otherwise. Today is only counted once its own session has ended.
    """
    if calendar_id not in SESSIONS:
        return {"completed": None, "reason": f"No session defined for '{calendar_id}'."}

    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    local_now = _local_now(calendar_id, now_utc)
    today = local_now.date()

    if last_bar_date is None:
        return {"completed": None, "reason": "No bars ingested, so nothing to compare against."}

    if isinstance(last_bar_date, str):
        last_bar_date = date.fromisoformat(last_bar_date[:10])

    observed = observed if observed is not None else set()
    session_over_today = local_now.time() >= SESSIONS[calendar_id]["close"]
    last_observed = max(observed) if observed else None

    completed: List[date] = []
    unverified: List[date] = []
    holidays: List[Dict[str, str]] = []
    d = last_bar_date + timedelta(days=1)
    while d <= today:
        is_due = (d < today) or (d == today and session_over_today)
        if d.weekday() < 5 and is_due:
            # Order matters. A date the published rule says was a holiday is excused
            # first, because the exchange states it was shut and no bar can exist.
            # The converse is never inferred: a date no rule explains is still a gap.
            holiday = modelled_holiday(calendar_id, d)
            if holiday:
                holidays.append({"date": d.isoformat(), "holiday": holiday})
            elif last_observed is not None and d > last_observed:
                # Past the end of everything we have ever ingested, and no holiday rule
                # explains it. The observed set cannot say whether this was an ad-hoc
                # closure -- 11 September 2001, Hurricane Sandy -- or a failure we never
                # noticed, and guessing either way is how a real gap gets excused.
                unverified.append(d)
            elif not observed or d in observed:
                completed.append(d)
            # else: a weekday inside the observed span with no bar anywhere in the
            # market -- that is a holiday, established by the data.
        d += timedelta(days=1)

    return {
        "completed": len(completed),
        "completed_dates": [c.isoformat() for c in completed],
        "unverified": len(unverified),
        "unverified_dates": [c.isoformat() for c in unverified],
        "holidays": len(holidays),
        "holiday_dates": holidays,
        "holiday_modelled": True,
        "last_observed_trading_date": last_observed.isoformat() if last_observed else None,
        "today": today.isoformat(),
        "local_now": local_now.strftime("%Y-%m-%d %H:%M %Z"),
    }


def closed_market_reason(
    exchange: str,
    last_bar_date: Optional[date],
    now_utc: Optional[datetime] = None,
    observed: Optional[Set[date]] = None,
) -> Dict:
    """
    The single call the staleness badge should make.

    Returns ``market_closed: True`` with a human-readable reason when the exchange has
    had no completed session since the newest bar -- in which case the data is current
    and must not be badged stale. Returns ``market_closed: False`` when at least one
    session closed without being captured.
    """
    cal = _calendar_for(exchange)
    st = market_state(exchange, now_utc)
    if cal is None:
        return {
            "market_closed": False,
            "badge": "UNKNOWN",
            "reason": st["note"],
            "market_state": st,
        }

    count = sessions_completed_since(cal, last_bar_date, now_utc, observed)
    n = count.get("completed")
    u = count.get("unverified", 0)

    if n is None:
        return {"market_closed": False, "badge": "UNKNOWN", "reason": count["reason"],
                "market_state": st, "sessions": count}

    if n >= 1:
        dates = ", ".join(count["completed_dates"][:5])
        return {
            "market_closed": False,
            "badge": "STALE",
            "reason": (
                f"{n} session(s) closed without a captured bar: {dates}. "
                f"This is a real ingestion gap, not a closed market."
            ),
            "market_state": st,
            "sessions": count,
        }

    if u >= 1:
        dates = ", ".join(count["unverified_dates"][:5])
        return {
            "market_closed": False,
            "badge": "UNVERIFIED_GAP",
            "reason": (
                f"{u} weekday(s) have passed since the newest bar and lie beyond the end "
                f"of the ingested history ({dates}). None of them is a holiday under the "
                f"exchange's published holiday rule, so either the exchange closed ad hoc "
                f"-- which no rule can predict -- or a close was missed. Which cannot be "
                f"established from the data held, so this is reported as unverified "
                f"rather than excused as a closed market."
            ),
            "market_state": st,
            "sessions": count,
        }

    if st["state"] == CLOSED_WEEKEND:
        reason = (
            f"No session since the {last_bar_date} close -- the market is shut for the "
            f"weekend. This is the most recent close that exists."
        )
    else:
        reason = (
            f"No completed session since the {last_bar_date} close as of "
            f"{count['local_now']}. Data is current; the next close has not happened yet."
        )
    return {"market_closed": True, "badge": "CLOSED_MARKET", "reason": reason,
            "market_state": st, "sessions": count}
