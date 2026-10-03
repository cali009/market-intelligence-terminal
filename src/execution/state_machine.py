"""
Phase 34.1 -- Order State Machine & Append-Only Audit Ledger
US + Canada Market Intelligence Platform

Enforces a bounded order lifecycle. Two properties are load-bearing:

1. ILLEGAL TRANSITIONS ARE IMPOSSIBLE, not merely discouraged. The transition table is the
   only authority; anything absent from it raises.
2. TERMINAL STATES ARE IMMUTABLE. Once an order reaches FILLED, CANCELLED, REJECTED,
   RISK_REJECTED, EXPIRED or INVALID, no further transition is accepted -- so an order can
   never be silently resurrected, re-filled, or have its rejection retroactively erased.

The ledger is append-only. `execution_state_transition` has no update or delete path in
this module, and every transition is written in the same operation that changes the order
row, so the order state and its history cannot diverge.
"""

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from src.data.db import db as _default_db
from src.execution.order_model import (
    Actor,
    NON_TERMINAL_STATES,
    OrderRequest,
    OrderState,
    RejectionReason,
    TERMINAL_STATES,
    order_id_from_client,
)


class IllegalTransitionError(Exception):
    """Raised when a transition is not present in the transition table."""

    def __init__(self, from_state: OrderState, to_state: OrderState):
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"Illegal order transition: {from_state.value} -> {to_state.value}"
        )


class TerminalStateError(IllegalTransitionError):
    """Raised when any transition is attempted out of a terminal state."""

    def __init__(self, state: OrderState, to_state: OrderState):
        # Bypass IllegalTransitionError.__init__ to set a terminal-specific message.
        Exception.__init__(
            self,
            f"Order is in terminal state {state.value}; transition to "
            f"{to_state.value} is refused. Terminal states are immutable.",
        )
        self.from_state = state
        self.to_state = to_state


# --------------------------------------------------------------------------------------
# Transition table -- the single authority on what may follow what
# --------------------------------------------------------------------------------------

LEGAL_TRANSITIONS: Dict[OrderState, frozenset] = {
    OrderState.NEW: frozenset({
        OrderState.RISK_APPROVED,
        OrderState.RISK_REJECTED,
        OrderState.INVALID,
        OrderState.CANCELLED,
    }),
    OrderState.RISK_APPROVED: frozenset({
        OrderState.SUBMITTED,
        OrderState.RISK_REJECTED,   # re-check at submission time
        OrderState.INVALID,
        OrderState.CANCELLED,
    }),
    OrderState.SUBMITTED: frozenset({
        OrderState.ACKED,
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.REJECTED,
        OrderState.CANCELLED,
        OrderState.EXPIRED,
    }),
    OrderState.ACKED: frozenset({
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.REJECTED,
        OrderState.EXPIRED,
    }),
    OrderState.PARTIALLY_FILLED: frozenset({
        OrderState.PARTIALLY_FILLED,  # further partial fills
        OrderState.FILLED,
        OrderState.CANCELLED,
        OrderState.EXPIRED,
    }),
    # Terminal states accept nothing.
    OrderState.FILLED: frozenset(),
    OrderState.CANCELLED: frozenset(),
    OrderState.REJECTED: frozenset(),
    OrderState.RISK_REJECTED: frozenset(),
    OrderState.EXPIRED: frozenset(),
    OrderState.INVALID: frozenset(),
}


def is_legal_transition(from_state: OrderState, to_state: OrderState) -> bool:
    """Pure predicate: is this transition permitted by the table?"""
    return to_state in LEGAL_TRANSITIONS.get(from_state, frozenset())


def assert_transition(from_state: OrderState, to_state: OrderState) -> None:
    """
    Validates a transition, distinguishing an immutable terminal state from an ordinary
    illegal edge. The distinction matters operationally: a terminal-state refusal means
    the order has already concluded, while an illegal edge means a caller is confused.
    """
    if from_state in TERMINAL_STATES:
        if to_state == from_state:
            # Idempotent re-assertion of the same terminal state is refused too: the
            # ledger must not record a no-op as if something happened.
            raise TerminalStateError(from_state, to_state)
        raise TerminalStateError(from_state, to_state)

    if not is_legal_transition(from_state, to_state):
        raise IllegalTransitionError(from_state, to_state)


