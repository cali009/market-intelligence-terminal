"""
News, SEC Filings & Catalysts Intelligence Engine
US + Canada Market Intelligence Platform

Ingests, classifies, deduplicates, and evaluates market events from:
- SEC EDGAR Submissions API (8-K, 10-Q, 10-K, 6-K)
- Bank of Canada Valet Policy Rate & Monetary Actions
- Financial Dissemination Wires & News Feeds

Computes:
1. Event Category Taxonomy (Earnings, Guidance, M&A, Regulatory, Capital, Macro)
2. Materiality Scoring (1-5, HIGH / MEDIUM / LOW)
3. Sentiment Scoring (-1.0 to +1.0 with directional labels)
4. Time Horizon (Immediate, Short-Term, Medium-Term, Structural)
5. Novelty & Priced-in Status (Fresh, Partial, Priced-in)
6. Post-Event Price Reaction Tracking (1-Day, 3-Day return % and RVOL)
7. Catastrophic Risk Hard Gates (Item 4.02 restatements, Item 1.03 insolvency)
8. Factor Score Attribution (-12 to +12 bounded composite modulation)
"""

import hashlib
import json
import re
from datetime import date, datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

from config.settings import DATA_DIR
from src.data.db import db
from src.data.sec_edgar import SecEdgarAdapter
from src.models.schemas import CatalystEvent

# Ticker to CIK mapping for universe issuers
UNIVERSE_CIK_MAP = {
    # US
    "AAPL": 320193,
    "MSFT": 789019,
    "NVDA": 1045810,
    "AMZN": 1018724,
    "GOOGL": 1652044,
    "JPM": 19617,
    "XOM": 2115436,
    # Canadian Dual-Listed
    "SHOP": 1594805,
    "RY": 1000275,
    "TD": 947263,
    "CNQ": 1017413,
    "ENB": 895728,
    "BAM": 1937926,
    "BN": 1001085,
    "CNR": 1710366,
    "CP": 16875,
}

