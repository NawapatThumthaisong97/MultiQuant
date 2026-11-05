"""
Institutional Vectorized Backtesting Engine for MultiQuant.
Evaluates:
1. Baseline Raw EMA Crossover (No Filters)
2. Trend Regime (EMA 50/200) + Volatility Spike Filter (ATR Scaling)
3. Advanced Institutional Microstructure Strategy:
   - Trend Regime (EMA 50/200)
   - Volatility Expansion (ATR)
   - Cumulative Volume Delta (CVD) Aggressive Order Flow confirmation
   - Open Interest (OI) Capital Inflow confirmation
   - Funding Rate Overcrowding & Liquidation Risk filter

Strictly simulates MultiQuant's Layer 2, Layer 3 (Risk), and Layer 4 (OMS Slippage/Fees).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import polars as pl

logger = logging.getLogger("multiquant.backtest")


@dataclass(frozen=True)
class BacktestMetrics:
    """Institutional Backtest Performance Summary."""

    strategy_name: str
    initial_capital: float
    final_equity: float
    total_return_pct: float
    annualized_return_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    profit_factor: float
    win_rate_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    total_fees_paid: float
    total_slippage_paid: float


class BacktestEngine:
    """
    Event-driven and Vectorized Hybrid Backtester.
    Strictly mimics MultiQuant's Layer 2 (Alpha), Layer 3 (Risk), and Layer 4 (OMS Slippage/Fees).
    """

    def __init__(
        self,
        initial_capital: float = 50.0,
        taker_fee_pct: float = 0.0005,  # 5 bps
        slippage_bps: float = 3.0,  # 3 bps
        risk_per_trade_pct: float = 0.02,  # 2% risk per trade
        atr_multiplier: float = 2.0,  # Stop Loss = 2x ATR
        risk_reward_ratio: float = 1.5,  # Take Profit = 3x ATR (1.5x Risk)
    ) -> None:
        self.initial_capital = initial_capital
        self.taker_fee_pct = taker_fee_pct
        self.slippage_fraction = slippage_bps / 10000.0
        self.risk_per_trade_pct = risk_per_trade_pct
        self.atr_multiplier = atr_multiplier
        self.risk_reward_ratio = risk_reward_ratio

    def run(
        self,
        df: pl.DataFrame,
        strategy_name: str = "Backtest_Strategy",
        fast_period: int = 9,
        slow_period: int = 21,
        trend_period: int = 50,
        use_trend_filter: bool = True,
        use_volatility_filter: bool = True,
        use_cvd_filter: bool = False,
        use_oi_filter: bool = False,
        use_funding_filter: bool = False,
    ) -> tuple[BacktestMetrics, pl.DataFrame]:
        """
        Executes backtest over historical DataFrame with optional microstructure features.
        Columns required: ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        Optional columns: ['taker_buy_volume', 'open_interest', 'funding_rate']
        """
        df_calc = self._prepare_indicators(df, fast_period, slow_period, trend_period)

        capital = self.initial_capital
        peak_equity = capital
        cash = capital
        position_qty = 0.0
        position_side = 0  # 1: Long, -1: Short, 0: Flat
        entry_price = 0.0
        stop_loss_price = 0.0
        take_profit_price = 0.0

        total_fees = 0.0
        total_slippage = 0.0
        trades: list[dict[str, Any]] = []
        equity_curve: list[float] = []

        timestamps = df_calc["timestamp"].to_list()
        closes = df_calc["close"].to_list()
        highs = df_calc["high"].to_list()
        lows = df_calc["low"].to_list()
        fast_emas = df_calc["ema_fast"].to_list()
        slow_emas = df_calc["ema_slow"].to_list()
        trend_emas = df_calc["ema_trend"].to_list()
        atrs = df_calc["atr"].to_list()
        vol_filter = df_calc["vol_filter_ok"].to_list()

        # Optional microstructure vectors
        cvd_divergence_ok = (
            df_calc["cvd_divergence_ok"].to_list()
            if "cvd_divergence_ok" in df_calc.columns
            else [True] * len(closes)
        )
        oi_expansion_ok = (
            df_calc["oi_expansion_ok"].to_list()
            if "oi_expansion_ok" in df_calc.columns
            else [True] * len(closes)
        )
        funding_safe_ok = (
            df_calc["funding_safe_ok"].to_list()
            if "funding_safe_ok" in df_calc.columns
            else [True] * len(closes)
        )

        n_bars = len(closes)

        for i in range(1, n_bars):
            curr_time = timestamps[i]
            close_p = closes[i]
            high_p = highs[i]
            low_p = lows[i]
            atr_val = atrs[i]

            # Mark to Market valuation
            unrealized_pnl = (close_p - entry_price) * position_qty if position_side != 0 else 0.0
            curr_equity = (
                cash + (position_qty * close_p if position_side == 1 else 0.0) + unrealized_pnl
            )
            if curr_equity > peak_equity:
                peak_equity = curr_equity
            equity_curve.append(curr_equity)

            # ---------------------------------------------------------
            # 1. Manage Active Position (Stop Loss / Take Profit)
            # ---------------------------------------------------------
            if position_side == 1:  # Active LONG
                hit_sl = low_p <= stop_loss_price
                hit_tp = high_p >= take_profit_price

                if hit_sl or hit_tp:
                    exit_price = stop_loss_price if hit_sl else take_profit_price
                    actual_exit = exit_price * (1.0 - self.slippage_fraction)
                    fee = (actual_exit * position_qty) * self.taker_fee_pct
                    pnl = (actual_exit - entry_price) * position_qty - fee
                    total_fees += fee
                    total_slippage += abs(exit_price - actual_exit) * position_qty

                    cash += (position_qty * actual_exit) - fee
                    trades.append(
                        {
                            "exit_time": curr_time,
                            "side": "LONG",
                            "pnl": pnl,
                            "reason": "SL" if hit_sl else "TP",
                            "is_win": pnl > 0,
                        }
                    )
                    position_qty = 0.0
                    position_side = 0

            elif position_side == -1:  # Active SHORT
                hit_sl = high_p >= stop_loss_price
                hit_tp = low_p <= take_profit_price

                if hit_sl or hit_tp:
                    exit_price = stop_loss_price if hit_sl else take_profit_price
                    actual_exit = exit_price * (1.0 + self.slippage_fraction)
                    fee = (actual_exit * position_qty) * self.taker_fee_pct
                    pnl = (entry_price - actual_exit) * position_qty - fee
                    total_fees += fee
                    total_slippage += abs(actual_exit - exit_price) * position_qty

                    cash += pnl
                    trades.append(
                        {
                            "exit_time": curr_time,
                            "side": "SHORT",
                            "pnl": pnl,
                            "reason": "SL" if hit_sl else "TP",
                            "is_win": pnl > 0,
                        }
                    )
                    position_qty = 0.0
                    position_side = 0

            # ---------------------------------------------------------
            # 2. Check for Fresh Entries if Flat
            # ---------------------------------------------------------
            if position_side == 0 and i >= 2:
                fast_curr = fast_emas[i]
                slow_curr = slow_emas[i]
                fast_prev = fast_emas[i - 1]
                slow_prev = slow_emas[i - 1]
                trend_curr = trend_emas[i]

                is_golden_cross = (fast_curr > slow_curr) and (fast_prev <= slow_prev)
                is_death_cross = (fast_curr < slow_curr) and (fast_prev >= slow_prev)

                trend_long_ok = (close_p > trend_curr) if use_trend_filter else True
                trend_short_ok = (close_p < trend_curr) if use_trend_filter else True
                vol_ok = vol_filter[i] if use_volatility_filter else True

                # Microstructure Gates
                cvd_ok = cvd_divergence_ok[i] if use_cvd_filter else True
                oi_ok = oi_expansion_ok[i] if use_oi_filter else True
                funding_ok = funding_safe_ok[i] if use_funding_filter else True

                signal_dir = 0
                if is_golden_cross and trend_long_ok and vol_ok and cvd_ok and oi_ok and funding_ok:
                    signal_dir = 1
                elif (
                    is_death_cross and trend_short_ok and vol_ok and cvd_ok and oi_ok and funding_ok
                ):
                    signal_dir = -1

                if signal_dir != 0:
                    stop_dist = max(atr_val * self.atr_multiplier, close_p * 0.006)
                    tp_dist = stop_dist * self.risk_reward_ratio
                    dollar_risk = curr_equity * self.risk_per_trade_pct

                    # Volatility Position Sizing capped at 40% notional
                    raw_qty = dollar_risk / stop_dist
                    max_qty = (curr_equity * 0.40) / close_p
                    qty = min(raw_qty, max_qty)

                    if qty * close_p >= 5.0 and cash >= (qty * close_p * 0.5):
                        actual_entry = (
                            close_p * (1.0 + self.slippage_fraction)
                            if signal_dir == 1
                            else close_p * (1.0 - self.slippage_fraction)
                        )
                        fee = (actual_entry * qty) * self.taker_fee_pct
                        total_fees += fee
                        total_slippage += abs(actual_entry - close_p) * qty

                        position_side = signal_dir
                        position_qty = qty
                        entry_price = actual_entry

                        if signal_dir == 1:
                            stop_loss_price = actual_entry - stop_dist
                            take_profit_price = actual_entry + tp_dist
                            cash -= (qty * actual_entry) + fee
                        else:
                            stop_loss_price = actual_entry + stop_dist
                            take_profit_price = actual_entry - tp_dist
                            cash -= fee

        final_equity = equity_curve[-1] if equity_curve else capital
        metrics = self._calculate_metrics(
            strategy_name, capital, final_equity, equity_curve, trades, total_fees, total_slippage
        )
        return metrics, df_calc

    def _prepare_indicators(
        self,
        df: pl.DataFrame,
        fast_period: int,
        slow_period: int,
        trend_period: int,
    ) -> pl.DataFrame:
        """Vectorized indicator & microstructure calculations via Polars."""
        res = df.with_columns(
            [
                pl.col("close").ewm_mean(span=fast_period, adjust=False).alias("ema_fast"),
                pl.col("close").ewm_mean(span=slow_period, adjust=False).alias("ema_slow"),
                pl.col("close").ewm_mean(span=trend_period, adjust=False).alias("ema_trend"),
            ]
        )

        # True Range and ATR
        res = res.with_columns([pl.col("close").shift(1).alias("prev_close")])
        res = res.with_columns(
            [
                pl.max_horizontal(
                    [
                        pl.col("high") - pl.col("low"),
                        (pl.col("high") - pl.col("prev_close")).abs(),
                        (pl.col("low") - pl.col("prev_close")).abs(),
                    ]
                )
                .fill_null(0.0)
                .alias("tr")
            ]
        )
        res = res.with_columns(
            [
                pl.col("tr").rolling_mean(window_size=14).fill_null(0.0).alias("atr"),
                pl.col("tr").rolling_mean(window_size=70).fill_null(1.0).alias("atr_baseline"),
            ]
        )

        # Volatility Spike Filter
        res = res.with_columns(
            [(pl.col("atr") <= (pl.col("atr_baseline") * 1.8)).alias("vol_filter_ok")]
        )

        # -------------------------------------------------------------
        # Microstructure Pillar 1: Cumulative Volume Delta (CVD)
        # -------------------------------------------------------------
        if "taker_buy_volume" in res.columns:
            # Volume Delta = Buyer Volume - Seller Volume
            res = res.with_columns(
                [
                    (
                        pl.col("taker_buy_volume") - (pl.col("volume") - pl.col("taker_buy_volume"))
                    ).alias("volume_delta")
                ]
            )
            res = res.with_columns([pl.col("volume_delta").cum_sum().alias("cvd")])
            res = res.with_columns([pl.col("cvd").rolling_mean(window_size=14).alias("cvd_ma")])
            # CVD confirms trend if CVD is expanding in direction of price
            res = res.with_columns([(pl.col("cvd") >= pl.col("cvd_ma")).alias("cvd_divergence_ok")])
        else:
            res = res.with_columns([pl.lit(True).alias("cvd_divergence_ok")])

        # -------------------------------------------------------------
        # Microstructure Pillar 2: Open Interest (OI) Expansion
        # -------------------------------------------------------------
        if "open_interest" in res.columns:
            res = res.with_columns(
                [pl.col("open_interest").rolling_mean(window_size=14).alias("oi_ma")]
            )
            # Healthy trend requires institutional capital commitment (OI >= average)
            res = res.with_columns(
                [(pl.col("open_interest") >= (pl.col("oi_ma") * 0.95)).alias("oi_expansion_ok")]
            )
        else:
            res = res.with_columns([pl.lit(True).alias("oi_expansion_ok")])

        # -------------------------------------------------------------
        # Microstructure Pillar 3: Funding Rate Overcrowding Shield
        # -------------------------------------------------------------
        if "funding_rate" in res.columns:
            # Dangerous if funding rate exceeds 0.05% (5 bps per 8h = extreme leverage crowd)
            res = res.with_columns(
                [(pl.col("funding_rate").abs() <= 0.0005).alias("funding_safe_ok")]
            )
        else:
            res = res.with_columns([pl.lit(True).alias("funding_safe_ok")])

        return res

    def _calculate_metrics(
        self,
        strategy_name: str,
        capital: float,
        final_equity: float,
        equity_curve: list[float],
        trades: list[dict[str, Any]],
        total_fees: float,
        total_slippage: float,
    ) -> BacktestMetrics:
        """Calculates risk-adjusted returns and institutional metrics."""
        total_return_pct = ((final_equity - capital) / capital) * 100.0

        eq = np.array(equity_curve) if equity_curve else np.array([capital])
        running_max = np.maximum.accumulate(eq)
        drawdowns = (eq - running_max) / running_max
        max_drawdown_pct = abs(float(np.min(drawdowns))) * 100.0

        returns = np.diff(eq) / eq[:-1] if len(eq) > 1 else np.array([0.0])
        std = np.std(returns) if len(returns) > 1 else 0.0
        sharpe = float((np.mean(returns) / std) * np.sqrt(365 * 24)) if std > 1e-9 else 0.0

        downside_returns = returns[returns < 0]
        downside_std = np.std(downside_returns) if len(downside_returns) > 1 else 0.0
        sortino = (
            float((np.mean(returns) / downside_std) * np.sqrt(365 * 24))
            if downside_std > 1e-9
            else 0.0
        )

        total_trades = len(trades)
        wins = [t for t in trades if t["is_win"]]
        losses = [t for t in trades if not t["is_win"]]
        win_count = len(wins)
        loss_count = len(losses)
        win_rate = (win_count / total_trades * 100.0) if total_trades > 0 else 0.0

        gross_profit = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))
        profit_factor = (
            (gross_profit / gross_loss) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
        )

        n_bars = max(1, len(equity_curve))
        years = n_bars / (365.0 * 24.0)
        annualized = (
            ((final_equity / capital) ** (1.0 / max(0.01, years)) - 1.0) * 100.0
            if years > 0 and final_equity > 0
            else 0.0
        )

        return BacktestMetrics(
            strategy_name=strategy_name,
            initial_capital=capital,
            final_equity=round(final_equity, 2),
            total_return_pct=round(total_return_pct, 2),
            annualized_return_pct=round(annualized, 2),
            sharpe_ratio=round(sharpe, 2),
            sortino_ratio=round(sortino, 2),
            max_drawdown_pct=round(max_drawdown_pct, 2),
            profit_factor=round(profit_factor, 2),
            win_rate_pct=round(win_rate, 2),
            total_trades=total_trades,
            winning_trades=win_count,
            losing_trades=loss_count,
            total_fees_paid=round(total_fees, 4),
            total_slippage_paid=round(total_slippage, 4),
        )
