import pytest

from src.core.models import (
    AlphaSignal,
    OrderSide,
    PortfolioState,
    Position,
    SignalDirection,
)
from src.layer3_risk.drawdown_guard import CircuitBreakerStatus, DrawdownGuard
from src.layer3_risk.position_sizer import VolatilityPositionSizer
from src.layer3_risk.risk_manager import PreTradeRiskManager


def create_sample_portfolio(equity: float = 100_000.0, peak: float = 100_000.0) -> PortfolioState:
    return PortfolioState(
        total_equity=equity,
        cash=equity,
        initial_capital=100_000.0,
        peak_equity=peak,
        positions={},
    )


def test_volatility_position_sizer_inverse_relationship():
    # 0.5% risk on $100k = $500 risk budget
    sizer = VolatilityPositionSizer(risk_per_trade_pct=0.005, atr_multiplier=2.0, max_allocation_pct=0.25)
    portfolio = create_sample_portfolio(100_000.0)

    # Low Volatility regime (ATR = $1,000 on BTC at $50,000 -> stop distance $2,000)
    low_vol_signal = AlphaSignal(
        symbol="BTCUSDT",
        direction=SignalDirection.LONG,
        strength=1.0,
        metadata={"atr": 1000.0},
    )
    res_low = sizer.calculate_size(low_vol_signal, portfolio, current_price=50000.0)
    assert res_low.is_valid is True
    # Expected qty = 500 / 2000 = 0.25 BTC

    # High Volatility regime (ATR = $2,500 on BTC at $50,000 -> stop distance $5,000)
    high_vol_signal = AlphaSignal(
        symbol="BTCUSDT",
        direction=SignalDirection.LONG,
        strength=1.0,
        metadata={"atr": 2500.0},
    )
    res_high = sizer.calculate_size(high_vol_signal, portfolio, current_price=50000.0)
    assert res_high.is_valid is True
    # Expected qty = 500 / 5000 = 0.10 BTC

    # In higher volatility, position size must be significantly smaller!
    assert res_low.quantity > res_high.quantity
    assert res_low.quantity == pytest.approx(0.25)
    assert res_high.quantity == pytest.approx(0.10)
    # While dollar risk stays identical ($500)
    assert res_low.dollar_risk == pytest.approx(500.0, abs=1.0)
    assert res_high.dollar_risk == pytest.approx(500.0, abs=1.0)


def test_volatility_position_sizer_rejection():
    sizer = VolatilityPositionSizer()
    portfolio = create_sample_portfolio(100_000.0)

    # Missing ATR
    no_atr_signal = AlphaSignal(
        symbol="BTCUSDT",
        direction=SignalDirection.LONG,
        strength=1.0,
        metadata={},
    )
    res = sizer.calculate_size(no_atr_signal, portfolio, current_price=50000.0)
    assert res.is_valid is False
    assert "ATR" in (res.rejection_reason or "")


def test_drawdown_guard_circuit_breaker():
    guard = DrawdownGuard(
        warning_drawdown_pct=0.08,
        max_drawdown_pct=0.15,
        emergency_drawdown_pct=0.20,
    )

    # 1. Normal state ($100k peak, $98k current = 2% DD)
    p1 = create_sample_portfolio(equity=98_000.0, peak=100_000.0)
    assert guard.update_equity(p1) == CircuitBreakerStatus.NORMAL
    assert guard.can_open_new_positions() is True
    assert guard.get_sizing_multiplier() == 1.0

    # 2. Warning state ($100k peak, $90k current = 10% DD)
    p2 = create_sample_portfolio(equity=90_000.0, peak=100_000.0)
    assert guard.update_equity(p2) == CircuitBreakerStatus.REDUCE_RISK
    assert guard.can_open_new_positions() is True
    assert guard.get_sizing_multiplier() == 0.5  # Sizing halved

    # 3. Halt state ($100k peak, $84k current = 16% DD)
    p3 = create_sample_portfolio(equity=84_000.0, peak=100_000.0)
    assert guard.update_equity(p3) == CircuitBreakerStatus.HALT_TRADING
    assert guard.can_open_new_positions() is False
    assert guard.get_sizing_multiplier() == 0.0

    # 4. Emergency state ($100k peak, $78k current = 22% DD)
    p4 = create_sample_portfolio(equity=78_000.0, peak=100_000.0)
    assert guard.update_equity(p4) == CircuitBreakerStatus.EMERGENCY_LIQUIDATE
    assert guard.can_open_new_positions() is False


def test_pre_trade_risk_manager_approval():
    manager = PreTradeRiskManager()
    portfolio = create_sample_portfolio(100_000.0)

    signal = AlphaSignal(
        symbol="BTCUSDT",
        direction=SignalDirection.LONG,
        strength=1.0,
        metadata={"atr": 250.0},
    )

    order = manager.evaluate_signal(signal, portfolio, current_price=60000.0)
    assert order is not None
    assert order.symbol == "BTCUSDT"
    assert order.side == OrderSide.BUY
    assert order.quantity > 0.0
    assert order.stop_price is not None
    assert order.stop_price < 60000.0


def test_pre_trade_risk_manager_max_positions_gate():
    manager = PreTradeRiskManager(max_open_positions=2)
    portfolio = create_sample_portfolio(100_000.0)
    # Fill up 2 open positions
    portfolio.positions["ETHUSDT"] = Position(
        symbol="ETHUSDT", quantity=5.0, entry_price=3000.0, current_price=3000.0
    )
    portfolio.positions["SOLUSDT"] = Position(
        symbol="SOLUSDT", quantity=50.0, entry_price=150.0, current_price=150.0
    )

    # 3rd new symbol must be rejected by Gate 2
    new_signal = AlphaSignal(
        symbol="BTCUSDT",
        direction=SignalDirection.LONG,
        strength=1.0,
        metadata={"atr": 200.0},
    )
    order = manager.evaluate_signal(new_signal, portfolio, current_price=60000.0)
    assert order is None
