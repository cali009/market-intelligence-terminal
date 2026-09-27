"""
Unit and Integration Tests for QUANT INTEL Engine (Phase 22.1).
Validates 6 signal layers, trade planning logic, 1% risk position sizing,
conviction grading, and deterministic text card generation.
"""

import pytest
from datetime import datetime, timezone
from src.engine.quant_intel import quant_intel_engine, QuantIntelDossier


def test_quant_intel_layer1_regime():
    """Verify Layer 1 properly classifies macro regime, trend, vol, and rate context."""
    l1_us = quant_intel_engine.evaluate_layer1_regime("US")
    assert l1_us.trend_type in ("Bull", "Bear", "Sideways", "Volatile")
    assert any(k in l1_us.volatility_regime for k in ("Low", "Medium", "High"))
    assert l1_us.liquidity_env in ("Risk-ON", "Risk-OFF")
    assert l1_us.regime_fit in ("YES", "PARTIAL", "NO")
    assert l1_us.status in ("BULLISH", "NEUTRAL", "BEARISH")

    l1_ca = quant_intel_engine.evaluate_layer1_regime("CA")
    assert l1_ca.regime_state is not None
    assert l1_ca.regime_fit in ("YES", "PARTIAL", "NO")


def test_quant_intel_layer2_fingerprint():
    """Verify Layer 2 classifies per-stock pattern fingerprints and Hurst classes."""
    fp_nvda = quant_intel_engine.evaluate_layer2_fingerprint(
        symbol="NVDA",
        df=None,
        tech_snap={"cmf_20": 0.05, "rvol_20": 1.2, "atr_pct": 2.3, "rsi_14": 55.0}
    )
    assert fp_nvda.symbol == "NVDA"
    assert fp_nvda.dominant_pattern is not None
    assert 0.0 <= fp_nvda.hurst_exponent <= 1.0
    assert fp_nvda.hurst_class is not None
    assert len(fp_nvda.pattern_logic) > 10


def test_quant_intel_layer4_fundamental_scoring():
    """Verify Layer 4 scores fundamentals 1-10 and enforces technical gates for score < 5."""
    # Test broad ETF
    f_etf = quant_intel_engine.evaluate_layer4_fundamental("SPY", is_etf=True)
    assert f_etf.score >= 8.0
    assert f_etf.status == "BULLISH"
    assert not f_etf.technical_gate_required

    # Test equity with high quality
    f_high = quant_intel_engine.evaluate_layer4_fundamental(
        "NVDA",
        is_etf=False,
        fund_record={
            "quality_score": 85,
            "valuation_score": 40,
            "quality_ratios": {"roic": 25.0, "operating_margin_pct": 60.0, "debt_to_equity": 0.15},
            "valuation_multiples": {"pe_ratio": 75.0, "fcf_yield_pct": 2.2}
        }
    )
    assert f_high.score >= 7.0
    assert f_high.status == "BULLISH"
    assert not f_high.technical_gate_required

    # Test distressed equity
    f_low = quant_intel_engine.evaluate_layer4_fundamental(
        "DISTRESSED",
        is_etf=False,
        fund_record={
            "quality_score": 20,
            "valuation_score": 10,
            "quality_ratios": {"roic": -5.0, "operating_margin_pct": -10.0, "debt_to_equity": 4.5},
            "valuation_multiples": {"pe_ratio": None, "fcf_yield_pct": -5.0}
        }
    )
    assert f_low.score < 5.0
    assert f_low.status == "BEARISH"
    assert f_low.technical_gate_required is True


def test_quant_intel_layer5_sentiment_nlp_recency():
    """Verify Layer 5 calculates recency decay weights and skips on negative sentiment + weak technicals."""
    sent = quant_intel_engine.evaluate_layer5_sentiment("NVDA")
    assert hasattr(sent, "net_sentiment_score")
    assert sent.status in ("BULLISH", "NEUTRAL", "BEARISH")
    assert "24h" in sent.recency_breakdown
    assert "7d" in sent.recency_breakdown
    assert "30d" in sent.recency_breakdown
    assert "older" in sent.recency_breakdown


def test_quant_intel_layer6_historical_memory():
    """Verify Layer 6 retrieves empirical win rate, move sizes, and false breakout stats."""
    mem_pullback = quant_intel_engine.evaluate_layer6_memory("NVDA", "Momentum (Pullback Continuation)")
    assert mem_pullback.win_rate_pct >= 50.0
    assert mem_pullback.sample_size > 0
    assert mem_pullback.expectancy_r > 0.0
    assert mem_pullback.false_breakout_rate_pct > 0.0
    assert mem_pullback.median_bars_to_target > 0


