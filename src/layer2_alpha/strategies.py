"""
Production Alpha Strategies.
High-throughput strategies leveraging Polars expressions and ATR metadata for Layer 3 Sizing.
"""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl

from src.core.models import AlphaSignal, SignalDirection
from src.layer2_alpha.base import BaseAlphaStrategy
from src.layer2_alpha.indicators import compute_atr, compute_bollinger_bands, compute_ema


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
