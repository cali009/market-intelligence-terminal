"""
Unit and Integration Tests for Phase 1 Research Journal, Watchlists & Feed Health Engine
US + Canada Quantitative Market Intelligence Platform
"""

import pytest
from src.engine.journal import journal_engine
from src.data.db import db


def test_journal_summary_calculation():
    summary_data = journal_engine.get_journal_summary()
    assert "summary" in summary_data
    assert "positions" in summary_data

    s = summary_data["summary"]
    assert s["total_positions_tracked"] >= 3
    assert s["open_positions_count"] >= 1
    assert s["total_open_value_usd"] > 0
    assert "cad_usd_exchange_rate" in s
    assert s["cad_usd_exchange_rate"] > 0

    for pos in summary_data["positions"]:
        assert "symbol" in pos
        assert "market" in pos
        assert pos["market"] in ("US", "CA")
        assert pos["currency"] in ("USD", "CAD")
        assert pos["status"] in ("OPEN", "CLOSED", "WATCHLIST")
        assert pos["shares"] > 0
        assert pos["entry_price"] > 0
        if pos["status"] == "OPEN":
            assert pos["market_value_local"] > 0
            assert pos["unrealized_pnl_local"] is not None
            assert pos["unrealized_pnl_pct"] is not None
        elif pos["status"] == "CLOSED":
            assert pos["realized_pnl_local"] is not None


def test_watchlists_retrieval_and_enrichment():
    watchlists = journal_engine.get_watchlists_with_metrics()
    assert len(watchlists) >= 2
    for wl in watchlists:
        assert "name" in wl
        assert "total_symbols" in wl
        assert len(wl["items"]) > 0
        for item in wl["items"]:
            assert "symbol" in item
            assert "market" in item
            assert item["market"] in ("US", "CA")
            assert item["last_price"] is not None
            assert item["composite_score"] is not None


def test_feed_health_status():
    health = journal_engine.compile_feed_health_status()
    assert "feeds" in health
    assert len(health["feeds"]) >= 4
    feed_ids = [f["feed_id"] for f in health["feeds"]]
    assert "us_equities_eod" in feed_ids
    assert "ca_equities_eod" in feed_ids
    assert "sec_edgar_xbrl" in feed_ids
    assert "boc_valet_macro" in feed_ids

    # HONESTY OVERHAUL (Phase 2): these assertions used to pin coverage at
    # >= 90.0 for every feed and a WARNING badge that the code never emitted.
    # Coverage is now measured as bars / (symbols x trading days) for the two
    # equity feeds and is None for EDGAR and Valet, where no universe exists to
    # be complete against. The badge vocabulary is what staleness_badge emits.
    # CLOSED_MARKET and UNVERIFIED_GAP joined the vocabulary when the badge stopped
    # being a pure function of hours-since-last-bar. A closed market is not stale
    # data, and weekdays past the end of the ingested history are unverified rather
    # than assumed either way. Every badge must also carry a reason.
    for feed in health["feeds"]:
        assert feed["status"] in ("HEALTHY", "DEGRADED", "STALE", "DOWN")
        assert feed["staleness_badge"] in (
            "FRESH", "AGING", "STALE", "UNKNOWN", "CLOSED_MARKET", "UNVERIFIED_GAP")
        cov = feed["coverage_pct"]
        assert cov is None or 0.0 <= cov <= 100.0

    # The two equity feeds are the ones the market verdict applies to. They must
    # publish the verdict, declare whether the market is shut, and never claim a
    # closed market while badging themselves stale.
    by_feed = {f["feed_id"]: f for f in health["feeds"]}
    for feed_id in ("us_equities_eod", "ca_equities_eod"):
        f = by_feed[feed_id]
        assert "market_closed" in f
        assert "market_state" in f
        assert f["staleness_reason"], f"{feed_id} badged without a reason"
        assert not (f["staleness_badge"] == "STALE" and f["market_closed"])
        detail = f["market_state_detail"]
        # This used to assert holiday_modelled is False, with the message "the calendar
        # does not model exchange holidays and must say so". That was true when the
        # calendar deliberately modelled nothing, and it is now false: the calendar
        # derives each exchange's published holiday rule. The invariant that survives
        # the change is the honest one -- the feed must always declare whether holidays
        # are modelled rather than leaving a reader to assume, and modelling them must
        # not become a way to excuse an unexplained weekday. That second half is what
        # test_market_calendar.py::test_negative_control_an_unexplained_weekday_is_still_a_gap
        # pins down.
        assert "holiday_modelled" in detail, f"{feed_id} does not declare whether holidays are modelled"
        assert isinstance(detail["holiday_modelled"], bool)
        assert detail["holiday_modelled"] is True, (
            "both equity calendars derive their published holiday rule, so the feed "
            "must say holidays are modelled rather than understate its own data")

    # The equity feeds have a real denominator, so their coverage must be measured.
    by_id = {f["feed_id"]: f for f in health["feeds"]}
    for feed_id in ("us_equities_eod", "ca_equities_eod"):
        assert by_id[feed_id]["coverage_pct"] is not None
        assert by_id[feed_id]["coverage_pct"] >= 90.0

    # The universe is 19 currently listed symbols and every feed must say so.
    for feed_id in ("us_equities_eod", "ca_equities_eod"):
        assert by_id[feed_id]["survivorship_biased"] is True
    assert "survivorship" in health["universe_note"].lower()
    # system_health is derived from the per-feed badges, never hard-coded.
    assert health["system_health"] in ("OPTIMAL", "DEGRADED", "STALE", "DOWN")

    assert "data_quality_events" in health
    assert len(health["data_quality_events"]) >= 1


def test_impersonal_journal_compliance():
    # Verify that no positions or notes contain direct buy/sell advice
    prohibited = ["you should buy", "we recommend buying", "guaranteed profit", "target return guaranteed"]
    summary_data = journal_engine.get_journal_summary()
    for pos in summary_data["positions"]:
        thesis = (pos.get("thesis_notes") or "").lower()
        for p in prohibited:
            assert p not in thesis, f"Prohibited advice phrase found in thesis: {p}"
