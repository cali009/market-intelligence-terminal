"""
Database Abstraction & Schema Migration Runner
Supports local SQLite / Cloudflare D1 and production PostgreSQL / TimescaleDB.
"""

import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from config.settings import settings, BASE_DIR

MIGRATIONS_DIR = BASE_DIR / "migrations"


class Database:
    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or settings.DATABASE_URL
        self._is_sqlite = self.db_url.startswith("sqlite")
        if self._is_sqlite:
            # Extract path: sqlite:///path/to/db.db
            self.sqlite_path = self.db_url.replace("sqlite:///", "")
            Path(self.sqlite_path).parent.mkdir(parents=True, exist_ok=True)

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
        finally:
            conn.close()

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
        conn = self.get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()


# Global database instance
db = Database()
