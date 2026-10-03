"""
Phase 34.1 Test Suite -- Order Model, State Machine & Append-Only Audit Ledger

Validates that:
  - order identity is deterministic and collision-detecting;
  - every LEGAL transition is accepted and every ILLEGAL one is refused;
  - all six terminal states are immutable, including against self-transition;
  - the ledger is monotonic, complete, and sufficient to replay the final state;
  - rejections cannot be recorded without a reason code;
  - no PII can enter the ledger.

Every test runs against an isolated throwaway SQLite file so ordering cannot matter.
"""

from datetime import date, datetime
from pathlib import Path
import tempfile

import pytest

from src.data.db import Database
from src.execution.order_model import (
    Actor,
    Market,
    NON_TERMINAL_STATES,
    OrderRequest,
    OrderSide,
    OrderState,
    OrderType,
    RejectionReason,
    TERMINAL_STATES,
    TimeInForce,
    build_client_order_id,
    order_id_from_client,
)
from src.execution.state_machine import (
    LEGAL_TRANSITIONS,
    DuplicateOrderError,
    ExecutionLedger,
    IllegalTransitionError,
    TerminalStateError,
    assert_transition,
    is_legal_transition,
)


@pytest.fixture()
def ledger(tmp_path):
    """An ExecutionLedger bound to a throwaway database, fresh for every test."""
    db_file = tmp_path / "exec_test.db"
    db = Database(db_url=f"sqlite:///{db_file}")
    return ExecutionLedger(db=db)


def make_request(symbol="AAPL", side=OrderSide.BUY, qty=100, strategy="STRAT_A",
                 nonce=0, **kw):
    cid = build_client_order_id(
        symbol, side.value, qty, kw.get("order_type", "MARKET"), strategy,
        kw.get("signal_date", date(2026, 10, 1)), nonce,
    )
    return OrderRequest(
        client_order_id=cid, symbol=symbol, market=kw.get("market", Market.US),
        side=side, quantity=qty, strategy_id=strategy,
        signal_date=kw.get("signal_date", date(2026, 10, 1)), **{
            k: v for k, v in kw.items() if k not in ("market", "signal_date", "order_type")
        } | ({"order_type": kw["order_type"]} if "order_type" in kw else {}),
    )


# Shortest legal path from NEW to each terminal state. REJECTED and EXPIRED are NOT
# reachable from NEW -- they require a submitted order -- so this cannot be a one-liner.
PATHS_TO_TERMINAL = {
    OrderState.RISK_REJECTED: [OrderState.RISK_REJECTED],
    OrderState.INVALID: [OrderState.INVALID],
    OrderState.CANCELLED: [OrderState.CANCELLED],
    OrderState.REJECTED: [OrderState.RISK_APPROVED, OrderState.SUBMITTED, OrderState.REJECTED],
    OrderState.EXPIRED: [OrderState.RISK_APPROVED, OrderState.SUBMITTED, OrderState.EXPIRED],
    OrderState.FILLED: [
        OrderState.RISK_APPROVED, OrderState.SUBMITTED,
        OrderState.ACKED, OrderState.PARTIALLY_FILLED, OrderState.FILLED,
    ],
}


def reach_terminal(ledger, order_id, terminal, reason=None):
    """Drives an order to a terminal state along a legal path."""
    actor = Actor.USER if terminal == OrderState.CANCELLED else Actor.GOVERNOR
    for st in PATHS_TO_TERMINAL[terminal]:
        # FILLED is a conclusion rather than a rejection, so it takes no reason code.
        ledger.transition(
            order_id, st,
            reason_code=None if st == OrderState.FILLED else reason,
            actor=actor,
        )


