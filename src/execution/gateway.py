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
from typing import Optional, Tuple

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
    decision: GovernorDecision
    state: OrderState

    @property
    def approved(self) -> bool:
        return self.state == OrderState.RISK_APPROVED

    @property
    def soft_limited(self) -> bool:
        return self.state == OrderState.RISK_APPROVED and self.decision.overall == Verdict.SOFT_LIMIT


class ExecutionGateway:
    """
    Creates the order, runs the governor, records the decision, and moves the order to its
    post-governor state. Every step is audited.
    """

    def __init__(
        self,
        ledger: Optional[ExecutionLedger] = None,
        governor: Optional[PreTradeRiskGovernor] = None,
        risk_ledger: Optional[RiskGovernorLedger] = None,
    ):
        self.ledger = ledger or ExecutionLedger()
        self.governor = governor or PreTradeRiskGovernor()
        self.risk_ledger = risk_ledger or RiskGovernorLedger()

    def submit(
        self,
        request: OrderRequest,
        ctx: GovernorContext,
        override: Optional[GovernorOverride] = None,
        adapter_id: str = "INTERNAL_SIMULATOR",
    ) -> SubmissionResult:
        """
        Runs the full pre-trade path.

        A HARD_REJECT concludes the order as RISK_REJECTED with its reason code. A
        SOFT_LIMIT is approved but the soft breaches remain recorded on the decision --
        they are surfaced, never silently resized away.
        """
        order_id = self.ledger.create_order(request, adapter_id=adapter_id, actor=Actor.SYSTEM)

        decision = self.governor.evaluate(request, ctx, override=override)
        self.risk_ledger.record(decision, order_id=order_id, override=override)

        if decision.overall == Verdict.HARD_REJECT:
            reason = RejectionReason(decision.rejection_reason)
            self.ledger.transition(
                order_id, OrderState.RISK_REJECTED,
                reason_code=reason, actor=Actor.GOVERNOR,
            )
            return SubmissionResult(order_id, decision, OrderState.RISK_REJECTED)

        # APPROVE and SOFT_LIMIT both proceed; the soft breaches stay on the decision.
        self.ledger.transition(
            order_id, OrderState.RISK_APPROVED, actor=Actor.GOVERNOR,
        )
        return SubmissionResult(order_id, decision, OrderState.RISK_APPROVED)


__all__ = ["ExecutionGateway", "SubmissionResult", "order_id_from_client"]
