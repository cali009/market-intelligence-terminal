"""
Master Backtesting & Walk-Forward Validation Runner
US + Canada Market Intelligence Platform
Executes systematic event-driven historical simulations across multiple strategy hypotheses:
1. 8-Factor Composite Ensemble
2. TAC-01 Pullback to Key Support
3. TAC-02 Volatility Squeeze Expansion
4. TAC-04 Momentum Leader Breakout
Outputs pre-computed performance profiles and equity curves to data/feeds/backtests.json.
"""

import json
import time
from pathlib import Path
from datetime import date, datetime, timezone

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import DATA_DIR
from src.engine.backtester import backtest_engine
from src.engine.cpcv_engine import cpcv_engine
from src.compliance.disclaimers import HYPOTHETICAL_BACKTEST_DISCLAIMER, DISCLAIMER_VERSION

FEEDS_DIR = DATA_DIR / "feeds"
DIST_DIR = DATA_DIR / "dist"


def run_all_backtests():
    start_time = time.monotonic()
    today_str = date.today().isoformat()
    generated_at = datetime.now(timezone.utc).isoformat()

    print(f"\n{'='*95}")
    print(f" US + CANADA STRATEGY BACKTESTING & WALK-FORWARD VALIDATION ENGINE")
    print(f" Execution Date: {today_str} | Strict Anti-Lookahead Protocol")
    print(f"{'='*95}\n")

    strategies = ["ENSEMBLE_8F", "TAC01_PULLBACK", "TAC02_SQUEEZE", "TAC04_MOMENTUM", "TAC03_BREAKOUT_CHASE"]
    results = {}

    for strat_id in strategies:
        s_start = time.monotonic()
        print(f">>> Running walk-forward simulation for: {strat_id}...")
        res = backtest_engine.run_strategy_backtest(strat_id)
        m = res["metrics"]
        elapsed_s = time.monotonic() - s_start
        status_tag = f"[{res.get('status', 'ACTIVE')}]"
        print(f"    ✓ {status_tag} {res['strategy_name']} ({elapsed_s:.2f}s):")
        print(f"      Trades: {m['total_trades']} | Win Rate: {m['win_rate_pct']}% | Net Return: {m['total_net_return_pct']}% | Sharpe: {m['sharpe_ratio']} | DSR: {m.get('deflated_sharpe_ratio')} | PBO: {m.get('probability_backtest_overfitting')}")
        results[strat_id] = res

    bundle = {
        "as_of_date": today_str,
        "generated_at": generated_at,
        "strategy_registry": backtest_engine.get_strategy_registry(),
        "strategies": results,
        "default_strategy": "ENSEMBLE_8F",
        "benchmark_symbol": "SPY",
        "cpcv_validation": cpcv_engine.generate_feed().to_dict(),
        "statutory_disclaimer": HYPOTHETICAL_BACKTEST_DISCLAIMER,
        "disclaimer_version": DISCLAIMER_VERSION,
    }

    # Ensure directories exist
    FEEDS_DIR.mkdir(parents=True, exist_ok=True)
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    for d in [FEEDS_DIR, DIST_DIR]:
        with open(d / "backtests.json", "w") as f:
            json.dump(bundle, f, indent=2)

    total_time = time.monotonic() - start_time
    print(f"\n{'='*95}")
    print(f" BACKTESTING COMPLETE: 4 strategies evaluated in {total_time:.2f} seconds.")
    print(f" Performance bundle saved to data/feeds/backtests.json and data/dist/backtests.json.")
    print(f"{'='*95}\n")

    return bundle


if __name__ == "__main__":
    run_all_backtests()
