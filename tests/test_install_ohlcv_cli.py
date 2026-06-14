from __future__ import annotations

from pathlib import Path

import pandas as pd

import quant_signal_sdk.cli as cli


class _FakeDownloader:
    instances: list["_FakeDownloader"] = []

    def __init__(self, exchange_id: str = "binance", market_type: str | None = None) -> None:
        self.exchange_id = exchange_id
        self.market_type = market_type
        self.calls: list[dict[str, object]] = []
        _FakeDownloader.instances.append(self)

    def get_clean_symbol(self, symbol: str) -> str:
        return symbol.replace("/", "").replace(":", "_")

    def list_symbols(self, *, market_type: str | None = None):
        return ["BTC/USDT", "ETH/USDT"]

    def sync_ohlcv(
        self,
        symbol: str,
        existing_df: pd.DataFrame | None = None,
        timeframe: str = "1h",
        *,
        since=None,
        until=None,
        discover_start: bool = True,
        fill_gaps: bool = False,
    ) -> pd.DataFrame:
        self.calls.append(
            {
                "symbol": symbol,
                "existing_rows": 0 if existing_df is None else len(existing_df),
                "timeframe": timeframe,
                "since": since,
                "until": until,
                "discover_start": discover_start,
                "fill_gaps": fill_gaps,
            }
        )
        return pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp("2024-01-01T00:00:00Z"),
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "volume": 123.0,
                }
            ]
        )


def test_help_lists_install_ohlcv() -> None:
    help_text = cli.build_parser().format_help()

    assert "install-ohlcv" in help_text


def test_install_data_alias_parses() -> None:
    args = cli.build_parser().parse_args(["install-data", "--exchange", "binance", "--symbols", "BTC/USDT"])

    assert args.command == "install-data"
    assert args.exchange == "binance"
    assert args.symbols == "BTC/USDT"


def test_backtest_and_upload_commands_still_parse() -> None:
    backtest_args = cli.build_parser().parse_args(["backtest", "--data-csv", "candles.csv"])
    upload_args = cli.build_parser().parse_args(["upload", "results", "--bot-id", "bot-1", "--api-key", "key-1"])

    assert backtest_args.command == "backtest"
    assert upload_args.command == "upload"


def test_install_ohlcv_writes_expected_parquet(monkeypatch, tmp_path) -> None:
    _FakeDownloader.instances.clear()
    monkeypatch.setattr(cli, "ExchangeDataDownloader", _FakeDownloader)

    written: dict[str, object] = {}

    def fake_to_parquet(self, path, index=False, **kwargs):
        written["path"] = Path(path)
        written["frame"] = self.copy(deep=True)
        written["index"] = index
        Path(path).write_text("fake parquet payload", encoding="utf-8")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", fake_to_parquet, raising=True)

    exit_code = cli.main(
        [
            "install-ohlcv",
            "--exchange",
            "binance",
            "--symbols",
            "BTC/USDT",
            "--data-root",
            str(tmp_path),
            "--since",
            "2024-01-01",
            "--until",
            "2024-01-02",
            "--fill-gaps",
        ]
    )

    assert exit_code == 0
    assert len(_FakeDownloader.instances) == 1
    downloader = _FakeDownloader.instances[0]
    assert downloader.exchange_id == "binance"
    assert downloader.market_type is None
    assert downloader.calls[0]["symbol"] == "BTC/USDT"
    assert downloader.calls[0]["discover_start"] is False
    assert downloader.calls[0]["fill_gaps"] is True

    output_path = Path(tmp_path) / "ohlcv" / "BTCUSDT.parquet"
    assert output_path.exists()
    assert written["path"] == output_path

    frame = written["frame"]
    assert list(frame.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert frame["close"].iloc[0] == 100.5
