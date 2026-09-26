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
        default_currency: str = "USD",
    ) -> List[FundamentalFact]:
        """
        Extract canonical fundamentals using deterministic fallback priority chains
        across both US-GAAP and IFRS-Full taxonomies.
        Covers US 10-K/10-Q and Canadian/Foreign 40-F/6-K/20-F filings.
        """
        facts_payload = self.get_company_facts(cik)
        facts_dict = facts_payload.get("facts", {})
        us_gaap = facts_dict.get("us-gaap", {})
        ifrs = facts_dict.get("ifrs-full", {})

        is_ifrs = bool(ifrs and not us_gaap)
        source_taxonomy = ifrs if is_ifrs else us_gaap
        source_label = "SEC_EDGAR_XBRL"

        knowledge_at = datetime.now(timezone.utc)
        current_year = date.today().year

        results: List[FundamentalFact] = []

        concept_chains = {
            "Revenues": {
                "gaap": [
                    "RevenueFromContractWithCustomerExcludingAssessedTax",
                    "Revenues",
                    "SalesRevenueNet",
                    "InterestAndDividendIncomeOperating",
                    "OperatingRevenueUnrealizedGainLossOnDerivativeInstruments",
                ],
                "ifrs": [
                    "Revenue",
                    "RevenueFromContractsWithCustomers",
                    "InterestRevenueCalculatedUsingEffectiveInterestMethod",
                    "RevenueFromSaleOfOilAndGasProducts",
                    "OperatingIncome",
                ],
            },
            "NetIncomeLoss": {
                "gaap": [
                    "NetIncomeLoss",
                    "ProfitLoss",
                    "NetIncomeLossAvailableToCommonStockholdersBasic",
                ],
                "ifrs": [
                    "ProfitLoss",
                    "ProfitLossAttributableToOwnersOfParent",
                ],
            },
            "OperatingIncome": {
                "gaap": [
                    "OperatingIncomeLoss",
                ],
                "ifrs": [
                    "ProfitLossFromOperatingActivities",
                    "OperatingProfit",
                    "ProfitLossBeforeTax",
                ],
            },
            "OperatingCashFlow": {
                "gaap": [
                    "NetCashProvidedByUsedInOperatingActivities",
                    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
                ],
                "ifrs": [
                    "CashFlowsFromUsedInOperatingActivities",
                ],
            },
            "StockholdersEquity": {
                "gaap": [
                    "StockholdersEquity",
                    "CommonStockholdersEquity",
                    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                ],
                "ifrs": [
                    "Equity",
                    "EquityAttributableToOwnersOfParent",
                ],
            },
            "TotalAssets": {
                "gaap": [
                    "Assets",
                ],
                "ifrs": [
                    "Assets",
                ],
            },
            "TotalDebt": {
                "gaap": [
                    "LongTermDebtNoncurrent",
                    "LongTermDebtAndCapitalLeaseObligations",
                    "DebtInstrumentCarryingAmount",
                ],
                "ifrs": [
                    "Borrowings",
                    "NoncurrentFinancialLiabilities",
                    "FinancialLiabilitiesAtAmortisedCost",
                ],
            },
            "GrossProfit": {
                "gaap": [
                    "GrossProfit",
                ],
                "ifrs": [
                    "GrossProfit",
                ],
            },
            "DilutedSharesOutstanding": {
                "gaap": [
                    "WeightedAverageNumberOfDilutedSharesOutstanding",
                    "CommonStockSharesOutstanding",
                ],
                "ifrs": [
                    "WeightedAverageShares",
                    "AdjustedWeightedAverageShares",
                ],
            },
            "DividendsPaid": {
                "gaap": [
                    "PaymentsOfDividends",
                    "PaymentsOfDividendsCommonStock",
                ],
                "ifrs": [
                    "DividendsPaidClassifiedAsFinancingActivities",
                    "DividendsPaidOrdinaryShares",
                ],
            },
        }

        valid_forms = ("10-K", "10-Q", "40-F", "6-K", "20-F")

        for canonical_name, tax_mapping in concept_chains.items():
            tag_list = tax_mapping["ifrs"] if is_ifrs else tax_mapping["gaap"]
            matched_tag = None
            matched_unit = None
            matched_observations = []

            for tag in tag_list:
                if tag not in source_taxonomy:
                    continue

                tag_data = source_taxonomy[tag]
                units_dict = tag_data.get("units", {})

                # Determine candidate unit key
                if canonical_name == "DilutedSharesOutstanding":
                    unit_key = "shares" if "shares" in units_dict else next(iter(units_dict.keys()), None)
                else:
                    if default_currency in units_dict:
                        unit_key = default_currency
                    elif "USD" in units_dict:
                        unit_key = "USD"
                    elif "CAD" in units_dict:
                        unit_key = "CAD"
                    else:
                        unit_key = next(iter(units_dict.keys()), None)

                if not unit_key or unit_key not in units_dict:
                    continue

                # Filter for valid observations within the observation window
                valid_obs = [
                    obs for obs in units_dict[unit_key]
                    if obs.get("form") in valid_forms
                    and obs.get("fy") and obs.get("fy") >= (current_year - recent_years)
                    and obs.get("val") is not None
                    and obs.get("end") and obs.get("filed")
                ]

                if valid_obs:
                    matched_tag = tag
                    matched_unit = unit_key
                    matched_observations = valid_obs
                    break

            if not matched_tag:
                continue

            for obs in matched_observations:
                fy = obs.get("fy")
                fp = obs.get("fp", "FY")
                val = obs.get("val")
                end_str = obs.get("end")
                filed_str = obs.get("filed")
                form = obs.get("form")

                period_end = date.fromisoformat(end_str)
                filing_date = date.fromisoformat(filed_str)

                # Point-in-time guard: filing_date >= period_end
                if filing_date < period_end:
                    filing_date = period_end

                if matched_unit in ("CAD", "USD"):
                    curr = matched_unit
                else:
                    curr = "CAD" if default_currency == "CAD" else "USD"

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
                        currency=curr,
                        source=source_label,
                        confidence="HIGH",
                    )
                )

        return results

        return results
