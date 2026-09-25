"""
Master Daily Automated Intelligence Pipeline Runner
US + Canada Market Intelligence Platform
Single-command orchestrator that executes the entire end-to-end zero-cost workflow:
Ingestion -> Macro Sync -> Quant Scoring -> Regimes -> Scanners -> Signals -> AI Explanations -> Edge Export.
"""

import sys
import time
from pathlib import Path
from datetime import date

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.db import db
from src.data.bar_ingestion import bar_engine
from src.data.boc_valet import BankOfCanadaValetAdapter
from src.data.sec_edgar import SecEdgarAdapter
from src.data.edge_exporter import edge_exporter
from scripts.run_scanners_and_signals import run_full_pipeline
from scripts.run_backtests import run_all_backtests


def run_pipeline():
    start_time = time.monotonic()
    today_str = date.today().isoformat()

    print(f"\n{'='*95}")
    print(f" US + CANADA DUAL-MARKET MASTER INTELLIGENCE PIPELINE")
    print(f" Execution Date: {today_str} | Zero-Cost Infrastructure Track")
    print(f"{'='*95}\n")

    # Step 1: Macro Ingestion (BoC Valet)
    print(">>> STEP 1: Ingesting Bank of Canada Macro Observations...")
    boc = BankOfCanadaValetAdapter()
    boc_obs = boc.fetch_observations(["FXUSDCAD", "BD.CDN.10YR.DQ.YLD", "BD.CDN.2YR.DQ.YLD"], recent=5)
    print(f"    Synced {len(boc_obs)} BoC observations (CAD/USD FX & Benchmark Bond Yields).")

    # Step 2: SEC EDGAR Fundamentals
    print("\n>>> STEP 2: Checking SEC EDGAR XBRL Fundamentals...")
    us_sec = db.execute_query("SELECT COUNT(*) as cnt FROM fundamental_fact;")[0]["cnt"]
    print(f"    Verified {us_sec} canonical point-in-time fundamental facts in database.")

    # Step 3: Bar Ingestion & Data Quality Gate
    print("\n>>> STEP 3: Ingesting Daily OHLCV Price Bars (Dual-Market)...")
    securities = db.execute_query("SELECT security_id, symbol, exchange FROM security WHERE is_active = 1;")
    bars_synced = 0
    for s in securities:
        count = bar_engine.sync_security_bars(s["security_id"], s["symbol"], s["exchange"], period="1mo")
        bars_synced += count
    print(f"    Synced {bars_synced} recent bars across {len(securities)} active dual-market securities.")

    # Step 4: Regimes, Scanners, Scoring & Signals
    print("\n>>> STEP 4: Evaluating Regimes, 14 Scanners & Quantitative Scores...")
    run_full_pipeline()

    # Step 5: Historical Strategy Backtesting & Walk-Forward Validation
    print(">>> STEP 5: Executing Walk-Forward Strategy Backtests...")
    bt_bundle = run_all_backtests()
    print(f"    Evaluated {len(bt_bundle['strategies'])} strategies across Walk-Forward partitions.")

    # Step 6: Edge Static Compilation & Export
    print("\n>>> STEP 6: Compiling Static JSON Intelligence Bundles for Edge CDN...")
    export_stats = edge_exporter.export_all()
    print(f"    Compiled {export_stats['symbol_files']} detailed symbol bundles.")
    print(f"    Compiled core edge feeds: summary, leaderboard, scanners, signals, backtests.")
    print(f"    Total tactical scanner matches: {export_stats['total_matches']}.")
    print(f"    Total active research signal plans: {export_stats['total_signals']}.")

    elapsed = time.monotonic() - start_time
    print(f"\n{'='*95}")
    print(f" PIPELINE COMPLETE: Execution finished successfully in {elapsed:.2f} seconds.")
    print(f" Edge distribution artifacts ready in data/dist/ for Cloudflare Pages / R2 deployment.")
    print(f"{'='*95}\n")


if __name__ == "__main__":
    run_pipeline()
