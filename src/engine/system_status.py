"""
HONESTY OVERHAUL — Phase 2: System status.

Replaces src/engine/production_scale.py, which published an entire fictional
operations business: 99.98% rolling uptime against a 99.90% SLA, eight
"OPERATIONAL" components with per-component latency figures, two resolved
incidents with timestamps, a SOC 2 control matrix, disaster-recovery drills, a
capacity model with per-tier unit economics and gross margins, five licensed
subscribers with monthly exchange accruals, and exchange declarations reading
"We certify under penalty of contract termination…".

None of that was measured. This is a static site on Firebase Hosting with no
backend, so there is no uptime to measure, no components to monitor, no
subscribers, and no certification to attest to.

What this module does publish is small and entirely derived from real artifacts:
when the pipeline last ran, how old the data is, how many feeds were written,
and what the data-quality ledger actually contains.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.data.db import db
from src.data.market_calendar import closed_market_reason, observed_trading_dates

# Beyond one trading day the data is stale; beyond two, badly stale. These are
# policy thresholds, not statistics.
STALE_AFTER_HOURS = 24.0
VERY_STALE_AFTER_HOURS = 48.0


class SystemStatusEngine:
    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]

    # -- measured inputs ---------------------------------------------------
    def _bar_stats(self) -> Dict[str, Any]:
        row = db.execute_query(
            """
            SELECT COUNT(*) AS bars,
                   COUNT(DISTINCT security_id) AS symbols,
                   MIN(trading_date) AS first_date,
                   MAX(trading_date) AS last_date
            FROM bar_1d
            """
        )
        r = row[0] if row else {}
        return {
            "bars": r.get("bars") or 0,
            "symbols": r.get("symbols") or 0,
            "first_date": r.get("first_date"),
            "last_date": r.get("last_date"),
        }

    @staticmethod
    def _hours_since_date(value: Optional[str]) -> Optional[float]:
        if not value:
            return None
        try:
            last = datetime.strptime(str(value)[:10], "%Y-%m-%d")
        except ValueError:
            return None
        return round((datetime.now() - last).total_seconds() / 3600.0, 1)

    def _newest_feed_timestamp(self, feeds_dir: Path) -> Optional[str]:
        """Most recent generated_at across the written feeds = last pipeline run."""
        newest: Optional[str] = None
        for p in sorted(feeds_dir.glob("*.json")):
            try:
                with open(p) as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            stamp = data.get("generated_at") if isinstance(data, dict) else None
            if isinstance(stamp, str) and (newest is None or stamp > newest):
                newest = stamp
        return newest

    def _quality_events(self) -> List[Dict[str, Any]]:
        rows = db.execute_query(
            """
            SELECT dimension, severity, status, COUNT(*) AS n
            FROM data_quality_event
            GROUP BY dimension, severity, status
            ORDER BY severity DESC, dimension
            """
        )
        return [dict(r) for r in (rows or [])]

    # -- feed --------------------------------------------------------------
    def generate_feed(self, feeds_dir: Optional[Path] = None) -> Dict[str, Any]:
        root = feeds_dir or (self.repo_root / "data" / "feeds")
        bars = self._bar_stats()
        last_run = self._newest_feed_timestamp(root)
        data_age_hours = self._hours_since_date(bars["last_date"])

        feed_files = sorted(p.name for p in root.glob("*.json"))

        # A closed market is not stale data. The hours-only ladder below called every
        # weekend morning STALE even when the Friday close was the newest close that
        # exists. The verdict now comes from the ingested bars: see market_calendar.
        us_verdict = closed_market_reason(
            "NYSE", bars["last_date"], observed=observed_trading_dates(["NYSE", "NASDAQ", "AMEX"]))
        ca_verdict = closed_market_reason(
            "TSX", bars["last_date"], observed=observed_trading_dates(["TSX", "TSXV"]))

        _SEVERITY = {"CLOSED_MARKET": 0, "CURRENT": 0, "UNKNOWN": 1,
                     "UNVERIFIED_GAP": 2, "AGING": 3, "STALE": 4}

        if data_age_hours is None:
            hours_label = "UNKNOWN"
        elif data_age_hours <= STALE_AFTER_HOURS:
            hours_label = "CURRENT"
        elif data_age_hours <= VERY_STALE_AFTER_HOURS:
            hours_label = "AGING"
        else:
            hours_label = "STALE"

        if us_verdict["market_closed"] and ca_verdict["market_closed"]:
            age_label = "CLOSED_MARKET"
            age_reason = us_verdict["reason"]
        else:
            age_label = hours_label if _SEVERITY.get(hours_label, 1) >= max(
                _SEVERITY.get(us_verdict["badge"], 1), _SEVERITY.get(ca_verdict["badge"], 1)
            ) else max((us_verdict["badge"], ca_verdict["badge"]),
                       key=lambda b: _SEVERITY.get(b, 1))
            age_reason = (us_verdict["reason"]
                          if _SEVERITY.get(us_verdict["badge"], 1)
                          >= _SEVERITY.get(ca_verdict["badge"], 1) else ca_verdict["reason"])
        age_label = "CURRENT" if age_label == "CLOSED_MARKET" and data_age_hours is not None \
            and data_age_hours <= STALE_AFTER_HOURS else age_label

        quality = self._quality_events()
        open_issues = sum(r["n"] for r in quality if r.get("status") not in ("RESOLVED", "CLOSED"))

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "last_pipeline_run": last_run,
            "last_pipeline_run_note": (
                "Most recent generated_at across the written feed files."
                if last_run else "No feed carries a generated_at timestamp."
            ),
            "data": {
                "bars": bars["bars"],
                "symbols": bars["symbols"],
                "first_date": bars["first_date"],
                "last_date": bars["last_date"],
                "age_hours": data_age_hours,
                "age_label": age_label,
                "age_reason": age_reason,
                "market_closed": bool(us_verdict["market_closed"] and ca_verdict["market_closed"]),
                # Read from the calendar rather than asserted. This was hard-coded
                # False while the calendar modelled no holidays; it now derives each
                # exchange's published holiday rule, so publishing a literal False
                # here would be a false statement about our own data.
                "holiday_modelled": bool(
                    us_verdict["market_state"].get("holiday_modelled")
                    and ca_verdict["market_state"].get("holiday_modelled")
                ),
                "market_detail": {
                    "us": {
                        "badge": us_verdict["badge"],
                        "state": us_verdict["market_state"]["state"],
                        "holiday": us_verdict["market_state"].get("holiday"),
                        "local_time": us_verdict["market_state"].get("local_time"),
                        "sessions_completed_since_last_bar": us_verdict["sessions"].get("completed"),
                        "weekdays_unverified": us_verdict["sessions"].get("unverified"),
                        "reason": us_verdict["reason"],
                    },
                    "ca": {
                        "badge": ca_verdict["badge"],
                        "state": ca_verdict["market_state"]["state"],
                        "holiday": ca_verdict["market_state"].get("holiday"),
                        "local_time": ca_verdict["market_state"].get("local_time"),
                        "sessions_completed_since_last_bar": ca_verdict["sessions"].get("completed"),
                        "weekdays_unverified": ca_verdict["sessions"].get("unverified"),
                        "reason": ca_verdict["reason"],
                    },
                },
                "survivorship_biased": True,
                "universe_note": (
                    "The universe is %d currently listed large-cap symbols. It is "
                    "survivorship-biased: delisted and acquired constituents are "
                    "absent, so statistics computed across it are optimistically biased."
                    % bars["symbols"]
                ),
            },
            "feeds_written": len(feed_files),
            "feed_files": feed_files,
            "data_quality": {
                "open_issues": open_issues,
                "by_category": quality,
                "note": (
                    "Rows from the data_quality_event ledger. Nothing here is "
                    "asserted beyond what that table contains."
                ),
            },
            "what_this_is_not": [
                "There is no uptime figure. This is a static site on Firebase "
                "Hosting with no backend service to measure.",
                "There are no subscribers, tiers, or entitlements. This is a "
                "personal research dashboard, not a product.",
                "There is no SOC 2 report, no audit, and no disaster-recovery "
                "programme. None has ever been performed.",
                "There is no exchange data agreement. Market data comes from "
                "Yahoo Finance's unofficial chart API.",
                "There is no broker connection. No orders are ever sent.",
            ],
        }


system_status_engine = SystemStatusEngine()
