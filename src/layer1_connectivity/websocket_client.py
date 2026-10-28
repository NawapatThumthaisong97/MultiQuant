"""
High-Availability WebSocket Market Client.
Implements auto-reconnect, exponential backoff with jitter, heartbeat ping/pong,
and streaming normalization into internal event models.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Callable, Coroutine

import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed, WebSocketException

from src.core.models import BarData, TickData
from src.layer1_connectivity.base import BaseMarketFeed, ConnectionState
from src.layer1_connectivity.feed_handler import MarketDataParser

logger = logging.getLogger("multiquant.websocket")


class BinanceWebSocketFeed(BaseMarketFeed):
    """
    Production-grade Binance WebSocket streaming connector.
    Supports bookTicker, trade, and kline streams with resilient reconnect.
    """

    def __init__(
        self,
        symbols: list[str],
        base_ws_url: str = "wss://stream.binance.com:9443/ws",
        stream_type: str = "bookTicker",  # 'bookTicker', 'trade', 'kline_1m'
        on_tick: Callable[[TickData], Coroutine[None, None, None]] | None = None,
        on_bar: Callable[[BarData], Coroutine[None, None, None]] | None = None,
        max_reconnect_retries: int = 15,
        base_backoff_sec: float = 1.0,
        max_backoff_sec: float = 30.0,
    ) -> None:
        super().__init__(symbols=symbols, on_tick=on_tick, on_bar=on_bar)
        self.base_ws_url = base_ws_url
        self.stream_type = stream_type
        self.max_reconnect_retries = max_reconnect_retries
        self.base_backoff_sec = base_backoff_sec
        self.max_backoff_sec = max_backoff_sec

        self._ws: ClientConnection | None = None
        self._running: bool = False
        self._listen_task: asyncio.Task[None] | None = None
        self._reconnect_count: int = 0

    def _build_stream_names(self) -> list[str]:
        """Convert symbols into Binance stream format (lowercase)."""
        streams = []
        for sym in self.symbols:
            clean_sym = sym.lower().replace("-", "").replace("_", "").replace("/", "")
            if self.stream_type.startswith("kline"):
                streams.append(f"{clean_sym}@{self.stream_type}")
            else:
                streams.append(f"{clean_sym}@{self.stream_type}")
        return streams

    def _build_stream_url(self) -> str:
        """Construct multi-stream WebSocket URL or single stream URL."""
        streams = self._build_stream_names()
        if len(streams) == 1:
            return f"{self.base_ws_url}/{streams[0]}"
        stream_param = "/".join(streams)
        return f"wss://stream.binance.com:9443/stream?streams={stream_param}"

    async def connect(self) -> None:
        """Start connection lifecycle loop in background."""
        if self._running:
            logger.warning("WebSocket feed is already running.")
            return

        self._running = True
        self._listen_task = asyncio.create_task(self._connection_loop())
        logger.info("Started WebSocket connection supervisor for symbols: %s", self.symbols)

    async def disconnect(self) -> None:
        """Gracefully disconnect and terminate background tasks."""
        self._running = False
        self.state = ConnectionState.DISCONNECTED

        if self._ws is not None:
            await self._ws.close()
            self._ws = None

        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None

        logger.info("WebSocket feed disconnected cleanly.")

    async def subscribe(self, symbols: list[str]) -> None:
        """Add new symbols dynamically and reconnect if active."""
        for s in symbols:
            s_up = s.upper()
            if s_up not in self.symbols:
                self.symbols.append(s_up)

        if self._running and self._ws is not None:
            # For multi-stream URL, reconnection applies new query params
            logger.info("Resubscribing with updated symbols: %s", self.symbols)
            await self._ws.close()

    async def _connection_loop(self) -> None:
        """Supervisor loop managing connection, message dispatch, and backoff."""
        while self._running:
            url = self._build_stream_url()
            self.state = ConnectionState.CONNECTING
            logger.info("Connecting to WebSocket: %s (attempt #%d)", url, self._reconnect_count + 1)

            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self.state = ConnectionState.CONNECTED
                    self._reconnect_count = 0  # Reset on successful connection
                    logger.info("WebSocket connected successfully.")

                    async for message in ws:
                        if not self._running:
                            break
                        await self._handle_message(message)

            except (ConnectionClosed, WebSocketException, OSError) as e:
                logger.warning("WebSocket connection dropped: %s", e)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Unexpected error in WebSocket loop: %s", e, exc_info=True)

            if not self._running:
                break

            self.state = ConnectionState.RECONNECTING
            self._reconnect_count += 1
            if self._reconnect_count > self.max_reconnect_retries:
                self.state = ConnectionState.FAILED
                logger.critical(
                    "Maximum reconnect attempts (%d) reached. Feed failed.",
                    self.max_reconnect_retries,
                )
                self._running = False
                break

            # Exponential backoff with Full Jitter: Sleep = Uniform(0, min(max_backoff, base * 2^attempt))
            calculated_backoff = min(
                self.max_backoff_sec, self.base_backoff_sec * (2 ** (self._reconnect_count - 1))
            )
            jittered_sleep = random.uniform(0.5, calculated_backoff)
            logger.info(
                "Reconnecting in %.2f seconds (attempt %d/%d)...",
                jittered_sleep,
                self._reconnect_count,
                self.max_reconnect_retries,
            )
            await asyncio.sleep(jittered_sleep)

    async def _handle_message(self, raw_message: str | bytes) -> None:
        """Parse incoming raw message and dispatch to registered callbacks."""
        try:
            if isinstance(raw_message, bytes):
                raw_message = raw_message.decode("utf-8")
            data = json.loads(raw_message)

            # Binance multi-stream messages are wrapped in {"stream": "...", "data": {...}}
            payload = data.get("data", data)

            event_type = payload.get("e")
            if event_type == "trade":
                tick = MarketDataParser.parse_binance_trade(payload)
                if tick and self.on_tick:
                    await self.on_tick(tick)
            elif event_type == "kline":
                bar = MarketDataParser.parse_binance_kline(payload)
                if bar and self.on_bar:
                    await self.on_bar(bar)
            elif "b" in payload and "a" in payload and "s" in payload:
                # bookTicker format
                tick = MarketDataParser.parse_binance_book_ticker(payload)
                if tick and self.on_tick:
                    await self.on_tick(tick)

        except Exception as e:
            logger.error("Error processing raw WebSocket message: %s", e, exc_info=True)
