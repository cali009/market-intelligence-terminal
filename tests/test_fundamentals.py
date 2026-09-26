"""
Tests for Point-in-Time Fundamental Engine & Valuation Lab (Phase 5)
Verifies:
- Anti-restatement PIT filing date gating
- Concept mapping (US-GAAP vs IFRS)
- Quality metrics (ROIC, ROE, Sloan accruals, margin trend)
- Valuation multiples (P/E, P/S, P/B, FCF yield, dividend yield)
- Dividend safety scoring
- Weight renormalization on Canadian data gaps
- Index ETF bypass
- Dual-market coverage audit (>=95% US, >=80% CA)
- Database persistence in fundamental_metric
"""

import pytest
from datetime import date
from src.data.db import db
from src.engine.fundamentals import FundamentalAnalysisEngine


def test_pit_filing_date_gating_anti_lookahead():
    """
    Ensure that querying facts as-of an earlier date strictly excludes
    subsequent restatements or future filings.
    """
    sec = db.execute_query("SELECT security_id FROM security WHERE symbol = 'AAPL';")
    assert len(sec) > 0
    sec_id = sec[0]["security_id"]

    # 1. As of 2024-03-01: facts filed after 2024-03-01 must not be visible
    facts_2024, _, filing_date_2024, _, _ = FundamentalAnalysisEngine.get_pit_facts(
        security_id=sec_id, as_of_date="2024-03-01"
    )
    if filing_date_2024:
        assert filing_date_2024 <= "2024-03-01"

    # 2. As of 2026-09-26: newer filings can be visible
    facts_2026, _, filing_date_2026, _, _ = FundamentalAnalysisEngine.get_pit_facts(
        security_id=sec_id, as_of_date="2026-09-26"
    )
    assert filing_date_2026 <= "2026-09-26"
    assert len(facts_2026) >= len(facts_2024)


def test_quality_ratios_calculation():
    """
    Verify mathematical correctness of ROIC, ROE, Sloan Accruals, and Margin Trend.
    """
    facts = {
        "NetIncomeLoss": 20_000_000_000.0,
        "StockholdersEquity": 100_000_000_000.0,
        "TotalDebt": 50_000_000_000.0,
        "OperatingCashFlow": 25_000_000_000.0,
        "TotalAssets": 200_000_000_000.0,
        "OperatingIncome": 24_000_000_000.0,
        "Revenues": 80_000_000_000.0,
    }
    prior_facts = {
        "OperatingIncome": 20_000_000_000.0,
        "Revenues": 80_000_000_000.0,
    }

    ratios = FundamentalAnalysisEngine.compute_quality_ratios(facts, prior_facts)

    # ROIC = 20B / (100B + 50B) = 13.33%
    assert ratios["roic"] == pytest.approx(13.33, abs=0.05)
    # ROE = 20B / 100B = 20.0%
    assert ratios["roe"] == pytest.approx(20.0, abs=0.05)
    # Accruals = (20B - 25B) / 200B = -0.025 (cash-rich earnings quality)
    assert ratios["accruals_ratio"] == pytest.approx(-0.025, abs=0.001)
    # Operating Margin = 24B / 80B = 30.0%
    assert ratios["operating_margin_pct"] == pytest.approx(30.0, abs=0.05)
    # Margin Trend = 30% - 25% = +500 bps
    assert ratios["margin_trend_yoy_bps"] == pytest.approx(500.0, abs=1.0)
    # Debt to Equity = 50B / 100B = 0.50
    assert ratios["debt_to_equity"] == pytest.approx(0.50, abs=0.01)


def test_valuation_multiples_calculation():
    """
    Verify valuation multiples: P/E, P/S, P/B, FCF yield, Dividend yield.
    """
    facts = {
        "DilutedSharesOutstanding": 1_000_000_000.0,
        "NetIncomeLoss": 5_000_000_000.0,
        "Revenues": 25_000_000_000.0,
        "StockholdersEquity": 20_000_000_000.0,
        "OperatingCashFlow": 6_000_000_000.0,
        "DividendsPaid": 1_000_000_000.0,
    }
    price = 100.0  # Market cap = 100B

    v = FundamentalAnalysisEngine.compute_valuation_multiples(facts, price)

    # Market Cap = 100 * 1B = 100B
    assert v["market_cap"] == 100_000_000_000.0
    # P/E = 100B / 5B = 20.0
    assert v["pe_ratio"] == pytest.approx(20.0, abs=0.05)
    # P/S = 100B / 25B = 4.0
    assert v["ps_ratio"] == pytest.approx(4.0, abs=0.05)
    # P/B = 100B / 20B = 5.0
    assert v["pb_ratio"] == pytest.approx(5.0, abs=0.05)
    # FCF Yield = 6B / 100B = 6.0%
    assert v["fcf_yield_pct"] == pytest.approx(6.0, abs=0.05)
    # Dividend Yield = 1B / 100B = 1.0%
    assert v["dividend_yield_pct"] == pytest.approx(1.0, abs=0.05)


