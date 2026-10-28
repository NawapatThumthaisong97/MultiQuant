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