def walk_to(ledger, order_id, target, reason=None):
    """Drives an order along the canonical happy path until it reaches `target`."""
    path = {
        OrderState.RISK_APPROVED: [OrderState.RISK_APPROVED],
        OrderState.SUBMITTED: [OrderState.RISK_APPROVED, OrderState.SUBMITTED],
        OrderState.ACKED: [OrderState.RISK_APPROVED, OrderState.SUBMITTED, OrderState.ACKED],
        OrderState.PARTIALLY_FILLED: [
            OrderState.RISK_APPROVED, OrderState.SUBMITTED,
            OrderState.ACKED, OrderState.PARTIALLY_FILLED,
        ],
        OrderState.FILLED: [
            OrderState.RISK_APPROVED, OrderState.SUBMITTED,
            OrderState.ACKED, OrderState.PARTIALLY_FILLED, OrderState.FILLED,
        ],
    }
    for st in path[target]:
        ledger.transition(order_id, st, reason_code=reason, actor=Actor.ADAPTER)


# ======================================================================================
# 1. Deterministic order identity
# ======================================================================================

class TestOrderIdentity:

    def test_client_order_id_is_deterministic(self):
        """The same intent must always produce the same identifier."""
        a = build_client_order_id("AAPL", "BUY", 100, "MARKET", "S1", date(2026, 10, 1))
        b = build_client_order_id("AAPL", "BUY", 100, "MARKET", "S1", date(2026, 10, 1))
        assert a == b

    def test_client_order_id_is_case_and_whitespace_insensitive(self):
        """Symbol normalization must not change identity."""
        a = build_client_order_id("aapl", "buy", 100, "market", "S1", date(2026, 10, 1))
        b = build_client_order_id(" AAPL ", "BUY", 100, "MARKET", "S1", date(2026, 10, 1))
        assert a == b

    @pytest.mark.parametrize("field,value", [
        ("symbol", "MSFT"),
        ("side", "SELL"),
        ("quantity", 101),
        ("order_type", "LIMIT"),
        ("strategy_id", "S2"),
        ("signal_date", date(2026, 10, 2)),
    ])
    def test_every_intent_component_affects_identity(self, field, value):
        """Changing any economic input must change the identifier, or collisions hide bugs."""
        base = dict(symbol="AAPL", side="BUY", quantity=100, order_type="MARKET",
                    strategy_id="S1", signal_date=date(2026, 10, 1))
        original = build_client_order_id(**base)
        base[field] = value
        assert build_client_order_id(**base) != original, f"{field} did not affect identity"

    def test_nonce_allows_intentional_reentry(self):
        """The one deliberate escape hatch from determinism."""
        a = build_client_order_id("AAPL", "BUY", 100, "MARKET", "S1", date(2026, 10, 1), nonce=0)
        b = build_client_order_id("AAPL", "BUY", 100, "MARKET", "S1", date(2026, 10, 1), nonce=1)
        assert a != b

    def test_identifier_is_prefixed_and_bounded(self):
        cid = build_client_order_id("AAPL", "BUY", 100, "MARKET", "S1", date(2026, 10, 1))
        assert cid.startswith("QI-")
        assert len(cid) == 27

    def test_order_id_derivation_is_stable_and_distinct(self):
        cid = build_client_order_id("AAPL", "BUY", 100, "MARKET", "S1", date(2026, 10, 1))
        assert order_id_from_client(cid) == order_id_from_client(cid)
        other = build_client_order_id("MSFT", "BUY", 100, "MARKET", "S1", date(2026, 10, 1))
        assert order_id_from_client(cid) != order_id_from_client(other)

    def test_no_bulk_collision_across_realistic_universe(self):
        """10k distinct intents must not collide -- the id space is load-bearing."""
        seen = set()
        for i in range(10_000):
            seen.add(build_client_order_id(
                f"SYM{i % 500}", "BUY" if i % 2 else "SELL", float(1 + i % 977),
                "MARKET", f"S{i % 17}", date(2026, 10, 1),
            ))
        assert len(seen) == 10_000


# ======================================================================================
# 2. Transition table completeness
# ======================================================================================

