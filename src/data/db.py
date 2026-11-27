"""
Database Abstraction & Schema Migration Runner
Supports local SQLite / Cloudflare D1 and production PostgreSQL / TimescaleDB.
"""

import sqlite3
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from config.settings import settings, BASE_DIR

MIGRATIONS_DIR = BASE_DIR / "migrations"


class Database:
    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or settings.DATABASE_URL
        self._is_sqlite = self.db_url.startswith("sqlite")
        # Holds this thread's cached read connection. See _read_connection().
        self._local = threading.local()
        if self._is_sqlite:
            # Extract path: sqlite:///path/to/db.db
            self.sqlite_path = self.db_url.replace("sqlite:///", "")
            Path(self.sqlite_path).parent.mkdir(parents=True, exist_ok=True)

    def _read_connection(self):
        """
        A connection for read queries.

        get_connection() opens a brand-new SQLite connection on every call, then runs
        two PRAGMAs against it. Measured while widening the universe from 19 to 163
        securities: the read path in a full pipeline run issues tens of thousands of
        these, and the connect-plus-PRAGMA cost dominated the queries themselves. Reads
        now reuse one connection per thread. Writes still take their own connection, so
        the write path's transaction and rollback semantics are untouched.

        Thread-local rather than shared, because sqlite3 connections are not safe to
        share across threads at their default check_same_thread setting.
        """
        if not self._is_sqlite:
            return self.get_connection(), True
        local = self._local
        conn = getattr(local, "read_conn", None)
        if conn is None:
            conn = sqlite3.connect(self.sqlite_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            local.read_conn = conn
        return conn, False

    def close_read_connection(self) -> None:
        """Drop this thread's cached read connection, if any."""
        conn = getattr(self._local, "read_conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._local.read_conn = None

    def get_connection(self):
        if self._is_sqlite:
            conn = sqlite3.connect(self.sqlite_path)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute("PRAGMA journal_mode = WAL;")
            return conn
        else:
            # PostgreSQL connection (e.g. psycopg2 / asyncpg)
            import psycopg2
            from psycopg2.extras import RealDictCursor
            return psycopg2.connect(self.db_url, cursor_factory=RealDictCursor)

    def run_migrations(self) -> None:
        """
        Execute migration scripts to ensure all tables, indexes, and constraints exist.
        """
        conn = self.get_connection()
        try:
            if self._is_sqlite:
                migration_file = MIGRATIONS_DIR / "001_initial_sqlite.sql"
            else:
                migration_file = MIGRATIONS_DIR / "001_initial_schema.sql"

            with open(migration_file, "r") as f:
                sql_script = f.read()

            if self._is_sqlite:
                conn.executescript(sql_script)
            else:
                with conn.cursor() as cur:
                    cur.execute(sql_script)
            conn.commit()
            self._ensure_identity_columns(conn)
            conn.commit()
        finally:
            conn.close()

    # Columns added after 001 shipped. The 001 script uses CREATE TABLE IF NOT EXISTS,
    # so it cannot add columns to a database that already exists; ALTER TABLE ADD COLUMN
    # is the only way, and it is not idempotent, hence the PRAGMA guard.
    _IDENTITY_COLUMNS = (
        # The company this symbol is supposed to resolve to, captured from the index
        # constituent list at seed time. Compared against whatever the data endpoint
        # actually returns before a single bar is written.
        ("expected_name", "TEXT"),
        ("expected_calendar", "TEXT"),
        # VERIFIED | MISMATCH | NEEDS_REVIEW | UNVERIFIED
        ("identity_status", "TEXT"),
        ("identity_evidence", "TEXT"),
        ("identity_checked_at", "TEXT"),
    )

    def _ensure_identity_columns(self, conn) -> None:
        if not self._is_sqlite:
            return
        cur = conn.cursor()
        existing = {row[1] for row in cur.execute("PRAGMA table_info(security);")}
        for column, kind in self._IDENTITY_COLUMNS:
            if column not in existing:
                cur.execute("ALTER TABLE security ADD COLUMN %s %s;" % (column, kind))

    def execute_write(self, query: str, params: Tuple = ()) -> int:
        """
        Execute an INSERT/UPDATE/DELETE query and return the lastrowid or rowcount.
        """
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            last_id = cursor.lastrowid
            conn.commit()
            return last_id
        finally:
            conn.close()

    def execute_query(self, query: str, params: Tuple = ()) -> List[Dict[str, Any]]:
        """
        Execute a SELECT query and return a list of dictionaries.
        """
        conn, owned = self._read_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            if owned:
                conn.close()


# Global database instance
db = Database()
