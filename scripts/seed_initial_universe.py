"""
Seed Initial Dual-Market Universe
Initializes canonical database and inserts benchmark US and Canadian securities.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.db import db

SEED_SECURITIES = [
    # US Large-Cap Benchmarks & Tech Leaders
    ("AAPL", "NASDAQ", "US", "USD", "Apple Inc.", "Information Technology", "0000320193", "XNYS"),
    ("MSFT", "NASDAQ", "US", "USD", "Microsoft Corporation", "Information Technology", "0000789019", "XNYS"),
    ("NVDA", "NASDAQ", "US", "USD", "NVIDIA Corporation", "Information Technology", "0001045810", "XNYS"),
    ("AMZN", "NASDAQ", "US", "USD", "Amazon.com Inc.", "Consumer Discretionary", "0001018724", "XNYS"),
    ("GOOGL", "NASDAQ", "US", "USD", "Alphabet Inc.", "Communication Services", "0001652044", "XNYS"),
    ("JPM", "NYSE", "US", "USD", "JPMorgan Chase & Co.", "Financials", "0000019617", "XNYS"),
    ("XOM", "NYSE", "US", "USD", "Exxon Mobil Corporation", "Energy", "0000034088", "XNYS"),
    ("SPY", "AMEX", "US", "USD", "SPDR S&P 500 ETF Trust", "Index ETF", None, "XNYS"),
    ("QQQ", "NASDAQ", "US", "USD", "Invesco QQQ Trust", "Index ETF", None, "XNYS"),

    # Canadian TSX Blue-Chip Benchmarks & Cross-Listed
    ("RY", "TSX", "CA", "CAD", "Royal Bank of Canada", "Financials", "0001000275", "XTSE"),
    ("TD", "TSX", "CA", "CAD", "Toronto-Dominion Bank", "Financials", "0000947263", "XTSE"),
    ("SHOP", "TSX", "CA", "CAD", "Shopify Inc.", "Information Technology", "0001594607", "XTSE"),
    ("ENB", "TSX", "CA", "CAD", "Enbridge Inc.", "Energy", "0000895728", "XTSE"),
    ("CNR", "TSX", "CA", "CAD", "Canadian National Railway", "Industrials", "0001086462", "XTSE"),
    ("CNQ", "TSX", "CA", "CAD", "Canadian Natural Resources", "Energy", "0001063259", "XTSE"),
    ("BN", "TSX", "CA", "CAD", "Brookfield Corporation", "Financials", "0001001085", "XTSE"),
    ("BAM", "TSX", "CA", "CAD", "Brookfield Asset Management", "Financials", "0001937968", "XTSE"),
    ("CP", "TSX", "CA", "CAD", "Canadian Pacific Kansas City", "Industrials", "0001686521", "XTSE"),
    ("XIU", "TSX", "CA", "CAD", "iShares S&P/TSX 60 Index ETF", "Index ETF", None, "XTSE"),
]


def seed_database():
    print("Running initial database migrations...")
    db.run_migrations()

    print(f"Seeding {len(SEED_SECURITIES)} initial dual-market securities...")
    for sym, exch, ctry, curr, name, sec, cik, cal in SEED_SECURITIES:
        db.execute_write(
            """
            INSERT INTO security (symbol, exchange, country, currency, name, sector, cik_padded, calendar_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, exchange) DO UPDATE SET
                name=excluded.name,
                sector=excluded.sector,
                cik_padded=excluded.cik_padded
            """,
            (sym, exch, ctry, curr, name, sec, cik, cal),
        )

    rows = db.execute_query("SELECT COUNT(*) as cnt FROM security;")
    print(f"Successfully seeded. Total active securities in master: {rows[0]['cnt']}")


if __name__ == "__main__":
    seed_database()
