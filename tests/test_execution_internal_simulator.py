"""
Phase 34.3 Test Suite -- Internal Simulator Adapter

The properties this suite exists to protect:

  1. NO LOOK-AHEAD -- a fill may only come from a bar strictly AFTER the signal date. This
     is the single easiest way to fabricate backtest performance, so it is asserted on
     every fill rather than assumed.
  2. THE BOOK CANNOT BE OVER-DRAWN -- cash conservation holds to the cent across both
     markets, including when a fill costs more than the reservation estimated.
  3. FEES COME FROM THE EXISTING FEE TABLE -- the simulator must not drift from
     execution_algo.get_exchange_fee.
  4. DETERMINISM -- same bars, same order, same fills. No hidden randomness.

Tests read real bars from the platform database (the simulator has no other source) but
write to an isolated ledger so ordering cannot matter.
"""

from datetime import date
import tempfile

import pytest

from src.data.db import Database
from src.engine.execution_algo import execution_algo_engine
from src.execution.adapters.base import AdapterCapabilities
from src.execution.adapters.internal_simulator import (
    InternalSimulatorAdapter,
    UnacceptedOrderError,
)
from src.execution.order_model import (
    Market,
    OrderRequest,
    OrderSide,
    OrderState,
    OrderType,
    build_client_order_id,
)
from src.execution.state_machine import ExecutionLedger

SIGNAL_DATE = date(2026, 9, 20)


@pytest.fixture()
def sim():
    ledger = ExecutionLedger(db=Database(db_url=f"sqlite:///{tempfile.mktemp(suffix='.db')}"))
    return InternalSimulatorAdapter(ledger=ledger, starting_cash_usd=100_000.0)


def big_book(cash=20_000_000_000.0):
    """
    An adapter with a book large enough to absorb the multi-million-share orders used to
    exercise the participation cap. A $100k book cannot: the simulator correctly rejects
    those orders at submission, which is the capital guard working as designed.
    """
    ledger = ExecutionLedger(db=Database(db_url=f"sqlite:///{tempfile.mktemp(suffix='.db')}"))
    return InternalSimulatorAdapter(ledger=ledger, starting_cash_usd=cash)


def make_order(sim, symbol="AAPL", market=Market.US, qty=100.0, side=OrderSide.BUY,
               order_type=OrderType.MARKET, limit_price=None, stop_price=None,
               signal_date=SIGNAL_DATE, nonce=0, cash=None):
    """Creates an order, submits it to the adapter, and drives it to SUBMITTED."""
    cid = build_client_order_id(symbol, side.value, qty, order_type.value, "S",
                                signal_date, nonce)
    req = OrderRequest(
        client_order_id=cid, symbol=symbol, market=market, side=side, quantity=qty,
        order_type=order_type, limit_price=limit_price, stop_price=stop_price,
        signal_date=signal_date,
    )
    ack = sim.submit_order(req)
    order_id = sim.ledger.create_order(req, adapter_id=sim.adapter_id)
    sim.ledger.transition(order_id, OrderState.RISK_APPROVED)
    if ack.accepted:
        sim.ledger.transition(order_id, OrderState.SUBMITTED)
    return req, order_id, ack


# ======================================================================================
# 1. Adapter contract
# ======================================================================================

class TestAdapterContract:

    def test_is_always_configured(self, sim):
        """No credentials, no external dependency -- it must never be unavailable."""
        assert sim.is_configured() is True

    def test_covers_both_markets(self, sim):
        assert sim.supports_market(Market.US)
        assert sim.supports_market(Market.CA)

    def test_is_not_external(self, sim):
        """
        This flag is what the Phase 34.4 Canadian firewall reads. The simulator is internal,
        so it is the one adapter permitted to route Canadian symbols.
        """
        assert sim.is_external is False

    def test_license_cleared(self, sim):
        """Nothing to license: every input is the platform's own bar data."""
        assert sim.license_cleared is True

    def test_capabilities_are_declared(self, sim):
        assert sim.capabilities.supports(OrderType.MARKET)
        assert sim.capabilities.supports(OrderType.LIMIT)
        assert sim.capabilities.supports(OrderType.VWAP)
        assert sim.capabilities.supports(OrderType.STOP_LIMIT)

    def test_unsupported_types_are_declared_as_such(self, sim):
        assert sim.capabilities.supports(OrderType.BRACKET) is False
        assert sim.capabilities.supports(OrderType.OCO) is False

    def test_validate_rejects_unsupported_type(self, sim):
        req = OrderRequest(
            client_order_id=build_client_order_id("AAPL", "BUY", 1, "BRACKET", "S", SIGNAL_DATE),
            symbol="AAPL", market=Market.US, side=OrderSide.BUY, quantity=1,
            order_type=OrderType.BRACKET, signal_date=SIGNAL_DATE,
        )
        ack = sim.validate_order(req)
        assert ack.accepted is False
        assert ack.reason == "CAPABILITY_UNSUPPORTED"

    def test_base_class_fails_closed_on_is_external(self):
        """
        A new adapter must opt INTO being internal. The default is external, so forgetting
        the flag makes an adapter fail the Canadian firewall rather than bypass it.
        """
        from src.execution.adapters.base import BrokerAdapter
        assert BrokerAdapter.is_external is True
        assert BrokerAdapter.license_cleared is False


