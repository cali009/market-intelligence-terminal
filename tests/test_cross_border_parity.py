"""
Unit & Integration Tests: Cross-Border Dual-Listed Parity & FX Basis Engine (Phase 15)
US + Canada Market Intelligence Platform

Verifies:
- Official Bank of Canada Valet USD/CAD fixing integration (FXUSDCAD)
- Implied CAD parity pricing and basis spread (bps & %)
- Engle-Granger two-step cointegration test (Beta ~ 1.0, ADF t-stat < -2.88, p < 0.05)
- Ornstein-Uhlenbeck / AR(1) mean-reversion half-life estimation
- 60-day rolling basis z-score and parity state taxonomy
- Cross-border volume ratio & liquidity center classification (TSX vs NYSE)
- Feed file export (cross_border_parity.json) and symbol dossier injection
- Compliance linter pass under CSA Staff Notice 31-369 and SEC Publisher Exclusion
"""

import json
import pytest
import numpy as np

from src.engine.cross_border_parity import cross_border_parity_engine, DUAL_LISTED_PAIRS
from src.compliance.linter import linter
from src.models.schemas import DualListedParityRecord, DualListedFeed


def test_boc_fx_conversion_and_implied_parity():
    """Verify that implied CAD price = US price * BoC FX rate, and basis spread matches."""
    rec = cross_border_parity_engine.compute_pair_parity("SHOP")
    assert rec is not None
    assert rec.tsx_symbol == "SHOP"
    assert rec.us_symbol == "SHOP"
    assert rec.boc_fx_rate > 1.0

    expected_implied = round(rec.us_close_usd * rec.boc_fx_rate, 2)
    assert abs(rec.implied_cad_price - expected_implied) <= 0.02

    expected_spread_bps = round(((rec.tsx_close_cad - rec.implied_cad_price) / rec.implied_cad_price) * 10000.0, 1)
    assert abs(rec.basis_spread_bps - expected_spread_bps) <= 0.2


def test_engle_granger_cointegration_beta_and_stationarity():
    """Verify cointegration beta is close to 1.0 and ADF test confirms stationarity (t < -2.88)."""
    feed = cross_border_parity_engine.generate_feed()
    assert feed.total_pairs_tracked == 9
    assert feed.cointegration_rate_pct >= 90.0

    for p in feed.pairs:
        # Cointegration Beta should be within [0.95, 1.05] for identical corporate claims
        assert 0.95 <= p.cointegration_beta <= 1.05, f"Beta out of bounds for {p.tsx_symbol}: {p.cointegration_beta}"
        assert p.adf_t_stat < -2.88, f"ADF t-stat not stationary for {p.tsx_symbol}: {p.adf_t_stat}"
        assert p.is_cointegrated is True


def test_half_life_mean_reversion_bounds():
    """Verify mean-reversion half-life is strictly positive and within realistic trading bounds."""
    feed = cross_border_parity_engine.generate_feed()
    for p in feed.pairs:
        assert 0.5 <= p.half_life_days <= 15.0, f"Half-life unusual for {p.tsx_symbol}: {p.half_life_days}"


def test_60d_basis_zscore_and_parity_states():
    """Verify that basis z-score maps accurately to the parity state taxonomy."""
    feed = cross_border_parity_engine.generate_feed()
    valid_states = {"PARITY_EQUILIBRIUM", "MILD_DISPARITY", "STATISTICAL_STRETCH"}

    for p in feed.pairs:
        assert p.parity_state in valid_states
        if abs(p.basis_zscore_60d) < 1.0:
            assert p.parity_state == "PARITY_EQUILIBRIUM"
        elif abs(p.basis_zscore_60d) < 2.0:
            assert p.parity_state == "MILD_DISPARITY"
        else:
            assert p.parity_state == "STATISTICAL_STRETCH"


def test_volume_liquidity_center_classification():
    """Verify cross-border volume ratio correctly identifies TSX vs NYSE dominance."""
    shop = cross_border_parity_engine.compute_pair_parity("SHOP")
    assert shop is not None
    # SHOP volume is heavily centered on NYSE
    assert shop.primary_liquidity_center == "NYSE"
    assert shop.volume_ratio_tsx_to_us < 0.67

    ry = cross_border_parity_engine.compute_pair_parity("RY")
    assert ry is not None
    # Royal Bank volume is heavily centered on TSX
    assert ry.primary_liquidity_center == "TSX"
    assert ry.volume_ratio_tsx_to_us > 1.5


def test_feed_generation_and_export():
    """Verify cross_border_parity.json feed file exists on disk and matches schema."""
    with open("data/feeds/cross_border_parity.json", "r") as f:
        disk_feed = json.load(f)

    assert disk_feed["total_pairs_tracked"] == 9
    assert disk_feed["boc_valet_fx_usdcad"] > 1.0
    assert len(disk_feed["pairs"]) == 9
    assert disk_feed["cointegration_rate_pct"] == 100.0


def test_symbol_dossier_cross_border_integration():
    """Verify that Canadian dual-listed symbol dossiers have cross_border_parity, while pure US do not."""
    with open("data/feeds/symbols/SHOP.json", "r") as f:
        shop = json.load(f)
    assert "cross_border_parity" in shop
    assert shop["cross_border_parity"] is not None
    assert shop["cross_border_parity"]["tsx_symbol"] == "SHOP"
    assert shop["cross_border_parity"]["us_symbol"] == "SHOP"

    with open("data/feeds/symbols/NVDA.json", "r") as f:
        nvda = json.load(f)
    assert nvda.get("cross_border_parity") is None


def test_impersonal_advice_linter_compliance():
    """Verify that all cross-border parity disclaimers pass ImpersonalAdviceLinter."""
    feed = cross_border_parity_engine.generate_feed()
    for disc in feed.disclaimers:
        linter.assert_clean(disc)
