"""
CBOE Volatility Index (^VIX) ingestion.

HONESTY OVERHAUL (Phase 3) — before this module existed there was no VIX data
anywhere in the platform. Two numbers stood in for it:

  * src/engine/macro_regime.py used `default_vix = 16.0` as the volatility input
    to every historical regime classification, for every date, in both markets.
  * src/engine/alerts.py emitted an alert reading "VIX climbed to 21.4" with
    `"vix": 21.4` in its payload. That alert was a typed-in literal, not an
    observation, and it contradicted the 16.0 the regime classifier used.

Both are replaced by a real fetch. The index is stored in its own table rather
than in bar_1d so that the 19-symbol equity universe — and every count, coverage
figure and survivorship statement derived from it — stays accurate.

Licensing note: Cboe VIX index levels are licensed commercial index data. This
dashboard is a personal research tool that displays a single level with
attribution and does not redistribute a data service. Nothing here is a
commercial redistribution, and no claim of a Cboe data agreement is made
anywhere in the codebase.

Canadian volatility: ^VIXC is not available from this source (HTTP 404, verified
2026-09-24). There is therefore no Canadian volatility index in the platform.
Where Canadian regime classification needs a volatility input it receives the US
VIX and the result is labelled as a proxy, because pretending a Canadian index
exists would be the same mistake this module removes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from src.data.db import db

CHART_API_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
VIX_SYMBOL = "^VIX"
VIX_QUERY_SYMBOL = "%5EVIX"
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS volatility_index_daily (
    index_symbol TEXT NOT NULL,
    trading_date TEXT NOT NULL,
    close        REAL NOT NULL,
    knowledge_at TEXT NOT NULL,
    PRIMARY KEY (index_symbol, trading_date)
);
"""


class VolatilityIndexEngine:
    """Fetches and stores the CBOE Volatility Index daily close series."""

    def __init__(self) -> None:
        db.execute_write(_CREATE_SQL)

    # ------------------------------------------------------------------
    def fetch_series(self, period: str = "2y") -> List[Dict[str, Any]]:
        """Fetch the daily close series for ^VIX from the chart API."""
        url = f"{CHART_API_BASE}/{VIX_QUERY_SYMBOL}"
        resp = requests.get(
            url, headers=REQUEST_HEADERS, params={"range": period, "interval": "1d"}, timeout=20
        )
        resp.raise_for_status()
        payload = resp.json()

        result = (payload.get("chart") or {}).get("result") or []
        if not result:
            raise ValueError("No chart data returned for ^VIX")

        result = result[0]
        timestamps = result.get("timestamp") or []
        closes = ((result.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []

        rows: List[Dict[str, Any]] = []
        for i, ts in enumerate(timestamps):
            if i >= len(closes) or closes[i] is None:
                continue
            rows.append({
                "trading_date": datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat(),
                "close": float(closes[i]),
            })
        if not rows:
            raise ValueError("No valid ^VIX closes parsed")
        return rows

    # ------------------------------------------------------------------
    def sync(self, period: str = "2y") -> int:
        """Fetch and upsert the ^VIX series. Returns the number of rows written."""
        rows = self.fetch_series(period=period)
        now = datetime.now(timezone.utc).isoformat()
        for r in rows:
            db.execute_write(
                """
                INSERT INTO volatility_index_daily (index_symbol, trading_date, close, knowledge_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(index_symbol, trading_date)
                DO UPDATE SET close = excluded.close, knowledge_at = excluded.knowledge_at;
                """,
                (VIX_SYMBOL, r["trading_date"], r["close"], now),
            )
        return len(rows)

    # ------------------------------------------------------------------
    def latest(self) -> Optional[Dict[str, Any]]:
        """The most recent stored ^VIX close, or None if nothing is stored.

        Callers must handle None. There is deliberately no fallback constant:
        returning 16.0 here would recreate the fabrication this module exists to
        remove.
        """
        rows = db.execute_query(
            """
            SELECT trading_date, close, knowledge_at
            FROM volatility_index_daily
            WHERE index_symbol = ?
            ORDER BY trading_date DESC
            LIMIT 1;
            """,
            (VIX_SYMBOL,),
        )
        return dict(rows[0]) if rows else None

    # ------------------------------------------------------------------
    def series(self, limit: int = 500) -> List[Dict[str, Any]]:
        """Stored ^VIX closes, oldest first."""
        rows = db.execute_query(
            """
            SELECT trading_date, close
            FROM volatility_index_daily
            WHERE index_symbol = ?
            ORDER BY trading_date DESC
            LIMIT ?;
            """,
            (VIX_SYMBOL, limit),
        )
        return [{"trading_date": r["trading_date"], "close": r["close"]} for r in reversed(rows)]

    # ------------------------------------------------------------------
    def lookup(self) -> Dict[str, float]:
        """{trading_date: close} for date-aligned joins."""
        return {r["trading_date"]: r["close"] for r in self.series(limit=5000)}

    # ------------------------------------------------------------------
    def age_hours(self) -> Optional[float]:
        """Hours since the newest stored ^VIX close. None if nothing is stored."""
        row = self.latest()
        if not row:
            return None
        try:
            last = datetime.strptime(str(row["trading_date"])[:10], "%Y-%m-%d")
        except ValueError:
            return None
        return round((datetime.now() - last).total_seconds() / 3600.0, 1)


volatility_index_engine = VolatilityIndexEngine()