# ======================================================================================
# 2. No look-ahead
# ======================================================================================

class TestNoLookAhead:

    @pytest.mark.parametrize("symbol,market", [
        ("AAPL", Market.US), ("SPY", Market.US), ("XIU", Market.CA), ("SHOP", Market.CA),
    ])
    def test_fill_bar_is_strictly_after_the_signal_date(self, sim, symbol, market):
        """
        The core integrity guarantee. An order must never fill on the bar that generated
        it, because that bar's close is what produced the signal.
        """
        req, oid, ack = make_order(sim, symbol=symbol, market=market, qty=10)
        assert ack.accepted, ack.detail
        fills = sim.fill_order(req, oid)
        assert fills, f"no fills produced for {symbol}"
        for f in fills:
            assert f.bar_date > SIGNAL_DATE.isoformat(), (
                f"{symbol} filled on {f.bar_date}, not after {SIGNAL_DATE}"
            )

    def test_get_bars_excludes_the_signal_date_itself(self, sim):
        """The query boundary is a strict inequality, not >=."""
        bars = sim.get_bars("AAPL", after=SIGNAL_DATE.isoformat(), limit=5)
        assert bars
        assert all(b["trading_date"] > SIGNAL_DATE.isoformat() for b in bars)

    def test_a_signal_on_the_last_bar_cannot_fill(self, sim):
        """
        With no future bar there is nothing to fill on. The order must expire rather than
        reach back into history.
        """
        latest = sim.db.execute_query("SELECT MAX(trading_date) d FROM bar_1d;")[0]["d"]
        req, oid, ack = make_order(
            sim, qty=10, signal_date=date.fromisoformat(latest), nonce=99,
        )
        assert ack.accepted
        fills = sim.fill_order(req, oid)
        assert fills == []
        assert sim.ledger.get_order(oid)["state"] == OrderState.EXPIRED.value

    def test_unknown_symbol_cannot_fill(self, sim):
        req, oid, ack = make_order(sim, symbol="ZZZZ", qty=10, nonce=77)
        assert ack.accepted is False
        assert ack.reason == "UNKNOWN_SYMBOL"


# ======================================================================================
# 3. Fill pricing
# ======================================================================================