class TestTransitionTable:

    def test_every_state_appears_in_the_table(self):
        """A state missing from the table would silently reject all of its transitions."""
        assert set(LEGAL_TRANSITIONS.keys()) == set(OrderState)

    def test_terminal_and_non_terminal_partition_is_complete(self):
        assert TERMINAL_STATES | NON_TERMINAL_STATES == set(OrderState)
        assert not (TERMINAL_STATES & NON_TERMINAL_STATES)

    def test_exactly_six_terminal_states(self):
        assert len(TERMINAL_STATES) == 6

    def test_terminal_states_have_no_outgoing_edges(self):
        for s in TERMINAL_STATES:
            assert LEGAL_TRANSITIONS[s] == frozenset(), f"{s.value} has outgoing edges"

    def test_non_terminal_states_all_have_outgoing_edges(self):
        """A non-terminal dead end would strand an order permanently."""
        for s in NON_TERMINAL_STATES:
            assert LEGAL_TRANSITIONS[s], f"{s.value} is a non-terminal dead end"

    def test_no_transition_targets_an_unknown_state(self):
        for src, targets in LEGAL_TRANSITIONS.items():
            for t in targets:
                assert t in set(OrderState)

    def test_no_self_transitions_except_partial_fill(self):
        """
        Only PARTIALLY_FILLED may follow itself (further partial fills). Every other
        self-transition would let the ledger record a no-op as though something happened.
        """
        for src, targets in LEGAL_TRANSITIONS.items():
            if src == OrderState.PARTIALLY_FILLED:
                assert src in targets
            else:
                assert src not in targets, f"{src.value} permits a self-transition"

    def test_happy_path_is_reachable_from_new(self):
        """FILLED must be reachable from NEW, or nothing could ever trade."""
        assert is_legal_transition(OrderState.NEW, OrderState.RISK_APPROVED)
        walk_states = [OrderState.RISK_APPROVED, OrderState.SUBMITTED, OrderState.ACKED,
                       OrderState.PARTIALLY_FILLED, OrderState.FILLED]
        prev = OrderState.NEW
        for st in walk_states:
            assert is_legal_transition(prev, st), f"{prev.value} -> {st.value} missing"
            prev = st

    def test_rejection_is_reachable_from_every_non_terminal_state(self):
        """An order must always be able to conclude as rejected or cancelled."""
        for s in NON_TERMINAL_STATES:
            assert OrderState.CANCELLED in LEGAL_TRANSITIONS[s], f"{s.value} cannot cancel"


# ======================================================================================
# 3. Legal transitions are accepted
# ======================================================================================

