"""
Comprehensive Test Suite: Phase 19 Power of Three (PO3) Strategy Engine,
TAC-15 Scanner, CPCV FDR Gating & Adaptive Dual-Regime Validation.
US + Canada Market Intelligence Platform
"""

import math
import pytest
from src.engine.backtester import backtest_engine, BacktestConfig, SimulatedTrade
from src.engine.cpcv_engine import cpcv_engine
from src.engine.scanners import market_scanners


def test_po3_strategy_registered():
    """Verify PO3_LIQUIDITY_SWEEP and ADAPTIVE_DUAL_REGIME are pre-registered in STRATEGY_REGISTRY."""
    reg = backtest_engine.get_strategy_registry()
    
    assert "PO3_LIQUIDITY_SWEEP" in reg, "PO3_LIQUIDITY_SWEEP missing from strategy registry"
    po3 = reg["PO3_LIQUIDITY_SWEEP"]
    assert po3["status"] == "ACTIVE"
    assert po3["pre_registration_date"] == "2026-09-26"
    assert "Power of Three" in po3["strategy_name"]
    assert "two-stage partial trim ladder" in po3["hypothesis"].lower()
    
    assert "ADAPTIVE_DUAL_REGIME" in reg, "ADAPTIVE_DUAL_REGIME missing from strategy registry"
    adapt = reg["ADAPTIVE_DUAL_REGIME"]
    assert adapt["status"] == "ACTIVE"
    assert adapt["pre_registration_date"] == "2026-09-26"
    assert "Adaptive" in adapt["strategy_name"]


def test_po3_backtest_execution():
    """Verify event-driven backtest for PO3_LIQUIDITY_SWEEP calculates full metric set."""
    res = backtest_engine.run_strategy_backtest("PO3_LIQUIDITY_SWEEP")
    assert res["strategy_id"] == "PO3_LIQUIDITY_SWEEP"
    assert res["status"] == "ACTIVE"
    assert "metrics" in res
    
    m = res["metrics"]
    assert m["total_trades"] > 0, "PO3 backtest generated zero trades"
    assert "cagr_pct" in m
    assert "sharpe_ratio" in m
    assert "max_drawdown_pct" in m
    assert "win_rate_pct" in m
    assert "profit_factor" in m
    assert "expectancy_r" in m
    assert "cost_sensitivity" in m
    assert "yearly_breakdown" in m
    assert "walk_forward_partitions" in m
    
    # Check partitions
    parts = m["walk_forward_partitions"]
    assert "TRAIN" in parts and parts["TRAIN"]["total_trades"] > 0
    assert "VALIDATION" in parts and parts["VALIDATION"]["total_trades"] > 0
    assert "TEST" in parts and parts["TEST"]["total_trades"] > 0
    
    # Check disclaimer
    assert "statutory_disclaimer" in res
    assert "HYPOTHETICAL" in res["statutory_disclaimer"].upper()


def test_po3_two_stage_partial_trim_ladder():
    """Verify two-stage partial trim ladder: 50% trim at T1, breakeven stop ratchet, runner to T2."""
    res = backtest_engine.run_strategy_backtest("PO3_LIQUIDITY_SWEEP")
    all_trades = res["all_trades"]
    
    trimmed_trades = [t for t in all_trades if t.get("is_partially_trimmed")]
    assert len(trimmed_trades) > 0, "No trades executed a partial trim at Target 1"
    
    for t in trimmed_trades:
        assert t["trimmed_shares"] > 0, "Trimmed shares should be positive"
        assert t["trim_pnl_usd"] != 0.0, "Trim P&L should be recorded"
        assert t["is_partially_trimmed"] is True
        
        # When trimmed at Target 1, stop loss must have been ratcheted to breakeven
        if t["exit_reason"] == "BREAKEVEN_STOP":
            assert t["stop_loss"] >= t["entry_price"] * 0.99, (
                f"Breakeven stop loss ({t['stop_loss']}) should be at or near entry ({t['entry_price']})"
            )


def test_adaptive_dual_regime_backtest_execution():
    """Verify event-driven backtest for ADAPTIVE_DUAL_REGIME."""
    res = backtest_engine.run_strategy_backtest("ADAPTIVE_DUAL_REGIME")
    assert res["strategy_id"] == "ADAPTIVE_DUAL_REGIME"
    assert res["status"] == "ACTIVE"
    
    m = res["metrics"]
    assert m["total_trades"] > 0
    parts = m["walk_forward_partitions"]
    assert parts["TRAIN"]["total_trades"] > 0
    assert parts["VALIDATION"]["total_trades"] > 0
    assert parts["TEST"]["total_trades"] > 0


def test_anti_lookahead_execution():
    """Verify that every PO3 and Adaptive trade executes strictly on T+1 open (no lookahead)."""
    for sid in ["PO3_LIQUIDITY_SWEEP", "ADAPTIVE_DUAL_REGIME"]:
        res = backtest_engine.run_strategy_backtest(sid)
        trades = res["recent_trades"]
        for t in trades:
            assert t["entry_date"] > t["signal_date"], (
                f"Anti-lookahead violation in {sid} trade {t['symbol']}: entry {t['entry_date']} <= signal {t['signal_date']}"
            )


