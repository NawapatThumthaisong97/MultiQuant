"""
High-Performance Vectorized Technical Indicators using Polars.
Leverages Polars Rust engine for lightning-fast calculations without GIL bottlenecks.
"""

from __future__ import annotations

import polars as pl


def compute_true_range(df: pl.DataFrame) -> pl.DataFrame:
    """
    Computes True Range (TR):
    TR = max(High - Low, abs(High - Close_prev), abs(Low - Close_prev))
    """
    prev_close = pl.col("close").shift(1)
    hl = pl.col("high") - pl.col("low")
    hc = (pl.col("high") - prev_close).abs()
    lc = (pl.col("low") - prev_close).abs()

    # Using pl.max_horizontal across expressions
    tr_expr = pl.max_horizontal(hl, hc, lc).fill_null(hl).alias("tr")
    return df.with_columns(tr_expr)


def compute_atr(df: pl.DataFrame, period: int = 14) -> pl.DataFrame:
    """
    Computes Average True Range (ATR) using Exponential Weighted Moving Average.
    Crucial for Layer 3 Volatility Position Sizing and dynamic Stop Loss.
    """
    df_tr = compute_true_range(df)
    atr_expr = pl.col("tr").ewm_mean(span=period, adjust=False).alias(f"atr_{period}")
    return df_tr.with_columns(atr_expr)


def compute_ema(
    df: pl.DataFrame,
    period: int,
    column: str = "close",
    out_col: str | None = None,
) -> pl.DataFrame:
    """Computes Exponential Moving Average (EMA) using Polars native ewm_mean."""
    target_col = out_col or f"ema_{period}"
    ema_expr = pl.col(column).ewm_mean(span=period, adjust=False).alias(target_col)
    return df.with_columns(ema_expr)


def compute_bollinger_bands(
    df: pl.DataFrame,
    period: int = 20,
    num_std: float = 2.0,
) -> pl.DataFrame:
    """
    Computes Bollinger Bands:
    - Middle Band: 20-period SMA
    - Upper Band: Middle + (num_std * std)
    - Lower Band: Middle - (num_std * std)
    - Bandwidth: (Upper - Lower) / Middle
    """
    mid_expr = pl.col("close").rolling_mean(window_size=period).alias("bb_mid")
    std_expr = pl.col("close").rolling_std(window_size=period).alias("bb_std")

    df_bb = df.with_columns([mid_expr, std_expr])

    upper_expr = (pl.col("bb_mid") + (pl.col("bb_std") * num_std)).alias("bb_upper")
    lower_expr = (pl.col("bb_mid") - (pl.col("bb_std") * num_std)).alias("bb_lower")

    df_bands = df_bb.with_columns([upper_expr, lower_expr])
    bandwidth_expr = ((pl.col("bb_upper") - pl.col("bb_lower")) / pl.col("bb_mid")).alias(
        "bb_bandwidth"
    )

    return df_bands.with_columns(bandwidth_expr)


def compute_liquidity_swings(
    df: pl.DataFrame,
    window: int = 5,
) -> pl.DataFrame:
    """
    Detects Liquidity Swings (Swing Highs and Swing Lows) for Smart Money Concepts (SMC).
    A swing high is formed when the current high is higher than surrounding candles.
    """
    # Look back window to detect local extrema
    roll_max = pl.col("high").rolling_max(window_size=window * 2 + 1, min_samples=window)
    roll_min = pl.col("low").rolling_min(window_size=window * 2 + 1, min_samples=window)

    is_swing_high = (pl.col("high") == roll_max).alias("is_swing_high")
    is_swing_low = (pl.col("low") == roll_min).alias("is_swing_low")

    return df.with_columns([is_swing_high, is_swing_low])


def compute_rsi(df: pl.DataFrame, period: int = 14) -> pl.DataFrame:
    """
    Computes Relative Strength Index (RSI) using Wilder's smoothing (EWM alpha = 1/period).
    Essential for 5m Mean-Reversion and Exhaustion detection.
    """
    delta = pl.col("close").diff()
    gain = pl.when(delta > 0).then(delta).otherwise(0.0)
    loss = pl.when(delta < 0).then(-delta).otherwise(0.0)

    avg_gain = gain.ewm_mean(alpha=1.0 / period, adjust=False)
    avg_loss = loss.ewm_mean(alpha=1.0 / period, adjust=False)

    rs = avg_gain / (avg_loss + 1e-9)
    rsi = (100.0 - (100.0 / (1.0 + rs))).fill_null(50.0).alias(f"rsi_{period}")
    return df.with_columns(rsi)


