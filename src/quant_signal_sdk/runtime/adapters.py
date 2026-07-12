from __future__ import annotations

import csv
from collections.abc import Callable
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

import requests
import pandas as pd

from ..client import QuantSignalClient
from ..models import SignalPayload
from ..timeframes import parse_timeframe_seconds
from .interfaces import BaseFeed, MarketEvent

if TYPE_CHECKING:
    import pandas as pd


logger = logging.getLogger(__name__)


class BaseTrigger(ABC):
    """Block until the next live ingestion tick is due.

    ScheduledRESTFeed depends only on this abstract trigger contract, so the
    timing policy can be swapped without changing data acquisition or payload
    normalization logic.
    """

    @abstractmethod
    def wait_for_next_tick(self) -> None:
        """Suspend execution until the trigger allows the next fetch."""


class IntervalTrigger(BaseTrigger):
    """Trigger that sleeps for a fixed interval between fetches."""

    def __init__(self, interval_seconds: float) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be greater than zero")
        self._interval_seconds = float(interval_seconds)

    def wait_for_next_tick(self) -> None:
        time.sleep(self._interval_seconds)


class CronTrigger(BaseTrigger):
    """Trigger that waits for the next wall-clock boundary for a timeframe.

    This is useful for feeds that should refresh exactly when a candle closes,
    such as 1h or 4h execution windows.
    """

    def __init__(self, timeframe: str) -> None:
        self._timeframe = timeframe.strip()
        self._interval_seconds = self._parse_timeframe_seconds(self._timeframe)

    def wait_for_next_tick(self) -> None:
        now = datetime.now(timezone.utc)
        epoch_seconds = now.timestamp()
        remainder = epoch_seconds % self._interval_seconds
        sleep_seconds = self._interval_seconds - remainder
        if sleep_seconds <= 1e-9:
            sleep_seconds = self._interval_seconds
        time.sleep(sleep_seconds)

    @classmethod
    def _parse_timeframe_seconds(cls, timeframe: str) -> int:
        return parse_timeframe_seconds(timeframe)


class DataFrameFeed(BaseFeed):
    """Replay a DataFrame as a stream of MarketEvent objects.

    Every row becomes one event. The timestamp is sourced from the configured
    timestamp column when present; otherwise the DataFrame index is used.
    All remaining columns are copied into the payload unchanged.
    """

    def __init__(self, df: pd.DataFrame, timestamp_col: str = "timestamp") -> None:
        self._dataframe = df
        self._timestamp_col = timestamp_col

    def stream(self) -> Iterator[MarketEvent]:
        records = self._dataframe.to_dict(orient="records")

        if self._timestamp_col in self._dataframe.columns:
            for payload in records:
                timestamp_value = payload.pop(self._timestamp_col)
                yield MarketEvent(timestamp=self._coerce_timestamp(timestamp_value), payload=payload)
            return

        if isinstance(self._dataframe.index, pd.DatetimeIndex):
            for timestamp_value, payload in zip(self._dataframe.index, records, strict=False):
                yield MarketEvent(timestamp=self._coerce_timestamp(timestamp_value), payload=payload)
            return

        raise ValueError(
            f"DataFrameFeed requires a '{self._timestamp_col}' column or a DatetimeIndex; "
            f"found columns={list(self._dataframe.columns)}"
        )

    def _coerce_timestamp(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, pd.Timestamp):
            timestamp = value.to_pydatetime()
            return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
        if isinstance(value, (int, float)):
            if value > 1e11:
                return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
            return datetime.fromtimestamp(float(value), tz=timezone.utc)

        parsed = pd.to_datetime(value, utc=True, errors="coerce")
        if pd.isna(parsed):
            raise ValueError(f"Unable to coerce timestamp value: {value!r}")
        timestamp = parsed.to_pydatetime()
        return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)


