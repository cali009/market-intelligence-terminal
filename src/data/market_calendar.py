"""
Session and closed-market determination for the exchanges this platform covers.

Deliberately does NOT model exchange holidays.

A holiday list would be typed-in data: it goes silently wrong the year nobody updates it,
and a wrong holiday is indistinguishable from a missing close. This project's rule is that
a figure is either computed from real data or absent with a reason, so instead:

  * Weekend closure is arithmetic and needs no list.
  * Session times below are the published, stable regular-session hours.
  * Whether a *past* weekday was actually a trading day is answered by ``bar_1d`` itself --
    the observed set of trading dates, not an assumption.
  * For *today*, if the session has not ended yet there is nothing to observe, so the state
    is reported from the clock. If the session has ended and no bar exists, that is a
    genuine gap and is reported as such rather than excused as a holiday.

The one thing this module will not do is guess that an unobserved weekday was a holiday.
Callers that need that must supply it explicitly and label the source.
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
PRE_MARKET = "PRE_MARKET"
OPEN = "OPEN"
AFTER_HOURS = "AFTER_HOURS"
UNKNOWN_CALENDAR = "UNKNOWN_CALENDAR"


def _calendar_for(exchange: str) -> Optional[str]:
    return EXCHANGE_TO_CALENDAR.get(str(exchange).strip().upper())


def _local_now(calendar_id: str, now_utc: datetime) -> datetime:
    return now_utc.astimezone(ZoneInfo(SESSIONS[calendar_id]["tz"]))


def market_state(exchange: str, now_utc: Optional[datetime] = None) -> Dict:
    """
    Where the given exchange sits relative to its regular session, right now.

    Returns a dict rather than a bare string because the honest answer carries its own
    caveat: this does not know about holidays, so ``OPEN`` means "inside session hours on
    a weekday", not "the exchange is definitely trading today".
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

    if weekday >= 5:
        state = CLOSED_WEEKEND
    elif local.time() < sess["open"]:
        state = PRE_MARKET
    elif local.time() < sess["close"]:
        state = OPEN
    else:
        state = AFTER_HOURS

    return {
        "calendar": cal,
        "state": state,
        "local_time": local.strftime("%Y-%m-%d %H:%M %Z"),
        "session_local": sess["label"],
        "holiday_modelled": False,
        "note": (
            "Session hours only. Weekday exchange holidays are NOT modelled: a holiday "
            "falling on a weekday will read as PRE_MARKET/OPEN/AFTER_HOURS here. Closed "
            "days are established from observed bars, not from this function."
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
    d = last_bar_date + timedelta(days=1)
    while d <= today:
        is_due = (d < today) or (d == today and session_over_today)
        if d.weekday() < 5 and is_due:
            if last_observed is not None and d > last_observed:
                # Past the end of everything we have ever ingested. The observed set
                # cannot say whether this was a holiday or a failure we never noticed,
                # and guessing either way is how a real gap gets excused as a holiday.
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
                f"of the ingested history ({dates}). Whether they were trading days cannot "
                f"be established from the data held, so this is reported as unverified "
                f"rather than excused as a closed market. Exchange holiday calendars are "
                f"not modelled."
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
