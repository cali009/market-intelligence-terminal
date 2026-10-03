"""
Phase 34.2 -- Pre-Trade Risk Governor
US + Canada Market Intelligence Platform

Twelve controls, evaluated on every order before it reaches any adapter. The control
taxonomy is taken from UMIR Rule 7.1 / Policy 7.1 Parts 7 and 8 rather than invented:
credit and capital thresholds, per-client limits, and limits on the value or volume of
unexecuted orders for a particular security or class of securities.

Three properties are load-bearing and are each covered by tests:

1. THE GOVERNOR NEVER RESIZES AN ORDER. A soft breach is reported with its limit, the
   observed value, and a reason code. It is never quietly "fixed" into a smaller size --
   that is how a risk limit becomes theatre.
2. AN OVERRIDE CANNOT BYPASS A HARD REJECT. Overrides downgrade SOFT_LIMIT to APPROVE and
   nothing more. Capital, regulatory, and data-integrity controls are not negotiable.
3. LIMITS COME FROM THE GOVERNOR, NEVER FROM THE REQUEST. A request selects a risk
   profile by name; it cannot supply limit values.

Severity ordering: HARD_REJECT > SOFT_LIMIT > APPROVE. The most severe control outcome
determines the overall decision, so a single hard breach can never be outvoted.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from src.data.db import db as _default_db
from src.execution.order_model import (
    Actor,
    Market,
    OrderRequest,
    RejectionReason,
)


class Verdict(str, Enum):
    APPROVE = "APPROVE"
    SOFT_LIMIT = "SOFT_LIMIT"
    HARD_REJECT = "HARD_REJECT"


# Severity ordering used for aggregation. Higher wins.
_SEVERITY = {Verdict.APPROVE: 0, Verdict.SOFT_LIMIT: 1, Verdict.HARD_REJECT: 2}


# --------------------------------------------------------------------------------------
# Limits -- the only source of truth for thresholds
# --------------------------------------------------------------------------------------

GOVERNOR_LIMITS: Dict[str, Dict[str, float]] = {
    "CONSERVATIVE": {
        "buying_power_pct": 70.0,
        "position_weight_pct": 10.0,
        "country_exposure_pct": 60.0,
        "unexecuted_value_pct": 15.0,
        "open_order_count": 10,
        "per_order_ceiling_usd": 10_000.0,
        "sector_concentration_pct": 20.0,
        "correlation_max": 0.75,
        "portfolio_beta_max": 1.10,
        "daily_gross_pct": 100.0,
    },
    "MODERATE": {
        "buying_power_pct": 90.0,
        "position_weight_pct": 18.0,
        "country_exposure_pct": 75.0,
        "unexecuted_value_pct": 25.0,
        "open_order_count": 25,
        "per_order_ceiling_usd": 25_000.0,
        "sector_concentration_pct": 30.0,
        "correlation_max": 0.85,
        "portfolio_beta_max": 1.30,
        "daily_gross_pct": 200.0,
    },
    "AGGRESSIVE": {
        "buying_power_pct": 95.0,
        "position_weight_pct": 25.0,
        "country_exposure_pct": 85.0,
        "unexecuted_value_pct": 35.0,
        "open_order_count": 50,
        "per_order_ceiling_usd": 40_000.0,
        "sector_concentration_pct": 40.0,
        "correlation_max": 0.92,
        "portfolio_beta_max": 1.50,
        "daily_gross_pct": 300.0,
    },
}

DEFAULT_PROFILE = "MODERATE"


# --------------------------------------------------------------------------------------
# Control registry
# --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ControlSpec:
    control_id: str
    name: str
    verdict_on_breach: Verdict
    reason_code: RejectionReason
    limit_key: Optional[str] = None   # None for controls without a numeric threshold


# The eight HARD_REJECT controls are capital, regulatory, or data-integrity constraints.
# The four SOFT_LIMIT controls are portfolio-shape concerns where an informed override is
# legitimate. See docs/16 section 3.1.
CONTROL_REGISTRY: List[ControlSpec] = [
    ControlSpec("C1", "Buying power / capital threshold", Verdict.HARD_REJECT,
                RejectionReason.RISK_C1_BUYING_POWER, "buying_power_pct"),
    ControlSpec("C2", "Single-position weight cap", Verdict.HARD_REJECT,
                RejectionReason.RISK_C2_POSITION_WEIGHT, "position_weight_pct"),
    ControlSpec("C3", "Country exposure cap", Verdict.HARD_REJECT,
                RejectionReason.RISK_C3_COUNTRY_EXPOSURE, "country_exposure_pct"),
    ControlSpec("C4", "Unexecuted order value per security", Verdict.HARD_REJECT,
                RejectionReason.RISK_C4_UNEXECUTED_VALUE, "unexecuted_value_pct"),
    ControlSpec("C5", "Open order count cap", Verdict.SOFT_LIMIT,
                RejectionReason.RISK_C5_OPEN_ORDER_COUNT, "open_order_count"),
    ControlSpec("C6", "Per-order notional ceiling", Verdict.HARD_REJECT,
                RejectionReason.RISK_C6_PER_ORDER_CEILING, "per_order_ceiling_usd"),
    ControlSpec("C7", "Sector concentration", Verdict.SOFT_LIMIT,
                RejectionReason.RISK_C7_SECTOR_CONCENTRATION, "sector_concentration_pct"),
    ControlSpec("C8", "Correlation to existing book", Verdict.SOFT_LIMIT,
                RejectionReason.RISK_C8_CORRELATION_TO_BOOK, "correlation_max"),
    ControlSpec("C9", "Beta-adjusted portfolio exposure", Verdict.SOFT_LIMIT,
                RejectionReason.RISK_C9_BETA_EXPOSURE, "portfolio_beta_max"),
    ControlSpec("C10", "Maximum daily gross traded", Verdict.HARD_REJECT,
                RejectionReason.RISK_C10_DAILY_GROSS_TRADED, "daily_gross_pct"),
    ControlSpec("C11", "Restricted list / stale data guard", Verdict.HARD_REJECT,
                RejectionReason.RISK_C11_RESTRICTED_OR_STALE, None),
    ControlSpec("C12", "Canadian external-routing firewall", Verdict.HARD_REJECT,
                RejectionReason.RISK_C12_CA_EXTERNAL_FIREWALL, None),
]

CONTROL_BY_ID = {c.control_id: c for c in CONTROL_REGISTRY}

# Data older than this many sessions cannot support a pre-trade decision.
MAX_DATA_AGE_SESSIONS = 1.0


# --------------------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------------------

@dataclass
class GovernorContext:
    """
    Everything the governor needs, supplied by the caller.

    Deliberately explicit rather than reaching into engines: the governor must be testable
    in isolation, and every input must be visible in the decision snapshot.
    """
    book_value_usd: float
    free_cash_usd: float                      # already net of reserved notional
    reserved_notional_usd: float = 0.0
    reference_price: float = 0.0              # in the symbol's own currency
    fx_cad_usd: float = 1.0                   # CAD -> USD
    position_value_usd: Dict[str, float] = field(default_factory=dict)
    country_exposure_usd: Dict[str, float] = field(default_factory=dict)
    sector_exposure_usd: Dict[str, float] = field(default_factory=dict)
    unexecuted_value_usd: Dict[str, float] = field(default_factory=dict)
    open_order_count: int = 0
    daily_gross_traded_usd: float = 0.0
    correlation_to_book: float = 0.0
    portfolio_beta: float = 1.0
    data_age_sessions: float = 0.0
    restricted_symbols: frozenset = frozenset()
    symbol_sector: Optional[str] = None
    target_adapter_id: Optional[str] = None
    target_adapter_is_external: bool = False


@dataclass
class GovernorOverride:
    """An explicit, attributed acknowledgement of a soft breach."""
    actor: Actor
    justification: str
    control_ids: frozenset = frozenset()   # empty = override every soft breach

    def __post_init__(self):
        if not self.justification or not self.justification.strip():
            raise ValueError("An override requires a written justification.")


# --------------------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------------------

@dataclass
class ControlOutcome:
    control_id: str
    control_name: str
    verdict: Verdict
    breach: bool
    limit_value: Optional[float]
    observed_value: Optional[float]
    reason_code: Optional[str]
    detail: str
    overridden: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "control_id": self.control_id,
            "control_name": self.control_name,
            "verdict": self.verdict.value,
            "breach": self.breach,
            "limit_value": self.limit_value,
            "observed_value": self.observed_value,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "overridden": self.overridden,
        }


@dataclass
class GovernorDecision:
    client_order_id: str
    symbol: str
    market: str
    quantity: float                      # echoed back unchanged -- see the no-resize rule
    order_notional_usd: float
    risk_profile: str
    overall: Verdict
    outcomes: List[ControlOutcome]
    limits_snapshot: Dict[str, float]
    override_applied: bool
    rejection_reason: Optional[str]
    decided_at: str

    @property
    def approved(self) -> bool:
        return self.overall == Verdict.APPROVE

    @property
    def breaches(self) -> List[ControlOutcome]:
        return [o for o in self.outcomes if o.breach]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "market": self.market,
            "quantity": self.quantity,
            "order_notional_usd": self.order_notional_usd,
            "risk_profile": self.risk_profile,
            "overall": self.overall.value,
            "outcomes": [o.to_dict() for o in self.outcomes],
            "limits_snapshot": self.limits_snapshot,
            "override_applied": self.override_applied,
            "rejection_reason": self.rejection_reason,
            "decided_at": self.decided_at,
        }


# --------------------------------------------------------------------------------------
# Governor
# --------------------------------------------------------------------------------------

class PreTradeRiskGovernor:
    """Evaluates all twelve controls and returns a single aggregated decision."""

    def __init__(self, limits: Optional[Dict[str, Dict[str, float]]] = None):
        self.limits = limits or GOVERNOR_LIMITS

    # ------------------------------------------------------------------ helpers

    def resolve_profile(self, requested: str) -> str:
        """
        Resolves a profile NAME to a known key. An unknown name falls back to the default
        rather than failing open -- and never to a wider profile than requested.
        """
        key = str(requested or "").upper().strip()
        return key if key in self.limits else DEFAULT_PROFILE

    def order_notional_usd(self, request: OrderRequest, ctx: GovernorContext) -> float:
        """Order value in USD, FX-normalized. Canadian notional is converted at ctx.fx."""
        fx = ctx.fx_cad_usd if request.market == Market.CA else 1.0
        return float(request.quantity) * float(ctx.reference_price) * float(fx)

    # ------------------------------------------------------------------ evaluate

    def evaluate(
        self,
        request: OrderRequest,
        ctx: GovernorContext,
        override: Optional[GovernorOverride] = None,
    ) -> GovernorDecision:
        """
        Runs every control and aggregates. This method does NOT mutate `request`.
        """
        profile = self.resolve_profile(request.risk_profile)
        limits = self.limits[profile]
        notional = self.order_notional_usd(request, ctx)

        outcomes = [self._run(spec, request, ctx, limits, notional)
                    for spec in CONTROL_REGISTRY]

        override_applied = False
        if override is not None:
            override_applied = self._apply_override(outcomes, override)

        overall = max((o.verdict for o in outcomes), key=lambda v: _SEVERITY[v])

        rejection_reason: Optional[str] = None
        if overall == Verdict.HARD_REJECT:
            # Report the first hard breach in registry order, so attribution is stable.
            rejection_reason = next(
                o.reason_code for o in outcomes if o.verdict == Verdict.HARD_REJECT
            )

        return GovernorDecision(
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            market=request.market.value,
            quantity=float(request.quantity),   # unchanged, always
            order_notional_usd=round(notional, 6),
            risk_profile=profile,
            overall=overall,
            outcomes=outcomes,
            limits_snapshot=dict(limits),
            override_applied=override_applied,
            rejection_reason=rejection_reason,
            decided_at=datetime.now(timezone.utc).isoformat(),
        )

    def _apply_override(
        self, outcomes: List[ControlOutcome], override: GovernorOverride
    ) -> bool:
        """
        Downgrades SOFT_LIMIT breaches to APPROVE. HARD_REJECT is never touched.

        Returns True only if something was actually downgraded, so a no-op override is
        not recorded as though it changed the decision.
        """
        applied = False
        for o in outcomes:
            if o.verdict != Verdict.SOFT_LIMIT or not o.breach:
                continue
            if override.control_ids and o.control_id not in override.control_ids:
                continue
            o.verdict = Verdict.APPROVE
            o.overridden = True
            applied = True
        return applied

    # ------------------------------------------------------------------ controls

    def _run(
        self,
        spec: ControlSpec,
        request: OrderRequest,
        ctx: GovernorContext,
        limits: Dict[str, float],
        notional: float,
    ) -> ControlOutcome:
        handler = getattr(self, f"_c{spec.control_id[1:].lower()}")
        limit_value, observed, breach, detail = handler(request, ctx, limits, notional)
        verdict = spec.verdict_on_breach if breach else Verdict.APPROVE
        return ControlOutcome(
            control_id=spec.control_id,
            control_name=spec.name,
            verdict=verdict,
            breach=breach,
            limit_value=limit_value,
            observed_value=observed,
            reason_code=spec.reason_code.value if breach else None,
            detail=detail,
        )

    def _c1(self, request, ctx, limits, notional):
        """UMIR 7.1 Part 7(a): exceeding pre-determined credit or capital thresholds."""
        cap = ctx.free_cash_usd * (limits["buying_power_pct"] / 100.0)
        breach = notional > cap
        return (round(cap, 2), round(notional, 2), breach,
                f"Order notional ${notional:,.2f} vs buying-power cap ${cap:,.2f} "
                f"({limits['buying_power_pct']:.0f}% of ${ctx.free_cash_usd:,.2f} free cash)")

    def _c2(self, request, ctx, limits, notional):
        """UMIR 7.1 Part 7(b): per-client limits -- single-position concentration."""
        if ctx.book_value_usd <= 0:
            return (None, None, True, "Book value is zero; position weight is undefined")
        existing = ctx.position_value_usd.get(request.symbol, 0.0)
        projected_pct = ((existing + notional) / ctx.book_value_usd) * 100.0
        cap = limits["position_weight_pct"]
        breach = projected_pct > cap
        return (cap, round(projected_pct, 4), breach,
                f"{request.symbol} projected weight {projected_pct:.2f}% vs cap {cap:.2f}%")

    def _c3(self, request, ctx, limits, notional):
        """UMIR 7.1 Part 7(b): per-client limits -- country exposure."""
        if ctx.book_value_usd <= 0:
            return (None, None, True, "Book value is zero; country exposure is undefined")
        existing = ctx.country_exposure_usd.get(request.market.value, 0.0)
        projected_pct = ((existing + notional) / ctx.book_value_usd) * 100.0
        cap = limits["country_exposure_pct"]
        breach = projected_pct > cap
        return (cap, round(projected_pct, 4), breach,
                f"{request.market.value} projected exposure {projected_pct:.2f}% vs cap {cap:.2f}%")

    def _c4(self, request, ctx, limits, notional):
        """UMIR 7.1 Part 7(c): limits on the value of unexecuted orders per security."""
        if ctx.book_value_usd <= 0:
            return (None, None, True, "Book value is zero; unexecuted value cap is undefined")
        existing = ctx.unexecuted_value_usd.get(request.symbol, 0.0)
        projected = existing + notional
        cap_usd = ctx.book_value_usd * (limits["unexecuted_value_pct"] / 100.0)
        breach = projected > cap_usd
        return (round(cap_usd, 2), round(projected, 2), breach,
                f"{request.symbol} unexecuted value ${projected:,.2f} vs cap ${cap_usd:,.2f}")

    def _c5(self, request, ctx, limits, notional):
        """UMIR 7.1 Part 7(c): limits on the volume of unexecuted orders."""
        projected = ctx.open_order_count + 1
        cap = limits["open_order_count"]
        breach = projected > cap
        return (cap, projected, breach,
                f"{projected} open orders vs cap {int(cap)}")

    def _c6(self, request, ctx, limits, notional):
        """UMIR 7.1 Part 7(b): per-client limits -- single-order size ceiling."""
        cap = limits["per_order_ceiling_usd"]
        breach = notional > cap
        return (cap, round(notional, 2), breach,
                f"Order notional ${notional:,.2f} vs per-order ceiling ${cap:,.2f}")

    def _c7(self, request, ctx, limits, notional):
        """Platform control: sector concentration."""
        if ctx.book_value_usd <= 0:
            return (None, None, True, "Book value is zero; sector concentration is undefined")
        sector = ctx.symbol_sector
        if not sector:
            return (None, None, False, "Sector unknown for this symbol; control not applicable")
        existing = ctx.sector_exposure_usd.get(sector, 0.0)
        projected_pct = ((existing + notional) / ctx.book_value_usd) * 100.0
        cap = limits["sector_concentration_pct"]
        breach = projected_pct > cap
        return (cap, round(projected_pct, 4), breach,
                f"Sector {sector} projected {projected_pct:.2f}% vs cap {cap:.2f}%")

    def _c8(self, request, ctx, limits, notional):
        """Platform control: correlation of this position to the existing book."""
        cap = limits["correlation_max"]
        observed = ctx.correlation_to_book
        breach = observed > cap
        return (cap, round(observed, 4), breach,
                f"Correlation to book {observed:.3f} vs max {cap:.3f}")

    def _c9(self, request, ctx, limits, notional):
        """Platform control: beta-adjusted portfolio exposure."""
        cap = limits["portfolio_beta_max"]
        observed = ctx.portfolio_beta
        breach = observed > cap
        return (cap, round(observed, 4), breach,
                f"Portfolio beta {observed:.3f} vs max {cap:.3f}")

    def _c10(self, request, ctx, limits, notional):
        """Platform control: maximum gross traded in a session (runaway-algo backstop)."""
        if ctx.book_value_usd <= 0:
            return (None, None, True, "Book value is zero; daily gross cap is undefined")
        projected_pct = ((ctx.daily_gross_traded_usd + notional) / ctx.book_value_usd) * 100.0
        cap = limits["daily_gross_pct"]
        breach = projected_pct > cap
        return (cap, round(projected_pct, 4), breach,
                f"Daily gross projected {projected_pct:.2f}% of book vs cap {cap:.2f}%")

    def _c11(self, request, ctx, limits, notional):
        """Platform control: restricted list and stale-data guard."""
        if request.symbol in ctx.restricted_symbols:
            return (None, None, True, f"{request.symbol} is on the restricted list")
        if ctx.data_age_sessions > MAX_DATA_AGE_SESSIONS:
            return (MAX_DATA_AGE_SESSIONS, ctx.data_age_sessions, True,
                    f"Reference data is {ctx.data_age_sessions:.1f} sessions old; "
                    f"max {MAX_DATA_AGE_SESSIONS:.1f}")
        return (MAX_DATA_AGE_SESSIONS, ctx.data_age_sessions, False,
                f"Data age {ctx.data_age_sessions:.1f} session(s); not restricted")

    def _c12(self, request, ctx, limits, notional):
        """
        CIRO / IIROC Dealer Member Rule 3200: a CIRO-registered order-execution-only dealer
        may not let a client generate orders through its own automated order system. A
        Canadian symbol therefore never reaches an external adapter.

        This control cannot be overridden and has no threshold.
        """
        breach = request.market == Market.CA and ctx.target_adapter_is_external
        adapter = ctx.target_adapter_id or "UNSET"
        if breach:
            return (None, None, True,
                    f"{request.symbol} is Canadian-listed and adapter '{adapter}' is "
                    "external; CIRO DMR 3200 blocks this route")
        return (None, None, False,
                f"Routing {request.market.value} via '{adapter}'"
                + (" (internal simulator)" if not ctx.target_adapter_is_external else ""))


# --------------------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------------------

class RiskGovernorLedger:
    """
    Persists one row per control evaluation. Every decision is recorded whether or not it
    breached, so "the governor looked and approved" is distinguishable from "the governor
    never ran".
    """

    def __init__(self, db=None):
        self.db = db or _default_db
        self._init_tables()

    def _init_tables(self) -> None:
        self.db.execute_write("""
            CREATE TABLE IF NOT EXISTS risk_governor_decision (
                decision_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                client_order_id TEXT NOT NULL,
                order_id        TEXT,
                symbol          TEXT NOT NULL,
                market          TEXT NOT NULL,
                control_id      TEXT NOT NULL,
                control_name    TEXT NOT NULL,
                verdict         TEXT NOT NULL,
                breach          INTEGER NOT NULL,
                limit_value     REAL,
                observed_value  REAL,
                reason_code     TEXT,
                overridden      INTEGER NOT NULL DEFAULT 0,
                override_actor  TEXT,
                risk_profile    TEXT NOT NULL,
                order_notional_usd REAL NOT NULL,
                limits_snapshot TEXT NOT NULL,
                detail          TEXT NOT NULL,
                decided_at      TEXT NOT NULL
            );
        """)
        self.db.execute_write("""
            CREATE INDEX IF NOT EXISTS idx_rgd_order
                ON risk_governor_decision (client_order_id, decision_id);
        """)
        # Per-adapter account snapshots. cash_reserved is what makes C1 enforceable.
        self.db.execute_write("""
            CREATE TABLE IF NOT EXISTS execution_account (
                account_id      INTEGER PRIMARY KEY AUTOINCREMENT,
                adapter_id      TEXT NOT NULL,
                cash_free_usd   REAL NOT NULL,
                cash_reserved_usd REAL NOT NULL,
                equity_usd      REAL NOT NULL,
                buying_power_usd REAL NOT NULL,
                snapshot_at     TEXT NOT NULL
            );
        """)

    def record(
        self,
        decision: GovernorDecision,
        order_id: Optional[str] = None,
        override: Optional[GovernorOverride] = None,
    ) -> int:
        """Writes one row per control outcome. Returns the number of rows written."""
        import json
        snapshot = json.dumps(decision.limits_snapshot, sort_keys=True)
        override_actor = override.actor.value if override else None
        written = 0
        for o in decision.outcomes:
            self.db.execute_write(
                """
                INSERT INTO risk_governor_decision (
                    client_order_id, order_id, symbol, market, control_id, control_name,
                    verdict, breach, limit_value, observed_value, reason_code, overridden,
                    override_actor, risk_profile, order_notional_usd, limits_snapshot,
                    detail, decided_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    decision.client_order_id, order_id, decision.symbol, decision.market,
                    o.control_id, o.control_name, o.verdict.value, 1 if o.breach else 0,
                    o.limit_value, o.observed_value, o.reason_code,
                    1 if o.overridden else 0, override_actor if o.overridden else None,
                    decision.risk_profile, decision.order_notional_usd, snapshot,
                    o.detail, decision.decided_at,
                ),
            )
            written += 1
        return written

    def breaches_for(self, client_order_id: str) -> List[Dict[str, Any]]:
        return self.db.execute_query(
            "SELECT * FROM risk_governor_decision "
            "WHERE client_order_id = ? AND breach = 1 ORDER BY decision_id ASC;",
            (client_order_id,),
        )

    def hard_rejection_count(self, since_iso: Optional[str] = None) -> int:
        """Distinct orders hard-rejected. Feeds Predicate 28."""
        if since_iso:
            rows = self.db.execute_query(
                "SELECT COUNT(DISTINCT client_order_id) AS n FROM risk_governor_decision "
                "WHERE verdict = 'HARD_REJECT' AND decided_at >= ?;",
                (since_iso,),
            )
        else:
            rows = self.db.execute_query(
                "SELECT COUNT(DISTINCT client_order_id) AS n FROM risk_governor_decision "
                "WHERE verdict = 'HARD_REJECT';",
            )
        return int(rows[0]["n"]) if rows else 0

    def snapshot_account(
        self, adapter_id: str, cash_free_usd: float, cash_reserved_usd: float,
        equity_usd: float,
    ) -> None:
        self.db.execute_write(
            """
            INSERT INTO execution_account (
                adapter_id, cash_free_usd, cash_reserved_usd, equity_usd,
                buying_power_usd, snapshot_at
            ) VALUES (?, ?, ?, ?, ?, ?);
            """,
            (
                adapter_id, float(cash_free_usd), float(cash_reserved_usd),
                float(equity_usd),
                float(cash_free_usd) - float(cash_reserved_usd),
                datetime.now(timezone.utc).isoformat(),
            ),
        )


__all__ = [
    "Verdict",
    "GOVERNOR_LIMITS",
    "DEFAULT_PROFILE",
    "CONTROL_REGISTRY",
    "CONTROL_BY_ID",
    "MAX_DATA_AGE_SESSIONS",
    "ControlSpec",
    "GovernorContext",
    "GovernorOverride",
    "ControlOutcome",
    "GovernorDecision",
    "PreTradeRiskGovernor",
    "RiskGovernorLedger",
]
