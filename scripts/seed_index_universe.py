"""
Seed the S&P 100 and S&P/TSX 60 constituent universe.

Source of the constituent lists
-------------------------------
data/universe/index_constituents_2026-11-22.json, fetched 2026-11-22 from the Wikipedia
REST API pages "S&P 100" (Components table) and "S&P/TSX 60" (Constituents table, which
cites TMX as of 2026-01-31). Company names are stored as expected_name so that bar
ingestion can prove the endpoint returned the security we think we asked for.

Ticker spelling
---------------
Multi-class tickers use a hyphen, not a dot, because that is the form the data endpoint
answers to. Verified individually on 2026-11-22:

    BRK.B   -> BRK-B        Berkshire Hathaway Inc.
    CCL.B   -> CCL-B.TO     CCL Industries Inc.
    GIB.A   -> GIB-A.TO     CGI Inc.
    RCI.B   -> RCI-B.TO     Rogers Communications Inc.
    TECK.B  -> TECK-B.TO    Teck Resources Limited
    CTC.A   -> CTC-A.TO     Canadian Tire Corporation, Limited
    BIP.UN  -> BIP-UN.TO    Brookfield Infrastructure Partners L.P.

Running this script is safe to repeat: it updates names and sectors in place and never
touches bars or identity verdicts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.db import db

CONSTITUENTS_FILE = Path(__file__).resolve().parent.parent / "data" / "universe" / \
    "index_constituents_2026-11-22.json"

# Index ticker as published -> ticker the data endpoint answers to.
TICKER_REMAP = {
    "BRK.B": "BRK-B",
    "CCL.B": "CCL-B",
    "GIB.A": "GIB-A",
    "RCI.B": "RCI-B",
    "TECK.B": "TECK-B",
    "CTC.A": "CTC-A",
    "BIP.UN": "BIP-UN",
}

# GICS sector as the index lists it -> the vocabulary already used in this database.
SECTOR_MAP = {
    "Information Technology": "Information Technology",
    "Health Care": "Health Care",
    "Financials": "Financials",
    "Financial Services": "Financials",
    "Consumer Discretionary": "Consumer Discretionary",
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Staples": "Consumer Staples",
    "Communication Services": "Communication Services",
    "Industrials": "Industrials",
    "Energy": "Energy",
    "Utilities": "Utilities",
    "Real Estate": "Real Estate",
    "Materials": "Materials",
    "Basic Materials": "Materials",
}

# Benchmarks the platform tracked before the constituent universe existed. They are not
# index constituents, so they need their own expectations; without one the identity gate
# records UNVERIFIED and, correctly, proves nothing.
BENCHMARK_EXPECTATIONS = {
    "SPY": ("SPDR S&P 500 ETF Trust", "XNYS"),
    "QQQ": ("Invesco QQQ Trust", "XNYS"),
    "XIU": ("iShares S&P/TSX 60 Index ETF", "XTSE"),
}

MARKETS = {
    "sp100": dict(exchange="NASDAQ", country="US", currency="USD", calendar="XNYS",
                  per_symbol_exchange=None),
    "tsx60": dict(exchange="TSX", country="CA", currency="CAD", calendar="XTSE",
                  per_symbol_exchange="TSX"),
}


def seed() -> dict:
    db.run_migrations()
    doc = json.loads(CONSTITUENTS_FILE.read_text())
    inserted = updated = skipped = 0

    for key, market in MARKETS.items():
        for entry in doc.get(key, []):
            raw_symbol = str(entry["symbol"]).strip().upper()
            symbol = TICKER_REMAP.get(raw_symbol, raw_symbol)
            name = str(entry["name"]).strip()
            sector = SECTOR_MAP.get(str(entry.get("sector", "")).strip(),
                                    entry.get("sector") or None)

            existing = db.execute_query(
                "SELECT security_id, name, sector, expected_name FROM security "
                "WHERE symbol = ? LIMIT 1;", (symbol,))

            if existing:
                # Keep the exchange the row was seeded with; SPY on AMEX and QQQ on
                # NASDAQ are both correct and the exchange is part of the unique key.
                db.execute_write(
                    "UPDATE security SET sector = COALESCE(?, sector), "
                    "expected_name = COALESCE(expected_name, ?), "
                    "expected_calendar = COALESCE(expected_calendar, ?) "
                    "WHERE symbol = ?;",
                    (sector, name, market["calendar"], symbol))
                updated += 1
                continue

            db.execute_write(
                """
                INSERT INTO security (symbol, exchange, country, currency, name, sector,
                                      calendar_id, is_active, expected_name,
                                      expected_calendar, identity_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'UNVERIFIED')
                ON CONFLICT(symbol, exchange) DO UPDATE SET
                    name = excluded.name,
                    sector = COALESCE(excluded.sector, security.sector),
                    expected_name = excluded.expected_name,
                    expected_calendar = excluded.expected_calendar
                """,
                (symbol, market["exchange"], market["country"], market["currency"],
                 name, sector, market["calendar"], name, market["calendar"]))
            inserted += 1

    for symbol, (name, calendar) in BENCHMARK_EXPECTATIONS.items():
        db.execute_write(
            "UPDATE security SET expected_name = COALESCE(expected_name, ?), "
            "expected_calendar = COALESCE(expected_calendar, ?) WHERE symbol = ?;",
            (name, calendar, symbol))

    total = db.execute_query("SELECT COUNT(*) AS c FROM security WHERE is_active = 1;")[0]["c"]
    return {"inserted": inserted, "updated": updated, "skipped": skipped, "total_active": total}


if __name__ == "__main__":
    result = seed()
    print("Seeded S&P 100 + S&P/TSX 60 constituent universe")
    for k, v in result.items():
        print("  %-14s %s" % (k + ":", v))