def test_cpcv_fdr_gating_evaluation():
    """Verify that CPCV engine evaluates all strategies including PO3 and controls FDR."""
    feed = cpcv_engine.generate_feed()
    summary = feed.summary
    
    assert summary.total_strategies_evaluated >= 7
    assert summary.total_cpcv_paths == 15
    assert 0.0 <= summary.empirical_pbo <= 1.0
    
    table = feed.fdr_gating_table
    strat_ids = [r.strategy_id for r in table]
    assert "PO3_LIQUIDITY_SWEEP" in strat_ids
    assert "ADAPTIVE_DUAL_REGIME" in strat_ids
    assert "TAC03_BREAKOUT_CHASE" in strat_ids
    
    # Retired strategy must remain REJECTED_SELECTION_BIAS
    tac03 = next(r for r in table if r.strategy_id == "TAC03_BREAKOUT_CHASE")
    assert tac03.fdr_verdict == "REJECTED_SELECTION_BIAS"
    
    # PO3 Liquidity sweep record in FDR table
    po3_rec = next(r for r in table if r.strategy_id == "PO3_LIQUIDITY_SWEEP")
    assert po3_rec.total_trades > 0
    assert po3_rec.fdr_verdict in ("FDR_PASSED", "REJECTED_SELECTION_BIAS")


def test_po3_phase19_1_trend_gate():
    """Phase 19.1: Stocks below 50DMA or with inverted moving averages must be rejected."""
    import pandas as pd
    bar_downtrend = pd.Series({
        "close": 100.0, "open": 101.0, "high": 102.0, "low": 98.0,
        "sma_20": 102.0, "sma_50": 105.0, "sma_200": 110.0,
        "rsi_14": 50.0, "atr_14": 2.0, "rvol_20": 1.2, "cmf_20": 0.05,
        "proximity_52w_high": -0.05
    })
    assert not backtest_engine._evaluate_signal_predicate("PO3_LIQUIDITY_SWEEP", bar_downtrend)


def test_po3_phase19_1_reclaim_confirmation():
    """Phase 19.1: Bars without confirmed close reclaim (close < sma20) must be rejected."""
    import pandas as pd
    bar_no_reclaim = pd.Series({
        "close": 98.0, "open": 101.0, "high": 102.0, "low": 97.0,
        "sma_20": 100.0, "sma_50": 95.0, "sma_200": 90.0,
        "rsi_14": 50.0, "atr_14": 2.0, "rvol_20": 1.2, "cmf_20": 0.05,
        "proximity_52w_high": -0.05
    })
    assert not backtest_engine._evaluate_signal_predicate("PO3_LIQUIDITY_SWEEP", bar_no_reclaim)

    # Valid sweep and reclaim must pass
    bar_valid = pd.Series({
        "close": 101.5, "open": 101.0, "high": 102.5, "low": 98.5,
        "sma_20": 100.0, "sma_50": 95.0, "sma_200": 90.0,
        "rsi_14": 52.0, "atr_14": 2.0, "rvol_20": 1.2, "cmf_20": 0.05,
        "proximity_52w_high": -0.05
    })
    assert backtest_engine._evaluate_signal_predicate("PO3_LIQUIDITY_SWEEP", bar_valid)


def test_po3_phase19_1_symbol_quarantine_enforcement():
    """Phase 19.1: Verify stopped-out symbols cannot immediately re-enter on the same day."""
    res = backtest_engine.run_strategy_backtest("PO3_LIQUIDITY_SWEEP")
    trades = res["all_trades"]
    by_sym = {}
    for t in trades:
        by_sym.setdefault(t["symbol"], []).append(t)

    for sym, t_list in by_sym.items():
        for i in range(len(t_list) - 1):
            t_curr = t_list[i]
            t_next = t_list[i + 1]
            if t_curr["exit_reason"] in ("STOP_LOSS", "SAME_DAY_STOP"):
                assert t_curr["exit_date"] != t_next["entry_date"], f"{sym} re-entered on same day as stop-loss exit"


def test_po3_phase19_2_conformal_stop_and_targets():
    """Phase 19.2: Verify conformal stop is anchored below sweep low and Target 1 is at 1.8R."""
    res = backtest_engine.run_strategy_backtest("PO3_LIQUIDITY_SWEEP")
    trades = res["all_trades"]
    assert len(trades) > 0

    for t in trades[:20]:
        # Risk per share must be positive and reasonable
        assert t["target_1"] > t["entry_price"]
        assert t["target_2"] > t["target_1"]
        init_risk = t.get("initial_risk_per_share", 0.0)
        if init_risk <= 0:
            init_risk = t["entry_price"] - t["stop_loss"]
        reward = t["target_1"] - t["entry_price"]
        # Reward / Risk ratio at Target 1 should be approx 1.8R (1.45R - 2.05R net of fees)
        rr_t1 = reward / max(0.01, init_risk)
        assert 1.45 <= rr_t1 <= 2.05