class TestLegalTransitionsAccepted:

    def test_full_happy_path_traverses(self, ledger):
        req = make_request(nonce=1)
        oid = ledger.create_order(req)
        walk_to(ledger, oid, OrderState.FILLED)
        assert ledger.get_order(oid)["state"] == OrderState.FILLED.value
        assert ledger.is_terminal(oid)

    @pytest.mark.parametrize("target", [
        OrderState.RISK_APPROVED, OrderState.SUBMITTED, OrderState.ACKED,
        OrderState.PARTIALLY_FILLED, OrderState.FILLED,
    ])
    def test_every_non_terminal_reachable_state(self, ledger, target):
        req = make_request(nonce=hash(target.value) % 10000 + 100)
        oid = ledger.create_order(req)
        walk_to(ledger, oid, target)
        assert ledger.get_order(oid)["state"] == target.value

    def test_multiple_partial_fills_are_permitted(self, ledger):
        """A single partial fill is not the real world; several are."""
        req = make_request(nonce=555)
        oid = ledger.create_order(req)
        walk_to(ledger, oid, OrderState.PARTIALLY_FILLED)
        for _ in range(4):
            ledger.transition(oid, OrderState.PARTIALLY_FILLED, actor=Actor.ADAPTER)
        assert ledger.get_order(oid)["state"] == OrderState.PARTIALLY_FILLED.value
        ledger.transition(oid, OrderState.FILLED, actor=Actor.ADAPTER)
        assert ledger.get_order(oid)["state"] == OrderState.FILLED.value

    @pytest.mark.parametrize("state,reason", [
        (OrderState.RISK_REJECTED, RejectionReason.RISK_C1_BUYING_POWER),
        (OrderState.INVALID, RejectionReason.MALFORMED_REQUEST),
        (OrderState.CANCELLED, RejectionReason.CANCELLED_BY_USER),
    ])
    def test_terminal_rejections_from_new(self, ledger, state, reason):
        req = make_request(nonce=abs(hash(state.value + reason.value)) % 9000 + 1000)
        oid = ledger.create_order(req)
        ledger.transition(oid, state, reason_code=reason, actor=Actor.GOVERNOR)
        row = ledger.get_order(oid)
        assert row["state"] == state.value
        assert row["rejection_reason"] == reason.value
        assert row["terminal_at"] is not None

    def test_direct_fill_from_submitted_is_legal(self, ledger):
        """A market order can fill without a separate ACK or partial."""
        req = make_request(nonce=777)
        oid = ledger.create_order(req)
        ledger.transition(oid, OrderState.RISK_APPROVED)
        ledger.transition(oid, OrderState.SUBMITTED)
        ledger.transition(oid, OrderState.FILLED, actor=Actor.ADAPTER)
        assert ledger.get_order(oid)["state"] == OrderState.FILLED.value

    def test_venue_rejection_from_submitted(self, ledger):
        req = make_request(nonce=888)
        oid = ledger.create_order(req)
        ledger.transition(oid, OrderState.RISK_APPROVED)
        ledger.transition(oid, OrderState.SUBMITTED)
        ledger.transition(oid, OrderState.REJECTED,
                          reason_code=RejectionReason.VENUE_REJECTED, actor=Actor.ADAPTER)
        assert ledger.get_order(oid)["rejection_reason"] == "VENUE_REJECTED"

    def test_expiry_from_acked(self, ledger):
        req = make_request(nonce=999)
        oid = ledger.create_order(req)
        walk_to(ledger, oid, OrderState.ACKED)
        ledger.transition(oid, OrderState.EXPIRED,
                          reason_code=RejectionReason.EXPIRED_UNFILLED)
        assert ledger.get_order(oid)["state"] == OrderState.EXPIRED.value


# ======================================================================================
# 4. Illegal transitions are refused
# ======================================================================================

class TestIllegalTransitionsRefused:

    def test_exhaustive_illegal_matrix(self, ledger):
        """
        Every (state, target) pair NOT in the table must raise. This is the core
        guarantee, so it is checked exhaustively rather than by sampling.
        """
        checked = 0
        for src in OrderState:
            for tgt in OrderState:
                if tgt in LEGAL_TRANSITIONS[src]:
                    continue
                if src in TERMINAL_STATES:
                    with pytest.raises(TerminalStateError):
                        assert_transition(src, tgt)
                else:
                    with pytest.raises(IllegalTransitionError):
                        assert_transition(src, tgt)
                checked += 1
        # 11 states squared minus the legal edges: 121 - 23 = 98 illegal pairs.
        total_pairs = len(OrderState) ** 2
        legal_edges = sum(len(v) for v in LEGAL_TRANSITIONS.values())
        assert checked == total_pairs - legal_edges == 98, (
            f"expected 98 illegal pairs, checked {checked}"
        )

    def test_new_cannot_jump_to_filled(self, ledger):
        req = make_request(nonce=1111)
        oid = ledger.create_order(req)
        with pytest.raises(IllegalTransitionError):
            ledger.transition(oid, OrderState.FILLED)
        # The failed attempt must not have mutated the order or the ledger.
        assert ledger.get_order(oid)["state"] == OrderState.NEW.value
        assert len(ledger.get_history(oid)) == 1

    def test_new_cannot_jump_to_submitted(self, ledger):
        """Skipping the governor is exactly what must be impossible."""
        req = make_request(nonce=1112)
        oid = ledger.create_order(req)
        with pytest.raises(IllegalTransitionError):
            ledger.transition(oid, OrderState.SUBMITTED)

    def test_failed_transition_leaves_no_ledger_row(self, ledger):
        req = make_request(nonce=1113)
        oid = ledger.create_order(req)
        before = len(ledger.get_history(oid))
        with pytest.raises(IllegalTransitionError):
            ledger.transition(oid, OrderState.EXPIRED,
                              reason_code=RejectionReason.EXPIRED_UNFILLED)
        assert len(ledger.get_history(oid)) == before

    def test_unknown_order_raises(self, ledger):
        with pytest.raises(KeyError):
            ledger.transition("ORD-doesnotexist", OrderState.FILLED)