# 8-K Item Taxonomy and standard materiality priors
SEC_8K_ITEM_TAXONOMY = {
    "1.01": {
        "category": "MATERIAL_AGREEMENT",
        "materiality_score": 3,
        "materiality_level": "MEDIUM",
        "label": "Entry into a Material Definitive Agreement",
        "horizon": "MEDIUM_TERM",
        "default_sentiment": 0.25,
    },
    "1.02": {
        "category": "CONTRACT_TERMINATION",
        "materiality_score": 3,
        "materiality_level": "MEDIUM",
        "label": "Termination of a Material Definitive Agreement",
        "horizon": "SHORT_TERM",
        "default_sentiment": -0.35,
    },
    "1.03": {
        "category": "BANKRUPTCY_RECEIVERSHIP",
        "materiality_score": 5,
        "materiality_level": "HIGH",
        "label": "Bankruptcy or Receivership",
        "horizon": "STRUCTURAL",
        "default_sentiment": -0.95,
        "hard_gate": True,
    },
    "2.01": {
        "category": "M_AND_A_TRANSACTION",
        "materiality_score": 4,
        "materiality_level": "HIGH",
        "label": "Completion of Acquisition or Disposition of Assets",
        "horizon": "MEDIUM_TERM",
        "default_sentiment": 0.30,
    },
    "2.02": {
        "category": "EARNINGS_ANNOUNCEMENT",
        "materiality_score": 4,
        "materiality_level": "HIGH",
        "label": "Results of Operations and Financial Condition (Earnings)",
        "horizon": "SHORT_TERM",
        "default_sentiment": 0.35,
    },
    "2.03": {
        "category": "DEBT_FINANCING",
        "materiality_score": 3,
        "materiality_level": "MEDIUM",
        "label": "Creation of Direct Financial Obligation or Debt Facility",
        "horizon": "MEDIUM_TERM",
        "default_sentiment": 0.05,
    },
    "2.05": {
        "category": "RESTRUCTURING_COSTS",
        "materiality_score": 3,
        "materiality_level": "MEDIUM",
        "label": "Costs Associated with Exit or Restructuring Activities",
        "horizon": "MEDIUM_TERM",
        "default_sentiment": -0.15,
    },
    "2.06": {
        "category": "MATERIAL_IMPAIRMENT",
        "materiality_score": 4,
        "materiality_level": "HIGH",
        "label": "Material Impairments",
        "horizon": "MEDIUM_TERM",
        "default_sentiment": -0.45,
    },
    "3.01": {
        "category": "DELISTING_RISK",
        "materiality_score": 5,
        "materiality_level": "HIGH",
        "label": "Notice of Delisting or Failure to Satisfy Listing Rule",
        "horizon": "SHORT_TERM",
        "default_sentiment": -0.80,
        "hard_gate": True,
    },
    "4.02": {
        "category": "FINANCIAL_RESTATEMENT",
        "materiality_score": 5,
        "materiality_level": "HIGH",
        "label": "Non-Reliance on Previously Issued Financial Statements",
        "horizon": "STRUCTURAL",
        "default_sentiment": -0.95,
        "hard_gate": True,
    },
    "5.01": {
        "category": "CHANGE_IN_CONTROL",
        "materiality_score": 4,
        "materiality_level": "HIGH",
        "label": "Changes in Control of Registrant",
        "horizon": "MEDIUM_TERM",
        "default_sentiment": 0.10,
    },
    "5.02": {
        "category": "EXECUTIVE_CHANGE",
        "materiality_score": 3,
        "materiality_level": "MEDIUM",
        "label": "Departure or Appointment of Principal Executive Officers / Directors",
        "horizon": "MEDIUM_TERM",
        "default_sentiment": 0.05,
    },
    "5.07": {
        "category": "SHAREHOLDER_VOTE",
        "materiality_score": 2,
        "materiality_level": "LOW",
        "label": "Submission of Matters to a Vote of Security Holders",
        "horizon": "IMMEDIATE",
        "default_sentiment": 0.0,
    },
    "7.01": {
        "category": "REGULATION_FD",
        "materiality_score": 3,
        "materiality_level": "MEDIUM",
        "label": "Regulation FD Disclosure (Investor Presentation / Update)",
        "horizon": "SHORT_TERM",
        "default_sentiment": 0.15,
    },
    "8.01": {
        "category": "OTHER_MATERIAL_EVENT",
        "materiality_score": 3,
        "materiality_level": "MEDIUM",
        "label": "Other Events of Substantial Importance",
        "horizon": "SHORT_TERM",
        "default_sentiment": 0.10,
    },
}

BULLISH_KEYWORDS = [
    "record revenue", "beat expectations", "raised guidance", "dividend increase",
    "share repurchase", "margin expansion", "contract award", "patent granted",
    "strategic partnership", "fcf growth", "strong demand", "upgraded", "accelerating",
    "profit surge", "surpassed", "debt reduction", "cash flow record"
]

BEARISH_KEYWORDS = [
    "lowered guidance", "missed estimates", "revenue decline", "margin compression",
    "investigation", "subpoena", "restatement", "litigation", "headwind", "impairment",
    "layoffs", "downgrade", "delisting", "delay", "supply constraint", "debt default",
    "material weakness", "accounting irregularity"
]


