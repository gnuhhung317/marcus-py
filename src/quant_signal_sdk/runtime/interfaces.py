from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator, Protocol, TypeAlias

from ..models import SignalPayload

Signal: TypeAlias = SignalPayload


@dataclass(frozen=True, slots=True)
class MarketEvent:
    timestamp: datetime
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PortfolioContext:
    positions: dict[str, Any] = field(default_factory=dict)


class BaseFeed(Protocol):
    def stream(self) -> Iterator[MarketEvent]:
        """Yield ordered market events with no callbacks."""


class BaseDispatcher(Protocol):
    def dispatch(self, signal: SignalPayload) -> None:
        """Execute side effects for a single signal."""


class BaseStrategy(Protocol):
    def on_event(self, event: MarketEvent, context: PortfolioContext) -> list[SignalPayload]:
        """Return signals for the current event and portfolio snapshot."""