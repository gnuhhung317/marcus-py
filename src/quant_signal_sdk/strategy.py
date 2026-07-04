from __future__ import annotations
"""Simple strategy implementations for demo bots.

Includes a simple SMA crossover strategy that emits OPEN_LONG/OPEN_SHORT signals.
"""
from datetime import datetime, timezone
import uuid
from typing import List, Optional
from statistics import mean

from .runtime.interfaces import BaseStrategy


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
            "signalId": f"sig_{uuid.uuid4()}",
            "botId": bot_id,
            "action": action,
            "entry": entry,
            "stopLoss": round(entry * 0.99, 2),
            "takeProfit": round(entry * 1.02, 2),
            "generatedTimestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "metadata": {"strategy": "sma", "short": self.short_window, "long": self.long_window},
        }


__all__ = ["BaseStrategy", "SimpleSmaStrategy"]
