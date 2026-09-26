"""
Unit & Integration Tests: Idiosyncratic Asset Fingerprint & Stationarity Engine (Phase 13)
US + Canada Market Intelligence Platform

Verifies:
- Lo's Modified Rescaled Range (R/S) Hurst Exponent with Bartlett HAC kernel
- Optimal Fractional Differentiation order (d*) & memory retention %
- Fractal Dimension Index (FDI) boundaries [1.05, 1.95]
- Amihud Illiquidity Ratio (daily price impact per $100k)
- 5 Institutional Microstructure Archetypes classification
- Edge feed export (asset_fingerprints.json) & symbol dossier injection
- Impersonal advice linter compliance (CSA Staff Notice 31-369 & SEC Publisher Exclusion)
"""

import json
import pytest
import numpy as np

from src.engine.asset_fingerprint import asset_fingerprint_engine
from src.compliance.linter import linter
from src.models.schemas import AssetFingerprint


def test_hurst_exponent_hac_calculation():
    """Verify Lo's Modified R/S with Bartlett HAC kernel on synthetic and real series."""
    # Synthetic trending series
    trending_prices = [100.0 * (1.02 ** i) for i in range(100)]
    h_trend, class_trend = asset_fingerprint_engine.calculate_hurst_exponent_hac(trending_prices)
    assert 0.30 <= h_trend <= 0.75
    assert h_trend >= 0.50

    # Synthetic mean-reverting series (sawtooth oscillation)
    mean_rev_prices = [100.0 + (5.0 if i % 2 == 0 else -5.0) for i in range(100)]
    h_rev, class_rev = asset_fingerprint_engine.calculate_hurst_exponent_hac(mean_rev_prices)
    assert 0.30 <= h_rev <= 0.75
    assert h_rev < 0.50
    assert class_rev == "MEAN_REVERTING"


def test_optimal_fractional_d_stationarity():
    """Verify minimum stationary order d* preserves >= 50% memory and varies across archetypes."""
    # Tech Gamma with high Hurst
    tech_frac = asset_fingerprint_engine.calculate_optimal_fractional_d(0.65, "ARCHETYPE_A_TECH_GAMMA")
    assert 0.25 <= tech_frac["fractional_d_order"] <= 0.75
    assert tech_frac["memory_retention_pct"] >= 50.0

    # Bank Regulated with low Hurst
    bank_frac = asset_fingerprint_engine.calculate_optimal_fractional_d(0.42, "ARCHETYPE_C_BANK_REGULATED")
    assert 0.25 <= bank_frac["fractional_d_order"] <= 0.75
    assert bank_frac["memory_retention_pct"] >= 50.0

    # Tech gamma retains more memory (lower d*) than bank regulated
    assert tech_frac["fractional_d_order"] < bank_frac["fractional_d_order"]
    assert tech_frac["memory_retention_pct"] > bank_frac["memory_retention_pct"]


def test_fractal_dimension_index_bounds():
    """Verify Fractal Dimension Index (FDI) remains bounded in [1.05, 1.95]."""
    prices = [100.0 + i for i in range(50)]
    highs = [p + 1.0 for p in prices]
    lows = [p - 1.0 for p in prices]

    fdi = asset_fingerprint_engine.calculate_fractal_dimension_index(prices, highs, lows)
    assert 1.05 <= fdi <= 1.95


def test_amihud_illiquidity_metric():
    """Verify Amihud illiquidity is strictly positive and non-NaN."""
    closes = [100.0 + (i * 0.2) for i in range(40)]
    volumes = [2000000.0] * 40

    amihud = asset_fingerprint_engine.calculate_amihud_illiquidity(closes, volumes)
    assert amihud > 0.0
    assert not np.isnan(amihud)


def test_microstructure_archetype_classification():
    """Verify accurate institutional archetype classification for US & Canadian securities."""
    aapl_arch, aapl_lbl = asset_fingerprint_engine.classify_microstructure_archetype("AAPL", "US")
    assert aapl_arch == "ARCHETYPE_A_TECH_GAMMA"
    assert "Tech" in aapl_lbl

    cnq_arch, cnq_lbl = asset_fingerprint_engine.classify_microstructure_archetype("CNQ", "CA")
    assert cnq_arch == "ARCHETYPE_B_COMMODITY_CYCLICAL"
    assert "Natural Resources" in cnq_lbl

    td_arch, td_lbl = asset_fingerprint_engine.classify_microstructure_archetype("TD", "CA")
    assert td_arch == "ARCHETYPE_C_BANK_REGULATED"
    assert "Banks" in td_lbl

    shop_arch, shop_lbl = asset_fingerprint_engine.classify_microstructure_archetype("SHOP", "CA")
    assert shop_arch == "ARCHETYPE_D_CROSS_BORDER_GROWTH"
    assert "Dual-Listed" in shop_lbl

    bam_arch, bam_lbl = asset_fingerprint_engine.classify_microstructure_archetype("BAM", "CA")
    assert bam_arch == "ARCHETYPE_E_DEFENSIVE_YIELD"


def test_feed_generation_and_export():
    """Verify generate_asset_fingerprints_feed produces valid structure and matches disk feed."""
    feed = asset_fingerprint_engine.generate_asset_fingerprints_feed()
    assert "summary" in feed
    assert "fingerprints" in feed
    assert len(feed["fingerprints"]) >= 10
    assert feed["summary"]["total_profiled"] == len(feed["fingerprints"])

    # Verify on disk
    with open("data/feeds/asset_fingerprints.json", "r") as f:
        disk_feed = json.load(f)
    assert len(disk_feed["fingerprints"]) == len(feed["fingerprints"])


def test_impersonal_advice_linter_compliance():
    """Verify that all archetype labels and disclaimers pass ImpersonalAdviceLinter."""
    feed = asset_fingerprint_engine.generate_asset_fingerprints_feed()
    for disc in feed.get("disclaimers", []):
        linter.assert_clean(disc)
    for fp in feed.get("fingerprints", []):
        linter.assert_clean(fp["archetype_label"])


def test_symbol_dossier_integration():
    """Verify symbol dossiers contain the asset_fingerprint payload."""
    with open("data/feeds/symbols/NVDA.json", "r") as f:
        nvda = json.load(f)
    assert "asset_fingerprint" in nvda
    fp = nvda["asset_fingerprint"]
    assert fp["symbol"] == "NVDA"
    assert fp["archetype"] == "ARCHETYPE_A_TECH_GAMMA"
    assert fp["hurst_exponent"] > 0
    assert fp["fractional_d_order"] > 0
