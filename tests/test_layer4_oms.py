import pytest

from src.core.models import (
    FillEvent,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    TickData,
)
from src.layer4_oms.execution_sim import ExecutionSimulator
from src.layer4_oms.oms_engine import OMSEngine
from src.layer4_oms.order_state import InvalidOrderStateTransition, validate_transition
from src.layer4_oms.position_tracker import PositionTracker


def test_order_state_transitions():
    # Valid transitions
    validate_transition(OrderStatus.PENDING, OrderStatus.ACCEPTED)
    validate_transition(OrderStatus.ACCEPTED, OrderStatus.FILLED)
    validate_transition(OrderStatus.ACCEPTED, OrderStatus.CANCELED)

    # Invalid transitions (Terminal state violation)
    with pytest.raises(InvalidOrderStateTransition):
        validate_transition(OrderStatus.FILLED, OrderStatus.CANCELED)

    with pytest.raises(InvalidOrderStateTransition):
        validate_transition(OrderStatus.CANCELED, OrderStatus.FILLED)


def test_execution_simulator_slippage_and_fees():
    sim = ExecutionSimulator(
        base_slippage_bps=10.0, taker_fee_pct=0.001
    )  # 10 bps slippage, 0.1% fee

    # Buy Market Order @ 50,000.0
    buy_order = OrderRequest(
        order_id="ord_buy_1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=1.0,
        price=50000.0,
    )
    fill_buy = sim.execute_market_order(buy_order, current_price=50000.0)

    # 10 bps = 0.1% slippage -> 50,000 * 1.001 = 50,050.0
    assert fill_buy.filled_price == pytest.approx(50050.0)
    assert fill_buy.slippage == pytest.approx(50.0)
    # Fee = 50,050 * 1.0 * 0.001 = 50.05
    assert fill_buy.fee == pytest.approx(50.05)


def test_position_tracker_accounting():
    tracker = PositionTracker()

    # 1. Buy 1.0 BTC @ 50,000 (fee 20.0)
    fill1 = FillEvent(
        order_id="1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        filled_quantity=1.0,
        filled_price=50000.0,
        fee=20.0,
    )
    tracker.process_fill(fill1)
    pos = tracker.get_position("BTCUSDT")
    assert pos.quantity == 1.0
    assert pos.entry_price == 50000.0

    # 2. Scale into another 1.0 BTC @ 60,000 (avg entry should be 55,000)
    fill2 = FillEvent(
        order_id="2",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        filled_quantity=1.0,
        filled_price=60000.0,
        fee=20.0,
    )
    tracker.process_fill(fill2)
    assert pos.quantity == 2.0
    assert pos.entry_price == 55000.0

    # 3. Sell 1.0 BTC @ 65,000 (Realized PnL = (65,000 - 55,000) * 1.0 - 20 fee = 9,980)
    fill3 = FillEvent(
        order_id="3",
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        status=OrderStatus.FILLED,
        filled_quantity=1.0,
        filled_price=65000.0,
        fee=20.0,
    )
    realized_pnl = tracker.process_fill(fill3)
    assert realized_pnl == pytest.approx(9980.0)
    assert pos.quantity == 1.0
    assert pos.entry_price == 55000.0


@pytest.mark.asyncio
async def test_oms_engine_market_and_limit_execution():
    fills_received = []

    async def on_fill(fill: FillEvent):
        fills_received.append(fill)

    oms = OMSEngine(on_fill=on_fill)

    # 1. Market Order execution
    market_order = OrderRequest(
        order_id="ord_mkt_1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=0.5,
    )
    fill = await oms.submit_order(market_order, current_price=60000.0)
    assert fill is not None
    assert fill.status == OrderStatus.FILLED
    assert oms.get_order_status("ord_mkt_1") == OrderStatus.FILLED
    assert oms.get_positions()["BTCUSDT"].quantity == 0.5

    # 2. Resting Limit Order (Buy BTC @ 58,000 when current is 60,000)
    limit_order = OrderRequest(
        order_id="ord_lmt_1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.2,
        price=58000.0,
    )
    await oms.submit_order(limit_order, current_price=60000.0)
    assert len(oms.get_open_orders()) == 1

    # Market tick at 59,000 (Does NOT trigger limit buy)
    tick1 = TickData(symbol="BTCUSDT", bid_price=58999.0, ask_price=59000.0, last_price=59000.0)
    await oms.process_market_tick(tick1)
    assert len(oms.get_open_orders()) == 1

    # Market tick at 57,990 (Triggers limit buy!)
    tick2 = TickData(symbol="BTCUSDT", bid_price=57980.0, ask_price=57990.0, last_price=57985.0)
    triggered_fills = await oms.process_market_tick(tick2)
    assert len(triggered_fills) == 1
    assert triggered_fills[0].order_id == "ord_lmt_1"
    assert len(oms.get_open_orders()) == 0
    assert oms.get_positions()["BTCUSDT"].quantity == pytest.approx(0.7)  # 0.5 + 0.2
