import pytest

from src.core.models import (
    FillEvent,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    PortfolioState,
    TickData,
)
from src.layer6_monitoring.discord_notifier import DiscordNotifier
from src.layer6_monitoring.metrics import MetricsCollector


@pytest.mark.asyncio
async def test_discord_notifier_graceful_suppression():
    # Without webhook url, it gracefully returns False without throwing
    notifier = DiscordNotifier(webhook_url=None)

    fill = FillEvent(
        order_id="ord_discord_1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        filled_quantity=0.1,
        filled_price=65000.0,
        fee=3.25,
    )
    res_fill = await notifier.notify_fill(fill)
    assert res_fill is False

    res_cb = await notifier.notify_circuit_breaker(
        status="HALT_TRADING",
        drawdown_pct=0.16,
        total_equity=84000.0,
    )
    assert res_cb is False

    portfolio = PortfolioState(total_equity=100000.0, cash=100000.0)
    res_summary = await notifier.notify_portfolio_summary(portfolio)
    assert res_summary is False

    res_open = await notifier.notify_trade_open(
        symbol="BTCUSDT",
        side="BUY",
        entry_price=65000.0,
        quantity=0.1,
        position_value=6500.0,
        stop_loss=63500.0,
        take_profit=67250.0,
    )
    assert res_open is False

    res_close = await notifier.notify_trade_close(
        symbol="BTCUSDT",
        closed_side="LONG",
        entry_price=65000.0,
        exit_price=67250.0,
        quantity=0.1,
        realized_pnl=225.0,
        exit_reason="Take Profit Triggered",
        total_equity=100225.0,
        win_count=1,
        loss_count=0,
    )
    assert res_close is False

    await notifier.close()


def test_metrics_collector_recording():
    # Test metrics recording functions
    collector = MetricsCollector(port=9099)

    tick = TickData(symbol="BTCUSDT", bid_price=64000.0, ask_price=64001.0, last_price=64000.5)
    collector.record_tick(tick)

    order = OrderRequest(
        order_id="ord_metrics_1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=0.5,
    )
    collector.record_order(order)

    fill = FillEvent(
        order_id="ord_metrics_1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        filled_quantity=0.5,
        filled_price=64000.0,
        fee=16.0,
    )
    collector.record_fill(fill)

    portfolio = PortfolioState(
        total_equity=99984.0,
        cash=67984.0,
        peak_equity=100000.0,
    )
    collector.record_portfolio(portfolio, realized_pnl=150.0)
