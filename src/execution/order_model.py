"""
Phase 34.1 -- Normalized Order Model & Deterministic Order Identity
US + Canada Market Intelligence Platform

Represents an order honestly BEFORE it is executed: a normalized request, a bounded
vocabulary of order states and rejection reasons, and a deterministic client order
identifier so that the same signal always produces the same order identity.

Design notes
------------
`client_order_id` is derived, not assigned. Two identical intents from the same strategy
on the same signal date collapse to the same identifier, which makes duplicate submission
detectable at the database layer rather than by convention.
"""

from datetime import date, datetime, timezone
from enum import Enum
import hashlib
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, model_validator


# --------------------------------------------------------------------------------------
# Vocabularies
# --------------------------------------------------------------------------------------

class Market(str, Enum):
    """Tradeable market. Drives the Canadian external-routing firewall."""
    US = "US"
    CA = "CA"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    SELL_SHORT = "SELL_SHORT"
    BUY_TO_COVER = "BUY_TO_COVER"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"
    BRACKET = "BRACKET"
    OCO = "OCO"
    TWAP = "TWAP"
    VWAP = "VWAP"
    POV = "POV"


class TimeInForce(str, Enum):
    DAY = "DAY"
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"


class OrderState(str, Enum):
    """
    Order lifecycle states.

    Six are terminal. A terminal state is immutable: the state machine refuses every
    transition out of one, so an order can never be silently resurrected or re-filled
    after it has concluded.
    """
    # Non-terminal
    NEW = "NEW"
    RISK_APPROVED = "RISK_APPROVED"
    SUBMITTED = "SUBMITTED"
    ACKED = "ACKED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"

    # Terminal
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"              # rejected by the venue
    RISK_REJECTED = "RISK_REJECTED"    # rejected by the pre-trade governor
    EXPIRED = "EXPIRED"
    INVALID = "INVALID"                # malformed, never reached the governor


TERMINAL_STATES = frozenset({
    OrderState.FILLED,
    OrderState.CANCELLED,
    OrderState.REJECTED,
    OrderState.RISK_REJECTED,
    OrderState.EXPIRED,
    OrderState.INVALID,
})

NON_TERMINAL_STATES = frozenset(s for s in OrderState if s not in TERMINAL_STATES)


class RejectionReason(str, Enum):
    """
    Reason codes. Every terminal rejection carries one, so "why did this not trade?"
    is always answerable from the ledger alone.

    The RISK_* family is reserved for the Phase 34.2 pre-trade governor and its
    controls C1-C12.
    """
    # Identity / validation
    MALFORMED_REQUEST = "MALFORMED_REQUEST"
    DUPLICATE_CLIENT_ORDER_ID = "DUPLICATE_CLIENT_ORDER_ID"
    UNKNOWN_SYMBOL = "UNKNOWN_SYMBOL"
    QUANTITY_NOT_POSITIVE = "QUANTITY_NOT_POSITIVE"
    MISSING_LIMIT_PRICE = "MISSING_LIMIT_PRICE"
    MISSING_STOP_PRICE = "MISSING_STOP_PRICE"

    # Routing / venue capability
    NO_ADAPTER_AVAILABLE = "NO_ADAPTER_AVAILABLE"
    ADAPTER_NOT_CONFIGURED = "ADAPTER_NOT_CONFIGURED"
    CAPABILITY_UNSUPPORTED = "CAPABILITY_UNSUPPORTED"
    MARKET_CLOSED = "MARKET_CLOSED"

    # Regulatory firewall (Phase 34.4)
    CANADIAN_EXTERNAL_ROUTING_BLOCKED = "CANADIAN_EXTERNAL_ROUTING_BLOCKED"
    ADAPTER_LICENSE_NOT_CLEARED = "ADAPTER_LICENSE_NOT_CLEARED"

    # Venue rejections
    VENUE_REJECTED = "VENUE_REJECTED"
    INSUFFICIENT_LIQUIDITY = "INSUFFICIENT_LIQUIDITY"
    PRICE_OUTSIDE_LIMIT = "PRICE_OUTSIDE_LIMIT"

    # Lifecycle
    CANCELLED_BY_USER = "CANCELLED_BY_USER"
    CANCELLED_BY_SYSTEM = "CANCELLED_BY_SYSTEM"
    EXPIRED_UNFILLED = "EXPIRED_UNFILLED"
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"

    # Pre-trade governor (Phase 34.2) -- one per control C1..C12
    RISK_C1_BUYING_POWER = "RISK_C1_BUYING_POWER"
    RISK_C2_POSITION_WEIGHT = "RISK_C2_POSITION_WEIGHT"
    RISK_C3_COUNTRY_EXPOSURE = "RISK_C3_COUNTRY_EXPOSURE"
    RISK_C4_UNEXECUTED_VALUE = "RISK_C4_UNEXECUTED_VALUE"
    RISK_C5_OPEN_ORDER_COUNT = "RISK_C5_OPEN_ORDER_COUNT"
    RISK_C6_PER_ORDER_CEILING = "RISK_C6_PER_ORDER_CEILING"
    RISK_C7_SECTOR_CONCENTRATION = "RISK_C7_SECTOR_CONCENTRATION"
    RISK_C8_CORRELATION_TO_BOOK = "RISK_C8_CORRELATION_TO_BOOK"
    RISK_C9_BETA_EXPOSURE = "RISK_C9_BETA_EXPOSURE"
    RISK_C10_DAILY_GROSS_TRADED = "RISK_C10_DAILY_GROSS_TRADED"
    RISK_C11_RESTRICTED_OR_STALE = "RISK_C11_RESTRICTED_OR_STALE"
    RISK_C12_CA_EXTERNAL_FIREWALL = "RISK_C12_CA_EXTERNAL_FIREWALL"