class TestFillPricing:

    def test_market_order_fills_at_the_bar_open(self, sim):
        req, oid, ack = make_order(sim, qty=100)
        bars = sim.get_bars("AAPL", after=SIGNAL_DATE.isoformat(), limit=1)
        fills = sim.fill_order(req, oid)
        assert len(fills) == 1
        assert fills[0].price == pytest.approx(float(bars[0]["open"]), abs=1e-6)

    def test_friction_is_adverse_for_a_buy(self, sim):
        req, oid, ack = make_order(sim, qty=100)
        fills = sim.fill_order(req, oid)
        f = fills[0]
        assert f.effective_price > f.price, "a buy must pay up, not down"
        assert f.effective_price == pytest.approx(f.price * 1.001, rel=1e-9)

    def test_friction_is_adverse_for_a_sell(self, sim):
        req, oid, ack = make_order(sim, qty=100, side=OrderSide.SELL, nonce=1)
        fills = sim.fill_order(req, oid)
        assert fills
        f = fills[0]
        assert f.effective_price < f.price, "a sell must receive less, not more"
        assert f.effective_price == pytest.approx(f.price * 0.999, rel=1e-9)

    def test_friction_bps_is_recorded(self, sim):
        req, oid, ack = make_order(sim, qty=100)
        f = sim.fill_order(req, oid)[0]
        assert f.friction_bps == InternalSimulatorAdapter.FRICTION_BPS

    def test_market_order_is_a_taker(self, sim):
        req, oid, ack = make_order(sim, qty=100)
        assert sim.fill_order(req, oid)[0].liquidity_flag == "TAKER"

    def test_limit_order_is_a_maker(self, sim):
        bars = sim.get_bars("AAPL", after=SIGNAL_DATE.isoformat(), limit=1)
        req, oid, ack = make_order(
            sim, qty=100, order_type=OrderType.LIMIT,
            limit_price=float(bars[0]["high"]) + 1.0, nonce=2,
        )
        assert ack.accepted
        assert sim.fill_order(req, oid)[0].liquidity_flag == "MAKER"

    def test_buy_limit_fills_at_the_better_of_open_and_limit(self, sim):
        """A buy limit above the open should fill at the open, not pay up to the limit."""
        bars = sim.get_bars("AAPL", after=SIGNAL_DATE.isoformat(), limit=1)
        generous = float(bars[0]["high"]) + 5.0
        req, oid, ack = make_order(sim, qty=100, order_type=OrderType.LIMIT,
                                   limit_price=generous, nonce=3)
        f = sim.fill_order(req, oid)[0]
        assert f.price == pytest.approx(float(bars[0]["open"]), abs=1e-6)

    def test_buy_limit_below_the_range_does_not_fill(self, sim):
        """A limit no bar reaches must expire, never fill at some convenient price."""
        req, oid, ack = make_order(sim, qty=100, order_type=OrderType.LIMIT,
                                   limit_price=1.0, nonce=4)
        assert ack.accepted
        assert sim.fill_order(req, oid) == []
        assert sim.ledger.get_order(oid)["state"] == OrderState.EXPIRED.value

    def test_sell_limit_above_the_range_does_not_fill(self, sim):
        req, oid, ack = make_order(sim, qty=100, side=OrderSide.SELL,
                                   order_type=OrderType.LIMIT,
                                   limit_price=100_000.0, nonce=5)
        assert ack.accepted
        assert sim.fill_order(req, oid) == []

    def test_buy_stop_fills_only_when_the_high_reaches_it(self, sim):
        bars = sim.get_bars("AAPL", after=SIGNAL_DATE.isoformat(),
                            limit=InternalSimulatorAdapter.FILL_WINDOW_BARS)
        attainable = min(float(b["high"]) for b in bars) - 0.01
        req, oid, ack = make_order(sim, qty=100, order_type=OrderType.STOP,
                                   stop_price=attainable, nonce=6)
        assert ack.accepted
        assert sim.fill_order(req, oid), "an attainable stop should fill"

    def test_buy_stop_above_the_range_does_not_fill(self, sim):
        req, oid, ack = make_order(sim, qty=100, order_type=OrderType.STOP,
                                   stop_price=1_000_000.0, nonce=7)
        assert ack.accepted
        assert sim.fill_order(req, oid) == []


# ======================================================================================
# 4. Fees reuse the existing table
# ======================================================================================

class TestFeeReuse:

    @pytest.mark.parametrize("symbol,market,qty", [
        ("AAPL", Market.US, 100), ("SPY", Market.US, 100),
        ("XIU", Market.CA, 100), ("RY", Market.CA, 400),
    ])
    def test_fee_matches_execution_algo_exactly(self, sim, symbol, market, qty):
        """
        The simulator must not reimplement fees. Any drift here silently misstates every
        performance number the platform reports.
        """
        req, oid, ack = make_order(sim, symbol=symbol, market=market, qty=qty,
                                   nonce=hash(symbol) % 1000)
        assert ack.accepted
        f = sim.fill_order(req, oid)[0]
        expected = execution_algo_engine.get_exchange_fee(
            int(round(f.quantity)), market.value, is_maker=False,
        )
        assert f.fee == pytest.approx(expected, abs=1e-9)

    def test_maker_rebate_is_negative_for_a_limit_order(self, sim):
        """Limit orders earn the maker rebate, so the fee must be negative."""
        bars = sim.get_bars("AAPL", after=SIGNAL_DATE.isoformat(), limit=1)
        req, oid, ack = make_order(sim, qty=100, order_type=OrderType.LIMIT,
                                   limit_price=float(bars[0]["high"]) + 1.0, nonce=11)
        f = sim.fill_order(req, oid)[0]
        assert f.fee < 0, f"expected a rebate, got {f.fee}"