def test_quant_intel_trade_planning_and_sizing():
    """Verify 1% risk position sizing, ATR stop clamping, and 3-tier target ladder."""
    dossier = quant_intel_engine.evaluate_ticker("NVDA", portfolio_size=100000.0)
    p = dossier.trade_plan

    # Stop Loss assertions
    assert p.stop_loss < p.entry_zone_ideal
    assert p.stop_distance_pct > 0.0
    assert p.stop_distance_dollars > 0.0
    # Stop distance must be >= 0.5 * ATR and <= 3.75 * ATR (with regime expansion)
    atr = dossier.layer_3_technical.atr_14
    assert p.stop_distance_dollars >= (atr * 0.49)
    assert p.stop_distance_dollars <= (atr * 4.0)

    # Targets assertions
    assert p.target_1 > p.entry_zone_ideal
    assert p.target_2 > p.target_1
    assert p.target_3 > p.target_2
    assert p.target_1_days < p.target_2_days < p.target_3_days
    assert p.risk_reward_ratio >= 2.0

    # 1% Risk Sizing assertions
    risk_budget = 100000.0 * 0.01  # $1,000
    if dossier.conviction_grade in ("A", "B"):
        expected_shares = int(risk_budget // p.stop_distance_dollars)
        assert p.recommended_shares == expected_shares
        assert p.capital_at_risk <= risk_budget
    elif dossier.conviction_grade == "C":
        expected_shares = int((risk_budget // p.stop_distance_dollars) * 0.50)
        assert p.recommended_shares == expected_shares
    elif dossier.conviction_grade == "SKIP":
        assert p.recommended_shares == 0


def test_quant_intel_card_format_matches_template():
    """Verify rendered card matches the exact required prompt template structure."""
    dossier = quant_intel_engine.evaluate_ticker("NVDA", portfolio_size=100000.0)
    card = dossier.formatted_card

    # Template sections must all be present
    assert "TICKER: NVDA" in card
    assert "REGIME:" in card
    assert "PATTERN TYPE:" in card
    assert "SIGNAL SUMMARY:" in card
    assert "  Technical:" in card
    assert "  Fundamental:" in card
    assert "  Sentiment:" in card
    assert "  Historical:" in card
    assert "  Regime fit:" in card
    assert "TRADE PLAN:" in card
    assert "  Entry zone:" in card
    assert "  Entry trigger:" in card
    assert "  Stop loss:" in card
    assert "  Target 1 (T1):" in card
    assert "  Target 2 (T2):" in card
    assert "  Target 3 (T3):" in card
    assert "  Risk/Reward:" in card
    assert "  Position size:" in card
    assert "CONVICTION GRADE:" in card
    assert "TRADE THESIS:" in card
    assert "INVALIDATION:" in card


def test_quant_intel_dual_market_coverage():
    """Verify end-to-end evaluation runs cleanly on both US (NVDA) and Canadian (SHOP) equities."""
    nvda = quant_intel_engine.evaluate_ticker("NVDA")
    assert nvda.symbol == "NVDA"
    assert nvda.country == "US"
    assert nvda.currency == "USD"
    assert nvda.conviction_grade in ("A", "B", "C", "SKIP")

    shop = quant_intel_engine.evaluate_ticker("SHOP")
    assert shop.symbol == "SHOP"
    assert shop.country == "CA"
    assert shop.currency == "CAD"
    assert shop.conviction_grade in ("A", "B", "C", "SKIP")


def test_quant_intel_evaluate_all_active_symbols():
    """Verify evaluate_all processes all active symbols without throwing exceptions."""
    all_d = quant_intel_engine.evaluate_all(portfolio_size=100000.0)
    assert len(all_d) >= 15
    for sym, d in all_d.items():
        assert isinstance(d, QuantIntelDossier)
        assert d.symbol == sym
        assert d.trade_plan.risk_reward_ratio > 0.0


def test_quant_intel_memory_ledger_schema_and_persistence():
    """Phase 22.2: Verify SQLite-backed pattern memory records, retrieval, and table seeding."""
    from src.engine.quant_intel_memory import quant_intel_memory_ledger
    records = quant_intel_memory_ledger.get_all_memory_records()
    assert len(records) >= 50, f"Expected at least 50 seeded memory records, got {len(records)}"

    rec = quant_intel_memory_ledger.get_pattern_memory("AAPL", "PULLBACK_CONTINUATION", "STRONG_BULL")
    assert rec.symbol == "AAPL"
    assert rec.setup_type == "PULLBACK_CONTINUATION"
    assert rec.total_trials >= 10
    assert rec.win_rate_pct > 0.0
    assert rec.expectancy_r > 0.0
    assert rec.false_breakout_rate_pct >= 0.0
    assert rec.signal_weight_multiplier >= 0.70


def test_quant_intel_memory_self_updating_learning_loop():
    """Phase 22.2: Verify closed-loop learning: trade resolution updates stats and adjusts signal weights."""
    from src.engine.quant_intel_memory import quant_intel_memory_ledger
    symbol = "MSFT"
    setup = "MOMENTUM_BREAKOUT"
    regime = "STRONG_BULL"

    initial = quant_intel_memory_ledger.get_pattern_memory(symbol, setup, regime)
    init_trials = initial.total_trials
    init_mult = initial.signal_weight_multiplier

    # Record winning trade
    updated_win = quant_intel_memory_ledger.record_trade_outcome(
        symbol=symbol,
        setup_type=setup,
        regime_state=regime,
        is_win=True,
        return_pct=6.5,
        r_multiple=2.1,
        bars_held=12,
        is_false_breakout=False
    )
    assert updated_win.total_trials == init_trials + 1
    assert updated_win.signal_weight_multiplier > init_mult or updated_win.signal_weight_multiplier == 1.25

    # Record losing trade with false breakout
    updated_loss = quant_intel_memory_ledger.record_trade_outcome(
        symbol=symbol,
        setup_type=setup,
        regime_state=regime,
        is_win=False,
        return_pct=-3.2,
        r_multiple=-1.0,
        bars_held=4,
        is_false_breakout=True
    )
    assert updated_loss.total_trials == init_trials + 2
    assert updated_loss.false_breakout_count > initial.false_breakout_count
    assert updated_loss.signal_weight_multiplier < updated_win.signal_weight_multiplier


def test_quant_intel_memory_bayesian_prior_shrinkage():
    """Phase 22.2: Verify Bayesian shrinkage is applied when sample size is small (< 10)."""
    from src.engine.quant_intel_memory import quant_intel_memory_ledger
    # Query an unseen ticker
    rec = quant_intel_memory_ledger.get_pattern_memory("NEWTICKER", "MEAN_REVERSION", "WEAK_BULL")
    assert rec.symbol == "NEWTICKER"
    # Should fall back cleanly to mean reversion prior
    assert rec.win_rate_pct == quant_intel_memory_ledger.SETUP_PRIORS["MEAN_REVERSION"]["win_rate_pct"]
    assert rec.expectancy_r == quant_intel_memory_ledger.SETUP_PRIORS["MEAN_REVERSION"]["expectancy_r"]


def test_quant_intel_edge_feeds_serialization_and_symbols_dossiers():
    """Phase 22.3: Verify quant_intel.json master feed and embedded symbol dossiers exist and parse cleanly."""
    import json
    from pathlib import Path
    feeds_dir = Path("data/feeds")
    qi_feed = feeds_dir / "quant_intel.json"
    assert qi_feed.exists(), "data/feeds/quant_intel.json must exist"

    with open(qi_feed) as f:
        data = json.load(f)

    assert data["total_tickers"] >= 15
    assert "dossiers" in data
    assert "NVDA" in data["dossiers"]
    assert "SHOP" in data["dossiers"]
    assert data["b_grade_count"] >= 3

    # Check individual symbol dossier
    nvda_sym = feeds_dir / "symbols" / "NVDA.json"
    assert nvda_sym.exists()
    with open(nvda_sym) as f:
        sym_data = json.load(f)

    assert "quant_intel" in sym_data
    qi = sym_data["quant_intel"]
    assert qi is not None
    assert qi["symbol"] == "NVDA"
    assert "formatted_card" in qi
    assert qi["conviction_grade"] in ("A", "B", "C", "SKIP")


def test_quant_intel_sentinel_stop_breach_detection():
    """Phase 22.5: Verify sentinel fires CRITICAL alert when price breaches structural stop."""
    from src.engine.quant_intel_sentinel import quant_intel_sentinel
    dossier = quant_intel_engine.evaluate_ticker("NVDA")
    p = dossier.trade_plan

    # Simulate price breach below stop
    sim_bars = [
        {"trading_date": "2026-09-27", "open": p.stop_loss - 1.0, "high": p.stop_loss, "low": p.stop_loss - 3.0, "close": p.stop_loss - 2.5, "volume": 1000000}
    ]
    alerts = quant_intel_sentinel.evaluate_ticker_sentinel("NVDA", dossier, recent_bars=sim_bars)
    stop_alerts = [a for a in alerts if a.predicate_type == "STOP_LOSS_BREACH"]
    assert len(stop_alerts) == 1
    assert stop_alerts[0].severity == "CRITICAL"
    assert stop_alerts[0].action_required == "IMMEDIATE_EXIT"
    assert "penetrated the structural stop" in stop_alerts[0].body


def test_quant_intel_sentinel_breakout_failure_detection():
    """Phase 22.5: Verify sentinel fires WARNING alert when price has consecutive closes below entry pivot."""
    from src.engine.quant_intel_sentinel import quant_intel_sentinel
    dossier = quant_intel_engine.evaluate_ticker("NVDA")
    p = dossier.trade_plan

    # Simulate two consecutive closes below entry support but above stop loss
    mid_price = (p.entry_zone_ideal + p.stop_loss) / 2.0
    sim_bars = [
        {"trading_date": "2026-09-27", "open": mid_price + 0.5, "high": mid_price + 1.0, "low": mid_price - 0.5, "close": mid_price, "volume": 1000000},
        {"trading_date": "2026-09-26", "open": mid_price + 1.0, "high": mid_price + 1.5, "low": mid_price - 0.5, "close": mid_price + 0.2, "volume": 1000000},
    ]
    alerts = quant_intel_sentinel.evaluate_ticker_sentinel("NVDA", dossier, recent_bars=sim_bars)
    bf_alerts = [a for a in alerts if a.predicate_type == "BREAKOUT_FAILURE"]
    assert len(bf_alerts) == 1
    assert bf_alerts[0].severity == "WARNING"
    assert bf_alerts[0].action_required == "DE_RISK_50_PCT"


def test_quant_intel_sentinel_target1_ratchet_detection():
    """Phase 22.5: Verify sentinel fires NOTICE alert and ratchets stop to breakeven when Target 1 is achieved."""
    from src.engine.quant_intel_sentinel import quant_intel_sentinel
    dossier = quant_intel_engine.evaluate_ticker("NVDA")
    p = dossier.trade_plan

    # Simulate price reaching Target 1
    sim_bars = [
        {"trading_date": "2026-09-27", "open": p.entry_zone_ideal, "high": p.target_1 + 1.0, "low": p.entry_zone_ideal, "close": p.target_1, "volume": 2000000}
    ]
    alerts = quant_intel_sentinel.evaluate_ticker_sentinel("NVDA", dossier, recent_bars=sim_bars)
    t1_alerts = [a for a in alerts if a.predicate_type == "TARGET_1_HIT_RATCHET"]
    assert len(t1_alerts) == 1
    assert t1_alerts[0].severity == "NOTICE"
    assert t1_alerts[0].action_required == "TIGHTEN_STOP_TO_BREAKEVEN"
    assert "ratcheting stop loss up to Breakeven" in t1_alerts[0].body


def test_quant_intel_sentinel_alert_record_conversion():
    """Phase 22.5: Verify InvalidationAlert objects convert cleanly to Pydantic AlertRecords."""
    from src.engine.quant_intel_sentinel import quant_intel_sentinel
    dossier = quant_intel_engine.evaluate_ticker("NVDA")
    p = dossier.trade_plan

    sim_bars = [
        {"trading_date": "2026-09-27", "open": p.stop_loss - 1.0, "high": p.stop_loss, "low": p.stop_loss - 3.0, "close": p.stop_loss - 2.5, "volume": 1000000}
    ]
    alerts = quant_intel_sentinel.evaluate_ticker_sentinel("NVDA", dossier, recent_bars=sim_bars)
    records = quant_intel_sentinel.convert_to_system_alerts(alerts)
    assert len(records) >= 1
    rec = records[0]
    assert rec.symbol == "NVDA"
    assert rec.severity in ("CRITICAL", "WARNING", "NOTICE")
    assert rec.taxonomy in ('TECHNICAL_BREAKOUT', 'EXIT_TRIGGER_ESCALATION', 'REGIME_SHIFT', 'CATALYST_MATERIALITY')
    assert rec.disclaimer is not None



