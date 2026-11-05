"""
Production Alpha Strategies.
High-throughput strategies leveraging Polars expressions and ATR metadata for Layer 3 Sizing.
"""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl

from src.core.models import AlphaSignal, SignalDirection
from src.layer2_alpha.base import BaseAlphaStrategy
from src.layer2_alpha.indicators import (
    compute_atr,
    compute_bollinger_bands,
    compute_donchian_channels,
    compute_ema,
    compute_microstructure_flow,
)


class TrendVolatilityAlpha(BaseAlphaStrategy):
    """
    Academic Quant-Filtered Trend Following Strategy for BTC:
    - Fast EMA (9) vs Slow EMA (21) for Time-Series Momentum
    - Trend Regime Filter (50/200 EMA): Only Long in Bull Regime, Only Short in Bear Regime
    - Volatility Spike Filter: Bypasses toxic flash crashes when ATR spikes > 2x historical mean
    - Dynamic ATR export for Layer 3 Risk Sizer
    """

    def __init__(
        self,
        name: str = "quant_regime_trend",
        fast_period: int = 9,
        slow_period: int = 21,
        trend_period: int = 50,
        atr_period: int = 14,
        warmup_period: int = 50,
    ) -> None:
        super().__init__(name=name, warmup_period=warmup_period)
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.trend_period = trend_period
        self.atr_period = atr_period
        self.parameters = {
            "fast_period": fast_period,
            "slow_period": slow_period,
            "trend_period": trend_period,
            "atr_period": atr_period,
        }

    def generate_signal(self, symbol: str, df: pl.DataFrame) -> AlphaSignal | None:
        if not self.is_warmed_up(df):
            return None

        # Compute indicators in single vectorized pipeline
        df_calc = compute_ema(df, period=self.fast_period, column="close", out_col="ema_fast")
        df_calc = compute_ema(df_calc, period=self.slow_period, column="close", out_col="ema_slow")
        df_calc = compute_ema(
            df_calc, period=self.trend_period, column="close", out_col="ema_trend"
        )
        df_calc = compute_atr(df_calc, period=self.atr_period)

        # Get latest closed bar values
        latest = df_calc.tail(1)
        prev = df_calc.tail(2).head(1)

        fast_curr = latest["ema_fast"][0]
        slow_curr = latest["ema_slow"][0]
        trend_curr = latest["ema_trend"][0]
        close_curr = latest["close"][0]
        atr_curr = latest[f"atr_{self.atr_period}"][0]

        fast_prev = prev["ema_fast"][0]
        slow_prev = prev["ema_slow"][0]

        # Trend and Volatility Gates
        is_bull_regime = close_curr > trend_curr
        is_bear_regime = close_curr < trend_curr
        atr_mean_raw = df_calc[f"atr_{self.atr_period}"].mean()
        atr_mean = float(str(atr_mean_raw)) if atr_mean_raw is not None else float(atr_curr)
        vol_is_stable = atr_curr <= (atr_mean * 2.0)

        # Calculate signal strength (normalized trend distance)
        spread_pct = abs(fast_curr - slow_curr) / slow_curr
        strength = min(1.0, max(0.2, spread_pct * 100.0))

        # Golden Cross strictly in Bull Regime + Non-chaotic Volatility
        if fast_curr > slow_curr and fast_prev <= slow_prev and is_bull_regime and vol_is_stable:
            return AlphaSignal(
                symbol=symbol,
                direction=SignalDirection.LONG,
                strength=strength,
                timestamp=datetime.now(UTC),
                strategy_name=self.name,
                metadata={
                    "atr": float(atr_curr),
                    "close": float(close_curr),
                    "fast_ema": float(fast_curr),
                    "slow_ema": float(slow_curr),
                    "trend_ema": float(trend_curr),
                },
            )
        # Death Cross strictly in Bear Regime + Non-chaotic Volatility
        elif fast_curr < slow_curr and fast_prev >= slow_prev and is_bear_regime and vol_is_stable:
            return AlphaSignal(
                symbol=symbol,
                direction=SignalDirection.SHORT,
                strength=strength,
                timestamp=datetime.now(UTC),
                strategy_name=self.name,
                metadata={
                    "atr": float(atr_curr),
                    "close": float(close_curr),
                    "fast_ema": float(fast_curr),
                    "slow_ema": float(slow_curr),
                    "trend_ema": float(trend_curr),
                },
            )

        return None