# ======================================================================================
# 5. Participation cap and partial fills
# ======================================================================================

class TestParticipation:

    def test_a_huge_order_splits_across_bars(self):
        """No fill may exceed the participation share of its bar's volume."""
        sim = big_book()
        req, oid, ack = make_order(sim, qty=20_000_000, nonce=21)
        assert ack.accepted
        fills = sim.fill_order(req, oid)
        bars = sim.get_bars("AAPL", after=SIGNAL_DATE.isoformat(),
                            limit=InternalSimulatorAdapter.FILL_WINDOW_BARS)
        by_date = {b["trading_date"]: float(b["volume"]) for b in bars}
        for f in fills:
            cap = by_date[f.bar_date] * (InternalSimulatorAdapter.MAX_PARTICIPATION_PCT / 100.0)
            assert f.quantity <= cap + 1e-6, f"{f.bar_date} exceeded its participation cap"
        assert len(fills) > 1, "a 20M share order must split"

    def test_unfillable_remainder_expires(self):
        sim = big_book()
        req, oid, ack = make_order(sim, qty=20_000_000, nonce=22)
        sim.fill_order(req, oid)
        assert sim.ledger.get_order(oid)["state"] == OrderState.EXPIRED.value

    def test_small_order_fills_in_one_piece(self, sim):
        req, oid, ack = make_order(sim, qty=100, nonce=23)
        assert len(sim.fill_order(req, oid)) == 1


# ======================================================================================
# 6. Capital integrity -- the defect this phase closes
# ======================================================================================

class TestCapitalIntegrity:

    @pytest.mark.parametrize("symbol,market,qty", [
        ("AAPL", Market.US, 100), ("AAPL", Market.US, 50), ("SPY", Market.US, 100),
        ("XIU", Market.CA, 200), ("SHOP", Market.CA, 100), ("RY", Market.CA, 300),
    ])
    def test_cash_is_conserved_to_the_cent(self, sim, symbol, market, qty):
        """
        free + reserved + spent must equal starting capital exactly. A reservation is an
        estimate taken from the last close while the fill uses the next bar's open, so the
        two routinely differ -- the difference must be reconciled, not floored away.
        """
        req, oid, ack = make_order(sim, symbol=symbol, market=market, qty=qty,
                                   nonce=abs(hash(symbol + str(qty))) % 5000)
        assert ack.accepted, ack.detail
        sim.fill_order(req, oid)
        a = sim.get_account()
        total = a.cash_free + a.cash_reserved + sim.spent_total
        assert total == pytest.approx(100_000.0, abs=0.01), (
            f"{symbol} {qty}: free {a.cash_free:,.2f} + reserved {a.cash_reserved:,.2f} "
            f"+ spent {sim.spent_total:,.2f} = {total:,.4f}"
        )

    @pytest.mark.parametrize("symbol,market,qty", [
        ("AAPL", Market.US, 100), ("XIU", Market.CA, 200), ("SHOP", Market.CA, 100),
    ])
    def test_cash_never_goes_negative(self, sim, symbol, market, qty):
        req, oid, ack = make_order(sim, symbol=symbol, market=market, qty=qty,
                                   nonce=abs(hash(symbol)) % 4000 + 500)
        assert ack.accepted
        sim.fill_order(req, oid)
        assert sim.get_account().cash_free >= -0.01

    def test_reservation_is_released_after_a_complete_fill(self, sim):
        req, oid, ack = make_order(sim, qty=100, nonce=31)
        sim.fill_order(req, oid)
        assert sim.get_account().cash_reserved == 0.0

    def test_reservation_is_released_on_cancel(self, sim):
        req, oid, ack = make_order(sim, qty=100, nonce=32)
        assert ack.accepted
        reserved_before = sim.get_account().cash_reserved
        assert reserved_before > 0
        sim.cancel_order(req, oid)
        a = sim.get_account()
        assert a.cash_reserved == 0.0
        assert a.cash_free == pytest.approx(100_000.0, abs=0.01)
        assert sim.ledger.get_order(oid)["state"] == OrderState.CANCELLED.value

    def test_reservation_is_released_on_expiry(self, sim):
        req, oid, ack = make_order(sim, qty=100, order_type=OrderType.LIMIT,
                                   limit_price=1.0, nonce=33)
        assert ack.accepted
        assert sim.fill_order(req, oid) == []
        assert sim.get_account().cash_reserved == 0.0

    def test_oversized_order_is_refused_at_submission(self, sim):
        """300 AAPL at ~$336 is ~$100.8k against a $100k book."""
        req, oid, ack = make_order(sim, qty=300, nonce=34)
        assert ack.accepted is False
        assert ack.reason == "RISK_C1_BUYING_POWER"
        assert sim.get_account().cash_reserved == 0.0

    def test_a_rejected_order_cannot_be_filled(self, sim):
        """
        Regression guard for a real defect: filling an order the adapter never accepted
        spends cash that was never reserved, over-drawing the book.
        """
        req, oid, ack = make_order(sim, qty=300, nonce=35)
        assert ack.accepted is False
        with pytest.raises(UnacceptedOrderError):
            sim.fill_order(req, oid)
        assert sim.spent_total == 0.0
        assert sim.get_account().cash_free == pytest.approx(100_000.0)

    def test_tight_cash_is_not_overdrawn(self, sim):
        """A book with just enough cash must still conserve exactly."""
        ledger = ExecutionLedger(db=Database(db_url=f"sqlite:///{tempfile.mktemp(suffix='.db')}"))
        tight = InternalSimulatorAdapter(ledger=ledger, starting_cash_usd=34_000.0)
        req, oid, ack = make_order(tight, qty=100, nonce=36)
        assert ack.accepted
        tight.fill_order(req, oid)
        a = tight.get_account()
        assert a.cash_free + a.cash_reserved + tight.spent_total == pytest.approx(34_000.0, abs=0.01)
        assert a.cash_free >= -0.01

    def test_position_is_recorded(self, sim):
        req, oid, ack = make_order(sim, qty=100, nonce=37)
        sim.fill_order(req, oid)
        assert sim.positions.get("AAPL") == pytest.approx(100.0)

    def test_sell_reduces_the_position(self, sim):
        b, ob, ackb = make_order(sim, qty=100, nonce=38)
        sim.fill_order(b, ob)
        s, os_, acks = make_order(sim, qty=100, side=OrderSide.SELL, nonce=39)
        sim.fill_order(s, os_)
        assert sim.positions.get("AAPL") is None, "a flat position should be dropped"


