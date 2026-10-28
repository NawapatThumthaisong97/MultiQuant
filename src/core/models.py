"""
MultiQuant Core Models & Event Definitions.
Institutional-grade, strongly typed, immutable data structures.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


class SignalDirection(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class ImmutableBaseModel(BaseModel):
    """Base model enforcing immutability and strict type safety."""

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
        populate_by_name=True,
    )


class TickData(ImmutableBaseModel):
    """Normalized real-time market tick."""

    symbol: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    bid_price: float
    ask_price: float
    last_price: float
    volume: float = 0.0

    @property
    def mid_price(self) -> float:
        return (self.bid_price + self.ask_price) / 2.0

    @property
    def spread(self) -> float:
        return self.ask_price - self.bid_price


class BarData(ImmutableBaseModel):
    """OHLCV Candle Bar representation."""

    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    timeframe: str = "1m"


class AlphaSignal(ImmutableBaseModel):
    """Alpha signal produced by Layer 2 strategy engine."""

    symbol: str
    direction: SignalDirection
    strength: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score 0.0 to 1.0")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    strategy_name: str = "generic_alpha"
    metadata: dict[str, Any] = Field(default_factory=dict)


class OrderRequest(ImmutableBaseModel):
    """Request to place an order created by Risk/OMS."""

    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float = Field(gt=0.0)
    price: float | None = None
    stop_price: float | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    client_tag: str | None = None


class FillEvent(ImmutableBaseModel):
    """Execution receipt returned from OMS / Exchange."""

    order_id: str
    symbol: str
    side: OrderSide
    status: OrderStatus
    filled_quantity: float
    filled_price: float
    fee: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    slippage: float = 0.0
    message: str | None = None


class Position(BaseModel):
    """Tracks active position and performance."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    symbol: str
    quantity: float = 0.0
    entry_price: float = 0.0
    current_price: float = 0.0
    realized_pnl: float = 0.0

    @property
    def is_open(self) -> bool:
        return abs(self.quantity) > 1e-8

    @property
    def side(self) -> SignalDirection:
        if self.quantity > 1e-8:
            return SignalDirection.LONG
        elif self.quantity < -1e-8:
            return SignalDirection.SHORT
        return SignalDirection.FLAT

    @property
    def unrealized_pnl(self) -> float:
        if not self.is_open:
            return 0.0
        return (self.current_price - self.entry_price) * self.quantity

    @property
    def notional_value(self) -> float:
        return abs(self.quantity * self.current_price)


class PortfolioState(BaseModel):
    """Account balance and risk state snapshot."""

    total_equity: float
    cash: float
    currency: str = "USD"
    positions: dict[str, Position] = Field(default_factory=dict)
    initial_capital: float = 100_000.0
    peak_equity: float = 100_000.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        dd = (self.peak_equity - self.total_equity) / self.peak_equity
        return max(0.0, dd)