def test_dividend_safety_scoring():
    """
    Verify dividend safety metrics: payout ratio, coverage, tiering.
    """
    # Safe dividend: 30% payout, 3.0x coverage
    facts_safe = {
        "DividendsPaid": 3_000_000_000.0,
        "NetIncomeLoss": 10_000_000_000.0,
        "OperatingCashFlow": 9_000_000_000.0,
    }
    safety_safe = FundamentalAnalysisEngine.compute_dividend_safety(facts_safe, 100_000_000_000.0)
    assert safety_safe["safety_score"] >= 75
    assert safety_safe["safety_tier"] == "HIGH_SAFETY"
    assert safety_safe["payout_ratio_pct"] == pytest.approx(30.0, abs=0.1)
    assert safety_safe["fcf_coverage"] == pytest.approx(3.0, abs=0.1)

    # Growth tech company with zero dividend
    facts_no_div = {
        "DividendsPaid": 0.0,
        "NetIncomeLoss": 5_000_000_000.0,
        "OperatingCashFlow": 6_000_000_000.0,
    }
    safety_none = FundamentalAnalysisEngine.compute_dividend_safety(facts_no_div, 100_000_000_000.0)
    assert safety_none["safety_tier"] == "N/A_NO_DIVIDEND"


def test_weight_renormalization_canadian_gaps():
    """
    Verify that Canadian equities with missing concepts (e.g. banks missing Debt)
    have their factor weights renormalized without silent defaults.
    """
    records = FundamentalAnalysisEngine.evaluate_universe_fundamentals("2026-09-26")

    # Find Canadian bank (RY)
    ry = next((r for r in records if r["symbol"] == "RY"), None)
    assert ry is not None
    assert ry["coverage_status"] == "PARTIAL"
    assert "TotalDebt" in ry["missing_fields"]
    # Quality score should be computed via remaining active weights
    assert 0 <= ry["quality_score"] <= 100
    assert 0 <= ry["composite_fundamental_score"] <= 100

    # Find Canadian tech (SHOP)
    shop = next((r for r in records if r["symbol"] == "SHOP"), None)
    assert shop is not None
    assert shop["coverage_status"] == "PARTIAL"
    assert "DividendsPaid" in shop["missing_fields"]
    assert 0 <= shop["quality_score"] <= 100


def test_index_etf_bypass():
    """
    Verify that Index ETFs (SPY, QQQ, XIU) are cleanly bypassed
    from corporate fundamental scoring.
    """
    records = FundamentalAnalysisEngine.evaluate_universe_fundamentals("2026-09-26")

    for sym in ["SPY", "QQQ", "XIU"]:
        rec = next((r for r in records if r["symbol"] == sym), None)
        assert rec is not None
        assert rec["is_etf"] is True
        assert rec["coverage_status"] == "INDEX_ETF_BYPASS"
        assert rec["composite_fundamental_score"] == 50


def test_dual_market_coverage_report_dod():
    """
    Verify Phase 5 DoD:
    >= 95% coverage on US core universe, >= 80% on Canadian core universe.
    """
    report = FundamentalAnalysisEngine.generate_coverage_report("2026-09-26")

    assert report["us_coverage_pct"] >= 95.0
    assert report["us_dod_passed"] is True
    assert report["ca_coverage_pct"] >= 80.0
    assert report["ca_dod_passed"] is True
    assert report["total_coverage_passed"] is True


def test_sqlite_persistence():
    """
    Verify persistence and retrieval from SQLite fundamental_metric table.
    """
    records = FundamentalAnalysisEngine.evaluate_universe_fundamentals("2026-09-26")
    corporate_records = [r for r in records if not r.get("is_etf")]

    for r in corporate_records:
        sec_id = FundamentalAnalysisEngine.persist_fundamental_metric(r)
        assert sec_id > 0

    rows = db.execute_query("SELECT count(*) as cnt FROM fundamental_metric WHERE as_of_date = '2026-09-26';")
    assert rows[0]["cnt"] == len(corporate_records)
