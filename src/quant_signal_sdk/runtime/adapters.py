from __future__ import annotations

import csv
from datetime import datetime, timezone
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TYPE_CHECKING

from ..client import QuantSignalClient
from ..models import SignalPayload
from .interfaces import MarketEvent

if TYPE_CHECKING:
    import pandas as pd


class LiveRESTFeed:
    def __init__(
        self,
        *,
        spot_symbol: str,
        futures_symbol: str | None = None,
        exchange_id: str = "binance",
        timeframe: str = "1h",
        interval_seconds: float = 60.0,
        ohlcv_limit: int = 200,
        funding_limit: int = 8,
        config: dict[str, Any] | None = None,
    ) -> None:
        self._spot_symbol = spot_symbol
        self._futures_symbol = futures_symbol or spot_symbol
        self._exchange_id = exchange_id
        self._timeframe = timeframe
        self._interval_seconds = interval_seconds
        self._ohlcv_limit = ohlcv_limit
        self._funding_limit = funding_limit
        self._config = dict(config or {})
        self._exchange = self._build_exchange()

    def stream(self) -> Iterator[MarketEvent]:
        import time

        while True:
            spot_ohlcv = self._exchange.fetch_ohlcv(self._spot_symbol, timeframe=self._timeframe, limit=self._ohlcv_limit)
            futures_ohlcv = self._exchange.fetch_ohlcv(self._futures_symbol, timeframe=self._timeframe, limit=self._ohlcv_limit)

            funding_history: list[Any] = []
            if hasattr(self._exchange, "fetch_funding_rate_history"):
                funding_history = self._exchange.fetch_funding_rate_history(self._futures_symbol, limit=self._funding_limit)

            timestamp = self._latest_timestamp(spot_ohlcv, futures_ohlcv)
            payload = {
                "spot": {
                    "symbol": self._spot_symbol,
                    "ohlcv": spot_ohlcv,
                    "latest": spot_ohlcv[-1] if spot_ohlcv else None,
                },
                "futures": {
                    "symbol": self._futures_symbol,
                    "ohlcv": futures_ohlcv,
                    "latest": futures_ohlcv[-1] if futures_ohlcv else None,
                },
                "funding": {
                    "symbol": self._futures_symbol,
                    "history": funding_history,
                    "latest": funding_history[-1] if funding_history else None,
                },
            }
            yield MarketEvent(timestamp=timestamp, payload=payload)
            time.sleep(self._interval_seconds)

    def _build_exchange(self) -> Any:
        try:
            import ccxt  # type: ignore
        except Exception as exc:  # pragma: no cover - import guard
            raise ImportError("LiveRESTFeed requires ccxt. Install quant-signal-sdk[market-data].") from exc

        exchange_class = getattr(ccxt, self._exchange_id)
        exchange = exchange_class(self._config)
        if hasattr(exchange, "load_markets"):
            exchange.load_markets()
        return exchange

    def _latest_timestamp(self, spot_ohlcv: list[Any], futures_ohlcv: list[Any]) -> datetime:
        latest_ms = None
        for rows in (spot_ohlcv, futures_ohlcv):
            if rows:
                candidate = rows[-1][0]
                if latest_ms is None or candidate > latest_ms:
                    latest_ms = candidate
        if latest_ms is None:
            return datetime.now(timezone.utc)
        return datetime.fromtimestamp(float(latest_ms) / 1000.0, tz=timezone.utc)


class LiveHTTPDispatcher:
    def __init__(self, client: QuantSignalClient, bot_api_key: str | None = None) -> None:
        self._client = client
        self._bot_api_key = bot_api_key

    def dispatch(self, signal: SignalPayload) -> None:
        payload = signal.model_dump(mode="json", by_alias=True, exclude_none=True)
        if self._bot_api_key:
            self._client.send_payload_with_bot_key(payload, bot_api_key=self._bot_api_key)
            return
        self._client.send_signal(signal)


class ParquetReplayFeed:
    def __init__(self, dataframe: "pd.DataFrame", timestamp_column: str | None = None) -> None:
        self._dataframe = dataframe
        self._timestamp_column = timestamp_column

    def stream(self) -> Iterator[MarketEvent]:
        columns = list(self._dataframe.columns)
        timestamp_position = self._timestamp_position(columns)

        for row in self._dataframe.itertuples(index=True, name=None):
            timestamp = self._extract_timestamp_from_tuple(row, timestamp_position)
            payload = self._payload_from_tuple(row, columns, timestamp_position)
            yield MarketEvent(timestamp=timestamp, payload=payload)

    def _timestamp_position(self, columns: list[str]) -> int | None:
        if not self._timestamp_column:
            return None
        try:
            return columns.index(self._timestamp_column) + 1
        except ValueError:
            return None

    def _extract_timestamp_from_tuple(self, row: tuple[Any, ...], timestamp_position: int | None) -> datetime:
        if timestamp_position is not None and timestamp_position < len(row):
            return self._coerce_timestamp(row[timestamp_position])

        index_value = row[0]
        if isinstance(index_value, datetime):
            return index_value if index_value.tzinfo else index_value.replace(tzinfo=timezone.utc)

        if hasattr(index_value, "to_pydatetime"):
            value = index_value.to_pydatetime()
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

        return datetime.now(timezone.utc)

    def _payload_from_tuple(self, row: tuple[Any, ...], columns: list[str], timestamp_position: int | None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for position, column_name in enumerate(columns, start=1):
            if timestamp_position is not None and position == timestamp_position:
                continue
            value = row[position]
            if value is not None:
                payload[column_name] = value
        return payload

    def _coerce_timestamp(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, (int, float)):
            if value > 1e11:
                return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        parsed_text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(parsed_text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class MockDispatcher:
    def __init__(self) -> None:
        self.ledger: list[SignalPayload] = []

    def dispatch(self, signal: SignalPayload) -> None:
        self.ledger.append(signal.model_copy(deep=True))

    def export_csv(self, file_path: str | Path) -> None:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        field_names = [
            "signalId",
            "botId",
            "action",
            "symbol",
            "marketType",
            "orderType",
            "entry",
            "stopLoss",
            "takeProfit",
            "amount",
            "leverage",
            "marginMode",
            "reduceOnly",
            "status",
            "generatedTimestamp",
            "timeframe",
            "metadata",
        ]

        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=field_names)
            writer.writeheader()
            for signal in self.ledger:
                writer.writerow(signal.model_dump(mode="json", by_alias=True, exclude_none=True))