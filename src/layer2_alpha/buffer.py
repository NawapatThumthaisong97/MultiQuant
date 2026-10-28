"""
Rolling Candle Buffer for Real-time Alpha Computation.
Maintains bounded in-memory OHLCV history with fast conversion to Polars DataFrames.
"""

from __future__ import annotations

from collections import deque

import polars as pl

from src.core.models import BarData


class RollingBarBuffer:
    """Maintains a fixed-size deque of recent BarData per symbol for low-latency Polars transforms."""

    def __init__(self, maxlen: int = 500) -> None:
        self.maxlen = maxlen
        self._buffers: dict[str, deque[BarData]] = {}

    def add_bar(self, bar: BarData) -> None:
        """Append a completed bar to the symbol's buffer."""
        symbol = bar.symbol.upper()
        if symbol not in self._buffers:
            self._buffers[symbol] = deque(maxlen=self.maxlen)
        self._buffers[symbol].append(bar)

    def get_bars(self, symbol: str) -> list[BarData]:
        """Retrieve raw BarData list for symbol."""
        symbol = symbol.upper()
        return list(self._buffers.get(symbol, []))

    def count(self, symbol: str) -> int:
        """Return number of bars currently buffered for symbol."""
        return len(self._buffers.get(symbol.upper(), []))

    def to_polars(self, symbol: str) -> pl.DataFrame | None:
        """
        Convert buffered bars into a strongly typed Polars DataFrame.
        Returns None if buffer is empty.
        """
        bars = self.get_bars(symbol)
        if not bars:
            return None

        data = {
            "timestamp": [b.timestamp for b in bars],
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
        }

        # Polars schema enforcement
        return pl.DataFrame(
            data,
            schema={
                "timestamp": pl.Datetime("us", "UTC"),
                "open": pl.Float64,
                "high": pl.Float64,
                "low": pl.Float64,
                "close": pl.Float64,
                "volume": pl.Float64,
            },
        ).sort("timestamp")

    def clear(self, symbol: str | None = None) -> None:
        """Clear buffer for a specific symbol or all symbols."""
        if symbol:
            self._buffers.pop(symbol.upper(), None)
        else:
            self._buffers.clear()
