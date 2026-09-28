"""
Unit & Integration Tests for Phase 25: Real-Time WebSocket Streaming & Tick Microstructure Pipeline
US + Canada Market Intelligence Platform
"""

import json
from pathlib import Path
import pytest
import numpy as np

from config.settings import DATA_DIR
from src.compliance.linter import linter
from src.engine.microstructure import (
    microstructure_engine,
    classify_trade_lee_ready,
    compute_order_book_imbalance,
    compute_microprice,
    compute_kyle_lambda,
    detect_absorption_signal,
    detect_liquidity_state,
    DISCLAIMERS,
)
from src.models.schemas import (
    OrderBookLevel,
    OrderBookSnapshot,
    TickMessage,
    MicrostructureSnapshotFeed,
)
from src.engine.quant_intel_sentinel import quant_intel_sentinel


class TestTickMicrostructure:
    """Test suite covering trade classification, DOM imbalance, Kyle's lambda, and sentinel alerts."""

    def test_lee_ready_trade_signing(self):
        """Verifies Lee-Ready (1991) trade signing algorithm for buyer/seller aggressor classification."""
        bid, ask = 100.00, 100.02
        # 1. Trade above mid (100.01) -> BUY_AGGRESSOR (lifted ask)
        assert classify_trade_lee_ready(100.02, 100.01, bid, ask) == "BUY_AGGRESSOR"

        # 2. Trade below mid -> SELL_AGGRESSOR (hit bid)
        assert classify_trade_lee_ready(100.00, 100.01, bid, ask) == "SELL_AGGRESSOR"

        # 3. Trade at mid, price uptick -> BUY_AGGRESSOR
        assert classify_trade_lee_ready(100.01, 100.00, bid, ask) == "BUY_AGGRESSOR"

        # 4. Trade at mid, price downtick -> SELL_AGGRESSOR
        assert classify_trade_lee_ready(100.01, 100.02, bid, ask) == "SELL_AGGRESSOR"

    def test_order_book_imbalance_bounds(self):
        """Verifies Order Book Imbalance (OBI) is mathematically bounded in [-1.0, 1.0]."""
        # Neutral book
        bids = [OrderBookLevel(price=100.0 - (i*0.01), size=1000, order_count=5, side="BID", depth_tier=i+1) for i in range(5)]
        asks = [OrderBookLevel(price=100.02 + (i*0.01), size=1000, order_count=5, side="ASK", depth_tier=i+1) for i in range(5)]
        obi_neutral = compute_order_book_imbalance(bids, asks)
        assert obi_neutral == pytest.approx(0.0, abs=1e-3)

        # Heavily bid-slanted book
        bids_heavy = [OrderBookLevel(price=100.0 - (i*0.01), size=5000, order_count=15, side="BID", depth_tier=i+1) for i in range(5)]
        obi_bullish = compute_order_book_imbalance(bids_heavy, asks)
        assert obi_bullish > 0.50
        assert obi_bullish <= 1.00

        # Heavily ask-slanted book
        asks_heavy = [OrderBookLevel(price=100.02 + (i*0.01), size=6000, order_count=18, side="ASK", depth_tier=i+1) for i in range(5)]
        obi_bearish = compute_order_book_imbalance(bids, asks_heavy)
        assert obi_bearish < -0.50
        assert obi_bearish >= -1.00

    def test_microprice_weighted_calculation(self):
        """Verifies microprice falls strictly between best bid and best ask, biased by order size."""
        bids = [OrderBookLevel(price=100.00, size=3000, order_count=10, side="BID", depth_tier=1)]
        asks = [OrderBookLevel(price=100.02, size=1000, order_count=4, side="ASK", depth_tier=1)]

        microprice = compute_microprice(bids, asks)
        # When bid size is 3x ask size, microprice should be closer to ask (100.015)
        assert microprice > 100.010
        assert 100.00 <= microprice <= 100.02

    def test_kyle_lambda_positive_resiliency(self):
        """Verifies Kyle's Lambda price impact coefficient produces bounded positive values."""
        now_iso = "2026-09-28T20:00:00.000Z"
        ticks = [
            TickMessage(tick_id=f"T{i}", symbol="SPY", timestamp=now_iso, price=100.0 + (i*0.02), volume=200, aggressor_side="BUY_AGGRESSOR", bid=99.99, ask=100.01, bid_size=1000, ask_size=1000)
            for i in range(10)
        ]
        k_lambda = compute_kyle_lambda(ticks)
        assert 0.001 <= k_lambda <= 0.250

    def test_absorption_signal_logic(self):
        """Verifies detection of bullish absorption at support and bearish exhaustion at resistance."""
        # 1. Bullish absorption at support ($100.00) with positive CVD delta and high buyer pct
        abs_bull = detect_absorption_signal(
            price=100.50,
            support_level=100.00,
            resistance_level=110.00,
            cvd_1m=1200,
            obi=0.25,
            buyer_pct=62.0
        )
        assert abs_bull == "BULLISH_ABSORPTION"

        # 2. Bearish exhaustion at resistance ($110.00) with negative CVD and heavy ask-side OBI
        abs_bear = detect_absorption_signal(
            price=109.80,
            support_level=95.00,
            resistance_level=110.00,
            cvd_1m=-1500,
            obi=-0.30,
            buyer_pct=40.0
        )
        assert abs_bear == "BEARISH_EXHAUSTION"

        # 3. Normal equilibrium
        assert detect_absorption_signal(105.00, 100.00, 110.00, 100, 0.05, 50.0) == "NONE"

    def test_liquidity_state_detection(self):
        """Verifies detection of normal spread vs spread expansion vs flash liquidity void."""
        nominal_spread = 0.02
        assert detect_liquidity_state(0.02, nominal_spread) == "NORMAL"
        assert detect_liquidity_state(0.04, nominal_spread) == "SPREAD_EXPANSION"
        assert detect_liquidity_state(0.07, nominal_spread) == "LIQUIDITY_VOID"

    def test_dual_market_microstructure_snapshot_feed(self, tmp_path):
        """Verifies full master microstructure feed generation, dual-market coverage, and serialization."""
        feed = microstructure_engine.generate_feed(force_refresh=True)
        assert isinstance(feed, MicrostructureSnapshotFeed)
        assert len(feed.symbols) >= 19
        assert feed.market_summary["us_symbols_count"] >= 8
        assert feed.market_summary["ca_symbols_count"] >= 8

        # Test SPY (US) and XIU (Canada)
        spy_dossier = feed.symbols["SPY"]
        assert spy_dossier.country == "US"
        assert len(spy_dossier.order_book.bids) == 5
        assert len(spy_dossier.order_book.asks) == 5
        assert len(spy_dossier.recent_ticks) > 0

        xiu_dossier = feed.symbols["XIU"]
        assert xiu_dossier.country == "CA"
        assert len(xiu_dossier.order_book.bids) == 5

        # Test serialization
        out_file = microstructure_engine.export_feed(tmp_path, force_refresh=True)
        assert out_file.exists()
        with open(out_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "symbols" in data
        assert "market_summary" in data
        assert "disclaimers" in data

    def test_generate_live_tick(self):
        """Verifies real-time live tick generation for WebSocket/SSE broadcasting."""
        for sym in ["SPY", "SHOP"]:
            tick = microstructure_engine.generate_live_tick(sym)
            assert isinstance(tick, TickMessage)
            assert tick.symbol == sym
            assert tick.price > 0
            assert tick.volume > 0
            assert tick.aggressor_side in ["BUY_AGGRESSOR", "SELL_AGGRESSOR"]
            assert tick.bid - 0.02 <= tick.price <= tick.ask + 0.02

    def test_quant_intel_sentinel_microstructure_predicates(self):
        """Verifies QuantIntelSentinel triggers LIQUIDITY_VOID_SPIKE and MICROSTRUCTURE_SELL_SWEEP."""
        class MockPlan:
            stop_loss = 95.0
            entry_zone_ideal = 100.0
            stop_distance_pct = 5.0
            target_1 = 108.0

        class MockMicroMetrics:
            liquidity_state = "LIQUIDITY_VOID"
            order_book_imbalance = -0.35
            cvd_1m_delta = -1800

        class MockDossier:
            trade_plan = MockPlan()
            layer_1_regime = None
            layer_3_technical = None
            layer_5_sentiment = None
            microstructure_metrics = MockMicroMetrics()

        alerts = quant_intel_sentinel.evaluate_ticker_sentinel(
            symbol="TEST",
            dossier=MockDossier(),
            recent_bars=[{"trading_date": "2026-09-28", "open": 100.0, "high": 102.0, "low": 99.0, "close": 100.5, "volume": 500000}]
        )

        # Check for LIQUIDITY_VOID_SPIKE
        void_alert = next((a for a in alerts if a.predicate_type == "LIQUIDITY_VOID_SPIKE"), None)
        assert void_alert is not None
        assert void_alert.severity == "WARNING"
        assert void_alert.action_required == "USE_LIMIT_ORDERS_MANDATORY"

        # Check for MICROSTRUCTURE_SELL_SWEEP
        sweep_alert = next((a for a in alerts if a.predicate_type == "MICROSTRUCTURE_SELL_SWEEP"), None)
        assert sweep_alert is not None
        assert sweep_alert.severity == "WARNING"
        assert sweep_alert.action_required == "DE_RISK_OR_TIGHTEN_STOP"

        # Verify conversion to formal AlertRecord
        sys_records = quant_intel_sentinel.convert_to_system_alerts([void_alert, sweep_alert])
        assert len(sys_records) == 2
        for r in sys_records:
            linter.assert_clean(r.headline)
            linter.assert_clean(r.body)

    def test_compliance_linter_zero_violations(self):
        """Asserts zero regulatory or promissory violations in microstructure disclaimers."""
        for disc in DISCLAIMERS:
            linter.assert_clean(disc)