# --------------------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------------------

class ExecutionLedger:
    """
    Persists orders and their immutable transition history.

    `db` is injectable so tests can run against a scratch database without touching the
    production file.
    """

    def __init__(self, db=None):
        self.db = db or _default_db
        self._init_tables()

    def _init_tables(self) -> None:
        self.db.execute_write("""
            CREATE TABLE IF NOT EXISTS execution_order (
                order_id            TEXT PRIMARY KEY,
                client_order_id     TEXT NOT NULL UNIQUE,
                adapter_id          TEXT,
                symbol              TEXT NOT NULL,
                market              TEXT NOT NULL,
                side                TEXT NOT NULL,
                quantity            REAL NOT NULL,
                order_type          TEXT NOT NULL,
                time_in_force       TEXT NOT NULL,
                limit_price         REAL,
                stop_price          REAL,
                state               TEXT NOT NULL,
                state_seq           INTEGER NOT NULL DEFAULT 0,
                strategy_id         TEXT NOT NULL,
                risk_profile        TEXT NOT NULL,
                signal_date         TEXT,
                requested_at        TEXT NOT NULL,
                submitted_at        TEXT,
                terminal_at         TEXT,
                rejection_reason    TEXT,
                created_at          TEXT NOT NULL
            );
        """)
        # Append-only history. No UPDATE or DELETE path exists in this module.
        self.db.execute_write("""
            CREATE TABLE IF NOT EXISTS execution_state_transition (
                seq             INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id        TEXT NOT NULL,
                client_order_id TEXT NOT NULL,
                from_state      TEXT,
                to_state        TEXT NOT NULL,
                reason_code     TEXT,
                actor           TEXT NOT NULL,
                at              TEXT NOT NULL
            );
        """)
        self.db.execute_write("""
            CREATE INDEX IF NOT EXISTS idx_est_order
                ON execution_state_transition (order_id, seq);
        """)

    # ------------------------------------------------------------------ creation

    def create_order(
        self,
        request: OrderRequest,
        adapter_id: Optional[str] = None,
        actor: Actor = Actor.SYSTEM,
    ) -> str:
        """
        Persists a new order in state NEW and writes the genesis ledger row.

        Raises on a duplicate client_order_id: the UNIQUE index is the duplicate guard,
        so a re-emitted signal collides here rather than being double-filled downstream.
        """
        order_id = order_id_from_client(request.client_order_id)
        now = datetime.now(timezone.utc).isoformat()

        existing = self.db.execute_query(
            "SELECT order_id, state FROM execution_order WHERE client_order_id = ?;",
            (request.client_order_id,),
        )
        if existing:
            raise DuplicateOrderError(
                request.client_order_id, existing[0]["state"],
                f"client_order_id {request.client_order_id} already exists "
                f"in state {existing[0]['state']}; refusing duplicate submission",
            )

        try:
            self.db.execute_write(
                """
                INSERT INTO execution_order (
                    order_id, client_order_id, adapter_id, symbol, market, side,
                    quantity, order_type, time_in_force, limit_price, stop_price,
                    state, state_seq, strategy_id, risk_profile, signal_date,
                    requested_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?);
                """,
                (
                    order_id,
                    request.client_order_id,
                    adapter_id,
                    request.symbol,
                    request.market.value,
                    request.side.value,
                    float(request.quantity),
                    request.order_type.value,
                    request.time_in_force.value,
                    request.limit_price,
                    request.stop_price,
                    OrderState.NEW.value,
                    request.strategy_id,
                    request.risk_profile,
                    request.signal_date.isoformat() if request.signal_date else None,
                    request.requested_at.isoformat(),
                    now,
                ),
            )
        except Exception as exc:  # pragma: no cover - defensive, race on UNIQUE index
            raise DuplicateOrderError(
                request.client_order_id, OrderState.NEW.value, str(exc)
            ) from exc

        self._append_transition(
            order_id=order_id,
            client_order_id=request.client_order_id,
            from_state=None,
            to_state=OrderState.NEW,
            reason_code=None,
            actor=actor,
            at=now,
        )
        return order_id

    # ------------------------------------------------------------------ transitions

    def transition(
        self,
        order_id: str,
        to_state: OrderState,
        reason_code: Optional[RejectionReason] = None,
        actor: Actor = Actor.SYSTEM,
        adapter_id: Optional[str] = None,
    ) -> OrderState:
        """
        Moves an order to `to_state`, refusing illegal and terminal transitions.

        Rejections must carry a reason code: a terminal rejection without one is not
        answerable from the ledger, which defeats the purpose of keeping it.
        """
        row = self.get_order(order_id)
        if row is None:
            raise KeyError(f"Unknown order_id: {order_id}")

        current = OrderState(row["state"])
        assert_transition(current, to_state)

        if to_state in TERMINAL_STATES and to_state in (
            OrderState.REJECTED,
            OrderState.RISK_REJECTED,
            OrderState.INVALID,
            OrderState.EXPIRED,
            OrderState.CANCELLED,
        ) and reason_code is None:
            raise ValueError(
                f"Transition to {to_state.value} requires a reason_code; "
                "a rejection without a reason is not auditable."
            )

        now = datetime.now(timezone.utc).isoformat()
        is_terminal = to_state in TERMINAL_STATES

        self.db.execute_write(
            """
            UPDATE execution_order
               SET state = ?,
                   state_seq = state_seq + 1,
                   submitted_at = COALESCE(submitted_at, ?),
                   terminal_at = ?,
                   rejection_reason = COALESCE(?, rejection_reason),
                   adapter_id = COALESCE(?, adapter_id)
             WHERE order_id = ?;
            """,
            (
                to_state.value,
                now if to_state == OrderState.SUBMITTED else None,
                now if is_terminal else None,
                reason_code.value if reason_code else None,
                adapter_id,
                order_id,
            ),
        )

        self._append_transition(
            order_id=order_id,
            client_order_id=row["client_order_id"],
            from_state=current,
            to_state=to_state,
            reason_code=reason_code,
            actor=actor,
            at=now,
        )
        return to_state

    def _append_transition(
        self,
        order_id: str,
        client_order_id: str,
        from_state: Optional[OrderState],
        to_state: OrderState,
        reason_code: Optional[RejectionReason],
        actor: Actor,
        at: str,
    ) -> int:
        return self.db.execute_write(
            """
            INSERT INTO execution_state_transition (
                order_id, client_order_id, from_state, to_state, reason_code, actor, at
            ) VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (
                order_id,
                client_order_id,
                from_state.value if from_state else None,
                to_state.value,
                reason_code.value if reason_code else None,
                actor.value,
                at,
            ),
        )

    # ------------------------------------------------------------------ reads

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        rows = self.db.execute_query(
            "SELECT * FROM execution_order WHERE order_id = ?;", (order_id,)
        )
        return rows[0] if rows else None

    def get_history(self, order_id: str) -> List[Dict[str, Any]]:
        """Full transition history, oldest first. This is the replayable audit trail."""
        return self.db.execute_query(
            "SELECT * FROM execution_state_transition WHERE order_id = ? ORDER BY seq ASC;",
            (order_id,),
        )

    def get_orders_by_state(self, state: OrderState) -> List[Dict[str, Any]]:
        return self.db.execute_query(
            "SELECT * FROM execution_order WHERE state = ? ORDER BY created_at ASC;",
            (state.value,),
        )

    def open_order_count(self) -> int:
        """Orders in a non-terminal state. Consumed by governor control C5."""
        placeholders = ",".join("?" for _ in NON_TERMINAL_STATES)
        rows = self.db.execute_query(
            f"SELECT COUNT(*) AS n FROM execution_order WHERE state IN ({placeholders});",
            tuple(s.value for s in NON_TERMINAL_STATES),
        )
        return int(rows[0]["n"]) if rows else 0

    def is_terminal(self, order_id: str) -> bool:
        row = self.get_order(order_id)
        return bool(row) and OrderState(row["state"]) in TERMINAL_STATES


class DuplicateOrderError(Exception):
    """Raised when a client_order_id collides with an existing order."""

    def __init__(self, client_order_id: str, existing_state: str, message: str):
        self.client_order_id = client_order_id
        self.existing_state = existing_state
        super().__init__(message)


__all__ = [
    "LEGAL_TRANSITIONS",
    "IllegalTransitionError",
    "TerminalStateError",
    "DuplicateOrderError",
    "ExecutionLedger",
    "is_legal_transition",
    "assert_transition",
]