def compute_adx(df: pl.DataFrame, period: int = 14) -> pl.DataFrame:
    """
    Computes Average Directional Index (ADX) via Polars vectorized expressions.
    Used to classify market regime: Trending (ADX >= 25) vs Ranging/Sideway (ADX < 20).
    """
    up_move = pl.col("high") - pl.col("high").shift(1)
    down_move = pl.col("low").shift(1) - pl.col("low")

    plus_dm = (
        pl.when((up_move > down_move) & (up_move > 0)).then(up_move).otherwise(0.0).fill_null(0.0)
    )
    minus_dm = (
        pl.when((down_move > up_move) & (down_move > 0))
        .then(down_move)
        .otherwise(0.0)
        .fill_null(0.0)
    )

    df_tr = compute_true_range(df) if "tr" not in df.columns else df
    atr_smooth = pl.col("tr").ewm_mean(alpha=1.0 / period, adjust=False) + 1e-9

    plus_di = 100.0 * (plus_dm.ewm_mean(alpha=1.0 / period, adjust=False) / atr_smooth)
    minus_di = 100.0 * (minus_dm.ewm_mean(alpha=1.0 / period, adjust=False) / atr_smooth)

    dx = (100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)).fill_null(0.0)
    adx = dx.ewm_mean(alpha=1.0 / period, adjust=False).fill_null(0.0).alias(f"adx_{period}")

    return df_tr.with_columns(
        [
            plus_di.alias(f"plus_di_{period}"),
            minus_di.alias(f"minus_di_{period}"),
            adx,
        ]
    )


def compute_donchian_channels(df: pl.DataFrame, period: int = 48) -> pl.DataFrame:
    """
    Computes structural Donchian Channels (shifted by 1 bar to prevent look-ahead bias).
    On 5m bars, period=48 represents the prior 4-hour structural High/Low range.
    """
    upper = (
        pl.col("high")
        .shift(1)
        .rolling_max(window_size=period, min_samples=2)
        .fill_null(pl.col("high"))
        .alias(f"donchian_high_{period}")
    )
    lower = (
        pl.col("low")
        .shift(1)
        .rolling_min(window_size=period, min_samples=2)
        .fill_null(pl.col("low"))
        .alias(f"donchian_low_{period}")
    )
    return df.with_columns([upper, lower])


def compute_microstructure_flow(df: pl.DataFrame, rvol_period: int = 24) -> pl.DataFrame:
    """
    Computes Orderflow Microstructure features from Taker Buy Volume:
    - taker_buy_ratio: Aggressive buyer share of total volume (0.0 to 1.0)
    - volume_delta: Taker Buy Volume - Taker Sell Volume
    - cvd: Cumulative Volume Delta
    - rvol: Relative Volume vs rolling average
    """
    vol_safe = pl.col("volume") + 1e-9
    rvol = (
        (
            pl.col("volume")
            / (pl.col("volume").rolling_mean(window_size=rvol_period, min_samples=1) + 1e-9)
        )
        .fill_null(1.0)
        .alias("rvol")
    )

    if "taker_buy_volume" in df.columns:
        taker_ratio = (
            (pl.col("taker_buy_volume") / vol_safe).clip(0.0, 1.0).alias("taker_buy_ratio")
        )
        vol_delta = (
            pl.col("taker_buy_volume") - (pl.col("volume") - pl.col("taker_buy_volume"))
        ).alias("volume_delta")
    else:
        taker_ratio = pl.lit(0.5).alias("taker_buy_ratio")
        vol_delta = pl.lit(0.0).alias("volume_delta")

    res = df.with_columns([rvol, taker_ratio, vol_delta])
    return res.with_columns([pl.col("volume_delta").cum_sum().alias("cvd")])