# ======================================================================================
# 5. Terminal immutability
# ======================================================================================

class TestTerminalImmutability:

    @pytest.mark.parametrize("terminal,reason", [
        (OrderState.FILLED, None),
        (OrderState.RISK_REJECTED, RejectionReason.RISK_C6_PER_ORDER_CEILING),
        (OrderState.INVALID, RejectionReason.MALFORMED_REQUEST),
        (OrderState.CANCELLED, RejectionReason.CANCELLED_BY_SYSTEM),
        (OrderState.REJECTED, RejectionReason.VENUE_REJECTED),
        (OrderState.EXPIRED, RejectionReason.EXPIRED_UNFILLED),
    ])
    def test_every_terminal_state_rejects_every_target(self, ledger, terminal, reason):
        """
        For each of the six terminal states, attempt a transition to every state in the
        vocabulary and require refusal. This is what makes a rejection un-erasable.
        """
        req = make_request(nonce=abs(hash(terminal.value)) % 9000 + 2000)
        oid = ledger.create_order(req)
        reach_terminal(ledger, oid, terminal, reason)

        assert ledger.get_order(oid)["state"] == terminal.value
        history_len = len(ledger.get_history(oid))

        for target in OrderState:
            with pytest.raises(TerminalStateError):
                ledger.transition(oid, target, reason_code=reason)

        # State and history must be untouched by all eleven refusals.
        assert ledger.get_order(oid)["state"] == terminal.value
        assert len(ledger.get_history(oid)) == history_len

    def test_self_transition_out_of_terminal_is_refused(self, ledger):
        """Re-asserting FILLED must not write a phantom ledger row."""
        req = make_request(nonce=3001)
        oid = ledger.create_order(req)
        walk_to(ledger, oid, OrderState.FILLED)
        n = len(ledger.get_history(oid))
        with pytest.raises(TerminalStateError):
            ledger.transition(oid, OrderState.FILLED)
        assert len(ledger.get_history(oid)) == n

    def test_terminal_at_is_stamped_once(self, ledger):
        req = make_request(nonce=3002)
        oid = ledger.create_order(req)
        walk_to(ledger, oid, OrderState.FILLED)
        stamped = ledger.get_order(oid)["terminal_at"]
        assert stamped is not None
        assert isinstance(datetime.fromisoformat(stamped), datetime)

    def test_non_terminal_orders_have_no_terminal_at(self, ledger):
        req = make_request(nonce=3003)
        oid = ledger.create_order(req)
        walk_to(ledger, oid, OrderState.ACKED)
        assert ledger.get_order(oid)["terminal_at"] is None


# ======================================================================================
# 6. Audit ledger integrity
# ======================================================================================

