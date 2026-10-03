"""
Phase 34.3 -- Broker Adapter Interface
US + Canada Market Intelligence Platform

The seam at which an execution venue attaches. Defined here rather than in Phase 34.4 as
originally planned, because the internal simulator has to conform to something.

Capabilities are DECLARED, not inferred. The router refuses to send an order type an
adapter does not declare, reporting CAPABILITY_UNSUPPORTED instead of silently
substituting a market order. That matters: IBKR's paper environment documents no VWAP,
Auction, RFQ or Pegged-to-Market support, while this platform's execution_algo engine
already simulates VWAP. Without declaration the platform would appear to support an order
type it cannot route.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.execution.order_model import Market, OrderRequest, OrderType


@dataclass(frozen=True)
class AdapterCapabilities:
    """What an adapter can actually do. Every flag defaults to False."""
    market: bool = False
    limit: bool = False
    stop: bool = False
    stop_limit: bool = False
    bracket: bool = False
    oco: bool = False
    twap: bool = False
    vwap: bool = False
    pov: bool = False
    fractional: bool = False
    short: bool = False
    streaming: bool = False

    def supports(self, order_type: OrderType) -> bool:
        return bool(getattr(self, order_type.value.lower(), False))


@dataclass
class AdapterAck:
    """An adapter's response to a submission."""
    accepted: bool
    venue_order_id: Optional[str] = None
    reason: Optional[str] = None
    detail: str = ""


@dataclass
class Fill:
    """One execution against a resting order."""
    order_id: str
    client_order_id: str
    symbol: str
    side: str
    quantity: float
    price: float                 # gross, before friction
    friction_bps: float
    effective_price: float       # net of friction, the price actually paid or received
    fee: float                   # from execution_algo.get_exchange_fee
    liquidity_flag: str          # MAKER or TAKER
    bar_date: str                # the bar that produced this fill
    filled_at: str


@dataclass
class AccountSnapshot:
    adapter_id: str
    cash_free: float
    cash_reserved: float
    equity: float
    buying_power: float
    positions: Dict[str, float] = field(default_factory=dict)
    snapshot_at: str = ""


class BrokerAdapter(ABC):
    """
    Base class for execution venues.

    `is_external` is the flag the Canadian firewall reads. Any adapter that routes to a
    real broker must set it True, which makes Canadian symbols unreachable through it.
    The internal simulator sets it False and is therefore permitted for both markets.
    """

    adapter_id: str = "UNSPECIFIED"
    markets: frozenset = frozenset()
    capabilities: AdapterCapabilities = AdapterCapabilities()
    is_external: bool = True          # fail closed: external unless explicitly internal
    license_cleared: bool = False     # a venue may not be used without this

    @abstractmethod
    def is_configured(self) -> bool:
        """True if the adapter has what it needs to run (credentials, data, etc.)."""

    @abstractmethod
    def validate_order(self, request: OrderRequest) -> AdapterAck:
        """Cheap structural check: is this order routable here at all?"""

    def supports_market(self, market: Market) -> bool:
        return market in self.markets

    def supports_order_type(self, order_type: OrderType) -> bool:
        return self.capabilities.supports(order_type)


__all__ = [
    "AdapterCapabilities",
    "AdapterAck",
    "Fill",
    "AccountSnapshot",
    "BrokerAdapter",
]
