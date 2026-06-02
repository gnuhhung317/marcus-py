"""Small CCXT wrapper to fetch OHLCV data for strategies.

`ccxt` is an optional dependency. If it's not installed the module will raise
an ImportError with instructions to install the `market-data` extra.
"""
from typing import List

try:
    import ccxt  # type: ignore
except Exception as exc:  # ImportError or other import-time errors
    ccxt = None  # type: ignore
    _CCXT_IMPORT_ERROR = exc


class CCXTClient:
    def __init__(self, exchange_id: str = "binance"):
        if ccxt is None:
            raise ImportError(
                "ccxt is not installed. Install it with: `pip install .[market-data]` "
                "or `pip install ccxt`."
            ) from _CCXT_IMPORT_ERROR

        if not hasattr(ccxt, exchange_id):
            raise ValueError(f"Unknown exchange: {exchange_id}")
        self.exchange = getattr(ccxt, exchange_id)()
        try:
            self.exchange.load_markets()
        except Exception:
            # best-effort, some test envs may not allow network calls
            pass

    def fetch_ohlcv(self, symbol: str, timeframe: str = "1m", limit: int = 100) -> List[List[float]]:
        """Return OHLCV rows as provided by ccxt: [timestamp, open, high, low, close, volume]

        Raises ccxt.BaseError on network / exchange errors.
        """
        return self.exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)


def close_prices_from_ohlcv(ohlcv: List[List[float]]) -> List[float]:
    return [float(row[4]) for row in ohlcv if len(row) >= 5]