# ======================================================================================
# 7. Determinism
# ======================================================================================

class TestDeterminism:

    def test_identical_inputs_produce_identical_fills(self):
        """Same bars, same order, same fills. There is no randomness in the fill model."""
        def run(nonce):
            s = big_book()
            req, oid, ack = make_order(s, qty=5_000_000, nonce=nonce)
            return [(f.bar_date, f.quantity, f.price, f.effective_price, f.fee)
                    for f in s.fill_order(req, oid)]

        # Different nonces give different client order ids but the same economics.
        assert run(101) == run(102)

    def test_repeated_runs_of_the_same_adapter_match(self):
        sim = big_book()
        req, oid, ack = make_order(sim, qty=1_000_000, nonce=103)
        first = [(f.bar_date, f.quantity) for f in sim.fill_order(req, oid)]
        s2 = big_book()
        req2, oid2, _ = make_order(s2, qty=1_000_000, nonce=104)
        assert first == [(f.bar_date, f.quantity) for f in s2.fill_order(req2, oid2)]


# ======================================================================================
# 8. Canadian routing
# ======================================================================================

class TestCanadianRouting:

    def test_canadian_orders_fill_on_the_internal_adapter(self, sim):
        """
        The whole point of an internal venue: CIRO DMR 3200 bars Canadian symbols from
        external order-execution-only dealers, so they must work here.
        """
        req, oid, ack = make_order(sim, symbol="XIU", market=Market.CA, qty=100, nonce=41)
        assert ack.accepted
        fills = sim.fill_order(req, oid)
        assert fills
        assert sim.ledger.get_order(oid)["state"] == OrderState.FILLED.value

    def test_canadian_notional_is_fx_converted(self, sim):
        """A CAD fill must reduce USD capital by the converted amount, not the face amount."""
        req, oid, ack = make_order(sim, symbol="XIU", market=Market.CA, qty=100, nonce=42)
        fills = sim.fill_order(req, oid)
        expected_usd = 100 * fills[0].effective_price * sim.fx_cad_usd
        assert sim.spent_total == pytest.approx(expected_usd + abs(
            sim._to_usd(fills[0].fee, Market.CA)), abs=0.01)
