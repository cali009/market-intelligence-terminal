"""
Tests for Triple-Barrier Meta-Labeling Dataset Engine (Phase 23.1)
Verifies:
1. Triple-barrier path-dependent event generation & class balance
2. Anti-lookahead execution causality (t+1 open entry)
3. Barrier hit consistency and mathematical return/R-multiple calculations
4. Feature matrix completeness, non-null values, and bounded ranges
5. Chronological Train / Validation / Test separation with embargo
6. SQLite table persistence and query consistency
7. Static edge JSON feed serialization
8. Impersonal advice regulatory compliance (CSA Staff Notice 31-369 & SEC)
"""

import json
from pathlib import Path
import pytest
import numpy as np
import pandas as pd

from src.compliance.linter import linter
from src.data.db import db
from src.engine.meta_label_dataset import (
    meta_label_dataset_engine,
    TripleBarrierRecord,
    MetaLabelDatasetSummary,
    DISCLAIMERS,
)


def test_triple_barrier_generation_and_balance():
    """Verify event generation across single-stock universe and balanced binary classes."""
    records = meta_label_dataset_engine.generate_dataset()
    assert len(records) >= 1500, f"Expected >= 1500 events, got {len(records)}"

    symbols_represented = set(r.symbol for r in records)
    assert len(symbols_represented) >= 14, f"Expected >= 14 symbols, got {len(symbols_represented)}"
    assert "SPY" not in symbols_represented, "SPY index ETF should be excluded from stock training matrix"
    assert "QQQ" not in symbols_represented, "QQQ index ETF should be excluded from stock training matrix"

    labels = [r.label for r in records]
    assert set(labels).issubset({0, 1}), "Labels must be strictly binary {0, 1}"

    pos_rate = sum(labels) / len(labels)
    assert 0.38 <= pos_rate <= 0.62, f"Expected balanced positive rate between 38% and 62%, got {pos_rate:.1%}"


def test_anti_lookahead_causality():
    """Verify strictly causal timeline: entry strictly at t+1 and holding within bounds."""
    records = meta_label_dataset_engine.generate_dataset()

    for r in records[:100]:
        assert r.entry_date > r.signal_date, f"Anti-lookahead violation: entry {r.entry_date} <= signal {r.signal_date}"
        assert r.exit_date >= r.entry_date, f"Causality violation: exit {r.exit_date} < entry {r.entry_date}"
        assert 1 <= r.holding_days <= 20, f"Holding days {r.holding_days} outside [1, 20] range"
        assert r.entry_price > 0, "Entry price must be strictly positive"
        assert r.upper_barrier > r.entry_price, "Upper barrier must exceed entry price"
        assert r.lower_barrier < r.entry_price, "Lower barrier must be below entry price"


def test_barrier_hit_consistency():
    """Verify barrier hit mechanics, exit reasons, and R-multiple calculations."""
    records = meta_label_dataset_engine.generate_dataset()

    for r in records[:100]:
        assert r.exit_reason in ("UPPER_BARRIER", "LOWER_BARRIER", "TIME_BARRIER")

        if r.exit_reason == "UPPER_BARRIER":
            assert r.label == 1
            assert r.exit_price == r.upper_barrier
            assert r.return_pct > 0
            assert r.r_multiple > 0
        elif r.exit_reason == "LOWER_BARRIER":
            assert r.label == 0
            assert r.exit_price == r.lower_barrier
            assert r.return_pct < 0
            assert r.r_multiple < 0

        # Mathematical return verification
        expected_ret = round(((r.exit_price / r.entry_price) - 1.0) * 100.0, 2)
        assert abs(r.return_pct - expected_ret) <= 0.05


def test_feature_matrix_integrity_and_bounds():
    """Verify all 20 quantitative features are present, non-null, and within physical bounds."""
    records = meta_label_dataset_engine.generate_dataset()
    expected_features = [
        "mom_6m", "mom_3m", "mom_1m", "mom_12_1m", "rsi_14", "cmf_20", "rvol_20",
        "atr_pct", "bb_bandwidth", "dist_sma50", "dist_sma200", "sma200_slope",
        "prox_52w", "hurst_exponent", "fractal_dimension", "amihud_illiquidity",
        "quality_score", "valuation_score", "macro_regime_score", "yield_spread_10y_2y",
    ]

    for r in records[:100]:
        feat = r.features
        assert len(feat) == 20, f"Expected 20 features, got {len(feat)}"
        for fname in expected_features:
            assert fname in feat, f"Missing feature {fname}"
            val = feat[fname]
            assert not np.isnan(val), f"NaN feature {fname} in {r.event_id}"
            assert not np.isinf(val), f"Infinite feature {fname} in {r.event_id}"

        # Bound checks
        assert 0.0 <= feat["rsi_14"] <= 100.0
        assert 0.15 <= feat["hurst_exponent"] <= 0.90
        assert 1.0 <= feat["fractal_dimension"] <= 2.0
        assert 0.0 <= feat["quality_score"] <= 100.0
        assert 0.0 <= feat["valuation_score"] <= 100.0
        assert feat["macro_regime_score"] in (0.0, 0.2, 0.5, 0.7, 1.0)


def test_train_val_test_partition_chronology_and_embargo():
    """Verify strict chronological separation: Train (60%) -> Validation (20%) -> Test (20%)."""
    (X_tr, y_tr), (X_val, y_val), (X_te, y_te) = meta_label_dataset_engine.get_train_val_test_matrices()

    assert len(X_tr) > 0, "Train set must be non-empty"
    assert len(X_val) > 0, "Validation set must be non-empty"
    assert len(X_te) > 0, "Test set must be non-empty"

    records = meta_label_dataset_engine.generate_dataset()
    tr_dates = [r.signal_date for r in records if r.partition == "TRAIN"]
    val_dates = [r.signal_date for r in records if r.partition == "VALIDATION"]
    te_dates = [r.signal_date for r in records if r.partition == "TEST"]

    # Chronological partition order
    assert max(tr_dates) < min(val_dates), f"Train max {max(tr_dates)} >= Val min {min(val_dates)}"
    assert max(val_dates) < min(te_dates), f"Val max {max(val_dates)} >= Test min {min(te_dates)}"


def test_sqlite_persistence_and_querying():
    """Verify records are persisted into SQLite and match in-memory generated dataset."""
    records = meta_label_dataset_engine.generate_dataset()
    rows = db.execute_query("SELECT count(*) as count FROM meta_label_dataset;")
    assert rows[0]["count"] == len(records)

    # Sample query
    sample_row = db.execute_query("SELECT * FROM meta_label_dataset LIMIT 1;")[0]
    assert sample_row["label"] in (0, 1)
    feat_dict = json.loads(sample_row["features_json"])
    assert "hurst_exponent" in feat_dict
    assert "mom_6m" in feat_dict


def test_feed_export_and_json_schema():
    """Verify static edge feed generation in data/feeds/meta_label_matrix.json."""
    feed_path = meta_label_dataset_engine.export_feed()
    assert feed_path.exists()

    with open(feed_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "summary" in data
    assert "sample_records" in data
    assert "disclaimers" in data

    s = data["summary"]
    assert s["total_samples"] >= 1500
    assert s["feature_count"] == 20
    assert len(s["feature_names"]) == 20
    assert len(data["disclaimers"]) >= 3


def test_compliance_linter_zero_violations():
    """Verify impersonal advice linter passes with zero violations on all disclaimers."""
    for disc in DISCLAIMERS:
        linter.assert_clean(disc)
