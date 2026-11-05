import polars as pl

from src.layer2_alpha.backtest import BacktestEngine
from src.layer2_alpha.strategies import MTFVolatilitySqueezeAlpha


def test_backtest_engine_execution_and_metrics():
    # Construct synthetic OHLCV data
    bars = []
    price = 60000.0
    for i in range(150):
        # Create an upward trend with some pullbacks
        price += 50.0 if i % 3 != 0 else -30.0
        bars.append(
            {
                "timestamp": f"2026-01-01 {i:02d}:00:00",
                "open": price - 10.0,
                "high": price + 40.0,
                "low": price - 30.0,
                "close": price,
                "volume": 100.0,
            }
        )
    df = pl.DataFrame(bars)

    engine = BacktestEngine(initial_capital=50.0)
    metrics, df_calc = engine.run(
        df,
        fast_period=5,
        slow_period=15,
        trend_period=30,
        use_trend_filter=True,
        use_volatility_filter=True,
    )

    assert "ema_fast" in df_calc.columns
    assert "ema_slow" in df_calc.columns
    assert "atr" in df_calc.columns
    assert "vol_filter_ok" in df_calc.columns

    assert metrics.initial_capital == 50.0
    assert isinstance(metrics.final_equity, float)
    assert isinstance(metrics.sharpe_ratio, float)
    assert isinstance(metrics.max_drawdown_pct, float)
    assert metrics.max_drawdown_pct >= 0.0


def test_backtest_5m_presets():
    bars = []
    price = 60000.0
    for i in range(200):
        price += 35.0 if i % 4 != 0 else -45.0
        bars.append(
            {
                "timestamp": f"2026-01-01 {i // 12:02d}:{(i % 12) * 5:02d}:00",
                "open": price - 15.0,
                "high": price + 50.0,
                "low": price - 40.0,
                "close": price,
                "volume": 120.0 + (i % 10) * 30.0,
                "taker_buy_volume": 75.0 + (i % 10) * 20.0,
            }
        )
    df = pl.DataFrame(bars)
    engine = BacktestEngine(initial_capital=50.0)
    df_prep = engine.prepare_5m_preset_signals(df)

    for col in [
        "P1_RETAIL_EMA_5M",
        "P2_MINIMALIST_3PILLAR",
        "P3_VOLATILITY_SQUEEZE",
        "P4_SQUEEZE_ADX_REGIME",
        "P5_INSTITUTIONAL_ELITE",
        "P6_MTF_SQUEEZE_CHAMPION",
    ]:
        assert col in df_prep.columns
        m = engine.run_5m_preset(df_prep, preset_col=col, strategy_name=col)
        assert m.initial_capital == 50.0
        assert m.max_drawdown_pct >= 0.0

    strat = MTFVolatilitySqueezeAlpha(warmup_period=50)
    _ = strat.generate_signal("BTCUSDT", df)
