"""
Unit and Integration Tests for Algorithmic Execution Simulator & Transaction Cost Analysis (Phase 25.3)
US + Canada Dual-Market Intelligence Platform
"""

import pytest
from src.compliance.linter import linter
from src.engine.microstructure import microstructure_engine
from src.engine.execution_algo import execution_algo_engine, TCA_DISCLAIMERS
from src.engine.quant_intel import quant_intel_engine, QuantIntelTradePlan
from src.engine.quant_intel_sentinel import quant_intel_sentinel, InvalidationAlert
from src.models.schemas import OrderBookSnapshot, OrderBookLevel, MicrostructureMetrics


class TestExecutionAlgoAndTCA:
    """Test suite validating Almgren-Chriss/Kyle impact models, slicing engines, fee structures, and TCA."""

    @pytest.fixture(autouse=True)
    def setup_dossiers(self):
        self.us_dossier = microstructure_engine.build_symbol_dossier("SPY")
        self.ca_dossier = microstructure_engine.build_symbol_dossier("SHOP")

    def test_direct_market_order_sweep_and_vwap(self):
        """Verify direct market orders correctly sweep DOM depth and calculate volume-weighted fill prices."""
        book = self.us_dossier.order_book
        res = execution_algo_engine.simulate_direct_market_order(
            symbol="SPY",
            side="BUY",
            shares=600,
            book=book,
            country="US",
            kyle_lambda=0.015,
        )
        assert res.strategy == "DIRECT_MARKET"
        assert res.total_shares == 600
        assert res.maker_taker_status == "TAKER"
        assert res.fill_probability_pct >= 99.0
        # Total shares across child slices must equal input shares
        assert sum(s.shares for s in res.child_slices) == 600
        # Buy fill price must be greater than or equal to top ask
        assert res.expected_effective_price >= book.asks[0].price
        # Taker fee must be positive (fee paid)
        assert res.exchange_fees > 0.0

    def test_passive_limit_order_spread_capture_and_rebate(self):
        """Verify limit passive orders capture spread, earn maker rebates, and compute OBI-driven fill probabilities."""
        book = self.us_dossier.order_book
        res = execution_algo_engine.simulate_limit_passive_order(
            symbol="SPY",
            side="BUY",
            shares=300,
            book=book,
            country="US",
            obi=0.25,
            kyle_lambda=0.015,
        )
        assert res.strategy == "LIMIT_PASSIVE"
        assert res.total_shares == 300
        assert res.maker_taker_status == "MAKER"
        # Spread cost must be negative (spread capture benefit)
        assert res.spread_cost < 0.0
        # Net maker exchange fees must be negative (rebate earned)
        assert res.exchange_fees < 0.0
        # Fill probability must be within realistic bounds [25%, 95%]
        assert 25.0 <= res.fill_probability_pct <= 95.0

    def test_twap_order_uniform_slicing_and_invariants(self):
        """Verify TWAP uniform slicing maintains share count invariants and monotonic time offsets."""
        book = self.us_dossier.order_book
        total_shares = 750
        num_slices = 5
        res = execution_algo_engine.simulate_twap_order(
            symbol="SPY",
            side="BUY",
            shares=total_shares,
            book=book,
            country="US",
            kyle_lambda=0.015,
            num_slices=num_slices,
            horizon_mins=15,
        )
        assert res.strategy == "TWAP"
        assert len(res.child_slices) == num_slices
        assert sum(s.shares for s in res.child_slices) == total_shares
        # Offsets must be strictly increasing
        offsets = [s.offset_sec for s in res.child_slices]
        assert offsets == sorted(offsets)
        assert offsets[0] == 0

    def test_vwap_u_curve_volume_weighting(self):
        """Verify VWAP allocates heavier child order weights at market open and close bins."""
        book = self.ca_dossier.order_book
        total_shares = 1000
        res = execution_algo_engine.simulate_vwap_order(
            symbol="SHOP",
            side="BUY",
            shares=total_shares,
            book=book,
            country="CA",
            kyle_lambda=0.020,
            horizon_mins=30,
        )
        assert res.strategy == "VWAP"
        assert sum(s.shares for s in res.child_slices) == total_shares
        slices = res.child_slices
        # First (Open) and last (Close) slices must have larger share allocations than midday (index 2)
        assert slices[0].shares > slices[2].shares
        assert slices[-1].shares > slices[2].shares

    def test_pov_iceberg_participation_capping(self):
        """Verify POV dynamic slicing maintains target participation rate and share sum conservation."""
        book = self.us_dossier.order_book
        total_shares = 1200
        res = execution_algo_engine.simulate_pov_order(
            symbol="SPY",
            side="BUY",
            shares=total_shares,
            book=book,
            country="US",
            kyle_lambda=0.015,
            target_participation=0.10,
        )
        assert res.strategy == "POV_10"
        assert sum(s.shares for s in res.child_slices) == total_shares
        assert len(res.child_slices) >= 2

    def test_dual_market_fee_schedules_us_vs_ca(self):
        """Verify US Reg NMS vs Canadian TSX maker/taker fee rates differ according to respective market structures."""
        shares = 1000
        us_taker = execution_algo_engine.get_exchange_fee(shares, country="US", is_maker=False)
        ca_taker = execution_algo_engine.get_exchange_fee(shares, country="CA", is_maker=False)
        us_maker = execution_algo_engine.get_exchange_fee(shares, country="US", is_maker=True)
        ca_maker = execution_algo_engine.get_exchange_fee(shares, country="CA", is_maker=True)

        # US taker fee (~$3.17) is higher than CA taker fee (~$1.75)
        assert us_taker > ca_taker > 0.0
        # US maker rebate (~-$1.83) gives a larger credit than CA maker rebate (~-$0.75)
        assert us_maker < ca_maker < 0.0

    def test_evaluate_all_strategies_and_recommendation(self):
        """Verify multi-strategy evaluation produces valid comparisons and quantitative routing rationales."""
        book = self.us_dossier.order_book
        metrics = self.us_dossier.metrics
        res = execution_algo_engine.evaluate_all_strategies(
            symbol="SPY",
            side="BUY",
            shares=400,
            book=book,
            metrics=metrics,
            country="US",
        )
        assert "recommended_strategy" in res
        assert res["recommended_strategy"] in ("DIRECT_MARKET", "LIMIT_PASSIVE", "TWAP", "VWAP", "POV_10")
        assert len(res["strategies"]) == 5
        assert len(res["summary_comparison"]) == 5
        for s in res["summary_comparison"]:
            assert s["effective_price"] > 0.0
            assert "slippage_bps" in s
            assert "total_cost" in s

    def test_quant_intel_trade_plan_tca_integration(self):
        """Verify QUANT INTEL trade plan and formatted institutional card include TCA execution parameters."""
        dossier = quant_intel_engine.evaluate_ticker("NVDA")
        plan = dossier.trade_plan
        assert plan.tca_execution_strategy in ("DIRECT_MARKET", "LIMIT_PASSIVE", "TWAP", "VWAP", "POV_10")
        assert plan.tca_effective_fill_price > 0.0
        assert plan.tca_optimal_slices >= 1
        assert "Execution TCA:" in dossier.formatted_card

    def test_quant_intel_sentinel_slippage_warning_predicate(self):
        """Verify Sentinel triggers EXECUTION_SLIPPAGE_WARNING when projected slippage exceeds 15 bps."""
        class MockPlan:
            entry_zone_ideal = 100.0
            entry_zone_max = 102.0
            stop_loss = 94.0
            stop_distance_pct = 6.0
            target_1 = 110.0
            tca_execution_strategy = "VWAP"
            tca_expected_slippage_bps = 22.5  # High slippage trigger > 15.0 bps

        class MockDossier:
            symbol = "ILLIQ"
            trade_plan = MockPlan()
            layer_1_regime = None
            layer_3_technical = None
            layer_5_sentiment = None
            microstructure_metrics = None

        mock_bars = [{"trading_date": "2026-09-28", "close": 100.0, "open": 100.0, "high": 100.0, "low": 100.0, "volume": 1000}]
        alerts = quant_intel_sentinel.evaluate_ticker_sentinel("ILLIQ", MockDossier(), mock_bars)
        types = [a.predicate_type for a in alerts]
        assert "EXECUTION_SLIPPAGE_WARNING" in types
        alert = next(a for a in alerts if a.predicate_type == "EXECUTION_SLIPPAGE_WARNING")
        assert alert.action_required == "ROUTE_VIA_ALGO_SLICING"
        assert alert.trigger_level == 22.5

    def test_compliance_linter_zero_violations(self):
        """Verify all execution disclaimers, rationales, and TCA outputs adhere strictly to CSA 31-369 and SEC rules."""
        for disc in TCA_DISCLAIMERS:
            assert len(linter.lint_text(disc)) == 0

        res = execution_algo_engine.evaluate_all_strategies(
            symbol="SHOP",
            side="BUY",
            shares=500,
            book=self.ca_dossier.order_book,
            metrics=self.ca_dossier.metrics,
            country="CA",
        )
        assert len(linter.lint_text(res["recommendation_rationale"])) == 0
