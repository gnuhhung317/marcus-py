from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, List
import pandas as pd

class DataProvider(ABC):
    """Abstract base class for market data sourcing."""

    @abstractmethod
    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
        """Fetch OHLCV data and return as a Pandas DataFrame indexed by datetime."""
        pass

class CcxtDataProvider(DataProvider):
    """Concrete data provider sourcing data via CCXT."""

    def __init__(self, exchange_id: str = "binance", config: dict[str, Any] | None = None):
        try:
            import ccxt
        except ImportError:
            raise RuntimeError("Missing optional dependency: ccxt. Install via pip install ccxt.")
        
        ex_class = getattr(ccxt, exchange_id)
        self._exchange = ex_class(config or {})
    
    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
        raw = self._exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        
        df = pd.DataFrame(
            raw, 
            columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("datetime", inplace=True)
        df.drop(columns=["timestamp"], inplace=True)
        return df
