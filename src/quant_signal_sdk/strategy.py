from __future__ import annotations
"""Simple strategy implementations for demo bots.

Includes a simple SMA crossover strategy that emits OPEN_LONG/OPEN_SHORT signals.
"""
from typing import List, Optional
from statistics import mean

from .ccxt_client import close_prices_from_ohlcv


class SimpleSmaStrategy:
    def __init__(self, short_window: int = 5, long_window: int = 15):
        if short_window >= long_window:
            raise ValueError("short_window must be < long_window")
        self.short_window = short_window
        self.long_window = long_window

    def decide(self, close_prices: List[float]) -> Optional[str]:
        """Return action string: 'OPEN_LONG' or 'OPEN_SHORT' or None if insufficient data."""
        if len(close_prices) < self.long_window:
            return None
        short_ma = mean(close_prices[-self.short_window:])
        long_ma = mean(close_prices[-self.long_window:])
        if short_ma > long_ma:
            return "OPEN_LONG"
        else:
            return "OPEN_SHORT"

    def generate_signal_payload(self, bot_id: str, close_prices: List[float]):
        action = self.decide(close_prices)
        if action is None:
            return None
        entry = close_prices[-1]
        return {
            "signalId": f"sig_{int(entry)}_{len(close_prices)}",
            "botId": bot_id,
            "action": action,
            "entry": entry,
            "stopLoss": round(entry * 0.99, 2),
            "takeProfit": round(entry * 1.02, 2),
            "generatedTimestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "metadata": {"strategy": "sma", "short": self.short_window, "long": self.long_window},
        }

from abc import ABC, abstractmethod
from typing import Any, Mapping

from .models import SignalPayload


class BaseStrategy(ABC):
    @abstractmethod
    def on_market_data(self, tick: Mapping[str, Any]) -> SignalPayload | None:
        """Return a signal when conditions are met, otherwise None."""

    async def on_market_data_async(self, tick: Mapping[str, Any]) -> SignalPayload | None:
        return self.on_market_data(tick)
