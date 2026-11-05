"""
CLI Runner for MultiQuant Historical Backtesting on Binance BTC Data.
Supports two benchmark modes:
1. 5m-year (Default): Full-Year 2026 5-Minute BTCUSDT Benchmark across 5 Orthogonal Quant Presets (~89,050 candles).
2. 3tier: 1-Hour 3-Tier Microstructure Benchmark (1,000 candles).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import aiohttp
import polars as pl

from src.layer2_alpha.backtest import BacktestEngine, BacktestMetrics

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("multiquant.run_backtest")


async def fetch_or_load_full_year_5m(
    symbol: str = "BTCUSDT",
    cache_path: Path = PROJECT_ROOT / "data" / "btcusdt_5m_2026.parquet",
    force_refresh: bool = False,
) -> pl.DataFrame:
    """Loads full-year 2026 5m candles from local Parquet cache or fetches concurrently from Binance."""
    if cache_path.exists() and not force_refresh:
        df = pl.read_parquet(cache_path)
        logger.info(
            "Loaded %d cached 5m candles for %s from %s (%s -> %s)",
            len(df),
            symbol,
            cache_path.name,
            df["timestamp"][0],
            df["timestamp"][-1],
        )
        return df

    start_ts = int(datetime(2026, 1, 1, 0, 0, 0).timestamp() * 1000)
    end_ts = int(time.time() * 1000)
    step_ms = 1000 * 5 * 60 * 1000
    starts = list(range(start_ts, end_ts, step_ms))
    logger.info("Downloading %d batches of 1,000 5m candles from Binance API...", len(starts))

    sem = asyncio.Semaphore(15)

    async def fetch_batch(session: aiohttp.ClientSession, st: int) -> list[list[str | float | int]]:
        url = (
            f"https://api.binance.com/api/v3/klines?symbol={symbol}"
            f"&interval=5m&limit=1000&startTime={st}"
        )
        async with sem:
            for _ in range(3):
                try:
                    async with session.get(url, timeout=15) as resp:
                        if resp.status == 200:
                            data: list[list[str | float | int]] = await resp.json()
                            return data
                except Exception:
                    await asyncio.sleep(0.5)
            return []

    async with aiohttp.ClientSession() as session:
        batches = await asyncio.gather(*(fetch_batch(session, st) for st in starts))

    rows = []
    for b in batches:
        for k in b:
            raw_ts = int(k[0])
            rows.append(
                {
                    "timestamp_ms": raw_ts,
                    "timestamp": datetime.utcfromtimestamp(raw_ts / 1000.0),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                    "taker_buy_volume": float(k[9]),
                }
            )

    df = pl.DataFrame(rows).unique(subset=["timestamp_ms"]).sort("timestamp_ms")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(cache_path)
    logger.info("Saved %d 5m candles to %s", len(df), cache_path)
    return df


def fetch_comprehensive_binance_data(
    symbol: str = "BTCUSDT",
    interval: str = "1h",
    total_candles: int = 1000,
) -> pl.DataFrame:
    """Fetches OHLCV, Taker Buy Volume, Open Interest, and Funding Rate from Binance."""
    logger.info(
        "Fetching %d candles with Microstructure data for %s (%s)...",
        total_candles,
        symbol,
        interval,
    )

    kline_url = (
        f"https://api.binance.com/api/v3/klines?symbol={symbol}"
        f"&interval={interval}&limit={min(total_candles, 1000)}"
    )
    req = urllib.request.Request(kline_url, headers={"User-Agent": "MultiQuant/1.0"})
    with urllib.request.urlopen(req, timeout=15) as res:
        raw_klines = json.loads(res.read().decode("utf-8"))

    oi_url = (
        f"https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}"
        f"&period={interval}&limit=500"
    )
    oi_map: dict[int, float] = {}
    try:
        req_oi = urllib.request.Request(oi_url, headers={"User-Agent": "MultiQuant/1.0"})
        with urllib.request.urlopen(req_oi, timeout=15) as res_oi:
            raw_oi = json.loads(res_oi.read().decode("utf-8"))
            for item in raw_oi:
                ts_key = int(item["timestamp"]) // (3600 * 1000) * (3600 * 1000)
                oi_map[ts_key] = float(item["sumOpenInterest"])
    except Exception as e:
        logger.warning("Could not fetch Binance Open Interest history: %s", e)

    funding_url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=500"
    funding_map: dict[int, float] = {}
    try:
        req_f = urllib.request.Request(funding_url, headers={"User-Agent": "MultiQuant/1.0"})
        with urllib.request.urlopen(req_f, timeout=15) as res_f:
            raw_f = json.loads(res_f.read().decode("utf-8"))
            for item in raw_f:
                ts_key = int(item["fundingTime"]) // (3600 * 1000) * (3600 * 1000)
                funding_map[ts_key] = float(item["fundingRate"])
    except Exception as e:
        logger.warning("Could not fetch Binance Funding Rate history: %s", e)

    rows = []
    last_known_oi = 85000.0
    last_known_funding = 0.0001

    for k in raw_klines:
        raw_ts = int(k[0])
        hour_ts = raw_ts // (3600 * 1000) * (3600 * 1000)
        if hour_ts in oi_map:
            last_known_oi = oi_map[hour_ts]
        if hour_ts in funding_map:
            last_known_funding = funding_map[hour_ts]

        rows.append(
            {
                "timestamp": datetime.fromtimestamp(raw_ts / 1000.0),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "taker_buy_volume": float(k[9]),
                "open_interest": last_known_oi,
                "funding_rate": last_known_funding,
            }
        )

    return pl.DataFrame(rows)


def print_5m_presets_table(
    metrics_list: list[BacktestMetrics],
    df: pl.DataFrame,
    symbol: str,
) -> None:
    """Prints a comprehensive 5-Preset comparative benchmark table for Full-Year 5m BTC."""
    start_p = float(df["close"][0])
    end_p = float(df["close"][-1])
    high_p = float(df["high"].max())  # type: ignore[arg-type]
    low_p = float(df["low"].min())  # type: ignore[arg-type]
    bh_ret = (end_p - start_p) / start_p * 100.0

    print("\n" + "=" * 122)
    print(
        f"🏛️ MULTIQUANT FULL-YEAR 5-MINUTE PRESET BENCHMARK: {symbol} "
        f"({len(df):,} bars | {df['timestamp'][0]} -> {df['timestamp'][-1]})"
    )
    print(
        f"📉 Benchmark BTC Buy & Hold Return: {bh_ret:+.2f}% "
        f"(Start: ${start_p:,.2f} | High: ${high_p:,.2f} | Low: ${low_p:,.2f} | End: ${end_p:,.2f})"
    )
    print("=" * 122)
    header = (
        f"{'Preset / Strategy Combo':<34} | {'Trades':>6} | {'Win Rate':>8} | "
        f"{'Profit Factor':>13} | {'Net Return':>10} | {'Sharpe':>6} | "
        f"{'Max DD':>7} | {'Fees+Slip':>9} | {'Final Eq':>8}"
    )
    print(header)
    print("-" * 122)

    for m in metrics_list:
        drag = m.total_fees_paid + m.total_slippage_paid
        print(
            f"{m.strategy_name:<34} | {m.total_trades:>6d} | {m.win_rate_pct:>7.1f}% | "
            f"{m.profit_factor:>13.2f} | {m.total_return_pct:>+9.2f}% | {m.sharpe_ratio:>6.2f} | "
            f"{m.max_drawdown_pct:>6.2f}% | ${drag:>8.2f} | ${m.final_equity:>7.2f}"
        )
    print("=" * 122)


def main() -> None:
    parser = argparse.ArgumentParser(description="MultiQuant Institutional Backtesting")
    parser.add_argument("--symbol", default="BTCUSDT", help="Pair to backtest (default: BTCUSDT)")
    parser.add_argument(
        "--mode",
        choices=["5m-year", "3tier"],
        default="5m-year",
        help="Benchmark mode: 5m-year (Full Year 5m Presets) or 3tier (1h 3-Tier)",
    )
    parser.add_argument("--capital", type=float, default=50.0, help="Initial capital in USD")
    parser.add_argument("--refresh", action="store_true", help="Force re-download of 5m cache")
    args = parser.parse_args()

    engine = BacktestEngine(initial_capital=args.capital)

    if args.mode == "5m-year":
        df_5m = asyncio.run(
            fetch_or_load_full_year_5m(symbol=args.symbol, force_refresh=args.refresh)
        )
        df_prep = engine.prepare_5m_preset_signals(df_5m)

        presets = [
            ("P1_RETAIL_EMA_5M", "P1: Retail EMA 9/21 (Baseline)", 0.006, 2.0, 1.5, 1),
            ("P2_MINIMALIST_3PILLAR", "P2: Minimalist 3-Pillar Structure", 0.015, 4.5, 3.0, 72),
            ("P3_VOLATILITY_SQUEEZE", "P3: Volatility Squeeze (★ OPTIMAL)", 0.015, 4.5, 3.0, 72),
            ("P4_SQUEEZE_ADX_REGIME", "P4: Squeeze + ADX Trend Regime", 0.015, 4.5, 3.0, 72),
            ("P5_INSTITUTIONAL_ELITE", "P5: Institutional Orderflow + 1h CVD", 0.015, 4.5, 3.0, 72),
        ]

        results: list[BacktestMetrics] = []
        for col, label, min_stop, atr_m, rr, cd in presets:
            m = engine.run_5m_preset(
                df_prep,
                preset_col=col,
                strategy_name=label,
                min_stop_pct=min_stop,
                atr_mult=atr_m,
                rr_ratio=rr,
                cooldown_bars=cd,
            )
            results.append(m)

        print_5m_presets_table(results, df_5m, args.symbol)
    else:
        df_1h = fetch_comprehensive_binance_data(symbol=args.symbol, interval="1h", total_candles=1000)
        m_t1, _ = engine.run(
            df_1h,
            strategy_name="Tier 1: Baseline EMA",
            use_trend_filter=False,
            use_volatility_filter=False,
        )
        m_t2, _ = engine.run(
            df_1h,
            strategy_name="Tier 2: Quant Regime",
            use_trend_filter=True,
            use_volatility_filter=True,
        )
        m_t3, _ = engine.run(
            df_1h,
            strategy_name="Tier 3: Institutional Micro",
            use_trend_filter=True,
            use_volatility_filter=True,
            use_cvd_filter=True,
            use_oi_filter=True,
            use_funding_filter=True,
        )
        print_5m_presets_table([m_t1, m_t2, m_t3], df_1h, args.symbol)


if __name__ == "__main__":
    main()
