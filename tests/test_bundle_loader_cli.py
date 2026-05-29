from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from quant_signal_sdk.cli import _merge_bundle_streams, _normalize_stream_frame
from quant_signal_sdk.data_loader import BundleLoader
from quant_signal_sdk.core_strategy import FundingArbitrageConfig, FundingArbitrageStrategy
from quant_signal_sdk.runtime.interfaces import MarketEvent, PortfolioContext
from quant_signal_sdk.runtime.adapters import DataFrameFeed


def test_bundle_loader_resolves_relative_paths_against_manifest_directory(tmp_path, monkeypatch) -> None:
    bundle_dir = tmp_path / "bundle_v1"
    data_dir = bundle_dir / "ohlcv"
    data_dir.mkdir(parents=True)

    manifest = {
        "bundle_version": "1.0.0",
        "universe": [
            {
                "symbol": "BTCUSDT",
                "data_paths": {
                    "ohlcv": "ohlcv/BTC.parquet",
                },
            }
        ],
    }
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (bundle_dir / "ohlcv" / "BTC.parquet").write_bytes(b"")

    expected_path = (bundle_dir / "ohlcv" / "BTC.parquet").resolve()
    expected_frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-05-28T09:00:00Z"]),
            "open": [100.0],
        }
    )

    def fake_read_parquet(path, *args, **kwargs):
        assert Path(path).resolve() == expected_path
        return expected_frame.copy()

    monkeypatch.setattr(pd, "read_parquet", fake_read_parquet)

    loader = BundleLoader(bundle_dir)
    raw_data = loader.load_raw_asset_data("BTCUSDT")

    assert list(raw_data.keys()) == ["ohlcv"]
    assert raw_data["ohlcv"].equals(expected_frame)


def test_normalize_stream_frame_requires_explicit_timestamp_or_datetime_index() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["BTCUSDT"],
            "open": [100.0],
            "close": [101.0],
        }
    )

    with pytest.raises(ValueError, match="timestamp"):
        _normalize_stream_frame(frame, timestamp_column="timestamp")


def test_normalize_stream_frame_accepts_datetime_index() -> None:
    frame = pd.DataFrame(
        {
            "open": [100.0, 101.0],
            "close": [101.0, 102.0],
        },
        index=pd.to_datetime(["2026-05-28T09:00:00Z", "2026-05-28T09:15:00Z"]),
    )

    normalized = _normalize_stream_frame(frame, timestamp_column="timestamp")

    assert "timestamp" in normalized.columns
    assert normalized["timestamp"].iloc[0] == pd.Timestamp("2026-05-28T09:00:00Z")


def test_normalize_stream_frame_keeps_existing_timestamp_column() -> None:
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-05-28T09:00:00Z", "2026-05-28T09:15:00Z"]),
            "symbol": ["BTCUSDT", "BTCUSDT"],
            "open": [100.0, 101.0],
        }
    )

    normalized = _normalize_stream_frame(frame, timestamp_column="timestamp")

    assert list(normalized.columns) == ["timestamp", "symbol", "open"]
    assert normalized["timestamp"].iloc[0] == pd.Timestamp("2026-05-28T09:00:00Z")
    assert normalized["symbol"].iloc[0] == "BTCUSDT"


def test_merge_bundle_streams_applies_manifest_column_mapping_and_ffill() -> None:
    raw_asset_data = {
        "ohlcv": pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2026-05-28T09:00:00Z", "2026-05-28T09:15:00Z"]),
                "ohlcv__close": [100.0, 101.0],
            }
        ),
        "funding": pd.DataFrame(
            {
                "timestamp": pd.to_datetime(["2026-05-28T09:10:00Z"]),
                "funding__fundingRate": [0.0001],
            }
        ),
    }

    merged = _merge_bundle_streams(
        raw_asset_data,
        timestamp_column="timestamp",
        column_mapping={
            "ohlcv__close": "spot_close",
            "funding__fundingRate": "funding_rate",
        },
    )

    assert list(merged.columns) == ["timestamp", "spot_close", "funding_rate"]
    assert merged["spot_close"].tolist() == [100.0, 101.0]
    assert merged["funding_rate"].iloc[1] == 0.0001


def test_funding_arbitrage_strategy_consumes_flat_payload() -> None:
    strategy = FundingArbitrageStrategy(bot_id="bot-123", config=FundingArbitrageConfig(open_funding_threshold=0.0))
    event = MarketEvent(
        timestamp=datetime(2026, 5, 28, 9, 15, tzinfo=timezone.utc),
        payload={
            "spot_symbol": "BTCUSDT",
            "futures_symbol": "BTCUSDT-PERP",
            "spot_close": 60000.0,
            "futures_close": 60010.0,
            "funding_rate": 0.0002,
        },
    )

    signals = strategy.on_event(event, PortfolioContext())

    assert len(signals) == 2
    assert signals[0].symbol == "BTCUSDT"
    assert signals[1].symbol == "BTCUSDTPERP"
    assert signals[0].metadata["funding_rate"] == 0.0002
    assert signals[1].metadata["funding_rate"] == 0.0002


def test_dataframe_feed_uses_records_and_preserves_payload_fields() -> None:
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-05-28T09:00:00Z"]),
            "count": [3],
            "price": [101.5],
            "active": [True],
        }
    )

    feed = DataFrameFeed(frame)
    events = list(feed.stream())

    assert len(events) == 1
    assert events[0].timestamp == datetime(2026, 5, 28, 9, 0, tzinfo=timezone.utc)
    assert events[0].payload == {"count": 3, "price": 101.5, "active": True}
