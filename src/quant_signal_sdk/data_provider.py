from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

from .ccxt_client import ExchangeDataDownloader


class DataProvider(ABC):
    """Abstract base class for market data sourcing."""

    @abstractmethod
    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
        """Fetch OHLCV data and return as a Pandas DataFrame indexed by datetime."""
        raise NotImplementedError


class ExchangeDataProvider(DataProvider):
    """Concrete data provider that normalizes CCXT market data into DataFrames."""

    def __init__(
        self,
        exchange_id: str = "binance",
        config: dict[str, Any] | None = None,
        *,
        market_type: str | None = None,
    ) -> None:
        self._downloader = ExchangeDataDownloader(exchange_id=exchange_id, config=config, market_type=market_type)

    @property
    def downloader(self) -> ExchangeDataDownloader:
        return self._downloader

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 100,
        *,
        since: Any | None = None,
        until: Any | None = None,
        paginate: bool = False,
    ) -> pd.DataFrame:
        frame = self._downloader.fetch_ohlcv_frame(
            symbol,
            timeframe=timeframe,
            since=since,
            until=until,
            limit=limit,
            paginate=paginate,
        )
        indexed = frame.copy()
        indexed["datetime"] = indexed["timestamp"]
        indexed = indexed.set_index("datetime")
        indexed = indexed.drop(columns=["timestamp"])
        return indexed

    def list_symbols(
        self,
        *,
        quote_asset: str | None = None,
        market_type: str | None = None,
        active_only: bool = True,
        linear_only: bool | None = None,
        inverse_only: bool | None = None,
    ) -> list[str]:
        return self._downloader.list_symbols(
            quote_asset=quote_asset,
            market_type=market_type,
            active_only=active_only,
            linear_only=linear_only,
            inverse_only=inverse_only,
        )

    def fetch_funding_rate_history(
        self,
        symbol: str,
        *,
        since: Any | None = None,
        until: Any | None = None,
        limit: int = 1000,
        paginate: bool = False,
    ) -> pd.DataFrame:
        return self._downloader.fetch_funding_rate_history(
            symbol,
            since=since,
            until=until,
            limit=limit,
            paginate=paginate,
        )

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
        return self._downloader.sync_ohlcv(
            symbol,
            existing_df=existing_df,
            timeframe=timeframe,
            since=since,
            until=until,
            discover_start=discover_start,
            fill_gaps=fill_gaps,
        )

    def fill_gaps(
        self,
        symbol: str,
        df: pd.DataFrame,
        timeframe: str = "1h",
        *,
        gap_multiplier: float = 1.5,
    ) -> pd.DataFrame:
        return self._downloader.fill_gaps(symbol, df, timeframe=timeframe, gap_multiplier=gap_multiplier)

    def normalize_symbol(self, symbol: str) -> str:
        return self._downloader.normalize_symbol(symbol)

    def get_clean_symbol(self, symbol: str) -> str:
        return self._downloader.get_clean_symbol(symbol)


class CcxtDataProvider(ExchangeDataProvider):
    """Backward-compatible alias for the generic exchange data provider."""

    pass