class ScheduledRESTFeed(BaseFeed):
    """Poll an injected fetcher on a schedule and emit MarketEvent objects.

    Inversion of control is enforced by keeping the feed unaware of exchange
    clients, REST calls, or symbol selection. The end user injects a zero-arg
    `fetcher` that returns a Pandas DataFrame, and a `BaseTrigger` that defines
    when the next fetch should happen.

    Freshness is stateful: the feed only yields a new event when the latest row
    timestamp is strictly greater than the previous accepted timestamp. The
    final row is converted into a MarketEvent by routing through a local
    DataFrameFeed instance so the live payload contract matches backtest
    behavior exactly.
    """

    def __init__(
        self,
        *,
        fetcher: Callable[[], pd.DataFrame],
        trigger: BaseTrigger,
        max_retries: int = 10,
        retry_delay_sec: float = 0.5,
        timestamp_col: str = "timestamp",
    ) -> None:
        if max_retries < 1:
            raise ValueError("max_retries must be at least 1")
        if retry_delay_sec < 0:
            raise ValueError("retry_delay_sec must be greater than or equal to zero")
        self._fetcher = fetcher
        self._trigger = trigger
        self._max_retries = int(max_retries)
        self._retry_delay_sec = float(retry_delay_sec)
        self._timestamp_col = timestamp_col
        self._last_seen_timestamp: datetime | None = None

    def stream(self) -> Iterator[MarketEvent]:
        while True:
            self._trigger.wait_for_next_tick()
            try:
                event = self._fetch_next_fresh_event()
            except TimeoutError as exc:
                logger.error("Skipping live ingestion tick due to data fetch failure: %s", exc)
                continue

            self._last_seen_timestamp = event.timestamp
            yield event

    def _fetch_next_fresh_event(self) -> MarketEvent:
        last_error: Exception | None = None
        for _ in range(self._max_retries):
            frame = self._fetcher()
            try:
                event = self._frame_to_event(frame)
            except Exception as exc:
                last_error = exc
                if self._retry_delay_sec > 0:
                    time.sleep(self._retry_delay_sec)
                continue

            if self._last_seen_timestamp is None or event.timestamp > self._last_seen_timestamp:
                return event

            if self._retry_delay_sec > 0:
                time.sleep(self._retry_delay_sec)

        if last_error is not None:
            raise TimeoutError("ScheduledRESTFeed could not produce a fresh market event") from last_error
        raise TimeoutError("ScheduledRESTFeed could not produce a fresh market event")

    def _frame_to_event(self, frame: pd.DataFrame) -> MarketEvent:
        if frame.empty:
            raise ValueError("ScheduledRESTFeed requires non-empty market data from the injected fetcher")

        latest_frame = frame.tail(1).copy()
        feed = DataFrameFeed(latest_frame, timestamp_col=self._timestamp_col)
        try:
            return next(feed.stream())
        except StopIteration as exc:  # pragma: no cover - defensive guard
            raise ValueError("Unable to adapt the latest DataFrame row into a MarketEvent") from exc


LiveRESTFeed = ScheduledRESTFeed


class LiveHTTPDispatcher:
    def __init__(self, client: QuantSignalClient, bot_api_key: str | None = None) -> None:
        self._client = client
        self._bot_api_key = bot_api_key

    def dispatch(self, signal: SignalPayload) -> None:
        payload = signal.model_dump(mode="json", by_alias=True, exclude_none=True)
        if "generatedTimestamp" in payload and isinstance(payload["generatedTimestamp"], str):
            payload["generatedTimestamp"] = self._normalize_local_datetime(payload["generatedTimestamp"])
        logger.info(
            "dispatching signal signalId=%s botId=%s symbol=%s marketType=%s via=%s",
            signal.signal_id,
            signal.bot_id,
            signal.symbol,
            signal.market_type.value,
            "bot-key" if self._bot_api_key else "client",
        )
        try:
            if self._bot_api_key:
                self._client.send_payload_with_bot_key(payload, bot_api_key=self._bot_api_key)
                return
            self._client.send_signal(signal)
        except requests.HTTPError as exc:
            response = exc.response
            logger.error(
                "Signal dispatch failed status=%s url=%s body=%s payload=%s",
                getattr(response, "status_code", None),
                getattr(response, "url", None),
                getattr(response, "text", ""),
                payload,
            )
            raise

    def _normalize_local_datetime(self, value: str) -> str:
        return value.replace("Z", "").replace("+00:00", "")


class ParquetReplayFeed(DataFrameFeed):
    """Backward-compatible alias for DataFrameFeed.

    The old replay feed name is kept so existing examples and tests can keep
    importing it while the ingestion layer moves toward DataFrame-centric flows.
    """

    def __init__(self, dataframe: pd.DataFrame, timestamp_column: str | None = None) -> None:
        super().__init__(dataframe, timestamp_col=timestamp_column or "timestamp")


class MockDispatcher:
    def __init__(self) -> None:
        self.ledger: list[SignalPayload] = []

    def dispatch(self, signal: SignalPayload) -> None:
        logger.info(
            "capturing signal signalId=%s botId=%s symbol=%s marketType=%s",
            signal.signal_id,
            signal.bot_id,
            signal.symbol,
            signal.market_type.value,
        )
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