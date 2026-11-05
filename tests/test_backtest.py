import polars as pl

from src.layer2_alpha.backtest import BacktestEngine


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
