"""
Abstract Base Classes and Interfaces for Market Connectivity.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from enum import StrEnum

from src.core.models import BarData, TickData


class ConnectionState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    FAILED = "FAILED"


class BaseMarketFeed(ABC):
    """Interface contract that all exchange connectors must satisfy."""

    def __init__(
        self,
        symbols: list[str],
        on_tick: Callable[[TickData], Coroutine[None, None, None]] | None = None,
        on_bar: Callable[[BarData], Coroutine[None, None, None]] | None = None,
    ) -> None:
        self.symbols = [s.upper() for s in symbols]
        self.on_tick = on_tick
        self.on_bar = on_bar
        self.state = ConnectionState.DISCONNECTED

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the market data provider."""
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect and release resources cleanly."""
        pass

    @abstractmethod
    async def subscribe(self, symbols: list[str]) -> None:
        """Subscribe to market streams for specified symbols."""
        pass
