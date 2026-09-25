"""
SEC EDGAR XBRL & Submissions API Adapter
Official, free, public API for US Public Company Fundamentals & SEC Filings.
Enforces strict SEC Fair Access Policy: 8 req/sec limiter + declared User-Agent.
"""

import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
import requests

from config.settings import settings, DATA_DIR
from src.data.rate_limiter import registry
from src.models.schemas import FundamentalFact


class SecEdgarAdapter:
    """
    Adapter for SEC EDGAR APIs (data.sec.gov & sec.gov).
    Extracts XBRL facts and submission metadata with priority concept fallbacks.
    """
    def __init__(self):
        self.headers = {
            "User-Agent": settings.SEC_EDGAR_USER_AGENT,
            "Accept-Encoding": "gzip, deflate",
        }
        self.rate_limiter = registry.get_limiter("sec.gov", default_rate=8.0)
        self.cache_dir = DATA_DIR / "cache" / "sec_edgar"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._ticker_cik_cache: Optional[Dict[str, int]] = None

    def pad_cik(self, cik: int | str) -> str:
        """
        Pad CIK integer to strictly 10 digits as required by SEC EDGAR data.sec.gov paths.
        """
        return f"{int(cik):010d}"

    def get_ticker_to_cik_map(self, force_refresh: bool = False) -> Dict[str, int]:
        """
        Retrieve official ticker -> CIK mapping from sec.gov/files/company_tickers.json.
        Cached locally for 24 hours.
        """
        if self._ticker_cik_cache and not force_refresh:
            return self._ticker_cik_cache

        cache_file = self.cache_dir / "company_tickers.json"
        if not force_refresh and cache_file.exists():
            # Check age < 24h
            if time.time() - cache_file.stat().st_mtime < 86400:
                with open(cache_file, "r") as f:
                    self._ticker_cik_cache = json.load(f)
                    return self._ticker_cik_cache

        self.rate_limiter.acquire(1.0)
        url = "https://www.sec.gov/files/company_tickers.json"
        resp = requests.get(url, headers=self.headers, timeout=20)
        resp.raise_for_status()
        raw_data = resp.json()

        # Map ticker -> cik_str
        mapping = {
            item["ticker"].upper(): int(item["cik_str"])
            for item in raw_data.values()
        }

        with open(cache_file, "w") as f:
            json.dump(mapping, f)

        self._ticker_cik_cache = mapping
        return mapping

    def get_company_facts(self, cik: int | str) -> Dict[str, Any]:
        """
        Retrieve full XBRL company facts for a CIK.
        data.sec.gov/api/xbrl/companyfacts/CIK##########.json
        """
        self.rate_limiter.acquire(1.0)
        padded = self.pad_cik(cik)
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{padded}.json"

        resp = requests.get(url, headers=self.headers, timeout=25)
        if resp.status_code == 404:
            raise ValueError(f"Company facts not found for CIK {padded}")
        resp.raise_for_status()
        return resp.json()

    def get_company_submissions(self, cik: int | str, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Retrieve official company submissions (recent 8-K, 10-Q, 10-K, etc.) from SEC EDGAR.
        data.sec.gov/submissions/CIK##########.json
        Cached locally for 12 hours.
        """
        padded = self.pad_cik(cik)
        cache_file = self.cache_dir / f"submissions_CIK{padded}.json"
        if not force_refresh and cache_file.exists():
            if time.time() - cache_file.stat().st_mtime < 43200:
                with open(cache_file, "r") as f:
                    return json.load(f)

        self.rate_limiter.acquire(1.0)
        url = f"https://data.sec.gov/submissions/CIK{padded}.json"
        resp = requests.get(url, headers=self.headers, timeout=25)
        if resp.status_code == 404:
            raise ValueError(f"Submissions not found for CIK {padded}")
        resp.raise_for_status()
        data = resp.json()

        with open(cache_file, "w") as f:
            json.dump(data, f)

        return data

    @staticmethod
    def format_filing_document_url(cik: int | str, accession_number: str, primary_document: str) -> str:
        """
        Construct verified SEC EDGAR archive hyperlink for a specific filing document.
        """
        clean_accession = accession_number.replace("-", "")
        int_cik = int(cik)
        return f"https://www.sec.gov/Archives/edgar/data/{int_cik}/{clean_accession}/{primary_document}"

    def extract_canonical_fundamentals(
        self,
        security_id: int,
        cik: int | str,
        recent_years: int = 3,
    ) -> List[FundamentalFact]:
        """
        Extract canonical fundamentals using deterministic fallback priority chains:
        - Revenue
        - Net Income
        - Operating Cash Flow
        - Stockholders Equity
        - Diluted Shares Outstanding
        """
        facts_payload = self.get_company_facts(cik)
        us_gaap = facts_payload.get("facts", {}).get("us-gaap", {})
        knowledge_at = datetime.now(timezone.utc)
        current_year = date.today().year

        results: List[FundamentalFact] = []

        # Concept priority fallback definitions
        concept_chains = {
            "Revenues": [
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues",
                "SalesRevenueNet",
                "InterestAndDividendIncomeOperating",
                "OperatingRevenueUnrealizedGainLossOnDerivativeInstruments",
            ],
            "NetIncomeLoss": [
                "NetIncomeLoss",
                "ProfitLoss",
                "NetIncomeLossAvailableToCommonStockholdersBasic",
            ],
            "OperatingCashFlow": [
                "NetCashProvidedByUsedInOperatingActivities",
                "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
            ],
            "StockholdersEquity": [
                "StockholdersEquity",
                "CommonStockholdersEquity",
                "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            ],
            "DilutedSharesOutstanding": [
                "WeightedAverageNumberOfDilutedSharesOutstanding",
                "CommonStockSharesOutstanding",
            ],
        }

        for canonical_name, tag_list in concept_chains.items():
            matched_tag = None
            for tag in tag_list:
                if tag in us_gaap:
                    matched_tag = tag
                    break

            if not matched_tag:
                continue

            tag_data = us_gaap[matched_tag]
            units_dict = tag_data.get("units", {})
            # Look for USD units, or pure/shares for share count
            unit_key = "USD" if "USD" in units_dict else next(iter(units_dict.keys()), None)
            if not unit_key:
                continue

            observations = units_dict[unit_key]
            for obs in observations:
                form = obs.get("form", "")
                if form not in ("10-K", "10-Q"):
                    continue

                fy = obs.get("fy")
                if not fy or fy < (current_year - recent_years):
                    continue

                fp = obs.get("fp", "FY")
                val = obs.get("val")
                end_str = obs.get("end")
                filed_str = obs.get("filed")

                if val is None or not end_str or not filed_str:
                    continue

                period_end = date.fromisoformat(end_str)
                filing_date = date.fromisoformat(filed_str)

                # Point-in-time guard: filing_date >= period_end
                if filing_date < period_end:
                    filing_date = period_end

                currency = "USD" if unit_key == "USD" else "USD"

                results.append(
                    FundamentalFact(
                        security_id=security_id,
                        concept=canonical_name,
                        period_end=period_end,
                        filing_date=filing_date,
                        knowledge_at=knowledge_at,
                        fiscal_year=int(fy),
                        fiscal_period=fp if fp in ("Q1", "Q2", "Q3", "FY") else "FY",
                        form=form,
                        value=float(val),
                        currency=currency,
                        source="SEC_EDGAR_XBRL",
                        confidence="HIGH",
                    )
                )

        return results