class TestAuditLedger:

    def test_genesis_row_is_written_on_creation(self, ledger):
        req = make_request(nonce=4001)
        oid = ledger.create_order(req)
        hist = ledger.get_history(oid)
        assert len(hist) == 1
        assert hist[0]["from_state"] is None
        assert hist[0]["to_state"] == OrderState.NEW.value

    def test_sequence_numbers_are_strictly_monotonic(self, ledger):
        req = make_request(nonce=4002)
        oid = ledger.create_order(req)
        walk_to(ledger, oid, OrderState.FILLED)
        seqs = [r["seq"] for r in ledger.get_history(oid)]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs), "duplicate sequence numbers"
        assert all(b > a for a, b in zip(seqs, seqs[1:])), "sequence not strictly increasing"

    def test_history_replays_to_the_persisted_state(self, ledger):
        """
        The ledger alone must be sufficient to reconstruct the final state. If it is not,
        the audit trail is decorative rather than authoritative.
        """
        req = make_request(nonce=4003)
        oid = ledger.create_order(req)
        walk_to(ledger, oid, OrderState.FILLED)

        replayed = None
        for row in ledger.get_history(oid):
            if replayed is not None:
                # Each row's from_state must equal the previously replayed state.
                assert row["from_state"] == replayed, "chain broken in ledger"
            replayed = row["to_state"]

        assert replayed == ledger.get_order(oid)["state"]

    def test_state_seq_matches_history_length(self, ledger):
        req = make_request(nonce=4004)
        oid = ledger.create_order(req)
        walk_to(ledger, oid, OrderState.FILLED)
        row = ledger.get_order(oid)
        # Genesis row is not counted in state_seq (which starts at 0 for NEW).
        assert row["state_seq"] == len(ledger.get_history(oid)) - 1

    def test_history_records_actor_for_every_row(self, ledger):
        req = make_request(nonce=4005)
        oid = ledger.create_order(req)
        ledger.transition(oid, OrderState.RISK_APPROVED, actor=Actor.GOVERNOR)
        ledger.transition(oid, OrderState.SUBMITTED, actor=Actor.SYSTEM)
        ledger.transition(oid, OrderState.ACKED, actor=Actor.ADAPTER)
        actors = [r["actor"] for r in ledger.get_history(oid)]
        assert actors == ["SYSTEM", "GOVERNOR", "SYSTEM", "ADAPTER"]

    def test_actor_vocabulary_is_closed(self):
        """
        Actors are a closed enum, so no free-text username, email, or account number can
        be written into the ledger. This is the no-PII guarantee.
        """
        for a in Actor:
            assert a.value in {"SYSTEM", "GOVERNOR", "ADAPTER", "USER", "KILL_SWITCH"}
        assert len(Actor) == 5

    def test_reason_code_is_from_the_closed_vocabulary(self, ledger):
        req = make_request(nonce=4006)
        oid = ledger.create_order(req)
        ledger.transition(oid, OrderState.RISK_REJECTED,
                          reason_code=RejectionReason.RISK_C3_COUNTRY_EXPOSURE,
                          actor=Actor.GOVERNOR)
        row = ledger.get_history(oid)[-1]
        assert row["reason_code"] == "RISK_C3_COUNTRY_EXPOSURE"
        assert row["reason_code"] in {r.value for r in RejectionReason}

    def test_every_risk_control_has_a_reason_code(self):
        """Controls C1-C12 must each be representable, or breaches cannot be attributed."""
        risk_codes = {r.value for r in RejectionReason if r.value.startswith("RISK_C")}
        for n in range(1, 13):
            assert any(c.startswith(f"RISK_C{n}_") for c in risk_codes), f"C{n} missing"
        assert len(risk_codes) == 12

    def test_ledgers_are_isolated_per_order(self, ledger):
        a = ledger.create_order(make_request(nonce=4007))
        b = ledger.create_order(make_request(symbol="MSFT", nonce=4008))
        walk_to(ledger, a, OrderState.FILLED)
        assert len(ledger.get_history(b)) == 1


# ======================================================================================
# 7. Rejections require a reason
# ======================================================================================

