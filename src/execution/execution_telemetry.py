"""
Phase 34.5 -- Execution Telemetry
US + Canada Market Intelligence Platform

Aggregates the risk governor's decision ledger into the session-level metrics Predicate 28
(`PRE_TRADE_RISK_BREACH`) reports on.

Two properties of the ledger shape this module, and getting either wrong produces plausible
but meaningless numbers:

  1. `risk_governor_decision` holds ONE ROW PER CONTROL EVALUATION -- twelve rows per
     governor run. Counting rows would overstate every figure by 12x. An evaluation is
     identified by the pair (order_id, decided_at); all twelve rows of a single run share
     both.

  2. The same order_id can be evaluated SEVERAL TIMES, because order_id is derived
     deterministically from client_order_id and a retry reproduces it. An order may
     therefore carry both an APPROVE and a HARD_REJECT verdict across its history. The
     overall verdict of one evaluation is the MAX severity across its twelve control rows,
     not any single row.

Telemetry is counted PER EVALUATION rather than per order. Predicate 28 asks how often the
risk envelope is refusing what the signals generate, and each evaluation is one such
attempt; a strategy that retries into the same wall three times has produced three
rejections worth noticing. Per-order figures are exposed alongside so the distinction is
visible rather than buried.

No profitability claim is made here. These figures describe the alignment between signal
generation and the declared risk envelope, not expected returns.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.data.db import Database, db as _default_db

# Mirrors risk_governor._SEVERITY. Kept local so the SQL can rank verdicts without
# importing the governor, but the ordering must stay identical.
_SEVERITY_SQL = "CASE verdict WHEN 'HARD_REJECT' THEN 2 WHEN 'SOFT_LIMIT' THEN 1 ELSE 0 END"


@dataclass
class ExecutionTelemetry:
    """Session-level view of the governor's decision ledger."""
    session: str                                  # UTC date, YYYY-MM-DD
    evaluations: int = 0                          # distinct (order_id, decided_at) runs
    orders: int = 0                               # distinct order_id
    hard_rejections: int = 0                      # evaluations concluding HARD_REJECT
    soft_limits: int = 0
    approvals: int = 0
    submitted_notional_usd: float = 0.0           # sum across all evaluations
    rejected_notional_usd: float = 0.0            # sum across HARD_REJECT evaluations
    breached_controls: Dict[str, int] = field(default_factory=dict)
    top_rejected_symbols: List[Dict[str, Any]] = field(default_factory=list)
    generated_at: str = ""

    @property
    def rejected_share(self) -> float:
        """
        Rejected notional as a fraction of submitted notional. Zero when nothing was
        submitted, so a quiet session cannot divide by zero into a spurious breach.
        """
        if self.submitted_notional_usd <= 0:
            return 0.0
        return self.rejected_notional_usd / self.submitted_notional_usd

    def to_dict(self) -> Dict[str, Any]:
        d = dict(
            session=self.session,
            evaluations=self.evaluations,
            orders=self.orders,
            hard_rejections=self.hard_rejections,
            soft_limits=self.soft_limits,
            approvals=self.approvals,
            submitted_notional_usd=round(self.submitted_notional_usd, 2),
            rejected_notional_usd=round(self.rejected_notional_usd, 2),
            rejected_share=round(self.rejected_share, 6),
            breached_controls=dict(sorted(self.breached_controls.items())),
            top_rejected_symbols=self.top_rejected_symbols,
            generated_at=self.generated_at,
        )
        return d


def compute_execution_telemetry(
    db: Optional[Database] = None,
    session: Optional[str] = None,
) -> ExecutionTelemetry:
    """
    Aggregates the current (or given) UTC session from `risk_governor_decision`.

    A session with no rows returns a zeroed telemetry object rather than raising, because
    "the governor never ran" is a normal state for a platform that has not traded today.
    """
    conn = db or _default_db
    session = session or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now_str = datetime.now(timezone.utc).isoformat()

    try:
        conn.execute_query("SELECT 1 FROM risk_governor_decision LIMIT 1;")
    except Exception:
        return ExecutionTelemetry(session=session, generated_at=now_str)

    # One row per evaluation. `decided_at` is stored as ISO-8601 UTC, so its first ten
    # characters are the UTC date; matching on that avoids any timezone arithmetic.
    evaluations = conn.execute_query(
        f"""
        SELECT order_id,
               decided_at,
               MAX({_SEVERITY_SQL})          AS severity,
               MAX(order_notional_usd)       AS notional,
               MAX(symbol)                   AS symbol,
               MAX(market)                   AS market
        FROM risk_governor_decision
        WHERE substr(decided_at, 1, 10) = ?
        GROUP BY order_id, decided_at;
        """,
        (session,),
    )

    tel = ExecutionTelemetry(session=session, generated_at=now_str)
    tel.evaluations = len(evaluations)
    tel.orders = len({e["order_id"] for e in evaluations})

    rejected_by_symbol: Dict[str, float] = {}
    for e in evaluations:
        notional = float(e["notional"] or 0.0)
        tel.submitted_notional_usd += notional
        sev = int(e["severity"])
        if sev == 2:
            tel.hard_rejections += 1
            tel.rejected_notional_usd += notional
            sym = e["symbol"] or "UNKNOWN"
            rejected_by_symbol[sym] = rejected_by_symbol.get(sym, 0.0) + notional
        elif sev == 1:
            tel.soft_limits += 1
        else:
            tel.approvals += 1

    # Controls that actually breached, counted per evaluation rather than per row.
    for row in conn.execute_query(
        """
        SELECT control_id, COUNT(*) AS n FROM (
            SELECT DISTINCT control_id, order_id, decided_at
            FROM risk_governor_decision
            WHERE breach = 1 AND substr(decided_at, 1, 10) = ?
        ) GROUP BY control_id ORDER BY control_id;
        """,
        (session,),
    ):
        tel.breached_controls[row["control_id"]] = int(row["n"])

    tel.top_rejected_symbols = [
        {"symbol": s, "rejected_notional_usd": round(v, 2)}
        for s, v in sorted(rejected_by_symbol.items(), key=lambda kv: -kv[1])[:5]
    ]
    return tel


