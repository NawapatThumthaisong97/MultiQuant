import json

import pytest

from src.core.models import TickData
from src.layer1_connectivity.feed_handler import MarketDataParser
from src.layer1_connectivity.websocket_client import BinanceWebSocketFeed


def test_parse_binance_book_ticker():
    payload = {
        "u": 400900217,
        "s": "BTCUSDT",
        "b": "64230.50",
        "B": "1.25",
        "a": "64231.00",
        "A": "0.85",
    }
    tick = MarketDataParser.parse_binance_book_ticker(payload)
    assert tick is not None
    assert tick.symbol == "BTCUSDT"
    assert tick.bid_price == 64230.50
    assert tick.ask_price == 64231.00
    assert tick.mid_price == 64230.75
    assert tick.spread == 0.50


def test_parse_binance_trade():
    payload = {
        "e": "trade",
        "E": 1672531199000,
        "s": "ETHUSDT",
        "t": 123456,
        "p": "3450.25",
        "q": "2.5",
        "T": 1672531199000,
        "m": False,
    }
    tick = MarketDataParser.parse_binance_trade(payload)
    assert tick is not None
    assert tick.symbol == "ETHUSDT"
    assert tick.last_price == 3450.25
    assert tick.volume == 2.5


def test_parse_binance_kline():
    payload = {
        "e": "kline",
        "s": "SOLUSDT",
        "k": {
            "t": 1672531200000,
            "T": 1672531259999,
            "s": "SOLUSDT",
            "i": "1m",
            "o": "145.10",
            "c": "146.50",
            "h": "147.00",
            "l": "144.80",
            "v": "3500.0",
            "x": True,
        },
    }
    bar = MarketDataParser.parse_binance_kline(payload)
    assert bar is not None
    assert bar.symbol == "SOLUSDT"
    assert bar.open == 145.10
    assert bar.high == 147.00
    assert bar.low == 144.80
    assert bar.close == 146.50
    assert bar.volume == 3500.0
    assert bar.timeframe == "1m"


def test_parse_invalid_payload_returns_none():
    assert MarketDataParser.parse_binance_book_ticker({"broken": "data"}) is None
    assert MarketDataParser.parse_binance_trade({"broken": "data"}) is None
    assert MarketDataParser.parse_binance_kline({"broken": "data"}) is None


def test_binance_feed_stream_url_generation():
    feed_single = BinanceWebSocketFeed(symbols=["BTCUSDT"], stream_type="bookTicker")
    assert feed_single._build_stream_url() == "wss://stream.binance.com:9443/ws/btcusdt@bookTicker"

    feed_multi = BinanceWebSocketFeed(symbols=["BTCUSDT", "ETHUSDT"], stream_type="trade")
    assert "btcusdt@trade" in feed_multi._build_stream_url()
    assert "ethusdt@trade" in feed_multi._build_stream_url()


@pytest.mark.asyncio
async def test_feed_handle_message_dispatch():
    received_ticks = []

    async def mock_tick_handler(tick: TickData):
        received_ticks.append(tick)

    feed = BinanceWebSocketFeed(symbols=["BTCUSDT"], on_tick=mock_tick_handler)

    raw_trade_msg = json.dumps(
        {
            "e": "trade",
            "s": "BTCUSDT",
            "p": "65000.0",
            "q": "0.1",
            "T": 1672531200000,
        }
    )

    await feed._handle_message(raw_trade_msg)
    assert len(received_ticks) == 1
    assert received_ticks[0].symbol == "BTCUSDT"
    assert received_ticks[0].last_price == 65000.0
