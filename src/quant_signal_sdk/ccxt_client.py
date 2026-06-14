from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
import re
import time
from typing import Any, List

import pandas as pd

try:
    import ccxt  # type: ignore
except Exception as exc:  # ImportError or other import-time errors
    ccxt = None  # type: ignore
    _CCXT_IMPORT_ERROR = exc
else:
    _CCXT_IMPORT_ERROR = None


_TIMEFRAME_PATTERN = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)


def _ensure_ccxt() -> Any:
    if ccxt is None:
        raise ImportError(
            "ccxt is not installed. Install it with: `pip install .[market-data]` "
            "or `pip install ccxt`."
        ) from _CCXT_IMPORT_ERROR
    return ccxt


def _coerce_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, pd.Timestamp):
        dt = value.to_pydatetime()
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        if value > 1e11:
            return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
        return datetime.fromtimestamp(float(value), tz=timezone.utc)

    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"Unable to coerce timestamp value: {value!r}")
    dt = parsed.to_pydatetime()
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _coerce_ms(value: Any | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit() or (stripped[:1] in {"+", "-"} and stripped[1:].isdigit()):
            return int(stripped)
        try:
            numeric_value = float(stripped)
        except ValueError:
            pass
        else:
            if numeric_value.is_integer():
                return int(numeric_value)
    dt = _coerce_datetime(value)
    return int(dt.timestamp() * 1000)


def _timeframe_to_ms(timeframe: str) -> int:
    match = _TIMEFRAME_PATTERN.match(timeframe)
    if match is None:
        raise ValueError(
            f"Unsupported timeframe format: {timeframe!r}. Expected values like '15m', '1h', '4h', or '1d'."
        )
    quantity = int(match.group(1))
    unit = match.group(2).lower()
    unit_seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    return quantity * unit_seconds * 1000


def _unique_sorted_symbols(symbols: Iterable[str]) -> list[str]:
    return sorted(dict.fromkeys(symbols))


class ExchangeDataDownloader:
    """Exchange-agnostic CCXT downloader for OHLCV and funding data.

    This class centralizes the logic that used to live in the Binance-only
    script so the SDK can fetch and normalize data from many CCXT exchanges.
    """

    def __init__(
        self,
        exchange_id: str = "binance",
        config: dict[str, Any] | None = None,
        *,
        market_type: str | None = None,
    ) -> None:
        ccxt_module = _ensure_ccxt()
        if not hasattr(ccxt_module, exchange_id):
            raise ValueError(f"Unknown exchange: {exchange_id}")

        self.exchange_id = exchange_id
        self.config: dict[str, Any] = dict(config or {})
        self.config.setdefault("enableRateLimit", True)

        if market_type:
            options = dict(self.config.get("options") or {})
            options.setdefault("defaultType", market_type)
            self.config["options"] = options

        exchange_factory = getattr(ccxt_module, exchange_id)
        self.exchange = exchange_factory(self.config)
        self._load_markets_best_effort()

    def _load_markets_best_effort(self) -> None:
        try:
            self.markets = self.exchange.load_markets()
        except Exception:
            self.markets = getattr(self.exchange, "markets", {}) or {}

    def _rate_limit_sleep(self) -> None:
        rate_limit_ms = getattr(self.exchange, "rateLimit", None)
        if rate_limit_ms:
            time.sleep(float(rate_limit_ms) / 1000.0)

    def normalize_symbol(self, symbol: str) -> str:
        """Return a storage-friendly symbol label.

        Examples:
            BTC/USDT -> BTCUSDT
            BTC/USDT:USDT -> BTCUSDT_USDT
        """

        clean = str(symbol).strip().upper()
        return clean.replace("/", "").replace("-", "").replace(":", "_")

    def get_clean_symbol(self, symbol: str) -> str:
        return self.normalize_symbol(symbol)

    def list_symbols(
        self,
        *,
        quote_asset: str | None = None,
        market_type: str | None = None,
        active_only: bool = True,
        linear_only: bool | None = None,
        inverse_only: bool | None = None,
    ) -> list[str]:
        markets = getattr(self, "markets", {}) or {}
        requested_quote = quote_asset.upper() if quote_asset else None
        requested_market_type = market_type.lower() if market_type else None

        symbols: list[str] = []
        for market in markets.values():
            if not isinstance(market, dict):
                continue
            if active_only and market.get("active") is False:
                continue
            if requested_quote and str(market.get("quote", "")).upper() != requested_quote:
                continue
            if requested_market_type and not self._market_type_matches(market, requested_market_type):
                continue
            if linear_only is not None and bool(market.get("linear")) is not linear_only:
                continue
            if inverse_only is not None and bool(market.get("inverse")) is not inverse_only:
                continue

            symbol = market.get("symbol")
            if symbol:
                symbols.append(str(symbol))

        return _unique_sorted_symbols(symbols)

    def fetch_ohlcv_rows(
        self,
        symbol: str,
        timeframe: str = "1m",
        *,
        since: Any | None = None,
        until: Any | None = None,
        limit: int = 100,
        paginate: bool = False,
    ) -> List[List[float]]:
        if limit < 1:
            raise ValueError("limit must be greater than zero")

        since_ms = _coerce_ms(since)
        until_ms = _coerce_ms(until)
        rows: list[list[float]] = []
        cursor = since_ms
        page_limit = int(limit)
        step_ms = _timeframe_to_ms(timeframe)

        if paginate and since_ms is None:
            raise ValueError("paginate=True requires since to be provided")

        while True:
            kwargs: dict[str, Any] = {"timeframe": timeframe, "limit": page_limit}
            if cursor is not None:
                kwargs["since"] = cursor

            batch = self.exchange.fetch_ohlcv(symbol, **kwargs)
            if not batch:
                break

            normalized_batch = [row for row in batch if len(row) >= 6]
            if until_ms is not None:
                normalized_batch = [row for row in normalized_batch if int(row[0]) <= until_ms]
            if not normalized_batch:
                break

            rows.extend(normalized_batch)
            if not paginate:
                break

            last_ts = int(normalized_batch[-1][0])
            next_cursor = last_ts + 1
            if next_cursor <= (cursor or 0):
                next_cursor = (cursor or last_ts) + max(step_ms, 1)
            if until_ms is not None and next_cursor > until_ms:
                break
            if len(normalized_batch) < page_limit and until_ms is None:
                break

            cursor = next_cursor
            self._rate_limit_sleep()

        return rows

    def fetch_ohlcv_frame(
        self,
        symbol: str,
        timeframe: str = "1m",
        *,
        since: Any | None = None,
        until: Any | None = None,
        limit: int = 100,
        paginate: bool = False,
    ) -> pd.DataFrame:
        rows = self.fetch_ohlcv_rows(
            symbol,
            timeframe=timeframe,
            since=since,
            until=until,
            limit=limit,
            paginate=paginate,
        )
        if not rows:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
        frame = frame.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        return frame

    def discover_ohlcv_start(
        self,
        symbol: str,
        timeframe: str = "1h",
        *,
        start_year: int = 2019,
    ) -> int | None:
        current_year = datetime.now(timezone.utc).year
        years = list(range(start_year, current_year + 2))
        last_empty_year: int | None = None

        for year in years:
            probe_ts = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
            try:
                ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=probe_ts, limit=1)
            except Exception:
                last_empty_year = year
                continue

            if ohlcv:
                if last_empty_year is not None and last_empty_year == year - 1:
                    for month in range(1, 13):
                        month_ts = int(datetime(last_empty_year, month, 1, tzinfo=timezone.utc).timestamp() * 1000)
                        try:
                            month_ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=month_ts, limit=1)
                        except Exception:
                            continue
                        if month_ohlcv:
                            return int(month_ohlcv[0][0])
                return int(ohlcv[0][0])

            last_empty_year = year

        try:
            fallback = self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=1)
        except Exception:
            return None
        if fallback:
            return int(fallback[0][0])
        return None

    def sync_ohlcv(
        self,
        symbol: str,
        existing_df: pd.DataFrame | None = None,
        timeframe: str = "1h",
        *,
        since: Any | None = None,
        until: Any | None = None,
        discover_start: bool = True,
        fill_gaps: bool = True,
    ) -> pd.DataFrame:
        existing = self._normalize_ohlcv_frame(existing_df)

        if existing is not None and not existing.empty:
            start_fetch_ts = int(existing["timestamp"].max().value // 10**6) + 1
        elif since is not None:
            start_fetch_ts = _coerce_ms(since)
        elif discover_start:
            start_fetch_ts = self.discover_ohlcv_start(symbol, timeframe=timeframe)
        else:
            start_fetch_ts = None

        if start_fetch_ts is not None:
            fetched = self.fetch_ohlcv_frame(
                symbol,
                timeframe=timeframe,
                since=start_fetch_ts,
                until=until,
                limit=1000,
                paginate=True,
            )
        else:
            fetched = self.fetch_ohlcv_frame(
                symbol,
                timeframe=timeframe,
                since=since,
                until=until,
                limit=1000,
                paginate=False,
            )

        combined = self._combine_ohlcv_frames(existing, fetched)
        if fill_gaps and not combined.empty:
            combined = self.fill_gaps(symbol, combined, timeframe=timeframe)
        return combined

    def fill_gaps(
        self,
        symbol: str,
        df: pd.DataFrame,
        timeframe: str = "1h",
        *,
        gap_multiplier: float = 1.5,
    ) -> pd.DataFrame:
        normalized = self._normalize_ohlcv_frame(df)
        if normalized is None or normalized.empty:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        normalized = normalized.sort_values("timestamp").reset_index(drop=True)
        threshold = pd.Timedelta(milliseconds=int(_timeframe_to_ms(timeframe) * gap_multiplier))
        missing_frames: list[pd.DataFrame] = []

        timestamps = list(normalized["timestamp"])
        for previous, current in zip(timestamps, timestamps[1:]):
            if current - previous <= threshold:
                continue
            gap_fill = self.fetch_ohlcv_frame(
                symbol,
                timeframe=timeframe,
                since=int(previous.value // 10**6) + 1,
                until=int(current.value // 10**6) - 1,
                limit=1000,
                paginate=True,
            )
            if not gap_fill.empty:
                missing_frames.append(gap_fill)

        if not missing_frames:
            return normalized

        return self._combine_ohlcv_frames(normalized, pd.concat(missing_frames, ignore_index=True))

    def fetch_funding_rate_history(
        self,
        symbol: str,
        *,
        since: Any | None = None,
        until: Any | None = None,
        limit: int = 1000,
        paginate: bool = False,
    ) -> pd.DataFrame:
        has_fetch = getattr(self.exchange, "has", {}) or {}
        if isinstance(has_fetch, dict) and not has_fetch.get("fetchFundingRateHistory", False):
            return pd.DataFrame(columns=["timestamp", "funding_rate"])

        since_ms = _coerce_ms(since)
        until_ms = _coerce_ms(until)
        cursor = since_ms
        rows: list[dict[str, Any]] = []

        if paginate and since_ms is None:
            raise ValueError("paginate=True requires since to be provided")

        while True:
            kwargs: dict[str, Any] = {"symbol": symbol, "limit": limit}
            if cursor is not None:
                kwargs["since"] = cursor

            batch = self.exchange.fetch_funding_rate_history(**kwargs)
            if not batch:
                break

            normalized_batch = [item for item in batch if isinstance(item, dict) and item.get("timestamp") is not None]
            if until_ms is not None:
                normalized_batch = [item for item in normalized_batch if int(item["timestamp"]) <= until_ms]
            if not normalized_batch:
                break

            rows.extend(normalized_batch)
            if not paginate:
                break

            last_ts = int(normalized_batch[-1]["timestamp"])
            next_cursor = last_ts + 1
            if until_ms is not None and next_cursor > until_ms:
                break
            if len(normalized_batch) < limit and until_ms is None:
                break

            cursor = next_cursor
            self._rate_limit_sleep()

        if not rows:
            return pd.DataFrame(columns=["timestamp", "funding_rate"])

        frame = pd.DataFrame(rows)
        funding_col = self._find_column(frame.columns, {"fundingrate", "funding_rate"})
        if funding_col is None:
            return pd.DataFrame(columns=["timestamp", "funding_rate"])

        frame = frame[["timestamp", funding_col]].rename(columns={funding_col: "funding_rate"})
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
        frame = frame.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        return frame

    def _market_type_matches(self, market: dict[str, Any], requested: str) -> bool:
        market_type = str(market.get("type", "")).lower()
        if requested == market_type:
            return True
        if requested == "swap":
            return bool(market.get("swap") or market_type == "swap")
        if requested == "future":
            return bool(market.get("future") or market_type == "future")
        if requested == "spot":
            return bool(market.get("spot") or market_type == "spot")
        return False

    def _normalize_ohlcv_frame(self, df: pd.DataFrame | None) -> pd.DataFrame | None:
        if df is None:
            return None
        if df.empty:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        frame = df.copy()
        if "timestamp" not in frame.columns and isinstance(frame.index, pd.DatetimeIndex):
            frame = frame.reset_index().rename(columns={"index": "timestamp"})
        elif "timestamp" not in frame.columns:
            raise ValueError("OHLCV frame must contain a 'timestamp' column or a DatetimeIndex")

        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
        frame = frame.dropna(subset=["timestamp"])

        required = ["timestamp", "open", "high", "low", "close", "volume"]
        missing = [column for column in required if column not in frame.columns]
        if missing:
            raise ValueError(f"OHLCV frame is missing required columns: {', '.join(missing)}")

        return frame[required].sort_values("timestamp").reset_index(drop=True)

    def _combine_ohlcv_frames(self, *frames: pd.DataFrame | None) -> pd.DataFrame:
        valid_frames = [frame for frame in frames if frame is not None and not frame.empty]
        if not valid_frames:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        combined = pd.concat(valid_frames, ignore_index=True)
        combined = combined.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        return combined

    def _find_column(self, columns: Iterable[Any], candidates: set[str]) -> str | None:
        for column in columns:
            name = str(column).strip().lower()
            if name in candidates:
                return str(column)
        return None


class CCXTClient(ExchangeDataDownloader):
    def fetch_ohlcv(self, symbol: str, timeframe: str = "1m", limit: int = 100) -> List[List[float]]:
        """Return OHLCV rows as provided by ccxt: [timestamp, open, high, low, close, volume]."""

        return self.fetch_ohlcv_rows(symbol, timeframe=timeframe, limit=limit)


def close_prices_from_ohlcv(ohlcv: List[List[float]]) -> List[float]:
    return [float(row[4]) for row in ohlcv if len(row) >= 5]
