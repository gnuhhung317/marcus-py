from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping

from .models import SignalPayload


class BaseStrategy(ABC):
    @abstractmethod
    def on_market_data(self, tick: Mapping[str, Any]) -> SignalPayload | None:
        """Return a signal when conditions are met, otherwise None."""

    async def on_market_data_async(self, tick: Mapping[str, Any]) -> SignalPayload | None:
        return self.on_market_data(tick)
