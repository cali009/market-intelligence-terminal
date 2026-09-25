"""
Bank of Canada Valet API Adapter
Official, free, public API for Canadian Macroeconomic & Foreign Exchange Data.
"""

from datetime import date, datetime, timezone
from typing import List, Dict, Any, Optional
import requests

from config.settings import settings
from src.data.rate_limiter import registry
from src.models.schemas import MacroObservation


class BankOfCanadaValetAdapter:
    """
    Adapter for Bank of Canada Valet API.
    Provides daily CAD/USD rates, GoC benchmark bond yields, and policy interest rates.
    """
    def __init__(self):
        self.base_url = settings.BOC_VALET_BASE_URL
        self.rate_limiter = registry.get_limiter("bankofcanada.ca", default_rate=5.0)

    def fetch_observations(
        self,
        series_names: List[str],
        recent: Optional[int] = 10,
        start_date: Optional[date] = None,
    ) -> List[MacroObservation]:
        """
        Fetch observations for one or more series.
        series_names: e.g. ['FXUSDCAD', 'V39051', 'V39055', 'STATIC_LIQUIDITY_TARGET_CAN']
        """
        self.rate_limiter.acquire(1.0)

        series_str = ",".join(series_names)
        url = f"{self.base_url}/observations/{series_str}/json"
        params = {}
        if recent:
            params["recent"] = recent
        if start_date:
            params["start_date"] = start_date.isoformat()

        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        observations_raw = data.get("observations", [])
        results: List[MacroObservation] = []
        knowledge_at = datetime.now(timezone.utc)

        for obs in observations_raw:
            obs_date_str = obs.get("d")
            if not obs_date_str:
                continue
            obs_date = date.fromisoformat(obs_date_str)

            for series in series_names:
                if series in obs and "v" in obs[series]:
                    val_str = obs[series]["v"]
                    try:
                        val_float = float(val_str)
                        results.append(
                            MacroObservation(
                                series_id=series,
                                observation_date=obs_date,
                                knowledge_at=knowledge_at,
                                value=val_float,
                                source="BOC_VALET",
                                provenance_metadata={
                                    "url": url,
                                    "original_raw_value": val_str,
                                },
                            )
                        )
                    except ValueError:
                        continue  # Skip unparseable or blank values (holidays)

        return results

    def get_latest_cad_usd_fx(self) -> Optional[MacroObservation]:
        """
        Convenience method to retrieve the latest daily CAD/USD FX rate.
        """
        obs = self.fetch_observations(["FXUSDCAD"], recent=1)
        return obs[-1] if obs else None

    def get_canadian_yield_curve(self) -> Dict[str, float]:
        """
        Fetch latest 2Y and 10Y Government of Canada benchmark bond yields.
        BD.CDN.2YR.DQ.YLD: 2-Year Benchmark Yield
        BD.CDN.10YR.DQ.YLD: 10-Year Benchmark Yield
        """
        obs = self.fetch_observations(["BD.CDN.2YR.DQ.YLD", "BD.CDN.10YR.DQ.YLD"], recent=2)
        curve = {}
        for item in obs:
            curve[item.series_id] = item.value
        return curve
