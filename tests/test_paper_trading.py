"""
Unit & Integration Tests: Paper Trading Engine & Resolution Tracking (Phase 8)
US + Canada Market Intelligence Platform

Verifies:
- Virtual execution portfolio ($100,000 USD initial capital)
- Realistic execution fill modeling: T+1 Next-bar Open fill + 10 bps friction/side
- Resolution tracking: Maximum Favorable Excursion (MFE), Maximum Adverse Excursion (MAE),
  Realized R-multiple, and excursion efficiency ratio
- Parallel randomized-entry control group benchmark to establish empirical zero-alpha threshold (H0)
- Degradation monitor vs backtest expectancy, enforcing 40% kill criteria (§7.12)
- DoD Promotion Gate: At least 1 strategy promoted to LIVE_QUALIFIED (ENSEMBLE_8F)
- Feed export & schema compliance for edge distribution
"""

import json
import pytest
from src.engine.paper_trading import paper_trading_engine
from src.models.schemas import PaperTradeRecord, StrategyPaperScorecard


def test_paper_trading_execution_and_schema():
    """Verify that paper trading simulation generates valid trade records and scorecards."""
    feed = paper_trading_engine.get_paper_trading_feed()
    assert "scorecards" in feed
    assert "all_trades" in feed
    assert len(feed["scorecards"]) >= 5
    assert len(feed["all_trades"]) > 50

    # Test schema deserialization of a trade record
    first_trade = feed["all_trades"][0]
    trade_obj = PaperTradeRecord(**first_trade)
    assert trade_obj.symbol == first_trade["symbol"]
    assert trade_obj.shares > 0
    assert trade_obj.entry_price > 0
    assert trade_obj.status == "CLOSED"


def test_next_bar_open_fill_modeling_and_friction():
    """Verify that trades execute strictly after signal bar with 10 bps friction applied."""
    feed = paper_trading_engine.get_paper_trading_feed()
    trades = [t for t in feed["all_trades"] if not t.get("is_random_control", False)]

    assert len(trades) > 0
    for t in trades[:15]:
        assert t["entry_date"] > t["signal_date"], f"Lookahead fill detected: {t['entry_date']} <= {t['signal_date']}"
        # For LONG trades, entry price net should be higher than raw open due to slippage/commission
        assert t["entry_price_net"] > t["entry_price"], "Entry friction was not added to entry price"
        # Exit price net should be lower than exit price due to friction
        assert t["exit_price_net"] < t["exit_price"], "Exit friction was not deducted from exit price"
        # Total fee drag must be positive
        assert t["fee_drag_usd"] > 0, "Fee drag must be strictly positive"


def test_resolution_mfe_mae_tracking():
    """Verify that MFE, MAE, and Realized R are tracked throughout active trade duration."""
    feed = paper_trading_engine.get_paper_trading_feed()
    trades = feed["all_trades"]

    for t in trades[:15]:
        assert "mfe_pct" in t and t["mfe_pct"] >= 0, "MFE % must be non-negative"
        assert "mae_pct" in t and t["mae_pct"] >= 0, "MAE % must be non-negative"
        assert "mfe_r" in t and t["mfe_r"] >= 0, "MFE R must be non-negative"
        assert "mae_r" in t and t["mae_r"] >= 0, "MAE R must be non-negative"
        assert "r_multiple" in t, "Realized R-multiple must be tracked"
        assert t["holding_days"] >= 1, "Holding days must be at least 1 session"

    summary = feed["resolution_mfe_mae_summary"]
    assert "avg_mfe_r" in summary
    assert "avg_mae_r" in summary
    assert "aggregate_efficiency_ratio" in summary
    assert 0.0 <= summary["aggregate_efficiency_ratio"] <= 1.0


def test_random_control_group_benchmark():
    """Verify parallel randomized-entry control group benchmark establishing zero-alpha baseline."""
    feed = paper_trading_engine.get_paper_trading_feed()
    rc = feed["random_control_group"]

    assert rc["total_control_trades"] > 0
    assert "win_rate_pct" in rc
    assert "expectancy_r" in rc
    assert "methodology" in rc
    # Expectancy for random control should be close to zero (between -0.5R and +0.5R)
    assert -0.5 <= rc["expectancy_r"] <= 0.5


def test_degradation_monitor_and_kill_criteria():
    """Verify degradation monitoring comparing out-of-sample paper expectancy vs backtest."""
    feed = paper_trading_engine.get_paper_trading_feed()
    scorecards = {sc["strategy_id"]: sc for sc in feed["scorecards"]}

    # Ensemble should not be degraded
    ens = scorecards["ENSEMBLE_8F"]
    assert ens["expectancy_r"] > ens["backtest_expectancy_r"]
    assert ens["degradation_pct"] <= 0.0, "Ensemble outperformed backtest, degradation should be <= 0"

    # Breakout chase should show failure
    chase = scorecards["TAC03_BREAKOUT_CHASE"]
    assert chase["status"] == "RETIRED"
    assert chase["win_rate_pct"] < 50.0
    assert "disqualified" in chase["promotion_verdict"].lower() or "retired" in chase["promotion_verdict"].lower()


def test_dod_promotion_gate():
    """Verify Definition of Done: at least 1 strategy promoted to LIVE_QUALIFIED."""
    feed = paper_trading_engine.get_paper_trading_feed()
    scorecards = feed["scorecards"]

    promoted = [s for s in scorecards if s["status"] == "LIVE_QUALIFIED"]
    assert len(promoted) >= 1, f"DoD requires at least 1 strategy promoted to LIVE_QUALIFIED, got {len(promoted)}"
    assert promoted[0]["strategy_id"] == "ENSEMBLE_8F"
    assert "PROMOTED TO LIVE" in promoted[0]["promotion_verdict"]

    retired = [s for s in scorecards if s["status"] == "RETIRED"]
    assert len(retired) >= 1, f"Expected at least 1 strategy marked RETIRED, got {len(retired)}"
    assert retired[0]["strategy_id"] == "TAC03_BREAKOUT_CHASE"


def test_paper_trading_feed_file_export():
    """Verify that paper trading feed is successfully exported to disk and matches schema."""
    with open("data/feeds/paper_trading.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["virtual_capital_usd"] == 100000.0
    assert data["total_paper_trades"] >= 50
    assert len(data["scorecards"]) >= 5
    assert len(data["disclaimers"]) >= 2
