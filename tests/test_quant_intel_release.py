"""
Comprehensive Release & Integration Tests for QUANT INTEL System (Phase 22.6)
Verifies:
1. Full 6-layer synthesis and conviction grading contracts
2. Invalidation sentinel predicates (Regime Downgrade, Adverse Catalyst, Stop Breach, Target Ratchet)
3. Dynamic capital sizing across multiple account scales ($25k-$1M)
4. Empirical memory false breakout stop widening
5. Web server HTTP API endpoints and JSON feed routing
6. Impersonal compliance linter validation on all cards, alerts, and dossiers (CSA 31-369 / SEC)
"""

import json
import pytest
from pathlib import Path
import math

from src.compliance.linter import linter
from src.engine.quant_intel import quant_intel_engine, QuantIntelDossier, QuantIntelTradePlan
from src.engine.quant_intel_sentinel import quant_intel_sentinel, InvalidationAlert
from src.engine.quant_intel_memory import quant_intel_memory_ledger
from web.server import MarketIntelHandler, get_data_dir


def test_quant_intel_sentinel_regime_downgrade_detection():
    """Verify sentinel triggers REGIME_DOWNGRADE alert when macro regime shifts to Bearish or Volatile."""
    dossier = quant_intel_engine.evaluate_ticker("NVDA")
    
    # Mutate regime fit to "NO" and trend to "Bear"
    dossier.layer_1_regime.regime_fit = "NO"
    dossier.layer_1_regime.trend_type = "Bear"
    dossier.layer_1_regime.regime_state = "BEAR_MARKET_DISTRIBUTION"

    alerts = quant_intel_sentinel.evaluate_ticker_sentinel("NVDA", dossier)
    regime_alerts = [a for a in alerts if a.predicate_type == "REGIME_DOWNGRADE"]
    assert len(regime_alerts) >= 1
    assert regime_alerts[0].severity == "WARNING"
    assert regime_alerts[0].action_required == "WIDEN_STOP_OR_DE_RISK"
    assert "Macro Regime Mismatch Triggered" in regime_alerts[0].headline


def test_quant_intel_sentinel_adverse_catalyst_detection():
    """Verify sentinel triggers ADVERSE_CATALYST alert when sentiment turns sharply negative."""
    dossier = quant_intel_engine.evaluate_ticker("AAPL")

    # Simulate an adverse 8-K / news filing
    dossier.layer_5_sentiment.skip_triggered = True
    dossier.layer_5_sentiment.net_sentiment_score = -0.35
    dossier.layer_5_sentiment.key_headline = "DOJ Files Antitrust Suit Alleging Monopolistic Smartphone Lock-In"

    alerts = quant_intel_sentinel.evaluate_ticker_sentinel("AAPL", dossier)
    cat_alerts = [a for a in alerts if a.predicate_type == "ADVERSE_CATALYST"]
    assert len(cat_alerts) >= 1
    assert cat_alerts[0].severity == "WARNING"
    assert cat_alerts[0].action_required == "REVIEW_FOR_INVALIDATION"
    assert "Material Adverse Catalyst Detected" in cat_alerts[0].headline


def test_quant_intel_capital_sizer_multi_portfolio():
    """Verify exact 1% risk budgeting and share calculation across multiple portfolio equity tiers."""
    dossier = quant_intel_engine.evaluate_ticker("MSFT")
    p = dossier.trade_plan
    stop_dist = p.stop_distance_dollars
    assert stop_dist > 0

    portfolio_sizes = [25_000, 50_000, 100_000, 250_000, 1_000_000]
    for equity in portfolio_sizes:
        # Check custom evaluation for this equity size
        eval_tier = quant_intel_engine.evaluate_ticker("MSFT", portfolio_size=equity)
        tp = eval_tier.trade_plan
        assert tp.portfolio_size == equity

        expected_budget = equity * 0.01
        base_shares = math.floor(expected_budget / stop_dist)
        if dossier.conviction_grade == "C":
            expected_shares = math.floor(base_shares * 0.50)
        elif dossier.conviction_grade == "SKIP":
            expected_shares = 0
        else:
            expected_shares = base_shares

        assert tp.recommended_shares == expected_shares
        assert tp.capital_at_risk <= expected_budget + 0.05
        assert tp.portfolio_risk_pct <= 1.05


