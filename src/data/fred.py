"""
Federal Reserve Economic Data (FRED) API Adapter
St. Louis Fed official API for US Macroeconomic & Interest Rate Data.
"""

from datetime import date, datetime, timezone
from typing import List, Optional
import requests

from config.settings import settings
from src.data.rate_limiter import registry
from src.models.schemas import MacroObservation


class FredAdapter:
    """
    Adapter for FRED API.
    Provides US 2Y/10Y Treasury yields, Fed Funds, VIX, and credit spreads.
    """
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.FRED_API_KEY
        self.base_url = settings.FRED_BASE_URL
        self.rate_limiter = registry.get_limiter("stlouisfed.org", default_rate=1.5)

    def fetch_series(
        self,
        series_id: str,
        limit: int = 10,
        observation_start: Optional[date] = None,
    ) -> List[MacroObservation]:
        """
        Fetch time series observations from FRED.
        series_id: e.g. 'DGS10', 'DGS2', 'T10Y2Y', 'FEDFUNDS', 'VIXCLS'
        """
        if not self.api_key:
            # If no API key configured, return empty list (or mock in test)
            return []

        self.rate_limiter.acquire(1.0)
        url = f"{self.base_url}/series/observations"
        params = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": limit,
        }
        if observation_start:
            params["observation_start"] = observation_start.isoformat()

        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        results: List[MacroObservation] = []
        knowledge_at = datetime.now(timezone.utc)

        for obs in data.get("observations", []):
            date_str = obs.get("date")
            val_str = obs.get("value")
            if not date_str or not val_str or val_str == ".":
                continue  # '.' indicates holiday or missing value in FRED

            try:
                val_float = float(val_str)
                results.append(
                    MacroObservation(
                        series_id=series_id,
                        observation_date=date.fromisoformat(date_str),
                        knowledge_at=knowledge_at,
                        value=val_float,
                        source="FRED",
                        provenance_metadata={"series_id": series_id},
                    )
                )
            except ValueError:
                continue

        return results
