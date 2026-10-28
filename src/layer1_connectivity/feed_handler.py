"""
Market Data Normalizer and Parser.
Parses raw exchange payload formats (e.g. Binance Trade/Kline/Ticker) into immutable Core models.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from src.core.models import BarData, TickData

logger = logging.getLogger("multiquant.feed_handler")


class MarketDataParser:
    """Parses raw exchange JSON payloads into standardized TickData / BarData."""

    @staticmethod
    def parse_binance_book_ticker(payload: dict[str, Any]) -> TickData | None:
        """
        Parses Binance `<symbol>@bookTicker` payload:
        {
          "u":400900217,     // order book updateId
          "s":"BNBUSDT",     // symbol
          "b":"25.35190000", // best bid price
          "B":"31.21000000", // best bid qty
          "a":"25.36520000", // best ask price
          "A":"40.66000000"  // best ask qty
        }
        """
        try:
            if "s" not in payload or "b" not in payload or "a" not in payload:
                return None
            symbol = str(payload["s"]).upper()
            if not symbol:
                return None
            bid_price = float(payload["b"])
            ask_price = float(payload["a"])
            mid_price = (bid_price + ask_price) / 2.0

            return TickData(
                symbol=symbol,
                bid_price=bid_price,
                ask_price=ask_price,
                last_price=mid_price,
                volume=0.0,
                timestamp=datetime.now(UTC),
            )
        except (ValueError, KeyError) as e:
            logger.error("Failed to parse Binance bookTicker: %s (payload: %s)", e, payload)
            return None

    @staticmethod
    def parse_binance_trade(payload: dict[str, Any]) -> TickData | None:
        """
        Parses Binance `<symbol>@trade` payload:
        {
          "e": "trade",
          "E": 123456789,   // Event time
          "s": "BNBUSDT",   // Symbol
          "t": 12345,       // Trade ID
          "p": "0.001",     // Price
          "q": "100",       // Quantity
          "T": 123456785,   // Trade time
          "m": true         // Is buyer market maker?
        }
        """
        try:
            if "s" not in payload or "p" not in payload:
                return None
            symbol = str(payload["s"]).upper()
            if not symbol:
                return None
            price = float(payload["p"])
            qty = float(payload.get("q", 0.0))
            trade_time_ms = payload.get("T", payload.get("E", 0))
            dt = datetime.fromtimestamp(trade_time_ms / 1000.0, tz=UTC)

            # In trade stream, bid and ask aren't provided; last_price and volume are exact
            return TickData(
                symbol=symbol,
                bid_price=price,
                ask_price=price,
                last_price=price,
                volume=qty,
                timestamp=dt,
            )
        except (ValueError, KeyError) as e:
            logger.error("Failed to parse Binance trade: %s (payload: %s)", e, payload)
            return None

    @staticmethod
    def parse_binance_kline(payload: dict[str, Any]) -> BarData | None:
        """
        Parses Binance `<symbol>@kline_<interval>` payload:
        {
          "e": "kline",
          "s": "BTCUSDT",
          "k": {
            "t": 123400000, // Kline start time
            "T": 123460000, // Kline close time
            "s": "BTCUSDT", // Symbol
            "i": "1m",      // Interval
            "o": "0.0010",  // Open price
            "c": "0.0020",  // Close price
            "h": "0.0025",  // High price
            "l": "0.0015",  // Low price
            "v": "1000",    // Base asset volume
            "x": false      // Is this kline closed?
          }
        }
        """
        try:
            if "k" not in payload or not isinstance(payload["k"], dict):
                return None
            k = payload["k"]
            symbol = str(payload.get("s", k.get("s", ""))).upper()
            if not symbol or "o" not in k or "c" not in k or "h" not in k or "l" not in k:
                return None
            start_time_ms = k.get("t", 0)
            dt = datetime.fromtimestamp(start_time_ms / 1000.0, tz=UTC)

            return BarData(
                symbol=symbol,
                timestamp=dt,
                open=float(k["o"]),
                high=float(k["h"]),
                low=float(k["l"]),
                close=float(k["c"]),
                volume=float(k.get("v", 0.0)),
                timeframe=k.get("i", "1m"),
            )
        except (ValueError, KeyError) as e:
            logger.error("Failed to parse Binance kline: %s", e)
            return None
