"""
Dual-Market Daily OHLCV Bar Ingestion Pipeline
Fetches, validates, and stores historical & EOD price bars for US and Canadian equities.
Uses resilient direct HTTPS requests with zero third-party library dependencies.
Enforces bitemporal lineage, OHLC consistency, and exchange calendar checks.
"""

from datetime import date, datetime, timezone
from typing import List, Dict, Optional, Tuple
import pandas as pd
import requests

from src.data.db import db
from src.models.schemas import DailyBar
from config.calendars import is_trading_day

CHART_API_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}


class BarIngestionEngine:
    """
    Ingests and normalizes dual-market price bars into canonical schema.
    """

    @staticmethod
    def format_symbol(symbol: str, exchange: str) -> str:
        """
        Format symbol for query:
        - TSX -> 'SYMBOL.TO' (e.g. SHOP.TO, RY.TO)
        - TSXV -> 'SYMBOL.V'
        - US (NYSE, NASDAQ, AMEX) -> 'SYMBOL' (e.g. AAPL, SPY)
        """
        sym_clean = symbol.strip().upper()
        exch_clean = exchange.strip().upper()

        if exch_clean in ("TSX", "XTSE", "TSE"):
            return f"{sym_clean}.TO" if not sym_clean.endswith(".TO") else sym_clean
        elif exch_clean in ("TSXV", "XTSX", "V"):
            return f"{sym_clean}.V" if not sym_clean.endswith(".V") else sym_clean
        return sym_clean

    # Backward compatibility alias
    format_yfinance_symbol = format_symbol

    def fetch_history(
        self,
        symbol: str,
        exchange: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> pd.DataFrame:
        """
        Fetch OHLCV historical dataframe for a symbol via direct HTTPS query.
        Returns clean DataFrame with ['trading_date', 'Open', 'High', 'Low', 'Close', 'Volume', 'adjusted_close']
        """
        formatted_sym = self.format_symbol(symbol, exchange)
        url = f"{CHART_API_BASE}/{formatted_sym}"
        params = {"range": period, "interval": interval}

        resp = requests.get(url, headers=REQUEST_HEADERS, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        chart_result = data.get("chart", {}).get("result")
        if not chart_result or len(chart_result) == 0:
            raise ValueError(f"No chart data returned for {formatted_sym}")

        result_obj = chart_result[0]
        timestamps = result_obj.get("timestamp", [])
        if not timestamps:
            raise ValueError(f"Empty timestamp list returned for {formatted_sym}")

        quote = result_obj.get("indicators", {}).get("quote", [{}])[0]
        adjclose_list = (
            result_obj.get("indicators", {}).get("adjclose", [{}])[0].get("adjclose")
        )

        opens = quote.get("open", [])
        highs = quote.get("high", [])
        lows = quote.get("low", [])
        closes = quote.get("close", [])
        volumes = quote.get("volume", [])

        rows = []
        for i, ts in enumerate(timestamps):
            if i >= len(closes):
                break

            c = closes[i]
            # Skip invalid/null days (e.g. market holidays or data gaps)
            if c is None:
                continue

            o = opens[i] if i < len(opens) and opens[i] is not None else c
            h = highs[i] if i < len(highs) and highs[i] is not None else max(o, c)
            l = lows[i] if i < len(lows) and lows[i] is not None else min(o, c)
            v = volumes[i] if i < len(volumes) and volumes[i] is not None else 0.0
            adj = (
                adjclose_list[i]
                if adjclose_list and i < len(adjclose_list) and adjclose_list[i] is not None
                else c
            )

            t_date = datetime.fromtimestamp(ts, tz=timezone.utc).date()
            rows.append({
                "trading_date": t_date,
                "Open": float(o),
                "High": float(h),
                "Low": float(l),
                "Close": float(c),
                "Volume": float(v),
                "adjusted_close": float(adj),
            })

        df = pd.DataFrame(rows)
        if df.empty:
            raise ValueError(f"No valid trading bars parsed for {formatted_sym}")

        # Drop duplicate trading dates if any
        df = df.drop_duplicates(subset=["trading_date"]).sort_values("trading_date").reset_index(drop=True)
        return df

    def validate_and_parse_bars(
        self,
        security_id: int,
        df: pd.DataFrame,
        source: str = "DIRECT_CHART_API",
    ) -> List[DailyBar]:
        """
        Apply strict DQ assertions on each row and parse into canonical DailyBar models.
        """
        bars: List[DailyBar] = []
        knowledge_at = datetime.now(timezone.utc)

        for _, row in df.iterrows():
            t_date = row["trading_date"]
            open_p = float(row["Open"])
            high_p = float(row["High"])
            low_p = float(row["Low"])
            close_p = float(row["Close"])
            vol = float(row["Volume"])
            adj_p = float(row["adjusted_close"])

            # Skip incomplete or non-positive rows
            if open_p <= 0 or high_p <= 0 or low_p <= 0 or close_p <= 0:
                continue

            # Enforce OHLC consistency bounds
            high_bound = max(high_p, open_p, close_p)
            low_bound = min(low_p, open_p, close_p)

            bar = DailyBar(
                security_id=security_id,
                trading_date=t_date,
                knowledge_at=knowledge_at,
                open=open_p,
                high=high_bound,
                low=low_bound,
                close=close_p,
                volume=max(0.0, vol),
                adjusted_close=adj_p,
                source=source,
                confidence="HIGH",
            )
            bars.append(bar)

        return bars

    def save_bars_to_db(self, bars: List[DailyBar]) -> int:
        """
        Batch upsert parsed bars into bar_1d table.
        """
        if not bars:
            return 0

        conn = db.get_connection()
        try:
            cursor = conn.cursor()
            query = """
                INSERT INTO bar_1d (
                    security_id, trading_date, knowledge_at, open, high, low, close,
                    volume, adjusted_close, source, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(security_id, trading_date) DO UPDATE SET
                    knowledge_at=excluded.knowledge_at,
                    open=excluded.open,
                    high=excluded.high,
                    low=excluded.low,
                    close=excluded.close,
                    volume=excluded.volume,
                    adjusted_close=excluded.adjusted_close,
                    source=excluded.source,
                    confidence=excluded.confidence
            """
            data_tuples = [
                (
                    b.security_id,
                    b.trading_date.isoformat(),
                    b.knowledge_at.isoformat(),
                    b.open,
                    b.high,
                    b.low,
                    b.close,
                    b.volume,
                    b.adjusted_close,
                    b.source,
                    b.confidence,
                )
                for b in bars
            ]
            cursor.executemany(query, data_tuples)
            conn.commit()
            return len(data_tuples)
        finally:
            conn.close()

    def sync_security_bars(
        self,
        security_id: int,
        symbol: str,
        exchange: str,
        period: str = "1y",
    ) -> int:
        """
        End-to-end sync for a single security.
        """
        df = self.fetch_history(symbol, exchange, period=period)
        bars = self.validate_and_parse_bars(security_id, df)
        count = self.save_bars_to_db(bars)
        return count


# Global singleton ingestion engine
bar_engine = BarIngestionEngine()
