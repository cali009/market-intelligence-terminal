"""
Phase 34.4 -- Adapter Routing and Market Firewall
US + Canada Market Intelligence Platform

Three gates stand between a signal and a venue, and they are deliberately NOT equivalent:

  1. THE CANADIAN FIREWALL is regulatory and absolute. CIRO / IIROC Dealer Member Rule
     3200 A.1.(b)(i) bars a CIRO-registered order-execution-only dealer from letting a
     client generate orders through its own automated order system, and NI 23-103 §1.2(1)
     defines "automated order system" to include algorithms "developed or used by clients"
     -- which this platform's signal engine is. A Canadian symbol therefore never reaches
     an adapter marked external. No override, no threshold, no configuration.

  2. THE LICENSE GATE is commercial. An adapter whose data terms have not been cleared may
     not be used at all. `BrokerAdapter.license_cleared` defaults to False, so an adapter
     must opt in -- forgetting the flag fails closed. The Alpaca review (docs/16 §1.4)
     returned LICENSE_CLEARED = False; no external adapter ships in this release.

  3. THE KILL SWITCH is operational. An operator can halt routing at any time. Unlike the
     first two it is reversible, and EXTERNAL_ONLY deliberately falls through to the
     internal simulator: the intent is "stop using the broker", not "stop working".

Gate order matters because it decides which reason code an operator sees. The kill switch
is evaluated first -- an operator's explicit stop should never be masked by a downstream
diagnostic -- then the firewall, license, configuration, and capability checks.

Capability negotiation reports CAPABILITY_UNSUPPORTED rather than silently substituting a
market order. IBKR's paper environment documents no VWAP, Auction, RFQ or Pegged-to-Market
support while execution_algo already simulates VWAP; without declaration the platform
would claim to route an order type it cannot.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from src.data.db import Database, db as _default_db
from src.execution.adapters.base import BrokerAdapter
from src.execution.order_model import (
    Actor,
    Market,
    OrderRequest,
    RejectionReason,
)

# --------------------------------------------------------------------------------------
# Kill switch scopes
# --------------------------------------------------------------------------------------

KILL_ALL = "ALL"
KILL_EXTERNAL_ONLY = "EXTERNAL_ONLY"
SYMBOL_PREFIX = "SYMBOL:"
STRATEGY_PREFIX = "STRATEGY:"

# Reporting precedence. Deterministic, so the same active set always explains itself the
# same way rather than depending on set iteration order.
_SCOPE_PRECEDENCE = (KILL_ALL, KILL_EXTERNAL_ONLY, SYMBOL_PREFIX, STRATEGY_PREFIX)


def is_valid_scope(scope: str) -> bool:
    """ALL and EXTERNAL_ONLY are bare; SYMBOL and STRATEGY must carry a non-empty value."""
    if scope in (KILL_ALL, KILL_EXTERNAL_ONLY):
        return True
    for prefix in (SYMBOL_PREFIX, STRATEGY_PREFIX):
        if scope.startswith(prefix):
            return len(scope) > len(prefix)
    return False


# --------------------------------------------------------------------------------------
# Kill switch
# --------------------------------------------------------------------------------------

class KillSwitch:
    """
    Operator halt control.

    State is DERIVED from an append-only event log rather than held in a mutable flag, so
    the active set can never drift from the audit record of who stopped what and when.
    Replaying ENGAGE/DISENGAGE events in order reconstructs it exactly.
    """

    def __init__(self, db: Optional[Database] = None):
        self.db = db or _default_db
        self._ensure_table()

    def _ensure_table(self) -> None:
        if self.db is None:
            return
        self.db.execute_write(
            """
            CREATE TABLE IF NOT EXISTS kill_switch_event (
                event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                scope        TEXT    NOT NULL,
                action       TEXT    NOT NULL,
                actor        TEXT    NOT NULL,
                reason       TEXT,
                recorded_at  TEXT    NOT NULL
            );
            """
        )

    # -- mutation ----------------------------------------------------------------------

    def engage(self, scope: str, reason: str = "", actor: Actor = Actor.KILL_SWITCH) -> None:
        """Halts routing for a scope. Idempotent: re-engaging an active scope is a no-op."""
        if not is_valid_scope(scope):
            raise ValueError(f"Invalid kill switch scope: {scope!r}")
        if scope in self.active_scopes():
            return
        self._record(scope, "ENGAGE", actor, reason)

    def disengage(self, scope: str, reason: str = "", actor: Actor = Actor.KILL_SWITCH) -> None:
        """Restores routing for a scope. Idempotent."""
        if not is_valid_scope(scope):
            raise ValueError(f"Invalid kill switch scope: {scope!r}")
        if scope not in self.active_scopes():
            return
        self._record(scope, "DISENGAGE", actor, reason)

    def _record(self, scope: str, action: str, actor: Actor, reason: str) -> None:
        if self.db is None:
            return
        self.db.execute_write(
            "INSERT INTO kill_switch_event (scope, action, actor, reason, recorded_at)"
            " VALUES (?, ?, ?, ?, ?);",
            (scope, action, actor.value, reason,
             datetime.now(timezone.utc).isoformat()),
        )

    # -- state -------------------------------------------------------------------------

    def active_scopes(self) -> List[str]:
        """Replays the event log. Ordered by precedence, so reporting is deterministic."""
        if self.db is None:
            return []
        active = set()
        for row in self.db.execute_query(
            "SELECT scope, action FROM kill_switch_event ORDER BY event_id ASC;"
        ):
            if row["action"] == "ENGAGE":
                active.add(row["scope"])
            else:
                active.discard(row["scope"])
        return sorted(active, key=lambda s: (self._precedence(s), s))

    @staticmethod
    def _precedence(scope: str) -> int:
        for i, key in enumerate(_SCOPE_PRECEDENCE):
            if scope == key or scope.startswith(key):
                return i
        return len(_SCOPE_PRECEDENCE)

    def is_engaged(self, scope: str) -> bool:
        return scope in self.active_scopes()

    def is_halted(self) -> bool:
        """True when nothing at all may route."""
        return KILL_ALL in self.active_scopes()

    # -- evaluation --------------------------------------------------------------------

    def blocks(
        self,
        request: OrderRequest,
        adapter: Optional[BrokerAdapter] = None,
    ) -> Optional[str]:
        """
        Returns the scope that stops this order, or None.

        EXTERNAL_ONLY needs an adapter to evaluate; with no candidate there is nothing
        external about the route, so it cannot match.
        """
        active = self.active_scopes()
        for scope in active:
            if scope == KILL_ALL:
                return scope
            if scope == KILL_EXTERNAL_ONLY:
                if adapter is not None and adapter.is_external:
                    return scope
                continue
            if scope.startswith(SYMBOL_PREFIX):
                if request.symbol == scope[len(SYMBOL_PREFIX):]:
                    return scope
                continue
            if scope.startswith(STRATEGY_PREFIX):
                # The field is strategy_id. There is no `strategy` attribute on
                # OrderRequest, so reading one would raise at routing time.
                if request.strategy_id == scope[len(STRATEGY_PREFIX):]:
                    return scope
                continue
        return None

    def events(self) -> List[Dict]:
        if self.db is None:
            return []
        return self.db.execute_query(
            "SELECT * FROM kill_switch_event ORDER BY event_id ASC;"
        )


# --------------------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------------------

class AdapterRegistry:
    """
    Holds the adapters the platform can route to.

    Registration order is preference order: `for_market` returns candidates in that order
    and the router takes the first one that passes every gate.
    """

    def __init__(self):
        self._adapters: Dict[str, BrokerAdapter] = {}

    def register(self, adapter: BrokerAdapter, replace: bool = False) -> None:
        if adapter.adapter_id in self._adapters and not replace:
            raise ValueError(f"Adapter already registered: {adapter.adapter_id}")
        self._adapters[adapter.adapter_id] = adapter

    def get(self, adapter_id: Optional[str]) -> Optional[BrokerAdapter]:
        if adapter_id is None:
            return None
        return self._adapters.get(adapter_id)

    def all(self) -> List[BrokerAdapter]:
        return list(self._adapters.values())

    def for_market(self, market: Market, include_external: bool = True) -> List[BrokerAdapter]:
        """
        Candidates for a market, in preference order.

        `include_external=False` is how the Canadian firewall is applied during automatic
        selection: external adapters are not merely rejected, they are never considered.
        """
        return [
            a for a in self._adapters.values()
            if a.supports_market(market) and (include_external or not a.is_external)
        ]


# --------------------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------------------

@dataclass
class RoutingDecision:
    adapter: Optional[BrokerAdapter]
    allowed: bool
    reason_code: Optional[RejectionReason] = None
    detail: str = ""
    kill_switch_scope: Optional[str] = None
    attempted: List[str] = field(default_factory=list)

    @property
    def adapter_id(self) -> Optional[str]:
        return self.adapter.adapter_id if self.adapter else None

    @property
    def blocked_by_kill_switch(self) -> bool:
        return self.kill_switch_scope is not None

    @property
    def is_external(self) -> bool:
        """
        Whether the resolved adapter is external. False when nothing resolved, which is the
        safe answer: an unroutable order is not an externally-routed order.
        """
        return bool(self.adapter and self.adapter.is_external)


class AdapterRouter:
    """Resolves an order to an adapter, or explains precisely why it cannot be routed."""

    def __init__(
        self,
        registry: Optional[AdapterRegistry] = None,
        kill_switch: Optional[KillSwitch] = None,
    ):
        self.registry = registry or AdapterRegistry()
        self.kill_switch = kill_switch or KillSwitch()

    # -- public ------------------------------------------------------------------------

    def route(
        self,
        request: OrderRequest,
        preferred_adapter_id: Optional[str] = None,
    ) -> RoutingDecision:
        """
        Resolves the adapter for an order.

        Preference order is the explicit argument, then `request.adapter_hint`, then
        automatic selection. A stated preference is honoured exactly and never substituted:
        if that adapter is blocked, the order is blocked. Automatic selection picks the
        first candidate that passes every gate -- and for Canadian symbols external adapters
        are not candidates at all.
        """
        # `adapter_hint` is the caller's venue preference carried on the order itself. It
        # was previously declared on OrderRequest and read by nothing; honouring it here
        # gives it a single, well-defined meaning instead of leaving it a dead field.
        wanted = preferred_adapter_id or request.adapter_hint

        # 1. Kill switch first. An operator's stop must not be masked by a downstream
        #    diagnostic, and ALL/SYMBOL/STRATEGY do not depend on which adapter resolved.
        scope = self.kill_switch.blocks(
            request, self._candidate_for_switch(request, wanted),
        )
        if scope:
            return RoutingDecision(
                adapter=None, allowed=False,
                reason_code=RejectionReason.KILL_SWITCH_ACTIVE,
                detail=f"Kill switch active for scope '{scope}'",
                kill_switch_scope=scope,
            )

        if wanted:
            return self._route_explicit(request, wanted)
        return self._route_automatic(request)

    # -- explicit ----------------------------------------------------------------------

    def _route_explicit(self, request: OrderRequest, adapter_id: str) -> RoutingDecision:
        """
        Honours a named adapter exactly, with no fallback.

        No substitution here on purpose. Silently rerouting an order the caller addressed
        to a specific venue would hide the block that stopped it -- and for the Canadian
        firewall, hiding the block is worse than the block.
        """
        adapter = self.registry.get(adapter_id)
        if adapter is None:
            return RoutingDecision(
                adapter=None, allowed=False,
                reason_code=RejectionReason.NO_ADAPTER_AVAILABLE,
                detail=f"No adapter registered with id '{adapter_id}'",
            )
        decision = self._check_adapter(request, adapter)
        decision.attempted = [adapter_id]
        return decision

    # -- automatic ---------------------------------------------------------------------

    def _route_automatic(self, request: OrderRequest) -> RoutingDecision:
        # Canadian symbols: external adapters are excluded from the candidate set entirely.
        include_external = request.market != Market.CA
        candidates = self.registry.for_market(request.market, include_external=include_external)

        if not candidates:
            detail = (
                f"No adapter serves {request.market.value}"
                + ("; external adapters are barred from Canadian symbols (CIRO DMR 3200)"
                   if not include_external and self.registry.for_market(request.market) else "")
            )
            return RoutingDecision(
                adapter=None, allowed=False,
                reason_code=RejectionReason.NO_ADAPTER_AVAILABLE, detail=detail,
            )

        attempted, first_failure = [], None
        for adapter in candidates:
            decision = self._check_adapter(request, adapter)
            attempted.append(adapter.adapter_id)
            if decision.allowed:
                decision.attempted = attempted
                return decision
            if first_failure is None:
                first_failure = decision

        # Nothing passed. Report why the first candidate failed, and what else was tried.
        first_failure.attempted = attempted
        if len(attempted) > 1:
            first_failure.detail += f" (also tried: {', '.join(attempted[1:])})"
        return first_failure

    def _candidate_for_switch(
        self, request: OrderRequest, preferred_adapter_id: Optional[str],
    ) -> Optional[BrokerAdapter]:
        """
        The adapter an EXTERNAL_ONLY scope should be evaluated against, without running the
        other gates. An explicit choice wins; otherwise the preferred internal candidate.
        """
        explicit = self.registry.get(preferred_adapter_id)
        if explicit is not None:
            return explicit
        candidates = self.registry.for_market(request.market)
        return candidates[0] if candidates else None

    # -- gates -------------------------------------------------------------------------

    def _check_adapter(self, request: OrderRequest, adapter: BrokerAdapter) -> RoutingDecision:
        """
        The gate sequence, in the order an operator should read it.

        Market support is first because everything after it assumes the adapter could in
        principle serve the symbol. The Canadian firewall precedes the license gate: a
        regulatory block is more fundamental than a commercial one, and reporting the
        regulatory reason is the more useful diagnostic.
        """
        if not adapter.supports_market(request.market):
            return self._deny(adapter, RejectionReason.NO_ADAPTER_AVAILABLE,
                              f"'{adapter.adapter_id}' does not serve {request.market.value}")

        # Regulatory. Cannot be overridden, configured away, or fallen back around.
        if request.market == Market.CA and adapter.is_external:
            return self._deny(
                adapter, RejectionReason.CANADIAN_EXTERNAL_ROUTING_BLOCKED,
                f"{request.symbol} is Canadian-listed; CIRO DMR 3200 bars routing it through "
                f"external adapter '{adapter.adapter_id}'. Use an internal adapter.",
            )

        # Commercial. Fails closed: license_cleared defaults to False on the base class.
        if not adapter.license_cleared:
            return self._deny(
                adapter, RejectionReason.ADAPTER_LICENSE_NOT_CLEARED,
                f"'{adapter.adapter_id}' has not cleared its data licensing terms; "
                "it may not be used for any order",
            )

        if not adapter.is_configured():
            return self._deny(adapter, RejectionReason.ADAPTER_NOT_CONFIGURED,
                              f"'{adapter.adapter_id}' is not configured")

        if not adapter.supports_order_type(request.order_type):
            return self._deny(
                adapter, RejectionReason.CAPABILITY_UNSUPPORTED,
                f"'{adapter.adapter_id}' does not support {request.order_type.value}; "
                "refusing to substitute a different order type",
            )

        return RoutingDecision(adapter=adapter, allowed=True,
                               detail=f"Routed to '{adapter.adapter_id}'")

    @staticmethod
    def _deny(adapter: BrokerAdapter, reason: RejectionReason, detail: str) -> RoutingDecision:
        return RoutingDecision(adapter=adapter, allowed=False, reason_code=reason, detail=detail)


__all__ = [
    "KILL_ALL",
    "KILL_EXTERNAL_ONLY",
    "SYMBOL_PREFIX",
    "STRATEGY_PREFIX",
    "is_valid_scope",
    "KillSwitch",
    "AdapterRegistry",
    "RoutingDecision",
    "AdapterRouter",
]