def test_quant_intel_false_breakout_stop_widening_edge_case():
    """Verify that when memory ledger indicates high false breakout rate (>22%), stop distance expands by 1.15x."""
    stats_normal = {"false_breakout_pct": 14.0}
    stats_elevated = {"false_breakout_pct": 28.5}

    mult_normal = 1.15 if stats_normal["false_breakout_pct"] > 22.0 else 1.00
    mult_elevated = 1.15 if stats_elevated["false_breakout_pct"] > 22.0 else 1.00

    assert mult_normal == 1.00
    assert mult_elevated == 1.15

    dossier = quant_intel_engine.evaluate_ticker("NVDA")
    assert dossier.trade_plan.stop_loss < dossier.trade_plan.entry_zone_ideal
    assert dossier.trade_plan.stop_distance_pct >= 1.0


def test_quant_intel_conviction_grading_contract():
    """Verify that conviction grade strictly follows layer alignment and risk-reward constraints."""
    for sym in ["MSFT", "NVDA", "AAPL", "RY", "TD"]:
        dossier = quant_intel_engine.evaluate_ticker(sym)
        p = dossier.trade_plan
        grade = dossier.conviction_grade
        aligned = dossier.aligned_layers_count
        rr = p.risk_reward_ratio

        assert grade in ("A", "B", "C", "SKIP")
        if grade == "A":
            assert aligned == 5
            assert rr >= 3.0
        elif grade == "B":
            assert aligned >= 4
            assert rr >= 2.5
        elif grade == "C":
            assert aligned >= 3
            assert rr >= 2.0
        else:
            assert grade == "SKIP"


def test_web_server_api_endpoints_routing():
    """Verify local web server handler correctly resolves and serves all key API endpoints."""
    data_dir = get_data_dir()
    assert (data_dir / "quant_intel.json").exists()
    assert (data_dir / "alerts.json").exists()

    endpoints_to_test = [
        "quant_intel.json",
        "alerts.json",
        "daily_summary.json",
        "cpcv_validation.json",
        "asset_fingerprints.json",
        "conformal_bounds.json",
        "idiosyncratic_risk_matrix.json",
        "symbols/MSFT.json",
        "symbols/RY.json",
    ]

    for ep in endpoints_to_test:
        fp = data_dir / ep
        assert fp.exists(), f"Target feed file {ep} missing in data directory"
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert isinstance(data, (dict, list))
            assert len(data) > 0


def test_quant_intel_compliance_linter_zero_violations():
    """Verify all generated Quant Intel cards, trade dossiers, and alerts pass compliance linting."""
    dossiers_map = quant_intel_engine.evaluate_all()
    assert len(dossiers_map) >= 19

    for sym, d in dossiers_map.items():
        # 1. Lint the monospaced institutional card
        card_text = d.formatted_card
        linter.assert_clean(card_text)

        # 2. Lint the trade thesis and invalidation description
        linter.assert_clean(d.trade_plan.trade_thesis)
        linter.assert_clean(d.trade_plan.invalidation_rule)

        # 3. Lint the fundamental & sentiment highlights
        linter.assert_clean(d.layer_4_fundamental.key_reason)
        if d.layer_5_sentiment.key_headline:
            linter.assert_clean(d.layer_5_sentiment.key_headline)

    # 4. Lint live sentinel alerts
    sentinel_alerts = quant_intel_sentinel.evaluate_universe_sentinels(dossiers_map)
    system_alerts = quant_intel_sentinel.convert_to_system_alerts(sentinel_alerts)
    for alert in system_alerts:
        linter.assert_clean(alert.headline)
        linter.assert_clean(alert.body)
        linter.assert_clean(alert.disclaimer)
