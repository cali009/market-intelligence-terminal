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

    for feed in health["feeds"]:
        assert feed["status"] in ("HEALTHY", "DEGRADED", "STALE", "DOWN")
        assert feed["staleness_badge"] in ("FRESH", "WARNING", "STALE")
        assert feed["coverage_pct"] >= 90.0

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
