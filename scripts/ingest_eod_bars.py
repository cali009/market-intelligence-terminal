"""
Script to Ingest EOD Bars for Seeded Dual-Market Universe
Fetches 1 year of historical OHLCV bars and populates bar_1d table.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.db import db
from src.data.bar_ingestion import bar_engine


def run_ingestion():
    securities = db.execute_query("SELECT security_id, symbol, exchange, name FROM security WHERE is_active = 1;")
    print(f"Starting EOD bar ingestion for {len(securities)} active dual-market securities...")

    total_bars_saved = 0
    for s in securities:
        sec_id = s["security_id"]
        sym = s["symbol"]
        exch = s["exchange"]
        name = s["name"]

        try:
            print(f"  Fetching bars for {sym} ({exch} - {name})...", end="", flush=True)
            bars_count = bar_engine.sync_security_bars(
                security_id=sec_id,
                symbol=sym,
                exchange=exch,
                period="1y",
            )
            print(f" OK ({bars_count} bars)")
            total_bars_saved += bars_count
        except Exception as e:
            print(f" FAILED ({e})")

    total_in_db = db.execute_query("SELECT COUNT(*) as cnt FROM bar_1d;")[0]["cnt"]
    print(f"\nIngestion Complete: {total_bars_saved} bars processed. Total bars in database: {total_in_db}")


if __name__ == "__main__":
    run_ingestion()
