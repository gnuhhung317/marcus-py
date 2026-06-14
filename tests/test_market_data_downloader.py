from __future__ import annotations

import types
from datetime import datetime, timezone
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import quant_signal_sdk.ccxt_client as ccxt_client
from quant_signal_sdk.ccxt_client import CCXTClient, ExchangeDataDownloader
from quant_signal_sdk.data_provider import CcxtDataProvider, ExchangeDataProvider


class _FakeExchange:
    def __init__(self, exchange_id: str, config: dict | None = None) -> None:
        self.exchange_id = exchange_id
        self.config = config or {}
        self.rateLimit = 0
        self.has = {"fetchFundingRateHistory": True}
        self.markets = {
            "BTC/USDT": {"symbol": "BTC/USDT", "quote": "USDT", "active": True, "spot": True, "type": "spot"},
            "BTC/USDT:USDT": {
                "symbol": "BTC/USDT:USDT",
                "quote": "USDT",
                "active": True,
                "swap": True,
                "linear": True,
                "type": "swap",
            },
            "ETH/BTC": {"symbol": "ETH/BTC", "quote": "BTC", "active": False, "spot": True, "type": "spot"},
        }
        self.ohlcv_calls: list[tuple[str, str, int | None, int]] = []
        self.funding_calls: list[tuple[str, int | None, int]] = []

    def load_markets(self) -> dict[str, dict]:
        return self.markets

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1m", since: int | None = None, limit: int = 100):
        self.ohlcv_calls.append((symbol, timeframe, since, limit))
        rows = self._ohlcv_rows()
        filtered = [row for row in rows if since is None or row[0] >= since]
        return filtered[:limit]

    def fetch_funding_rate_history(self, symbol: str, since: int | None = None, limit: int = 1000):
        self.funding_calls.append((symbol, since, limit))
        rows = self._funding_rows()
        filtered = [row for row in rows if since is None or row["timestamp"] >= since]
        return filtered[:limit]

    def _ohlcv_rows(self) -> list[list[float]]:
        return [
            [0, 1.0, 2.0, 0.5, 1.5, 10.0],
            [3_600_000, 1.5, 2.5, 1.0, 2.0, 11.0],
            [7_200_000, 2.0, 3.0, 1.5, 2.5, 12.0],
            [10_800_000, 2.5, 3.5, 2.0, 3.0, 13.0],
        ]

    def _funding_rows(self) -> list[dict[str, float]]:
        return [
            {"timestamp": 0, "fundingRate": 0.0001},
            {"timestamp": 3_600_000, "fundingRate": 0.0002},
            {"timestamp": 7_200_000, "fundingRate": 0.0003},
        ]


def _patch_ccxt(monkeypatch) -> None:
    monkeypatch.setattr(
        ccxt_client,
        "ccxt",
        types.SimpleNamespace(
            binance=lambda config=None: _FakeExchange("binance", config),
            kraken=lambda config=None: _FakeExchange("kraken", config),
        ),
    )


def test_downloader_supports_multiple_exchange_ids(monkeypatch):
    _patch_ccxt(monkeypatch)

    binance = ExchangeDataDownloader("binance", market_type="swap")
    kraken = ExchangeDataDownloader("kraken")

    assert binance.exchange.exchange_id == "binance"
    assert kraken.exchange.exchange_id == "kraken"
    assert binance.list_symbols(quote_asset="USDT", market_type="swap") == ["BTC/USDT:USDT"]
    assert kraken.normalize_symbol("BTC/USDT:USDT") == "BTCUSDT_USDT"


def test_ohlcv_pagination_and_dataframe_sync(monkeypatch):
    _patch_ccxt(monkeypatch)

    downloader = ExchangeDataDownloader("binance")
    rows = downloader.fetch_ohlcv_rows("BTC/USDT:USDT", timeframe="1h", since=0, limit=2, paginate=True)
    assert [row[0] for row in rows] == [0, 3_600_000, 7_200_000, 10_800_000]

    frame = downloader.fetch_ohlcv_frame("BTC/USDT:USDT", timeframe="1h", since=0, limit=2, paginate=True)
    assert list(frame.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert frame["timestamp"].iloc[0] == pd.Timestamp("1970-01-01T00:00:00Z")
    assert frame["close"].iloc[-1] == 3.0


def test_sync_and_fill_gaps(monkeypatch):
    _patch_ccxt(monkeypatch)

    downloader = ExchangeDataDownloader("binance")
    existing = pd.DataFrame(
        [
            {
                "timestamp": pd.Timestamp("1970-01-01T00:00:00Z"),
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 10.0,
            },
            {
                "timestamp": pd.Timestamp("1970-01-01T03:00:00Z"),
                "open": 2.5,
                "high": 3.5,
                "low": 2.0,
                "close": 3.0,
                "volume": 13.0,
            },
        ]
    )

    synced = downloader.sync_ohlcv("BTC/USDT:USDT", existing_df=existing, timeframe="1h", discover_start=False)
    assert list(synced["timestamp"]) == [
        pd.Timestamp("1970-01-01T00:00:00Z"),
        pd.Timestamp("1970-01-01T01:00:00Z"),
        pd.Timestamp("1970-01-01T02:00:00Z"),
        pd.Timestamp("1970-01-01T03:00:00Z"),
    ]


def test_funding_history_normalization(monkeypatch):
    _patch_ccxt(monkeypatch)

    downloader = ExchangeDataDownloader("binance")
    frame = downloader.fetch_funding_rate_history("BTC/USDT:USDT", since=0, limit=2, paginate=True)

    assert list(frame.columns) == ["timestamp", "funding_rate"]
    assert list(frame["funding_rate"]) == [0.0001, 0.0002, 0.0003]
    assert frame["timestamp"].iloc[0] == pd.Timestamp("1970-01-01T00:00:00Z")


def test_compatibility_wrappers_preserve_public_api(monkeypatch):
    _patch_ccxt(monkeypatch)

    raw_client = CCXTClient("binance")
    provider = CcxtDataProvider("binance")
    generic_provider = ExchangeDataProvider("binance")

    raw_rows = raw_client.fetch_ohlcv("BTC/USDT", timeframe="1h", limit=2)
    assert raw_rows[0][0] == 0

    provider_frame = provider.fetch_ohlcv("BTC/USDT", timeframe="1h", limit=2)
    assert isinstance(provider_frame.index, pd.DatetimeIndex)
    assert provider_frame["close"].iloc[0] == 1.5

    generic_frame = generic_provider.fetch_ohlcv("BTC/USDT", timeframe="1h", limit=2)
    assert isinstance(generic_frame.index, pd.DatetimeIndex)
    assert generic_frame["close"].iloc[0] == 1.5
