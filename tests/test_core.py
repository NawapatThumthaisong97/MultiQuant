import asyncio

import pytest

from src.core.events import EventBus
from src.core.models import (
    PortfolioState,
    Position,
    SignalDirection,
    TickData,
)


def test_tick_data_properties():
    tick = TickData(
        symbol="BTC-USDT",
        bid_price=50000.0,
        ask_price=50010.0,
        last_price=50005.0,
        volume=1.5,
    )
    assert tick.mid_price == 50005.0
    assert tick.spread == 10.0
    assert tick.symbol == "BTC-USDT"


def test_position_pnl_calculation():
    pos = Position(symbol="BTC-USDT", quantity=2.0, entry_price=50000.0, current_price=52000.0)
    assert pos.is_open is True
    assert pos.side == SignalDirection.LONG
    assert pos.unrealized_pnl == 4000.0
    assert pos.notional_value == 104000.0

    # Test Short position
    short_pos = Position(
        symbol="ETH-USDT", quantity=-10.0, entry_price=3000.0, current_price=3100.0
    )
    assert short_pos.side == SignalDirection.SHORT
    assert short_pos.unrealized_pnl == -1000.0


def test_portfolio_drawdown():
    portfolio = PortfolioState(
        total_equity=85000.0,
        cash=85000.0,
        initial_capital=100000.0,
        peak_equity=100000.0,
    )
    assert portfolio.drawdown_pct == pytest.approx(0.15)


@pytest.mark.asyncio
async def test_event_bus_dispatch():
    bus = EventBus()
    received_events = []

    async def on_tick(tick: TickData):
        received_events.append(tick)

    bus.subscribe(TickData, on_tick)
    await bus.start()

    sample_tick = TickData(
        symbol="BTC-USDT",
        bid_price=60000.0,
        ask_price=60001.0,
        last_price=60000.5,
    )
    await bus.publish(sample_tick)
    await asyncio.sleep(0.05)
    await bus.stop()

    assert len(received_events) == 1
    assert received_events[0].symbol == "BTC-USDT"
