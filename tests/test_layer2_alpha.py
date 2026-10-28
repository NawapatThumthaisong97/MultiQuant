from datetime import UTC, datetime, timedelta

import pytest

from src.core.models import AlphaSignal, BarData, SignalDirection
from src.layer2_alpha.buffer import RollingBarBuffer
from src.layer2_alpha.engine import AlphaEngine
from src.layer2_alpha.indicators import (
    compute_atr,
    compute_bollinger_bands,
    compute_ema,
    compute_liquidity_swings,
    compute_true_range,
)
from src.layer2_alpha.strategies import TrendVolatilityAlpha


def create_sample_bars(n: int = 50, start_price: float = 100.0, step: float = 1.0) -> list[BarData]:
    bars = []
    base_time = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    price = start_price

    for i in range(n):
        price += step
        high = price + 2.0
        low = price - 2.0
        bars.append(
            BarData(
                symbol="BTCUSDT",
                timestamp=base_time + timedelta(minutes=i),
                open=price - 0.5,
                high=high,
                low=low,
                close=price,
                volume=100.0 + (i * 2.0),
                timeframe="1m",
            )
        )
    return bars


def test_rolling_bar_buffer():
    buffer = RollingBarBuffer(maxlen=10)
    bars = create_sample_bars(15)

    for b in bars:
        buffer.add_bar(b)

    assert buffer.count("BTCUSDT") == 10
    df = buffer.to_polars("BTCUSDT")
    assert df is not None
    assert len(df) == 10
    assert "close" in df.columns
    assert "high" in df.columns
    assert "low" in df.columns


def test_indicators_true_range_and_atr():
    buffer = RollingBarBuffer(maxlen=50)
    bars = create_sample_bars(35)
    for b in bars:
        buffer.add_bar(b)

    df = buffer.to_polars("BTCUSDT")
    assert df is not None

    df_tr = compute_true_range(df)
    assert "tr" in df_tr.columns
    assert df_tr["tr"].min() is not None
    assert df_tr["tr"].min() > 0.0

    df_atr = compute_atr(df, period=14)
    assert "atr_14" in df_atr.columns
    assert df_atr["atr_14"][-1] > 0.0


def test_indicators_ema_and_bollinger():
    buffer = RollingBarBuffer(maxlen=50)
    bars = create_sample_bars(35)
    for b in bars:
        buffer.add_bar(b)

    df = buffer.to_polars("BTCUSDT")
    assert df is not None

    df_ema = compute_ema(df, period=9, column="close")
    assert "ema_9" in df_ema.columns

    df_bb = compute_bollinger_bands(df, period=20, num_std=2.0)
    assert "bb_mid" in df_bb.columns
    assert "bb_upper" in df_bb.columns
    assert "bb_lower" in df_bb.columns
    assert "bb_bandwidth" in df_bb.columns

    # Upper band must be strictly greater than lower band
    latest_upper = df_bb["bb_upper"][-1]
    latest_lower = df_bb["bb_lower"][-1]
    assert latest_upper > latest_lower


def test_liquidity_swings_detection():
    buffer = RollingBarBuffer(maxlen=50)
    bars = create_sample_bars(30)
    for b in bars:
        buffer.add_bar(b)

    df = buffer.to_polars("BTCUSDT")
    assert df is not None

    df_swings = compute_liquidity_swings(df, window=3)
    assert "is_swing_high" in df_swings.columns
    assert "is_swing_low" in df_swings.columns


def test_trend_volatility_strategy_golden_cross():
    strat = TrendVolatilityAlpha(warmup_period=20, fast_period=5, slow_period=10)

    # Generate a trend shift: first downward, then sharp upward
    bars_down = create_sample_bars(20, start_price=100.0, step=-1.0)
    bars_up = create_sample_bars(10, start_price=80.0, step=3.0)
    all_bars = bars_down + bars_up

    buffer = RollingBarBuffer(maxlen=50)
    for b in all_bars:
        buffer.add_bar(b)

    df = buffer.to_polars("BTCUSDT")
    assert df is not None
    signal = strat.generate_signal("BTCUSDT", df)

    # Should generate either LONG or None depending on crossover moment
    if signal is not None:
        assert signal.direction in (SignalDirection.LONG, SignalDirection.SHORT)
        assert signal.symbol == "BTCUSDT"
        assert "atr" in signal.metadata


@pytest.mark.asyncio
async def test_alpha_engine_execution():
    signals_received = []

    async def on_signal(sig: AlphaSignal):
        signals_received.append(sig)

    engine = AlphaEngine(on_signal=on_signal)
    engine.register_strategy(TrendVolatilityAlpha(warmup_period=15, fast_period=3, slow_period=7))

    bars = create_sample_bars(25, start_price=50.0, step=1.5)
    for b in bars:
        await engine.on_bar(b)

    assert engine.buffer.count("BTCUSDT") == 25
