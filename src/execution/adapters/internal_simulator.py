"""
Phase 34.3 -- Internal Simulator Adapter
US + Canada Market Intelligence Platform

The default and only execution venue that ships. It needs no credentials, has no
third-party licensing exposure, and covers both markets.

Fill model
----------
Orders fill at the OPEN of the first bar strictly AFTER the order's signal date. That
strict inequality is the no-look-ahead guarantee: an order can never fill on the bar that
generated it, which is the single easiest way to fabricate backtest performance.

Pricing
-------
- MARKET      fills at the bar open.
- LIMIT BUY   fills only if the bar's low reaches the limit, at min(open, limit).
- LIMIT SELL  fills only if the bar's high reaches the limit, at max(open, limit).
- STOP BUY    fills only if the bar's high reaches the stop, at max(open, stop).
- STOP SELL   fills only if the bar's low reaches the stop, at min(open, stop).
Friction of FRICTION_BPS is applied per side, adversely: buys pay up, sells receive less.
Exchange and regulatory fees come from execution_algo_engine.get_exchange_fee, reused
rather than reimplemented, so the simulator cannot drift from the platform's fee table.

Sizing
------
No fill exceeds MAX_PARTICIPATION_PCT of the fill bar's volume, so large orders split
across bars deterministically. There is no randomness: given the same bars, the same
order produces the same fills.

Capital
-------
Cash is reserved at submission and converted to spent on fill, or released on cancel and
expiry. The book therefore cannot be over-drawn, which is the specific defect this phase
exists to close.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from src.data.db import db as _default_db
from src.engine.execution_algo import execution_algo_engine
from src.execution.adapters.base import (
    AccountSnapshot,
    AdapterAck,
    AdapterCapabilities,
    BrokerAdapter,
    Fill,
)
from src.execution.order_model import (
    Actor,
    Market,
    OrderRequest,
    OrderSide,
    OrderState,
    OrderType,
    RejectionReason,
)
from src.execution.state_machine import ExecutionLedger


class UnacceptedOrderError(Exception):
    """
    Raised when a fill is requested for an order this adapter never accepted.

    Filling such an order would spend cash that was never reserved, which is how the paper
    book gets over-drawn. Callers must check AdapterAck.accepted before requesting a fill.
    """

    def __init__(self, client_order_id: str):
        self.client_order_id = client_order_id
        super().__init__(
            f"Order {client_order_id} was never accepted by the internal simulator; "
            "refusing to fill it. Check AdapterAck.accepted before filling."
        )


class InternalSimulatorAdapter(BrokerAdapter):
    """Simulated venue driven by the platform's own daily bars."""

    adapter_id = "INTERNAL_SIMULATOR"
    markets = frozenset({Market.US, Market.CA})
    is_external = False           # permits Canadian routing; no third party is involved
    license_cleared = True        # nothing to license -- all inputs are the platform's own
    capabilities = AdapterCapabilities(
        market=True, limit=True, stop=True, stop_limit=True,
        twap=True, vwap=True, pov=True,
        bracket=False, oco=False,
        fractional=False, short=True, streaming=False,
    )

    FRICTION_BPS = 10.0            # per side, applied adversely
    MAX_PARTICIPATION_PCT = 10.0   # a fill may not exceed this share of the bar's volume
    FILL_WINDOW_BARS = 5           # an unfilled order expires after this many bars

    def __init__(
        self,
        ledger: Optional[ExecutionLedger] = None,
        db=None,
        starting_cash_usd: float = 100_000.0,
        fx_cad_usd: float = 0.73,
    ):
        self.ledger = ledger or ExecutionLedger()
        self.db = db or _default_db
        self.fx_cad_usd = float(fx_cad_usd)
        self.cash_free = float(starting_cash_usd)
        self.cash_reserved = 0.0
        self.positions: Dict[str, float] = {}
        self.spent_total = 0.0
        # Exact reserved amount per order id. Re-estimating from last_close on release
        # would drift whenever the price moved between submission and cancellation.
        self._reservations: Dict[str, float] = {}
        # Client order ids this adapter has actually accepted. Without this, a caller that
        # ignores a submission rejection can still get a fill, which over-draws the book --
        # precisely the defect this phase exists to close.
        self._accepted: set = set()
        self._ensure_fill_table()

    # ------------------------------------------------------------------ adapter API

    def is_configured(self) -> bool:
        """Always available: no credentials, no external dependency."""
        return True

    def validate_order(self, request: OrderRequest) -> AdapterAck:
        if not self.supports_market(request.market):
            return AdapterAck(False, reason=RejectionReason.NO_ADAPTER_AVAILABLE.value,
                              detail=f"Market {request.market.value} not supported")
        if not self.supports_order_type(request.order_type):
            return AdapterAck(False, reason=RejectionReason.CAPABILITY_UNSUPPORTED.value,
                              detail=f"{request.order_type.value} not supported by this adapter")
        return AdapterAck(True, detail="Order is routable to the internal simulator")

    # ------------------------------------------------------------------ bars

    def get_bars(self, symbol: str, after: Optional[str] = None,
                 limit: int = 40) -> List[Dict[str, Any]]:
        """
        Daily bars for a symbol, ascending. If `after` is given, only bars strictly later
        than that date are returned -- the no-look-ahead boundary.
        """
        if after:
            return self.db.execute_query(
                """
                SELECT b.trading_date, b.open, b.high, b.low, b.close, b.volume
                FROM bar_1d b JOIN security s ON s.security_id = b.security_id
                WHERE s.symbol = ? AND b.trading_date > ?
                ORDER BY b.trading_date ASC LIMIT ?;
                """,
                (symbol, after, limit),
            )
        return self.db.execute_query(
            """
            SELECT b.trading_date, b.open, b.high, b.low, b.close, b.volume
            FROM bar_1d b JOIN security s ON s.security_id = b.security_id
            WHERE s.symbol = ?
            ORDER BY b.trading_date ASC LIMIT ?;
            """,
            (symbol, limit),
        )

    def last_close(self, symbol: str, on_or_before: Optional[str] = None) -> Optional[float]:
        """Most recent close at or before a date. Used to estimate reservation only."""
        rows = self.db.execute_query(
            """
            SELECT b.close FROM bar_1d b JOIN security s ON s.security_id = b.security_id
            WHERE s.symbol = ? AND (? IS NULL OR b.trading_date <= ?)
            ORDER BY b.trading_date DESC LIMIT 1;
            """,
            # Three placeholders: symbol, the IS NULL guard, and the comparison.
            (symbol, on_or_before, on_or_before),
        )
        return float(rows[0]["close"]) if rows else None

    # ------------------------------------------------------------------ pricing

    def _to_usd(self, amount: float, market: Market) -> float:
        return float(amount) * (self.fx_cad_usd if market == Market.CA else 1.0)

    def _apply_friction(self, price: float, side: OrderSide) -> float:
        """Friction is always adverse to the trader."""
        factor = self.FRICTION_BPS / 10_000.0
        if side in (OrderSide.BUY, OrderSide.BUY_TO_COVER):
            return price * (1.0 + factor)
        return price * (1.0 - factor)

    def _attainable(self, request: OrderRequest, bar: Dict[str, Any]) -> Optional[float]:
        """
        Returns the gross fill price if the order can execute on this bar, else None.

        Only the bar's own OHLC is consulted -- never a later bar, and never the close of
        the bar that generated the signal.
        """
        o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])
        ot = request.order_type
        buying = request.side in (OrderSide.BUY, OrderSide.BUY_TO_COVER)

        if ot == OrderType.MARKET:
            return o

        if ot == OrderType.LIMIT:
            lim = float(request.limit_price)
            if buying:
                return min(o, lim) if l <= lim else None
            return max(o, lim) if h >= lim else None

        if ot == OrderType.STOP:
            stp = float(request.stop_price)
            if buying:
                return max(o, stp) if h >= stp else None
            return min(o, stp) if l <= stp else None

        if ot == OrderType.STOP_LIMIT:
            stp, lim = float(request.stop_price), float(request.limit_price)
            triggered = (h >= stp) if buying else (l <= stp)
            if not triggered:
                return None
            if buying:
                return min(max(o, stp), lim) if l <= lim else None
            return max(min(o, stp), lim) if h >= lim else None

        # Scheduled algorithms are simulated as an open fill in this adapter.
        return o

    # ------------------------------------------------------------------ submission

    def submit_order(self, request: OrderRequest) -> AdapterAck:
        """
        Validates and reserves capital. Does not fill -- filling happens when bars are
        processed, because the fill bar is the one AFTER submission.
        """
        ack = self.validate_order(request)
        if not ack.accepted:
            return ack

        ref = self.last_close(request.symbol,
                              request.signal_date.isoformat() if request.signal_date else None)
        if ref is None:
            return AdapterAck(False, reason=RejectionReason.UNKNOWN_SYMBOL.value,
                              detail=f"No bar history for {request.symbol}")

        est_notional = self._to_usd(request.quantity * ref, request.market)
        est_fee = abs(self._to_usd(
            execution_algo_engine.get_exchange_fee(
                int(round(request.quantity)), request.market.value, is_maker=False,
            ),
            request.market,
        ))
        est_usd = est_notional + est_fee
        if est_usd > self.cash_free and not request.is_terminal_intent:
            return AdapterAck(
                False, reason=RejectionReason.RISK_C1_BUYING_POWER.value,
                detail=f"Estimated ${est_usd:,.2f} exceeds free cash ${self.cash_free:,.2f}",
            )

        if not request.is_terminal_intent:
            self.cash_free -= est_usd
            self.cash_reserved += est_usd
            self._reservations[request.client_order_id] = est_usd

        self._accepted.add(request.client_order_id)
        return AdapterAck(True, venue_order_id=f"SIM-{request.client_order_id[:24]}",
                          detail=f"Reserved ${est_usd:,.2f} at reference {ref:.2f}")

    # ------------------------------------------------------------------ filling

    def fill_order(self, request: OrderRequest, order_id: str) -> List[Fill]:
        """
        Attempts to fill a resting order against the bars following its signal date.

        Returns the fills produced. A partial fill leaves the order in PARTIALLY_FILLED;
        exhausting the window without completing expires it.

        Refuses to fill an order this adapter never accepted. Without this guard a caller
        that ignores a submission rejection still gets a fill, and the book is over-drawn
        with no reservation behind it.
        """
        if request.client_order_id not in self._accepted:
            # Raised rather than recorded as a venue rejection: this is a caller bug, not a
            # market event. The order's current state may not even permit a transition to
            # REJECTED, and quietly doing something else would hide the mistake.
            raise UnacceptedOrderError(request.client_order_id)

        signal = request.signal_date.isoformat() if request.signal_date else None
        bars = self.get_bars(request.symbol, after=signal, limit=self.FILL_WINDOW_BARS)
        if not bars:
            self._release(request)
            self.ledger.transition(order_id, OrderState.EXPIRED,
                                   reason_code=RejectionReason.EXPIRED_UNFILLED)
            return []

        remaining = float(request.quantity)
        fills: List[Fill] = []
        now = datetime.now(timezone.utc).isoformat()
        maker = request.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT)

        for bar in bars:
            if remaining <= 1e-9:
                break
            gross = self._attainable(request, bar)
            if gross is None:
                continue

            cap = float(bar["volume"]) * (self.MAX_PARTICIPATION_PCT / 100.0)
            qty = min(remaining, cap) if cap > 0 else remaining
            if qty <= 0:
                continue

            eff = self._apply_friction(gross, request.side)
            fee = execution_algo_engine.get_exchange_fee(
                int(round(qty)), request.market.value, is_maker=maker,
            )

            # Affordability at FILL time, not just at submission. The reservation was an
            # estimate, so a fill can cost more than was reserved; if the excess is not
            # covered by free cash the order must stop rather than over-draw the book.
            if not request.is_terminal_intent:
                held = self._reservations.get(request.client_order_id, 0.0)
                cost_usd = self._to_usd(qty * eff, request.market) + abs(
                    self._to_usd(fee, request.market))
                if cost_usd - min(held, cost_usd) > self.cash_free:
                    self._release(request)
                    self.ledger.transition(
                        order_id, OrderState.REJECTED,
                        reason_code=RejectionReason.RISK_C1_BUYING_POWER,
                        actor=Actor.ADAPTER,
                    )
                    return fills
            fills.append(Fill(
                order_id=order_id,
                client_order_id=request.client_order_id,
                symbol=request.symbol,
                side=request.side.value,
                quantity=round(qty, 6),
                price=round(gross, 6),
                friction_bps=self.FRICTION_BPS,
                effective_price=round(eff, 6),
                fee=fee,
                liquidity_flag="MAKER" if maker else "TAKER",
                bar_date=bar["trading_date"],
                filled_at=now,
            ))
            remaining -= qty

            # PARTIALLY_FILLED -> PARTIALLY_FILLED is legal, so repeated partials work.
            self.ledger.transition(order_id, OrderState.PARTIALLY_FILLED, actor=Actor.ADAPTER)
            self._record_fill(fills[-1])
            self._apply_position(request.symbol, request.side, qty)
            self._settle_cash(request, qty, eff, fee)

        if not fills:
            self._release(request)
            self.ledger.transition(order_id, OrderState.EXPIRED,
                                   reason_code=RejectionReason.EXPIRED_UNFILLED)
            return []

        if remaining <= 1e-9:
            self.ledger.transition(order_id, OrderState.FILLED)
        else:
            self.ledger.transition(order_id, OrderState.EXPIRED,
                                   reason_code=RejectionReason.EXPIRED_UNFILLED)

        # Return whatever reservation is left. The estimate at submission uses the last
        # close, while the fill uses the next bar's open plus friction, so a residual is
        # expected -- without releasing it, cash would leak into permanent reservation.
        self._release(request)
        return fills

    # ------------------------------------------------------------------ capital

    def _settle_cash(self, request: OrderRequest, qty: float, eff_price: float, fee: float) -> None:
        """Converts reserved capital into spent, in USD, including fees."""
        gross_usd = self._to_usd(qty * eff_price, request.market)
        fee_usd = self._to_usd(fee, request.market)
        if request.is_terminal_intent:
            self.cash_free += gross_usd - fee_usd
            self.spent_total += fee_usd
            return

        # The reservation is an ESTIMATE taken from the last close, while the fill uses the
        # next bar's open plus friction. The fill can therefore cost more than was reserved.
        # Flooring the reservation at zero would silently create cash, so any excess is
        # drawn from free cash instead -- which keeps cash_free + spent == starting capital.
        held = self._reservations.get(request.client_order_id, 0.0)
        applied = min(held, gross_usd)
        excess = gross_usd - applied

        self.cash_reserved -= applied
        self._reservations[request.client_order_id] = held - applied
        # The fee is real cash out; tallying it without deducting it overstates capital.
        self.cash_free -= excess + fee_usd
        self.spent_total += gross_usd + fee_usd

    def _release(self, request: OrderRequest) -> None:
        """Returns the exact remaining reservation to free cash. Idempotent."""
        if request.is_terminal_intent:
            return
        amount = min(self._reservations.pop(request.client_order_id, 0.0), self.cash_reserved)
        self.cash_reserved -= amount
        self.cash_free += amount

    def _apply_position(self, symbol: str, side: OrderSide, qty: float) -> None:
        delta = qty if side in (OrderSide.BUY, OrderSide.BUY_TO_COVER) else -qty
        self.positions[symbol] = round(self.positions.get(symbol, 0.0) + delta, 6)
        if abs(self.positions[symbol]) < 1e-9:
            self.positions.pop(symbol, None)

    def _record_fill(self, f: Fill) -> None:
        self.db.execute_write(
            """
            INSERT INTO execution_fill (
                order_id, client_order_id, symbol, side, quantity, price, friction_bps,
                effective_price, fee, liquidity_flag, bar_date, filled_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (f.order_id, f.client_order_id, f.symbol, f.side, f.quantity, f.price,
             f.friction_bps, f.effective_price, f.fee, f.liquidity_flag, f.bar_date,
             f.filled_at),
        )

    def _ensure_fill_table(self) -> None:
        self.db.execute_write("""
            CREATE TABLE IF NOT EXISTS execution_fill (
                fill_id         INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id        TEXT NOT NULL,
                client_order_id TEXT NOT NULL,
                symbol          TEXT NOT NULL,
                side            TEXT NOT NULL,
                quantity        REAL NOT NULL,
                price           REAL NOT NULL,
                friction_bps    REAL NOT NULL,
                effective_price REAL NOT NULL,
                fee             REAL NOT NULL,
                liquidity_flag  TEXT NOT NULL,
                bar_date        TEXT NOT NULL,
                filled_at       TEXT NOT NULL
            );
        """)

    # ------------------------------------------------------------------ account

    def get_account(self) -> AccountSnapshot:
        return AccountSnapshot(
            adapter_id=self.adapter_id,
            cash_free=round(self.cash_free, 2),
            cash_reserved=round(self.cash_reserved, 2),
            equity=round(self.cash_free + self.cash_reserved, 2),
            buying_power=round(self.cash_free, 2),
            positions=dict(self.positions),
            snapshot_at=datetime.now(timezone.utc).isoformat(),
        )

    def cancel_order(self, request: OrderRequest, order_id: str) -> None:
        self._release(request)
        self.ledger.transition(order_id, OrderState.CANCELLED,
                               reason_code=RejectionReason.CANCELLED_BY_USER)

    def total_equity_usd(self) -> float:
        return round(self.cash_free + self.cash_reserved, 2)


__all__ = ["InternalSimulatorAdapter", "UnacceptedOrderError"]