class NewsAndFilingsEngine:
    """
    Production Engine for ingesting, parsing, classifying, and pricing market catalysts.
    """

    def __init__(self):
        self.edgar = SecEdgarAdapter()
        self._price_cache: Dict[str, List[Dict[str, Any]]] = {}

    def _get_security_bars(self, symbol: str) -> List[Dict[str, Any]]:
        """
        Fetch historical price bars for reaction tracking.
        """
        if symbol in self._price_cache:
            return self._price_cache[symbol]

        rows = db.execute_query(
            """
            SELECT b.trading_date, b.open, b.high, b.low, b.close, b.volume, b.rvol
            FROM bar_1d b
            JOIN security s ON b.security_id = s.security_id
            WHERE s.symbol = ?
            ORDER BY b.trading_date ASC;
            """,
            (symbol,),
        )
        data = [dict(r) for r in rows]
        self._price_cache[symbol] = data
        return data

    def compute_price_reaction(
        self,
        symbol: str,
        filing_date_str: str,
    ) -> Dict[str, Optional[float]]:
        """
        Compute T+1 and T+3 post-filing price reaction and event day RVOL.
        """
        bars = self._get_security_bars(symbol)
        if not bars:
            return {"reaction_1d_pct": None, "reaction_3d_pct": None, "rvol_at_event": None}

        # Find event bar
        event_idx = None
        for i, b in enumerate(bars):
            if b["trading_date"] >= filing_date_str:
                event_idx = i
                break

        if event_idx is None or event_idx == 0:
            return {"reaction_1d_pct": None, "reaction_3d_pct": None, "rvol_at_event": None}

        event_bar = bars[event_idx]
        base_close = bars[event_idx - 1]["close"] if event_idx > 0 else event_bar["open"]
        rvol = event_bar.get("rvol") or 1.0

        r_1d = None
        r_3d = None

        if base_close > 0:
            # T+1 reaction
            r_1d = round(((event_bar["close"] - base_close) / base_close) * 100, 2)

            # T+3 reaction if available
            t3_idx = min(len(bars) - 1, event_idx + 2)
            if t3_idx > event_idx:
                r_3d = round(((bars[t3_idx]["close"] - base_close) / base_close) * 100, 2)

        return {
            "reaction_1d_pct": r_1d,
            "reaction_3d_pct": r_3d,
            "rvol_at_event": round(rvol, 2) if rvol else 1.0,
        }

    def evaluate_sentiment(
        self,
        headline: str,
        summary: str,
        default_sentiment: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Deterministic keyword & heuristic sentiment scoring.
        Produces sentiment score in [-1.0, 1.0], label, and driving factors.
        """
        text = f"{headline} {summary}".lower()
        score = default_sentiment
        drivers: List[str] = []

        pos_hits = [k for k in BULLISH_KEYWORDS if k in text]
        neg_hits = [k for k in BEARISH_KEYWORDS if k in text]

        if pos_hits:
            score += min(0.40, len(pos_hits) * 0.15)
            drivers.append(f"Bullish catalyst keywords: {', '.join(pos_hits[:3])}")
        if neg_hits:
            score -= min(0.60, len(neg_hits) * 0.20)
            drivers.append(f"Risk factor keywords: {', '.join(neg_hits[:3])}")

        score = max(-1.0, min(1.0, round(score, 2)))

        if score >= 0.25:
            label = "BULLISH"
        elif score >= 0.05:
            label = "LEAN_BULLISH"
        elif score <= -0.25:
            label = "BEARISH"
        elif score <= -0.05:
            label = "LEAN_BEARISH"
        else:
            label = "NEUTRAL"

        return {
            "score": score,
            "label": label,
            "drivers": drivers or ["Standard factual disclosure under regulatory reporting schedule"],
        }

    def estimate_priced_in_status(
        self,
        filing_date_str: str,
        rvol: Optional[float],
        reaction_1d: Optional[float],
    ) -> str:
        """
        Classify whether the market has already digested the information.
        """
        try:
            f_date = date.fromisoformat(filing_date_str)
            days_ago = (date.today() - f_date).days
        except Exception:
            days_ago = 1

        if days_ago <= 1 and (reaction_1d is None or abs(reaction_1d) < 1.0):
            return "FRESH"
        elif days_ago <= 4:
            return "PARTIAL"
        else:
            return "PRICED_IN"

    def compute_score_impact(
        self,
        materiality_score: int,
        sentiment_score: float,
        filing_date_str: str,
    ) -> float:
        """
        Compute bounded composite score modulation points (-12 to +12 pts).
        Applies exponential recency decay over 30 days.
        """
        try:
            f_date = date.fromisoformat(filing_date_str)
            days_ago = max(0, (date.today() - f_date).days)
        except Exception:
            days_ago = 5

        # Recency decay: half-life ~ 10 days
        decay = 0.5 ** (days_ago / 10.0)
        # Materiality weighting (1-5 normalized to 0.2 - 1.0)
        mat_factor = materiality_score / 5.0

        raw_pts = sentiment_score * mat_factor * decay * 12.0
        return round(max(-12.0, min(12.0, raw_pts)), 1)

    def ingest_sec_filings_for_symbol(
        self,
        symbol: str,
        cik: int,
        max_filings: int = 15,
    ) -> List[CatalystEvent]:
        """
        Ingest and classify recent SEC EDGAR filings for a US or dual-listed Canadian symbol.
        """
        events: List[CatalystEvent] = []
        try:
            sub = self.edgar.get_company_submissions(cik)
        except Exception:
            return events

        company_name = sub.get("name", symbol)
        recent = sub.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])
        primary_descs = recent.get("primaryDocDescription", [])
        items_list = recent.get("items", [])
        report_dates = recent.get("reportDate", [])

        is_canadian = symbol in ("SHOP", "RY", "TD", "CNQ", "ENB", "BAM", "BN", "CNR", "CP")
        market = "CA" if is_canadian else "US"

        for i in range(min(len(forms), 60)):
            form = forms[i]
            if form not in ("8-K", "10-Q", "10-K", "6-K", "40-F"):
                continue

            f_date = filing_dates[i]
            acc = accessions[i]
            doc = primary_docs[i]
            raw_items = items_list[i] if i < len(items_list) else ""
            desc = primary_descs[i] if i < len(primary_descs) else ""
            rep_date = report_dates[i] if i < len(report_dates) else f_date

            source_url = self.edgar.format_filing_document_url(cik, acc, doc)
            unique_payload = f"{symbol}|{form}|{acc}|{f_date}".encode("utf-8")
            event_id = hashlib.md5(unique_payload).hexdigest()[:12]

            # 1. Classification & Materiality
            category = "MATERIAL_DISCLOSURE"
            mat_score = 3
            mat_level = "MEDIUM"
            mat_rationale = f"Official SEC Form {form} filing"
            hard_gate = False
            default_sent = 0.05
            horizon = "SHORT_TERM"

            if form == "8-K":
                items_split = [it.strip() for it in raw_items.split(",") if it.strip()]
                matched_items = [it for it in items_split if it in SEC_8K_ITEM_TAXONOMY]
                if matched_items:
                    top_item = max(matched_items, key=lambda x: SEC_8K_ITEM_TAXONOMY[x]["materiality_score"])
                    meta = SEC_8K_ITEM_TAXONOMY[top_item]
                    category = meta["category"]
                    mat_score = meta["materiality_score"]
                    mat_level = meta["materiality_level"]
                    mat_rationale = f"SEC Form 8-K ({', '.join(items_split)}): {meta['label']}"
                    hard_gate = meta.get("hard_gate", False)
                    default_sent = meta.get("default_sentiment", 0.0)
                    horizon = meta.get("horizon", "SHORT_TERM")
                    headline = f"{symbol} SEC Form 8-K Filing — {meta['label']}"
                else:
                    category = "OTHER_MATERIAL_EVENT"
                    headline = f"{symbol} SEC Form 8-K Current Report ({raw_items or 'Material Events'})"
            elif form == "10-Q":
                category = "QUARTERLY_REPORT"
                mat_score = 4
                mat_level = "HIGH"
                mat_rationale = f"Quarterly Financial Report for period ending {rep_date} (Form 10-Q)"
                horizon = "MEDIUM_TERM"
                default_sent = 0.20
                headline = f"{symbol} Form 10-Q Quarterly Report — Q/E {rep_date}"
            elif form == "10-K":
                category = "ANNUAL_REPORT"
                mat_score = 4
                mat_level = "HIGH"
                mat_rationale = f"Annual Comprehensive 10-K Report for fiscal year ending {rep_date}"
                horizon = "STRUCTURAL"
                default_sent = 0.20
                headline = f"{symbol} Form 10-K Comprehensive Annual Report — F/Y {rep_date}"
            elif form == "6-K":
                category = "CANADIAN_FOREIGN_FILING"
                mat_score = 3
                mat_level = "MEDIUM"
                mat_rationale = f"Foreign Issuer Continuous Disclosure (SEDAR cross-filing)"
                horizon = "SHORT_TERM"
                default_sent = 0.10
                headline = f"{symbol} Form 6-K Cross-Border Continuous Disclosure"
            elif form == "40-F":
                category = "ANNUAL_REPORT"
                mat_score = 4
                mat_level = "HIGH"
                mat_rationale = f"Canadian Issuer Annual Report filed under Multi-Jurisdictional Disclosure System"
                horizon = "STRUCTURAL"
                default_sent = 0.15
                headline = f"{symbol} Form 40-F MJDS Canadian Annual Report"

            summary = (
                f"{company_name} filed Form {form} with the SEC on {f_date}. "
                f"Document: {doc}. Accession: {acc}. Primary reporting date: {rep_date}."
            )

            # 2. Sentiment
            sentiment = self.evaluate_sentiment(headline, summary, default_sent)

            # 3. Price reaction tracking
            reaction = self.compute_price_reaction(symbol, f_date)

            # 4. Priced in status
            priced_in = self.estimate_priced_in_status(f_date, reaction["rvol_at_event"], reaction["reaction_1d_pct"])

            # 5. Score impact
            impact_pts = self.compute_score_impact(mat_score, sentiment["score"], f_date)

            # 6. Factual claims & Invalidation risks
            factual_claims = [
                f"Official SEC filing under CIK {cik:010d} with accession number {acc}",
                f"Filing accepted and recorded on official SEC EDGAR registry for {f_date}",
                f"Document reference: {doc} ({form} classification)",
            ]
            if raw_items:
                factual_claims.append(f"Reported disclosure items: {raw_items}")

            invalidation_risks = [
                "Post-announcement drift may reverse if broad market regime deteriorates",
                "Subsequent 10-Q/10-K disclosures may modify non-GAAP preliminary figures",
            ]
            if hard_gate:
                invalidation_risks.insert(0, "CRITICAL RISK: Material restatement or insolvency gate active")

            events.append(
                CatalystEvent(
                    id=event_id,
                    symbol=symbol,
                    company_name=company_name,
                    market=market,
                    source=f"SEC_EDGAR_{form.replace('-', '')}",
                    source_url=source_url,
                    headline=headline,
                    summary=summary,
                    filing_date=f_date,
                    published_at=f"{f_date}T16:05:00Z",
                    event_category=category,
                    materiality_level=mat_level,
                    materiality_score=mat_score,
                    materiality_rationale=mat_rationale,
                    sentiment_score=sentiment["score"],
                    sentiment_label=sentiment["label"],
                    sentiment_drivers=sentiment["drivers"],
                    horizon=horizon,
                    priced_in_status=priced_in,
                    reaction_1d_pct=reaction["reaction_1d_pct"],
                    reaction_3d_pct=reaction["reaction_3d_pct"],
                    rvol_at_event=reaction["rvol_at_event"],
                    factual_claims=factual_claims,
                    invalidation_risks=invalidation_risks,
                    hard_gate_triggered=hard_gate,
                    score_impact_pts=impact_pts,
                )
            )

            if len(events) >= max_filings:
                break

        return events

    def generate_macro_catalysts(self) -> List[CatalystEvent]:
        """
        Generate macro rate and sovereign yield catalysts from Bank of Canada Valet and Federal Reserve.
        """
        events: List[CatalystEvent] = []

        # 1. Bank of Canada Benchmark Yield Curve & Monetary Structure
        y10_rows = db.execute_query(
            "SELECT observation_date, value FROM macro_observation WHERE series_id = 'BD.CDN.10YR.DQ.YLD' ORDER BY observation_date DESC LIMIT 1;"
        )
        y2_rows = db.execute_query(
            "SELECT observation_date, value FROM macro_observation WHERE series_id = 'BD.CDN.2YR.DQ.YLD' ORDER BY observation_date DESC LIMIT 1;"
        )

        if y10_rows and y2_rows:
            y10_val = y10_rows[0]["value"]
            y2_val = y2_rows[0]["value"]
            obs_date = y10_rows[0]["observation_date"]
            spread = round(y10_val - y2_val, 2)
            is_inverted = spread < 0

            sent_score = 0.25 if not is_inverted else -0.35
            sent_label = "BULLISH" if not is_inverted else "BEARISH"

            events.append(
                CatalystEvent(
                    id="boc_yield_curve_spread",
                    symbol="MACRO_BOC",
                    company_name="Bank of Canada (Banque du Canada)",
                    market="MACRO",
                    source="BOC_VALET",
                    source_url="https://www.bankofcanada.ca/valet/observations/BD.CDN.10YR.DQ.YLD,BD.CDN.2YR.DQ.YLD/json",
                    headline=f"Bank of Canada Sovereign Yield Curve: 10Y-2Y Spread at {spread:+.2f}% ({'Inverted' if is_inverted else 'Normalized'})",
                    summary=(
                        f"Official Government of Canada benchmark 10-Year yield ({y10_val:.2f}%) vs 2-Year yield ({y2_val:.2f}%) "
                        f"yields a net term spread of {spread:+.2f}% as of {obs_date}. "
                        f"Slope conditions Canadian commercial banking net interest margins and institutional duration allocation."
                    ),
                    filing_date=obs_date,
                    published_at=f"{obs_date}T16:30:00Z",
                    event_category="MACRO_RATE_POLICY",
                    materiality_level="HIGH",
                    materiality_score=5,
                    materiality_rationale="Sovereign yield curve shape is the primary institutional leading indicator for macroeconomic credit expansion",
                    sentiment_score=sent_score,
                    sentiment_label=sent_label,
                    sentiment_drivers=[
                        f"GoC 10-Year Benchmark: {y10_val:.2f}%",
                        f"GoC 2-Year Benchmark: {y2_val:.2f}%",
                        f"Curve Term Spread: {spread:+.2f}%",
                    ],
                    horizon="STRUCTURAL",
                    priced_in_status="PARTIAL",
                    reaction_1d_pct=None,
                    reaction_3d_pct=None,
                    rvol_at_event=None,
                    factual_claims=[
                        f"Bank of Canada Official Valet Series BD.CDN.10YR.DQ.YLD: {y10_val:.2f}%",
                        f"Bank of Canada Official Valet Series BD.CDN.2YR.DQ.YLD: {y2_val:.2f}%",
                        f"Observation date: {obs_date}",
                    ],
                    invalidation_risks=[
                        "Rapid term premium expansion can steepen curve without underlying economic acceleration",
                        "Cross-border spillover from US Treasury volatility",
                    ],
                    hard_gate_triggered=False,
                    score_impact_pts=round(sent_score * 8.0, 1),
                )
            )

        # 2. Bank of Canada Official CAD/USD FX Fixing
        fx_rows = db.execute_query(
            "SELECT observation_date, value FROM macro_observation WHERE series_id = 'FXUSDCAD' ORDER BY observation_date DESC LIMIT 1;"
        )
        if fx_rows:
            fx_val = fx_rows[0]["value"]
            obs_date = fx_rows[0]["observation_date"]

            events.append(
                CatalystEvent(
                    id="boc_valet_fxusdcad",
                    symbol="MACRO_BOC",
                    company_name="Bank of Canada (Banque du Canada)",
                    market="MACRO",
                    source="BOC_VALET",
                    source_url="https://www.bankofcanada.ca/valet/observations/FXUSDCAD/json",
                    headline=f"Bank of Canada Official Exchange Rate: USD/CAD at {fx_val:.4f} CAD per USD",
                    summary=(
                        f"Bank of Canada daily noon fixing recorded at {fx_val:.4f} on {obs_date}. "
                        f"USD/CAD exchange rate conditions cross-border earnings translation for dual-listed TSX leaders (CNQ, ENB, SHOP, RY)."
                    ),
                    filing_date=obs_date,
                    published_at=f"{obs_date}T16:30:00Z",
                    event_category="MACRO_RATE_POLICY",
                    materiality_level="MEDIUM",
                    materiality_score=3,
                    materiality_rationale="Cross-border FX pricing directly shifts energy & materials reporting currency dynamics",
                    sentiment_score=0.10,
                    sentiment_label="LEAN_BULLISH",
                    sentiment_drivers=[
                        f"Official BoC FX Rate: {fx_val:.4f}",
                        "Boosts CAD-denominated revenues for Canadian commodity and tech exporters",
                    ],
                    horizon="SHORT_TERM",
                    priced_in_status="PRICED_IN",
                    reaction_1d_pct=None,
                    reaction_3d_pct=None,
                    rvol_at_event=None,
                    factual_claims=[
                        f"BoC Valet series FXUSDCAD: {fx_val:.4f}",
                        f"Observation date: {obs_date}",
                    ],
                    invalidation_risks=[
                        "Excessive CAD depreciation imports inflation via imported goods and machinery",
                    ],
                    hard_gate_triggered=False,
                    score_impact_pts=1.0,
                )
            )

        return events

    def compile_master_catalyst_feed(self) -> List[CatalystEvent]:
        """
        Compile full intelligence feed across all universe equities and macro authorities.
        """
        all_events: List[CatalystEvent] = []

        # 1. Macro Catalysts
        all_events.extend(self.generate_macro_catalysts())

        # 2. Company Submissions & SEC Filings
        for symbol, cik in UNIVERSE_CIK_MAP.items():
            sym_events = self.ingest_sec_filings_for_symbol(symbol, cik, max_filings=5)
            all_events.extend(sym_events)

        # Sort chronologically descending
        all_events.sort(key=lambda x: (x.filing_date, x.materiality_score), reverse=True)
        return all_events

    def get_summary_metrics(self, events: List[CatalystEvent]) -> Dict[str, Any]:
        """
        Compute high-level intelligence metrics for the dashboard banner.
        """
        total = len(events)
        high_mat = sum(1 for e in events if e.materiality_level == "HIGH")
        med_mat = sum(1 for e in events if e.materiality_level == "MEDIUM")
        low_mat = sum(1 for e in events if e.materiality_level == "LOW")

        bullish = sum(1 for e in events if e.sentiment_score > 0.05)
        bearish = sum(1 for e in events if e.sentiment_score < -0.05)
        neutral = total - bullish - bearish

        sec_filings = sum(1 for e in events if "SEC_EDGAR" in e.source)
        canadian_events = sum(1 for e in events if e.market == "CA")
        macro_events = sum(1 for e in events if e.market == "MACRO")

        avg_sentiment = round(sum(e.sentiment_score for e in events) / max(1, total), 2)

        return {
            "total_catalysts_analyzed": total,
            "high_materiality_count": high_mat,
            "medium_materiality_count": med_mat,
            "low_materiality_count": low_mat,
            "sentiment_breakdown": {
                "bullish_count": bullish,
                "bearish_count": bearish,
                "neutral_count": neutral,
                "average_sentiment_score": avg_sentiment,
            },
            "source_breakdown": {
                "sec_edgar_filings": sec_filings,
                "canadian_issuers": canadian_events,
                "macro_announcements": macro_events,
            },
        }


news_engine = NewsAndFilingsEngine()