class BollingerMeanReversionAlpha(BaseAlphaStrategy):
    """
    Mean Reversion Strategy using Bollinger Bands:
    - Long when price dips below lower band and closes back inside.
    - Short when price pierces upper band and closes back inside.
    """

    def __init__(
        self,
        name: str = "bb_mean_reversion",
        period: int = 20,
        num_std: float = 2.0,
        atr_period: int = 14,
        warmup_period: int = 25,
    ) -> None:
        super().__init__(name=name, warmup_period=warmup_period)
        self.period = period
        self.num_std = num_std
        self.atr_period = atr_period
        self.parameters = {
            "period": period,
            "num_std": num_std,
            "atr_period": atr_period,
        }

    def generate_signal(self, symbol: str, df: pl.DataFrame) -> AlphaSignal | None:
        if not self.is_warmed_up(df):
            return None

        df_calc = compute_bollinger_bands(df, period=self.period, num_std=self.num_std)
        df_calc = compute_atr(df_calc, period=self.atr_period)

        latest = df_calc.tail(1)
        prev = df_calc.tail(2).head(1)

        close_curr = latest["close"][0]
        close_prev = prev["close"][0]
        lower_curr = latest["bb_lower"][0]
        lower_prev = prev["bb_lower"][0]
        upper_curr = latest["bb_upper"][0]
        upper_prev = prev["bb_upper"][0]
        atr_curr = latest[f"atr_{self.atr_period}"][0]

        # Lower band bounce -> LONG
        if close_prev < lower_prev and close_curr > lower_curr:
            return AlphaSignal(
                symbol=symbol,
                direction=SignalDirection.LONG,
                strength=0.85,
                timestamp=datetime.now(UTC),
                strategy_name=self.name,
                metadata={
                    "atr": float(atr_curr),
                    "close": float(close_curr),
                    "bb_lower": float(lower_curr),
                },
            )
        # Upper band rejection -> SHORT
        elif close_prev > upper_prev and close_curr < upper_curr:
            return AlphaSignal(
                symbol=symbol,
                direction=SignalDirection.SHORT,
                strength=0.85,
                timestamp=datetime.now(UTC),
                strategy_name=self.name,
                metadata={
                    "atr": float(atr_curr),
                    "close": float(close_curr),
                    "bb_upper": float(upper_curr),
                },
            )

        return None


