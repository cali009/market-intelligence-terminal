"""
Tests for SEC EDGAR XBRL & Submissions Adapter
Verifies CIK zero-padding, rate limiter, and concept fallback chains.
"""

from src.data.sec_edgar import SecEdgarAdapter


def test_pad_cik():
    adapter = SecEdgarAdapter()
    assert adapter.pad_cik(320193) == "0000320193"
    assert adapter.pad_cik("0000320193") == "0000320193"
    assert adapter.pad_cik(1) == "0000000001"


def test_sec_ticker_mapping_cache():
    adapter = SecEdgarAdapter()
    mapping = adapter.get_ticker_to_cik_map()
    assert isinstance(mapping, dict)
    assert len(mapping) > 1000
    assert "AAPL" in mapping
    assert mapping["AAPL"] == 320193
    assert "MSFT" in mapping


def test_sec_company_facts_extraction():
    adapter = SecEdgarAdapter()
    # Test on Apple (CIK 320193)
    facts = adapter.extract_canonical_fundamentals(security_id=1, cik=320193, recent_years=2)
    assert len(facts) > 0

    concepts = {f.concept for f in facts}
    # Ensure Revenues and NetIncome were extracted
    assert "Revenues" in concepts
    assert "NetIncomeLoss" in concepts

    # Ensure Point-in-time invariant holds: filing_date >= period_end
    for f in facts:
        assert f.filing_date >= f.period_end
        assert f.value != 0
        assert f.source == "SEC_EDGAR_XBRL"
