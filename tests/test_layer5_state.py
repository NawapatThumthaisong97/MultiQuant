import pytest

from src.core.models import (
    FillEvent,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioState,
    Position,
    TickData,
)
from src.layer5_state.redis_cache import RedisStateCache
from src.layer5_state.sqlite_store import SQLiteStore
from src.layer5_state.state_manager import StateManager


@pytest.fixture
def temp_db_path(tmp_path):
    return str(tmp_path / "test_state.db")


@pytest.mark.asyncio
async def test_sqlite_store_crud(temp_db_path):
    store = SQLiteStore(db_path=temp_db_path)
    await store.connect()

    # 1. Save initial order
    order = OrderRequest(
        order_id="ord_test_101",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.5,
        price=62000.0,
        stop_price=60000.0,
    )
    await store.save_order(order, OrderStatus.ACCEPTED)

    loaded_order = await store.get_order("ord_test_101")
    assert loaded_order is not None
    assert loaded_order["symbol"] == "BTCUSDT"
    assert loaded_order["status"] == "ACCEPTED"
    assert loaded_order["quantity"] == 0.5

    # 2. Update order status to FILLED
    await store.save_order(order, OrderStatus.FILLED)
    updated_order = await store.get_order("ord_test_101")
    assert updated_order is not None
    assert updated_order["status"] == "FILLED"

    # 3. Save execution fill
    fill = FillEvent(
        order_id="ord_test_101",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        filled_quantity=0.5,
        filled_price=62000.0,
        fee=15.5,
        slippage=0.0,
    )
    await store.save_fill(fill)

    fills = await store.get_fills_by_order("ord_test_101")
    assert len(fills) == 1
    assert fills[0]["filled_price"] == 62000.0
    assert fills[0]["fee"] == 15.5

    # 4. Save portfolio snapshot
    portfolio = PortfolioState(
        total_equity=105000.0,
        cash=74000.0,
        peak_equity=105000.0,
        positions={
            "BTCUSDT": Position(
                symbol="BTCUSDT", quantity=0.5, entry_price=62000.0, current_price=62000.0
            )
        },
    )
    await store.save_portfolio_snapshot(portfolio)

    await store.disconnect()


@pytest.mark.asyncio
async def test_redis_cache_with_fallback():
    # Use 127.0.0.1 to avoid Windows IPv6 resolution latency
    cache = RedisStateCache(host="127.0.0.1", port=6379)
    await cache.connect()

    # 1. Tick caching
    tick = TickData(
        symbol="SOLUSDT",
        bid_price=145.20,
        ask_price=145.25,
        last_price=145.22,
        volume=12.0,
    )
    await cache.set_latest_tick(tick)
    cached_tick = await cache.get_latest_tick("SOLUSDT")
    assert cached_tick is not None
    assert cached_tick.symbol == "SOLUSDT"
    assert cached_tick.last_price == 145.22

    # 2. Portfolio state caching
    portfolio = PortfolioState(
        total_equity=98000.0,
        cash=98000.0,
        initial_capital=100000.0,
        peak_equity=100000.0,
    )
    await cache.set_portfolio_state(portfolio)
    cached_portfolio = await cache.get_portfolio_state()
    assert cached_portfolio is not None
    assert cached_portfolio.total_equity == 98000.0

    await cache.disconnect()


@pytest.mark.asyncio
async def test_state_manager_integration(temp_db_path):
    mgr = StateManager(db_path=temp_db_path)
    await mgr.initialize()

    order = OrderRequest(
        order_id="ord_sync_1",
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=2.0,
        price=3200.0,
    )

    # 1. Record pending order
    await mgr.record_order(order, OrderStatus.ACCEPTED)

    # 2. Record fill and update order to FILLED
    fill = FillEvent(
        order_id="ord_sync_1",
        symbol="ETHUSDT",
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        filled_quantity=2.0,
        filled_price=3201.0,
        fee=3.2,
    )
    await mgr.record_fill(fill)
    await mgr.record_order(order, OrderStatus.FILLED)

    # Verify persistence in SQLite
    persisted_order = await mgr.store.get_order("ord_sync_1")
    assert persisted_order is not None
    assert persisted_order["status"] == "FILLED"

    fills = await mgr.store.get_fills_by_order("ord_sync_1")
    assert len(fills) == 1
    assert fills[0]["filled_price"] == 3201.0

    await mgr.shutdown()