class Actor(str, Enum):
    """Who caused a transition. Kept deliberately coarse -- no PII in the ledger."""
    SYSTEM = "SYSTEM"
    GOVERNOR = "GOVERNOR"
    ADAPTER = "ADAPTER"
    USER = "USER"
    KILL_SWITCH = "KILL_SWITCH"


# --------------------------------------------------------------------------------------
# Order request
# --------------------------------------------------------------------------------------

class OrderRequest(BaseModel):
    """
    A normalized order intent, adapter-agnostic.

    Nothing here is venue-specific. Adapters translate; they do not redefine.
    """
    client_order_id: str = Field(..., min_length=8, max_length=64)
    symbol: str = Field(..., min_length=1, max_length=12)
    market: Market
    side: OrderSide
    quantity: float = Field(..., gt=0)
    order_type: OrderType = OrderType.MARKET
    time_in_force: TimeInForce = TimeInForce.DAY
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    strategy_id: str = Field(default="UNSPECIFIED", max_length=64)
    risk_profile: str = Field(default="MODERATE", max_length=16)
    signal_date: Optional[date] = None
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    adapter_hint: Optional[str] = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def _validate_price_requirements(self) -> "OrderRequest":
        """Price-bearing order types must actually carry their price."""
        if self.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and self.limit_price is None:
            raise ValueError(f"{self.order_type.value} requires limit_price")
        if self.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and self.stop_price is None:
            raise ValueError(f"{self.order_type.value} requires stop_price")
        if self.limit_price is not None and self.limit_price <= 0:
            raise ValueError("limit_price must be positive")
        if self.stop_price is not None and self.stop_price <= 0:
            raise ValueError("stop_price must be positive")
        return self

    @model_validator(mode="after")
    def _normalize_symbol(self) -> "OrderRequest":
        self.symbol = self.symbol.upper().strip()
        return self

    @property
    def is_terminal_intent(self) -> bool:
        """Closing intents reduce exposure and are treated distinctly by the governor."""
        return self.side in (OrderSide.SELL, OrderSide.BUY_TO_COVER)

    def to_dict(self) -> Dict[str, Any]:
        d = self.model_dump()
        for k, v in list(d.items()):
            if isinstance(v, Enum):
                d[k] = v.value
            elif isinstance(v, (datetime, date)):
                d[k] = v.isoformat()
        return d


# --------------------------------------------------------------------------------------
# Deterministic order identity
# --------------------------------------------------------------------------------------

def build_client_order_id(
    symbol: str,
    side: str,
    quantity: float,
    order_type: str,
    strategy_id: str,
    signal_date: Optional[date] = None,
    nonce: int = 0,
) -> str:
    """
    Derives a stable client order identifier from the order's economic intent.

    Determinism is the point: the same strategy re-emitting the same signal on the same
    date produces the same id, so a duplicate submission collides on the UNIQUE index and
    is rejected rather than silently double-filled. `nonce` is the only escape hatch, for
    the rare legitimate case of intentionally re-entering the same position.

    The identifier is prefixed so it is self-describing in logs and contains no PII.
    """
    canonical = "|".join([
        symbol.upper().strip(),
        str(side).upper(),
        f"{float(quantity):.6f}",
        str(order_type).upper(),
        str(strategy_id),
        signal_date.isoformat() if signal_date else "NODATE",
        str(int(nonce)),
    ])
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
    return f"QI-{digest}"


def order_id_from_client(client_order_id: str) -> str:
    """Internal order id, derived from the client id so the mapping is 1:1 and stateless."""
    digest = hashlib.sha256(client_order_id.encode("utf-8")).hexdigest()[:32]
    return f"ORD-{digest}"


__all__ = [
    "Market",
    "OrderSide",
    "OrderType",
    "TimeInForce",
    "OrderState",
    "TERMINAL_STATES",
    "NON_TERMINAL_STATES",
    "RejectionReason",
    "Actor",
    "OrderRequest",
    "build_client_order_id",
    "order_id_from_client",
]
