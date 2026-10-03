"""
Phase 34.2 -- Governed Submission Path
US + Canada Market Intelligence Platform

The single entry point through which an order enters the system. Its purpose in this
sub-phase is narrow but load-bearing: to make "no order can bypass the governor"
structurally true rather than a convention. There is no code path that creates an order
and reaches an adapter without a recorded governor decision.

Phase 34.3 attaches the internal simulator adapter to this path, and Phase 34.4 attaches
the router. Neither will add a second way in.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    # Imported for annotations only. router imports the adapter base but not the gateway,
    # so there is no cycle at runtime -- this just keeps the dependency direction explicit.
    from src.execution.router import AdapterRouter, RoutingDecision

from src.execution.order_model import (
    Actor,
    OrderRequest,
    OrderState,
    RejectionReason,
    order_id_from_client,
)
from src.execution.risk_governor import (
    GovernorContext,
    GovernorDecision,
    GovernorOverride,
    PreTradeRiskGovernor,
    RiskGovernorLedger,
    Verdict,
)
from src.execution.state_machine import ExecutionLedger


@dataclass
class SubmissionResult:
    order_id: str
    decision: Optional[GovernorDecision]
    state: OrderState
    routing: Optional["RoutingDecision"] = None

    @property
    def approved(self) -> bool:
        return self.state == OrderState.RISK_APPROVED

    @property
    def soft_limited(self) -> bool:
        return self.state == OrderState.RISK_APPROVED and self.decision is not None \
            and self.decision.overall == Verdict.SOFT_LIMIT

    @property
    def routing_blocked(self) -> bool:
        """True when routing refused the order, so the governor never ran."""
        return self.routing is not None and not self.routing.allowed


class ExecutionGateway:
    """
    Creates the order, routes it, runs the governor, records the decision, and moves the
    order to its post-governor state. Every step is audited.

    When a router is attached, routing precedes the governor and is authoritative: the
    order row records the adapter the router actually resolved, and the governor's Canadian
    firewall (C12) is fed from that resolution rather than from whatever the caller claimed.
    A caller cannot assert an adapter is internal and have that assertion believed.
    """

    def __init__(
        self,
        ledger: Optional[ExecutionLedger] = None,
        governor: Optional[PreTradeRiskGovernor] = None,
        risk_ledger: Optional[RiskGovernorLedger] = None,
        router: Optional["AdapterRouter"] = None,
    ):
        self.ledger = ledger or ExecutionLedger()
        self.governor = governor or PreTradeRiskGovernor()
        self.risk_ledger = risk_ledger or RiskGovernorLedger()
        self.router = router

    def submit(
        self,
        request: OrderRequest,
        ctx: GovernorContext,
        override: Optional[GovernorOverride] = None,
        adapter_id: Optional[str] = None,
    ) -> SubmissionResult:
        """
        Runs the full pre-trade path.

        Routing runs first and can stop the order outright: a kill-switch halt lands the
        order in CANCELLED, a routing failure in INVALID. Both are legal edges from NEW,
        and both carry the specific reason code so the audit trail says which gate stopped
        it. Neither has a governor decision, because the governor never ran.

        A HARD_REJECT concludes the order as RISK_REJECTED with its reason code. A
        SOFT_LIMIT is approved but the soft breaches remain recorded on the decision --
        they are surfaced, never silently resized away.
        """
        routing = self.router.route(request, preferred_adapter_id=adapter_id) \
            if self.router is not None else None

        # Record the adapter the router actually resolved. For a blocked order this is the
        # adapter that was refused, which is what an operator needs to see.
        resolved_id = (routing.adapter_id if routing and routing.adapter_id else None) \
            or adapter_id or "INTERNAL_SIMULATOR"

        order_id = self.ledger.create_order(
            request, adapter_id=resolved_id, actor=Actor.SYSTEM,
        )

        if routing is not None and not routing.allowed:
            # CANCELLED for an operator's halt (the order was fine, the system stopped it);
            # INVALID for a routing failure (this order cannot be served as written).
            state = (OrderState.CANCELLED if routing.blocked_by_kill_switch
                     else OrderState.INVALID)
            actor = Actor.KILL_SWITCH if routing.blocked_by_kill_switch else Actor.SYSTEM
            self.ledger.transition(
                order_id, state, reason_code=routing.reason_code, actor=actor,
            )
            return SubmissionResult(order_id, None, state, routing=routing)

        # The router is authoritative about the route, so the governor sees the truth.
        if routing is not None:
            ctx.target_adapter_id = routing.adapter_id
            ctx.target_adapter_is_external = routing.is_external

        decision = self.governor.evaluate(request, ctx, override=override)
        self.risk_ledger.record(decision, order_id=order_id, override=override)

        if decision.overall == Verdict.HARD_REJECT:
            reason = RejectionReason(decision.rejection_reason)
            self.ledger.transition(
                order_id, OrderState.RISK_REJECTED,
                reason_code=reason, actor=Actor.GOVERNOR,
            )
            return SubmissionResult(order_id, decision, OrderState.RISK_REJECTED, routing=routing)

        # APPROVE and SOFT_LIMIT both proceed; the soft breaches stay on the decision.
        self.ledger.transition(
            order_id, OrderState.RISK_APPROVED, actor=Actor.GOVERNOR,
        )
        return SubmissionResult(order_id, decision, OrderState.RISK_APPROVED, routing=routing)


__all__ = ["ExecutionGateway", "SubmissionResult", "order_id_from_client"]
