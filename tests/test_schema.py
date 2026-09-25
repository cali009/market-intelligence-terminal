"""
Tests for Canonical Database Schema & Migrations
Verifies table creation, primary keys, and constraint integrity.
"""

import tempfile
from pathlib import Path
from src.data.db import Database


def test_sqlite_migration_execution():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db = Database(db_url=f"sqlite:///{tmp.name}")
        db.run_migrations()

        # Check table presence
        tables_query = "SELECT name FROM sqlite_master WHERE type='table';"
        tables = [row["name"] for row in db.execute_query(tables_query)]

        expected = [
            "security",
            "bar_1d",
            "macro_observation",
            "fundamental_fact",
            "news_event",
            "data_quality_event",
            "score",
            "signal",
            "signal_outcome",
            "consent_record",
            "data_entitlement",
        ]
        for tbl in expected:
            assert tbl in tables

        # Insert a security master record
        sec_id = db.execute_write(
            """
            INSERT INTO security (symbol, exchange, country, currency, name, sector, calendar_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("SU", "TSX", "CA", "CAD", "Suncor Energy Inc.", "Energy", "XTSE"),
        )
        assert sec_id == 1

        rows = db.execute_query("SELECT * FROM security WHERE symbol = ?", ("SU",))
        assert len(rows) == 1
        assert rows[0]["name"] == "Suncor Energy Inc."
