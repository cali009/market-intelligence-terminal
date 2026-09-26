"""
Unit & Integration Tests: AI Regulatory Filing Deep-Read & Model Cards (Phase 11)
US + Canada Market Intelligence Platform

Verifies:
- Structural section chunking (10-K, 10-Q, AIF, MD&A, Item 5.02)
- Strict character-offset citation span resolution (DoD target: >= 99.5%)
- Period-over-period comparative section diffs (added/removed risks, tone shifts)
- Forward-looking financial guidance extraction with consensus benchmarking
- Item 5.02 executive leadership transition detection
- Automated hallucination audit (DoD target: 0.00% hallucinated tokens)
- Published Institutional Model Cards detailing architecture, limitations & prompt hashes
- Impersonal Advice Linter compliance (CSA Staff Notice 31-369 & SEC Publisher Exclusion)
"""

import json
import pytest

from src.engine.filing_deep_read import filing_deep_read_engine
from src.compliance.linter import linter
from src.models.schemas import (
    CitationSpan,
    SectionDiffRecord,
    GuidanceTargetRecord,
    ManagementChangeRecord,
    ModelCard,
)


def test_citation_span_verification_dod():
    """DoD: Verify that citation span character offsets [start:end] match verbatim source text."""
    diffs = filing_deep_read_engine.extract_section_diffs()
    assert len(diffs) >= 3

    verified_count = 0
    total_citations = 0

    for d in diffs:
        cit = d.citation
        total_citations += 1
        assert cit.is_verified is True, f"Citation {cit.citation_id} failed source verification"

        # Verify character offset extraction manually
        if d.symbol == "NVDA":
            source = filing_deep_read_engine.corpus["NVDA_10Q_2026_Q2"]
            extracted = source[cit.start_char:cit.end_char]
            assert extracted == cit.verbatim_text
            verified_count += 1
        elif d.symbol == "AAPL":
            source = filing_deep_read_engine.corpus["AAPL_10Q_2026_Q3"]
            extracted = source[cit.start_char:cit.end_char]
            assert extracted == cit.verbatim_text
            verified_count += 1
        elif d.symbol == "TD":
            source = filing_deep_read_engine.corpus["TD_TSX_2026_Q3"]
            extracted = source[cit.start_char:cit.end_char]
            assert extracted == cit.verbatim_text
            verified_count += 1

    resolution_pct = (verified_count / max(1, total_citations)) * 100.0
    assert resolution_pct >= 99.5, f"Citation resolution was {resolution_pct}%, DoD requires >= 99.5%"


def test_section_diffs_generation_and_categories():
    """Verify period-over-period section diffs correctly categorize added/removed risks."""
    diffs = filing_deep_read_engine.extract_section_diffs()
    nvda_diff = next(d for d in diffs if d.symbol == "NVDA")

    assert nvda_diff.current_filing == "10-Q Q2 2026"
    assert nvda_diff.prior_filing == "10-Q Q1 2026"
    assert nvda_diff.significance_score >= 80

    # Added items must contain export controls
    assert len(nvda_diff.added_items) > 0
    assert any("export" in str(item).lower() for item in nvda_diff.added_items)

    # Removed items must contain wafer fab bottlenecks
    assert len(nvda_diff.removed_items) > 0
    assert any("wafer" in str(item).lower() for item in nvda_diff.removed_items)

    # Material shifts
    assert len(nvda_diff.material_shifts) > 0


def test_forward_guidance_extraction():
    """Verify structured forward-looking financial guidance extraction and consensus comparison."""
    targets = filing_deep_read_engine.extract_forward_guidance()
    assert len(targets) >= 3

    symbols = {g.symbol for g in targets}
    assert "NVDA" in symbols
    assert "AAPL" in symbols
    assert "SHOP" in symbols

    for g in targets:
        assert g.range_low > 0
        assert g.range_high >= g.range_low
        assert g.comparison_vs_consensus in ["ABOVE", "IN_LINE", "BELOW", "UNTRACKED"]
        assert len(g.verbatim_excerpt) > 10
        assert g.citation.is_verified is True


def test_management_changes_item_502():
    """Verify Item 5.02 executive leadership and director transition detection."""
    mgmt = filing_deep_read_engine.extract_management_changes()
    assert len(mgmt) >= 3

    for m in mgmt:
        assert m.event_type in ["APPOINTMENT", "DEPARTURE", "PROMOTION", "RETIREMENT"]
        assert len(m.executive_name) > 3
        assert len(m.title) > 3
        assert len(m.effective_date) == 10  # YYYY-MM-DD
        assert m.citation is not None


def test_hallucination_audit_rate_zero():
    """DoD: Verify that the automated hallucination audit yields 0.00% hallucinated facts."""
    audit = filing_deep_read_engine.audit_hallucination_rate()

    assert audit["total_audit_sample_size"] >= 200
    assert audit["hallucinated_facts_count"] == 0
    assert audit["hallucination_rate_pct"] == 0.00
    assert audit["citation_resolution_pct"] >= 99.5
    assert "PASSED" in audit["numeric_verification_status"]
    assert "PASSED" in audit["causal_claims_verification"]


def test_published_model_cards_specifications():
    """DoD: Verify that institutional model cards are published and include pinned prompt versions."""
    cards = filing_deep_read_engine.get_published_model_cards()
    assert len(cards) >= 3

    card_ids = {c.model_id for c in cards}
    assert "MOD-NLP-DIFF-01" in card_ids
    assert "MOD-NLP-SENT-02" in card_ids
    assert "MOD-AGENT-RESEARCH-03" in card_ids

    for c in cards:
        assert c.context_window >= 4096
        assert c.citation_resolution_pct >= 99.5
        assert c.hallucination_rate_pct == 0.00
        assert c.cost_per_query_usd < 0.05
        assert len(c.intended_use) > 10
        assert len(c.out_of_scope) > 10
        assert len(c.bias_considerations) > 10
        assert len(c.prompt_versions) >= 1
        # Pinned prompt versions must contain sha256 hash or version tag
        for p_name, p_ver in c.prompt_versions.items():
            assert "sha256" in p_ver or "v" in p_ver


def test_impersonal_advice_linter_compliance():
    """Verify that all summaries, descriptions, and disclaimers pass ImpersonalAdviceLinter."""
    feed = filing_deep_read_engine.generate_ai_deep_read_feed()

    for d in feed["section_diffs"]:
        linter.assert_clean(d["summary"])
        for item in d.get("added_items", []):
            linter.assert_clean(item.get("text", ""))

    for c in feed["model_cards"]:
        linter.assert_clean(c["intended_use"])
        linter.assert_clean(c["out_of_scope"])

    for disc in feed["disclaimers"]:
        linter.assert_clean(disc)


def test_feed_file_export_and_schema():
    """Verify exported ai_deep_read.json feed exists on disk and matches schema."""
    with open("data/feeds/ai_deep_read.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "summary" in data
    assert data["summary"]["section_diffs_count"] >= 3
    assert data["summary"]["citation_resolution_pct"] >= 99.5
    assert data["summary"]["hallucination_rate_pct"] == 0.00
    assert len(data["section_diffs"]) >= 3
    assert len(data["guidance_targets"]) >= 3
    assert len(data["model_cards"]) >= 3
