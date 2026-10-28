"""
Fast In-Memory Cache with Redis & In-Memory Fallback.
Provides sub-millisecond retrieval of live market ticks, active orders, and portfolio state.
Automatically degrades gracefully to internal memory cache if Redis is unavailable.
"""

from __future__ import annotations

import logging

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from src.core.models import OrderRequest, PortfolioState, TickData

logger = logging.getLogger("multiquant.state.cache")


class RedisStateCache:
    """
    Sub-millisecond state cache backed by Redis with zero-dependency in-memory fallback.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 6379,
        password: str | None = None,
        db: int = 0,
    ) -> None:
        self.host = host
        self.port = port
        self.password = password
        self.db = db

        self._client: aioredis.Redis | None = None
        self._is_redis_alive: bool = False
        # In-memory dictionary fallback cache
        self._memory_cache: dict[str, str] = {}

    async def connect(self) -> None:
        """Attempt to connect to Redis; fall back to internal RAM cache if unreachable."""
        try:
            self._client = aioredis.Redis(
                host=self.host,
                port=self.port,
                password=self.password,
                db=self.db,
                socket_timeout=0.2,
                socket_connect_timeout=0.2,
                decode_responses=True,
            )
            # Ping to verify live connection
            await self._client.ping()
            self._is_redis_alive = True
            logger.info("Connected to Redis State Cache at %s:%d", self.host, self.port)
        except (RedisError, OSError) as e:
            self._is_redis_alive = False
            self._client = None
            logger.warning(
                "Redis unavailable (%s). Falling back to resilient In-Memory RAM Cache.",
                e,
            )

    async def disconnect(self) -> None:
        """Close cache connections."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._is_redis_alive = False
        logger.info("Closed State Cache connection.")

    @property
    def is_using_live_redis(self) -> bool:
        """Returns True if connected to live Redis server; False if in fallback mode."""
        return self._is_redis_alive

    async def set_latest_tick(self, tick: TickData) -> None:
        """Cache latest market tick."""
        key = f"tick:{tick.symbol.upper()}"
        data = tick.model_dump_json()
        await self._set(key, data)

    async def get_latest_tick(self, symbol: str) -> TickData | None:
        """Retrieve latest cached tick for symbol."""
        key = f"tick:{symbol.upper()}"
        raw = await self._get(key)
        if raw:
            try:
                return TickData.model_validate_json(raw)
            except Exception as e:
                logger.error("Failed to decode cached tick for %s: %s", symbol, e)
        return None

    async def set_active_order(self, order: OrderRequest) -> None:
        """Cache active order."""
        key = f"order:active:{order.order_id}"
        data = order.model_dump_json()
        await self._set(key, data)

    async def delete_active_order(self, order_id: str) -> None:
        """Remove active order from cache."""
        key = f"order:active:{order_id}"
        await self._delete(key)

    async def set_portfolio_state(self, portfolio: PortfolioState) -> None:
        """Cache latest portfolio balance snapshot."""
        key = "portfolio:latest"
        data = portfolio.model_dump_json()
        await self._set(key, data)

    async def get_portfolio_state(self) -> PortfolioState | None:
        """Retrieve latest portfolio state."""
        key = "portfolio:latest"
        raw = await self._get(key)
        if raw:
            try:
                return PortfolioState.model_validate_json(raw)
            except Exception as e:
                logger.error("Failed to decode cached portfolio: %s", e)
        return None

    # Internal routing between Redis and Memory Fallback
    async def _set(self, key: str, value: str) -> None:
        if self._is_redis_alive and self._client is not None:
            try:
                await self._client.set(key, value)
                return
            except RedisError as e:
                logger.warning("Redis write failed (%s); updating memory cache.", e)
                self._is_redis_alive = False
        self._memory_cache[key] = value

    async def _get(self, key: str) -> str | None:
        if self._is_redis_alive and self._client is not None:
            try:
                val = await self._client.get(key)
                if val is not None:
                    return str(val)
                return None
            except RedisError as e:
                logger.warning("Redis read failed (%s); reading from memory cache.", e)
                self._is_redis_alive = False
        return self._memory_cache.get(key)

    async def _delete(self, key: str) -> None:
        if self._is_redis_alive and self._client is not None:
            try:
                await self._client.delete(key)
                return
            except RedisError:
                self._is_redis_alive = False
        self._memory_cache.pop(key, None)