class MTFVolatilitySqueezeAlpha(BaseAlphaStrategy):
    """
    Flagship 5-Minute BTCUSDT Champion Strategy (P3 + 1h/12h Trend Alignment):
    - Multi-Timeframe EMA Cascade: 1h EMA(12) vs 12h EMA(144) & Close vs 48h EMA(576)
    - Volatility Compression: Bollinger Bandwidth Squeeze (< 0.85x 8h mean) + Band Breakout
    - Structural Range Breakout: 8-hour Donchian Channel (96 bars)
    - Orderflow Microstructure: RVOL >= 2.0x + Taker Buy Ratio >= 58% + ATR Spike Guard
    """

    def __init__(
        self,
        name: str = "mtf_volatility_squeeze",
        donchian_period: int = 96,
        macro_ema_period: int = 576,
        rvol_threshold: float = 2.0,
        taker_ratio_threshold: float = 0.58,
        warmup_period: int = 100,
    ) -> None:
        super().__init__(name=name, warmup_period=warmup_period)
        self.donchian_period = donchian_period
        self.macro_ema_period = macro_ema_period
        self.rvol_threshold = rvol_threshold
        self.taker_ratio_threshold = taker_ratio_threshold
        self.parameters = {
            "donchian_period": donchian_period,
            "macro_ema_period": macro_ema_period,
            "rvol_threshold": rvol_threshold,
            "taker_ratio_threshold": taker_ratio_threshold,
        }

    def generate_signal(self, symbol: str, df: pl.DataFrame) -> AlphaSignal | None:
        if not self.is_warmed_up(df):
            return None

        df_calc = compute_atr(df, period=14)
        df_calc = compute_ema(df_calc, period=12, out_col="ema_1h")
        df_calc = compute_ema(df_calc, period=144, out_col="ema_12h")
        df_calc = compute_ema(df_calc, period=self.macro_ema_period, out_col="ema_macro")
        df_calc = compute_bollinger_bands(df_calc, period=20, num_std=2.0)
        df_calc = compute_donchian_channels(df_calc, period=self.donchian_period)
        df_calc = compute_microstructure_flow(df_calc, rvol_period=24)

        df_calc = df_calc.with_columns(
            [
                pl.col("atr_14").rolling_mean(window_size=96, min_samples=1).alias("atr_base"),
                pl.col("bb_bandwidth")
                .rolling_mean(window_size=96, min_samples=1)
                .alias("bb_bw_mean"),
                pl.col("bb_bandwidth")
                .rolling_min(window_size=12, min_samples=1)
                .alias("bb_bw_min12"),
            ]
        )

        latest = df_calc.tail(1)
        close_curr = float(latest["close"][0])
        atr_curr = float(latest["atr_14"][0])
        atr_base = float(latest["atr_base"][0])
        ema_1h = float(latest["ema_1h"][0])
        ema_12h = float(latest["ema_12h"][0])
        ema_macro = float(latest["ema_macro"][0])
        bb_upper = float(latest["bb_upper"][0])
        bb_lower = float(latest["bb_lower"][0])
        bb_bw_min12 = float(latest["bb_bw_min12"][0])
        bb_bw_mean = float(latest["bb_bw_mean"][0])
        d_high = float(latest[f"donchian_high_{self.donchian_period}"][0])
        d_low = float(latest[f"donchian_low_{self.donchian_period}"][0])
        rvol = float(latest["rvol"][0])
        taker_ratio = float(latest["taker_buy_ratio"][0])

        vol_ok = atr_curr <= (atr_base * 2.2)
        squeeze_ok = bb_bw_min12 < (bb_bw_mean * 0.85)
        has_taker_col = "taker_buy_volume" in df.columns

        long_flow_ok = (rvol >= self.rvol_threshold) and (
            taker_ratio >= self.taker_ratio_threshold if has_taker_col else True
        )
        short_flow_ok = (rvol >= self.rvol_threshold) and (
            taker_ratio <= (1.0 - self.taker_ratio_threshold) if has_taker_col else True
        )

        if (
            squeeze_ok
            and vol_ok
            and close_curr > bb_upper
            and close_curr > d_high
            and close_curr > ema_macro
            and ema_1h > ema_12h
            and long_flow_ok
        ):
            return AlphaSignal(
                symbol=symbol,
                direction=SignalDirection.LONG,
                strength=0.95,
                timestamp=datetime.now(UTC),
                strategy_name=self.name,
                metadata={
                    "atr": atr_curr,
                    "close": close_curr,
                    "ema_1h": ema_1h,
                    "ema_12h": ema_12h,
                    "ema_macro": ema_macro,
                    "rvol": rvol,
                    "taker_buy_ratio": taker_ratio,
                },
            )
        elif (
            squeeze_ok
            and vol_ok
            and close_curr < bb_lower
            and close_curr < d_low
            and close_curr < ema_macro
            and ema_1h < ema_12h
            and short_flow_ok
        ):
            return AlphaSignal(
                symbol=symbol,
                direction=SignalDirection.SHORT,
                strength=0.95,
                timestamp=datetime.now(UTC),
                strategy_name=self.name,
                metadata={
                    "atr": atr_curr,
                    "close": close_curr,
                    "ema_1h": ema_1h,
                    "ema_12h": ema_12h,
                    "ema_macro": ema_macro,
                    "rvol": rvol,
                    "taker_buy_ratio": taker_ratio,
                },
            )

        return None