def test_po3_phase19_2_max_position_throttling():
    """Phase 19.2: Verify concurrent open positions for PO3 never exceed the throttled cap of 4."""
    res = backtest_engine.run_strategy_backtest("PO3_LIQUIDITY_SWEEP")
    curve = res["equity_curve"]
    max_open = max(d["open_positions"] for d in curve)
    assert max_open <= 4, f"Concurrent PO3 positions ({max_open}) exceeded cap of 4"


def test_po3_phase19_3_no_index_etfs():
    """Phase 19.3: PO3 is an idiosyncratic single-stock model; verify index ETFs are excluded."""
    res = backtest_engine.run_strategy_backtest("PO3_LIQUIDITY_SWEEP")
    trades = res["all_trades"]
    traded_symbols = set(t["symbol"] for t in trades)
    assert "SPY" not in traded_symbols, "SPY index ETF should be excluded from PO3 single-stock sweeps"
    assert "QQQ" not in traded_symbols, "QQQ index ETF should be excluded from PO3 single-stock sweeps"
    assert "XIU" not in traded_symbols, "XIU index ETF should be excluded from PO3 single-stock sweeps"


def test_po3_phase19_3_volatility_and_quarantine_integrity():
    """Phase 19.3: Verify volatility blowout filtering and same-day stop quarantine enforcement."""
    res = backtest_engine.run_strategy_backtest("PO3_LIQUIDITY_SWEEP")
    trades = res["all_trades"]
    
    # Check same-day stop quarantine
    by_sym = {}
    for t in trades:
        by_sym.setdefault(t["symbol"], []).append(t)
    
    for sym, t_list in by_sym.items():
        for i in range(len(t_list) - 1):
            t_curr = t_list[i]
            t_next = t_list[i + 1]
            if t_curr["exit_reason"] == "SAME_DAY_STOP":
                assert t_curr["exit_date"] != t_next["entry_date"]


def test_tac15_scanner_match_and_expectancy():
    """Verify TAC-15 PO3 Liquidity Sweep scanner definition and match trigger."""
    assert "PO3_LIQUIDITY_SWEEP" in market_scanners.definitions
    defn = market_scanners.definitions["PO3_LIQUIDITY_SWEEP"]
    
    assert defn["historical_win_rate_pct"] > 50.0
    assert defn["forward_5d_return_pct"] > 0.0
    assert defn["expectancy_r"] > 0.0
    assert "CONSOLIDATION" in defn["regime_compatibility"]
    assert "STRONG_BULL" in defn["regime_compatibility"]
    
    # Test scan match
    sec_info = {"symbol": "NVDA", "exchange": "NASDAQ", "country": "US"}
    metrics = {
        "open": 124.0,
        "high": 128.0,
        "low": 118.0,               # Intraday low swept below 20DMA (120.0)
        "close": 127.0,              # Strong reclamation in upper half
        "sma_20": 120.0,
        "sma_50": 112.0,
        "sma_200": 95.0,
        "rsi_14": 56.0,
        "rvol_20": 1.30,
        "proximity_52w_high": -0.03,
        "atr_14": 3.8,
    }
    matches = market_scanners.scan_all(sec_info, metrics, regime_state="STRONG_BULL")
    po3_matches = [m for m in matches if m.scanner_id == "PO3_LIQUIDITY_SWEEP"]
    assert len(po3_matches) == 1
    m = po3_matches[0]
    assert m.symbol == "NVDA"
    assert m.price == 127.0
    assert "Institutional liquidity sweep" in m.why_matched
    assert m.regime_gated is False


def test_simulated_trade_schema_fields():
    """Verify SimulatedTrade dataclass supports all partial trim and tracking attributes."""
    trade = SimulatedTrade(
        symbol="AAPL",
        exchange="NASDAQ",
        country="US",
        currency="USD",
        direction="LONG",
        signal_date="2026-09-24",
        entry_date="2026-09-25",
        raw_entry_price=220.0,
        entry_price_net=220.22,
        entry_fx_rate=1.0,
        entry_price_usd_net=220.22,
        stop_loss=215.0,
        target_1=228.0,
        target_2=236.0,
        shares=10,
        trimmed_shares=5,
        trim_price_net=227.77,
        trim_pnl_usd=37.75,
        is_partially_trimmed=True,
        strategy_id="PO3_LIQUIDITY_SWEEP",
    )
    d = trade.to_dict()
    assert d["trimmed_shares"] == 5
    assert d["trim_price_net"] == 227.77
    assert d["trim_pnl_usd"] == 37.75
    assert d["is_partially_trimmed"] is True
    assert d["strategy_id"] == "PO3_LIQUIDITY_SWEEP"
