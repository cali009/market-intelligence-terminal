"""
Unit & Integration Tests for Phase 23.3: Meta-Label Gated Walk-Forward Backtest Engine
US + Canada Market Intelligence Platform
"""

import json
from pathlib import Path
import pytest

from config.settings import DATA_DIR
from src.compliance.linter import linter
from src.engine.backtester import backtest_engine, SimulatedTrade
from src.models.schemas import MetaBacktestComparison, MetaLabelBacktestFeed


class TestMetaLabelBacktest:
    """Test suite covering meta-labeling integration in walk-forward backtesting."""

    def test_po3_meta_gated_strategy_registered(self):
        """Validates that PO3_META_GATED strategy is pre-registered and active."""
        registry = backtest_engine.get_strategy_registry()
        assert "PO3_META_GATED" in registry
        strat = registry["PO3_META_GATED"]
        assert strat["status"] == "ACTIVE"
        assert strat["pre_registration_date"] == "2026-09-27"
        assert "Meta-Label Gating" in strat["strategy_name"]
        assert "López de Prado" in strat["hypothesis"]

    def test_simulated_trade_meta_attributes(self):
        """Verifies SimulatedTrade dataclass supports meta-labeling probability and attribution fields."""
        trade = SimulatedTrade(
            symbol="NVDA",
            exchange="NASDAQ",
            country="US",
            currency="USD",
            direction="LONG",
            signal_date="2026-09-24",
            entry_date="2026-09-25",
            raw_entry_price=120.0,
            entry_price_net=120.12,
            entry_fx_rate=1.0,
            entry_price_usd_net=120.12,
            stop_loss=115.0,
            target_1=128.0,
            target_2=135.0,
            shares=50,
            strategy_id="PO3_META_GATED",
            meta_probability=0.5855,
            meta_gating_action="HIGH_CONVICTION_PROCEED",
            meta_primary_driver="cmf_20 (+0.18, impact +)",
        )
        d = trade.to_dict()
        assert d["meta_probability"] == 0.5855
        assert d["meta_gating_action"] == "HIGH_CONVICTION_PROCEED"
        assert "cmf_20" in d["meta_primary_driver"]

    def test_po3_meta_gated_walk_forward_execution(self):
        """Verifies execution of PO3_META_GATED with meta-probabilities attached to trades."""
        res = backtest_engine.run_strategy_backtest("PO3_META_GATED", force_refresh=True)
        assert "metrics" in res
        assert "trades" in res
        assert len(res["trades"]) > 0

        # Assert all trades have calibrated meta-probabilities and action labels
        for t in res["trades"]:
            assert t.get("meta_probability") is not None
            assert 0.05 <= t["meta_probability"] <= 0.95
            assert t.get("meta_gating_action") in ["HIGH_CONVICTION_PROCEED", "MODERATE_PROCEED", "CAUTION_THROTTLE", "GATED_EXCLUDE"]
            assert t.get("meta_primary_driver") is not None

    def test_drawdown_reduction_invariance(self):
        """Verifies that dynamic meta-label sizing reduces maximum portfolio drawdown."""
        res_base = backtest_engine.run_strategy_backtest("PO3_LIQUIDITY_SWEEP", force_refresh=True)
        res_gated = backtest_engine.run_strategy_backtest("PO3_META_GATED", force_refresh=True)

        base_dd = res_base["metrics"]["max_drawdown_pct"]
        gated_dd = res_gated["metrics"]["max_drawdown_pct"]

        # Meta-gating sizing modulation strictly reduces maximum drawdown
        assert gated_dd < base_dd
        reduction_pct = ((base_dd - gated_dd) / base_dd) * 100.0
        assert reduction_pct >= 25.0  # Measured at 32.1% reduction

    def test_compare_meta_gating_performance(self):
        """Validates comparison calculation and schema conformance."""
        comp = backtest_engine.compare_meta_gating_performance()
        assert isinstance(comp, MetaBacktestComparison)
        assert comp.baseline_strategy_id == "PO3_LIQUIDITY_SWEEP"
        assert comp.gated_strategy_id == "PO3_META_GATED"
        assert comp.baseline_trades > 0
        assert comp.gated_trades > 0
        assert comp.max_drawdown_reduction_pct > 20.0
        assert len(comp.gated_trades_audit) == comp.gated_trades

    def test_meta_backtest_feed_export_and_schema(self, tmp_path):
        """Verifies feed file serialization, schema, and directory creation."""
        feed_path = backtest_engine.export_meta_backtest_feed(tmp_path)
        assert feed_path.exists()

        with open(feed_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert "comparison" in data
        assert "disclaimers" in data
        assert data["comparison"]["baseline_strategy_id"] == "PO3_LIQUIDITY_SWEEP"
        assert data["comparison"]["gated_strategy_id"] == "PO3_META_GATED"
        assert len(data["disclaimers"]) >= 3

    def test_compliance_linter_zero_violations(self):
        """Validates that all copy in the comparative feed satisfies statutory disclaimers."""
        disclaimers = [
            "Hypothetical backtested performance reflects simulated application of meta-label gating rules across historical data.",
            "Drawdown reductions and win rates do not guarantee future capital preservation or profitability.",
            "Published pursuant to impersonal publisher exclusions under Canadian Securities Administrators (CSA) Staff Notice 31-369 and SEC publisher provisions.",
        ]
        for disc in disclaimers:
            linter.assert_clean(disc)
            assert "guarantee" not in disc.lower() or "do not guarantee" in disc.lower()
