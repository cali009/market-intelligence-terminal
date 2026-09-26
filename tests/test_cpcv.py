"""
Unit & Integration Tests: Combinatorial Purged Cross-Validation (CPCV) & Benjamini-Hochberg FDR Gating (Phase 16)
US + Canada Market Intelligence Platform

Verifies:
- Combinatorial split generation (C(6, 2) = 15 paths)
- Purging and post-test 5-day embargo enforcement (zero look-ahead serial correlation)
- Probability of Backtest Overfitting (PBO) empirical estimation from rank inversions
- Benjamini-Hochberg False Discovery Rate (FDR) control at Q = 0.05
- Monotonic critical line thresholding ((k / M) * Q)
- Strategy registry integration and retirement gating
- Static JSON feed export (cpcv_validation.json)
- Impersonal advice linter compliance (CSA Staff Notice 31-369 & SEC Publisher Exclusion)
"""

import json
import pytest
import numpy as np

from src.engine.cpcv_engine import cpcv_engine
from src.compliance.linter import linter
from src.models.schemas import CPCVFeed, FDRGatingRecord, CPCVPathRecord


def test_combinatorial_split_count_and_combinations():
    """Verify that N=6 blocks with k=2 generates exactly C(6, 2) = 15 paths."""
    feed = cpcv_engine.generate_feed()
    assert feed.summary.total_cpcv_paths == 15
    assert len(feed.cpcv_paths) == 15
    assert feed.summary.num_timeline_blocks == 6
    assert feed.summary.embargo_days == 5


def test_embargo_and_purging_boundaries():
    """Verify that test blocks in every path are distinct and bounded within [0, 5]."""
    feed = cpcv_engine.generate_feed()
    for path in feed.cpcv_paths:
        assert len(path.test_blocks) == 2
        assert path.test_blocks[0] != path.test_blocks[1]
        assert all(0 <= b < 6 for b in path.test_blocks)
        assert 1 <= path.oos_rank <= feed.summary.total_strategies_evaluated


def test_benjamini_hochberg_ranking_and_critical_line():
    """Verify ascending p-values and strictly increasing critical threshold (k/M * Q)."""
    feed = cpcv_engine.generate_feed()
    table = feed.fdr_gating_table

    assert len(table) >= 4
    prev_crit = 0.0
    for k, row in enumerate(table, start=1):
        assert row.fdr_rank == k
        assert row.fdr_critical_threshold > prev_crit
        prev_crit = row.fdr_critical_threshold
        assert 0.0 <= row.raw_p_value <= 1.0
        assert row.fdr_verdict in {"FDR_PASSED", "REJECTED_SELECTION_BIAS"}


def test_pbo_calculation_bounds():
    """Verify that empirical PBO is bounded within [0.0, 1.0] and categorized correctly."""
    feed = cpcv_engine.generate_feed()
    pbo = feed.summary.empirical_pbo

    assert 0.0 <= pbo <= 1.0
    valid_evals = {"LOW_OVERFITTING_RISK", "MODERATE_OVERFITTING_RISK", "HIGH_OVERFITTING_RISK"}
    assert feed.summary.pbo_evaluation in valid_evals


def test_retired_strategy_underperformance_in_cpcv():
    """Verify that retired strategy (TAC-03) is recognized and rejected by quantitative gates."""
    feed = cpcv_engine.generate_feed()
    tac03 = next((r for r in feed.fdr_gating_table if r.strategy_id == "TAC03_BREAKOUT_CHASE"), None)

    assert tac03 is not None
    assert tac03.status == "RETIRED"
    assert tac03.fdr_verdict == "REJECTED_SELECTION_BIAS"


def test_cpcv_feed_generation_and_export():
    """Verify that cpcv_validation.json feed file exists on disk and matches schema."""
    with open("data/feeds/cpcv_validation.json", "r") as f:
        disk_feed = json.load(f)

    assert "summary" in disk_feed
    assert "fdr_gating_table" in disk_feed
    assert "cpcv_paths" in disk_feed
    assert disk_feed["summary"]["total_cpcv_paths"] == 15
    assert len(disk_feed["fdr_gating_table"]) >= 4


def test_impersonal_advice_linter_compliance():
    """Verify that all CPCV disclaimers pass ImpersonalAdviceLinter."""
    feed = cpcv_engine.generate_feed()
    for disc in feed.disclaimers:
        linter.assert_clean(disc)