class TestRejectionAuditability:

    @pytest.mark.parametrize("state", [
        OrderState.REJECTED, OrderState.RISK_REJECTED, OrderState.INVALID,
        OrderState.EXPIRED, OrderState.CANCELLED,
    ])
    def test_reasonless_rejection_is_refused(self, ledger, state):
        """A rejection without a reason is not answerable from the ledger, so it is refused."""
        req = make_request(nonce=abs(hash(state.value)) % 9000 + 5000)
        oid = ledger.create_order(req)
        if state not in LEGAL_TRANSITIONS[OrderState.NEW]:
            ledger.transition(oid, OrderState.RISK_APPROVED)
            if state not in LEGAL_TRANSITIONS[OrderState.RISK_APPROVED]:
                ledger.transition(oid, OrderState.SUBMITTED)
        with pytest.raises(ValueError):
            ledger.transition(oid, state)
        # And the order must be left where it was.
        assert ledger.get_order(oid)["rejection_reason"] is None

    def test_filled_requires_no_reason(self, ledger):
        """FILLED is a conclusion, not a rejection, so no reason code is demanded."""
        req = make_request(nonce=5500)
        oid = ledger.create_order(req)
        walk_to(ledger, oid, OrderState.FILLED)
        assert ledger.get_order(oid)["rejection_reason"] is None


# ======================================================================================
# 8. Duplicate detection
# ======================================================================================

class TestDuplicateDetection:

    def test_duplicate_client_order_id_is_refused(self, ledger):
        req = make_request(nonce=6001)
        ledger.create_order(req)
        with pytest.raises(DuplicateOrderError):
            ledger.create_order(req)

    def test_duplicate_reports_the_existing_state(self, ledger):
        req = make_request(nonce=6002)
        oid = ledger.create_order(req)
        walk_to(ledger, oid, OrderState.ACKED)
        with pytest.raises(DuplicateOrderError) as exc:
            ledger.create_order(req)
        assert exc.value.existing_state == OrderState.ACKED.value

    def test_duplicate_does_not_corrupt_the_original(self, ledger):
        req = make_request(nonce=6003)
        oid = ledger.create_order(req)
        with pytest.raises(DuplicateOrderError):
            ledger.create_order(req)
        assert ledger.get_order(oid)["state"] == OrderState.NEW.value
        assert len(ledger.get_history(oid)) == 1

    def test_reemitted_signal_with_new_nonce_is_accepted(self, ledger):
        ledger.create_order(make_request(nonce=6004, signal_date=date(2026, 10, 1)))
        ledger.create_order(make_request(nonce=6004, signal_date=date(2026, 10, 2)))


# ======================================================================================
# 9. Order request validation
# ======================================================================================

