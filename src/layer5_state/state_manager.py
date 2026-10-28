"""
Unified State Manager.
Coordinates high-speed in-memory caching (Redis) and durable ACID persistence (SQLite WAL).
"""

from __future__ import annotations

import logging

from src.core.models import (
    FillEvent,
    OrderRequest,
    OrderStatus,
    PortfolioState,
    TickData,
)
from src.layer5_state.redis_cache import RedisStateCache
from src.layer5_state.sqlite_store import SQLiteStore

logger = logging.getLogger("multiquant.state.manager")


class StateManager:
    """
    Central Coordinator for System State.
    Ensures that volatile runtime data is cached for low-latency queries,
    while critical audit trails (orders, fills, portfolio history) are durably committed to SQLite.
    """

    def __init__(
        self,
        db_path: str = "multiquant_state.db",
        redis_host: str = "127.0.0.1",
        redis_port: int = 6379,
        redis_password: str | None = None,
    ) -> None:
        self.store = SQLiteStore(db_path=db_path)
        self.cache = RedisStateCache(
            host=redis_host,
            port=redis_port,
            password=redis_password,
        )

    async def initialize(self) -> None:
        """Start database and cache connections."""
        await self.store.connect()
        await self.cache.connect()
        logger.info("StateManager initialized successfully.")

    async def shutdown(self) -> None:
        """Gracefully close all connections."""
        await self.cache.disconnect()
        await self.store.disconnect()
        logger.info("StateManager shut down cleanly.")

    async def record_order(self, order: OrderRequest, status: OrderStatus) -> None:
        """Record an order to both fast cache and persistent store."""
        # Update fast cache
        if status in (OrderStatus.PENDING, OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED):
            await self.cache.set_active_order(order)
        else:
            await self.cache.delete_active_order(order.order_id)

        # Durably record in SQLite WAL
        await self.store.save_order(order, status)

    async def record_fill(self, fill: FillEvent) -> None:
        """Persist fill receipt to durable storage."""
        await self.store.save_fill(fill)

    async def record_portfolio_snapshot(self, portfolio: PortfolioState) -> None:
        """Cache current portfolio state and record historical snapshot."""
        await self.cache.set_portfolio_state(portfolio)
        await self.store.save_portfolio_snapshot(portfolio)

    async def cache_market_tick(self, tick: TickData) -> None:
        """Store latest real-time tick in fast cache."""
        await self.cache.set_latest_tick(tick)

    async def get_latest_tick(self, symbol: str) -> TickData | None:
        """Retrieve latest market tick from fast cache."""
        return await self.cache.get_latest_tick(symbol)

    async def get_cached_portfolio(self) -> PortfolioState | None:
        """Retrieve current portfolio state from cache."""
        return await self.cache.get_portfolio_state()