__all__ = ["ExecutionTelemetry", "compute_execution_telemetry"]


# --------------------------------------------------------------------------------------
# Feed export (Phase 34.5)
# --------------------------------------------------------------------------------------

def build_default_registry():
    """
    The registry the platform actually ships.

    Exactly one adapter: the internal simulator. No external adapter is registered because
    the Alpaca terms prohibit commercial use (docs/16 §1.4) and CIRO DMR 3200 bars Canadian
    symbols from external order-execution-only dealers. The registry and router exist so a
    licensed venue can be attached later; none is attached now.
    """
    from src.execution.adapters.internal_simulator import InternalSimulatorAdapter
    from src.execution.router import AdapterRegistry
    from src.execution.state_machine import ExecutionLedger

    reg = AdapterRegistry()
    reg.register(InternalSimulatorAdapter(ledger=ExecutionLedger(), db=_default_db))
    return reg


def export_execution_gateway_feed(
    db: Optional[Database] = None,
    output_path: Optional[str] = None,
    session: Optional[str] = None,
) -> str:
    """
    Writes `data/feeds/execution_gateway.json`.

    Surfaces execution telemetry, Predicate 28's status, the adapter inventory with its
    licensing and externality flags, the kill switch state, and the annual AOS attestation.
    The adapter flags are published rather than hidden so a reader can see for themselves
    that nothing external is registered.
    """
    import json
    from config.settings import DATA_DIR
    from src.engine.quant_intel_sentinel import (
        P28_CRITICAL_REJECTIONS,
        P28_HARD_REJECTION_TRIGGER,
        P28_REJECTED_NOTIONAL_SHARE,
        quant_intel_sentinel,
    )
    from src.execution.aos_attestation import artifact_digest, latest_attestation
    from src.execution.order_model import OrderType
    from src.execution.router import KillSwitch

    conn = db or _default_db
    tel = compute_execution_telemetry(db=conn, session=session)
    alerts = quant_intel_sentinel.evaluate_execution_sentinel(tel)
    fired = alerts[0] if alerts else None

    registry = build_default_registry()
    kill_switch = KillSwitch(db=conn)
    attestation = latest_attestation(conn)

    feed = {
        "as_of_date": tel.session,
        "generated_at": tel.generated_at,
        "session": tel.to_dict(),
        "predicate_28": {
            "predicate_type": "PRE_TRADE_RISK_BREACH",
            "fired": fired is not None,
            "severity": fired.severity if fired else None,
            "action_required": fired.action_required if fired else None,
            "headline": fired.headline if fired else None,
            "body": fired.body if fired else None,
            "thresholds": {
                "hard_rejection_trigger": P28_HARD_REJECTION_TRIGGER,
                "critical_rejections": P28_CRITICAL_REJECTIONS,
                "rejected_notional_share": P28_REJECTED_NOTIONAL_SHARE,
                "boundary_exclusive": True,
            },
        },
        "adapters": [
            {
                "adapter_id": a.adapter_id,
                "is_external": a.is_external,
                "license_cleared": a.license_cleared,
                "configured": a.is_configured(),
                "markets": sorted(m.value for m in a.markets),
                "order_types": sorted(
                    t.value for t in OrderType if a.capabilities.supports(t)
                ),
            }
            for a in registry.all()
        ],
        "external_adapters_registered": sum(1 for a in registry.all() if a.is_external),
        "canadian_routing": {
            "external_routing_permitted": False,
            "basis": ("CIRO / IIROC Dealer Member Rule 3200 A.1.(b)(i) and NI 23-103 "
                      "s.1.2(1): an order-execution-only dealer may not let a client "
                      "generate orders through its own automated order system."),
        },
        "kill_switch": {
            "active_scopes": kill_switch.active_scopes(),
            "halted": kill_switch.is_halted(),
            "events_recorded": len(kill_switch.events()),
        },
        "aos_attestation": (
            {**attestation.to_dict(), "artifact_sha256": artifact_digest(attestation.artifact_path)}
            if attestation else None
        ),
        "disclaimers": [
            "Decision-support and research only. Not investment advice and not a "
            "recommendation to buy or sell any security.",
            "No profitability claim is made. Execution telemetry describes the alignment "
            "between generated signals and the declared risk envelope, not expected returns.",
            "Paper simulation only. No order in this system routes to a live venue.",
            "No external broker adapter is registered. The internal simulator is the only "
            "execution venue.",
            "Canadian symbols never route to an external adapter. This is a regulatory "
            "constraint, not a configuration preference.",
        ],
    }

    path = output_path or str(DATA_DIR / "feeds" / "execution_gateway.json")
    # Create the parent as every other feed exporter does. Without this the first run
    # against a clean checkout -- or any redirected output directory -- fails outright.
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(feed, f, indent=2)
    return path