class TestOrderRequestValidation:

    def test_symbol_is_normalized(self):
        cid = build_client_order_id("AAPL", "BUY", 1, "MARKET", "S", date(2026, 10, 1))
        req = OrderRequest(client_order_id=cid, symbol="  aapl  ", market=Market.US,
                           side=OrderSide.BUY, quantity=1)
        assert req.symbol == "AAPL"

    @pytest.mark.parametrize("qty", [0, -1, -100.5])
    def test_non_positive_quantity_is_refused(self, qty):
        cid = build_client_order_id("AAPL", "BUY", 1, "MARKET", "S", date(2026, 10, 1))
        with pytest.raises(Exception):
            OrderRequest(client_order_id=cid, symbol="AAPL", market=Market.US,
                         side=OrderSide.BUY, quantity=qty)

    def test_limit_order_requires_limit_price(self):
        cid = build_client_order_id("AAPL", "BUY", 1, "LIMIT", "S", date(2026, 10, 1))
        with pytest.raises(Exception):
            OrderRequest(client_order_id=cid, symbol="AAPL", market=Market.US,
                         side=OrderSide.BUY, quantity=1, order_type=OrderType.LIMIT)

    def test_stop_order_requires_stop_price(self):
        cid = build_client_order_id("AAPL", "BUY", 1, "STOP", "S", date(2026, 10, 1))
        with pytest.raises(Exception):
            OrderRequest(client_order_id=cid, symbol="AAPL", market=Market.US,
                         side=OrderSide.BUY, quantity=1, order_type=OrderType.STOP)

    def test_stop_limit_requires_both_prices(self):
        cid = build_client_order_id("AAPL", "BUY", 1, "STOP_LIMIT", "S", date(2026, 10, 1))
        with pytest.raises(Exception):
            OrderRequest(client_order_id=cid, symbol="AAPL", market=Market.US,
                         side=OrderSide.BUY, quantity=1,
                         order_type=OrderType.STOP_LIMIT, limit_price=100.0)

    def test_negative_prices_are_refused(self):
        cid = build_client_order_id("AAPL", "BUY", 1, "LIMIT", "S", date(2026, 10, 1))
        with pytest.raises(Exception):
            OrderRequest(client_order_id=cid, symbol="AAPL", market=Market.US,
                         side=OrderSide.BUY, quantity=1,
                         order_type=OrderType.LIMIT, limit_price=-5.0)

    def test_closing_intent_is_identified(self):
        cid = build_client_order_id("AAPL", "SELL", 1, "MARKET", "S", date(2026, 10, 1))
        req = OrderRequest(client_order_id=cid, symbol="AAPL", market=Market.US,
                           side=OrderSide.SELL, quantity=1)
        assert req.is_terminal_intent is True

    def test_market_is_recorded_for_the_firewall(self, ledger):
        """Market must persist, or the Phase 34.4 Canadian firewall has nothing to read."""
        cid = build_client_order_id("SHOP", "BUY", 10, "MARKET", "S", date(2026, 10, 1))
        req = OrderRequest(client_order_id=cid, symbol="SHOP", market=Market.CA,
                           side=OrderSide.BUY, quantity=10)
        oid = ledger.create_order(req)
        assert ledger.get_order(oid)["market"] == "CA"

    def test_to_dict_serializes_enums_and_dates(self):
        cid = build_client_order_id("AAPL", "BUY", 1, "MARKET", "S", date(2026, 10, 1))
        req = OrderRequest(client_order_id=cid, symbol="AAPL", market=Market.US,
                           side=OrderSide.BUY, quantity=1, signal_date=date(2026, 10, 1))
        d = req.to_dict()
        assert d["market"] == "US"
        assert d["side"] == "BUY"
        assert d["signal_date"] == "2026-10-01"
        import json
        json.dumps(d)  # must be JSON-serializable


# ======================================================================================
# 10. Persistence and queries
# ======================================================================================

class TestPersistence:

    def test_orders_survive_a_new_ledger_instance(self, tmp_path):
        """State is durable, not in-memory."""
        db_file = tmp_path / "durable.db"
        req = make_request(nonce=7001)
        first = ExecutionLedger(db=Database(db_url=f"sqlite:///{db_file}"))
        oid = first.create_order(req)
        walk_to(first, oid, OrderState.ACKED)

        second = ExecutionLedger(db=Database(db_url=f"sqlite:///{db_file}"))
        assert second.get_order(oid)["state"] == OrderState.ACKED.value
        assert len(second.get_history(oid)) == 4

    def test_open_order_count_excludes_terminal(self, ledger):
        a = ledger.create_order(make_request(nonce=7002))
        b = ledger.create_order(make_request(symbol="MSFT", nonce=7003))
        c = ledger.create_order(make_request(symbol="NVDA", nonce=7004))
        walk_to(ledger, a, OrderState.FILLED)
        ledger.transition(b, OrderState.RISK_REJECTED,
                          reason_code=RejectionReason.RISK_C2_POSITION_WEIGHT,
                          actor=Actor.GOVERNOR)
        assert ledger.open_order_count() == 1  # only c remains open
        assert ledger.get_order(c)["state"] == OrderState.NEW.value

    def test_query_by_state(self, ledger):
        a = ledger.create_order(make_request(nonce=7005))
        ledger.create_order(make_request(symbol="MSFT", nonce=7006))
        ledger.transition(a, OrderState.RISK_APPROVED, actor=Actor.GOVERNOR)
        approved = ledger.get_orders_by_state(OrderState.RISK_APPROVED)
        assert len(approved) == 1
        assert approved[0]["order_id"] == a

    def test_tables_are_created_idempotently(self, tmp_path):
        db_file = tmp_path / "idem.db"
        db = Database(db_url=f"sqlite:///{db_file}")
        ExecutionLedger(db=db)
        ExecutionLedger(db=db)  # must not raise
