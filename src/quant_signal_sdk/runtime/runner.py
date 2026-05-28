from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..models import SignalAction, SignalPayload, SignalStatus
from .interfaces import BaseDispatcher, BaseFeed, BaseStrategy, PortfolioContext


logger = logging.getLogger(__name__)


class Runner:
    def __init__(
        self,
        feed: BaseFeed,
        strategy: BaseStrategy,
        dispatcher: BaseDispatcher,
        initial_context: PortfolioContext | None = None,
        after_signal_applied: Callable[[SignalPayload, PortfolioContext], None] | None = None,
    ) -> None:
        self._feed = feed
        self._strategy = strategy
        self._dispatcher = dispatcher
        self._context = initial_context or PortfolioContext()
        self._after_signal_applied = after_signal_applied

    @property
    def context(self) -> PortfolioContext:
        return self._context

    def run(self) -> PortfolioContext:
        for event in self._feed.stream():
            signals = self._strategy.on_event(event, self._snapshot_context())
            for signal in signals:
                try:
                    self._dispatcher.dispatch(signal)
                except Exception:
                    logger.exception("Dispatch failed for signalId=%s botId=%s symbol=%s", signal.signal_id, signal.bot_id, signal.symbol)
                    continue

                self._context = self._apply_signal(self._context, signal)
                if self._after_signal_applied is not None:
                    self._after_signal_applied(signal, self._context)
        return self._context

    def _snapshot_context(self) -> PortfolioContext:
        return PortfolioContext(positions=self._copy_positions(self._context.positions))

    def _apply_signal(self, context: PortfolioContext, signal: SignalPayload) -> PortfolioContext:
        positions = self._copy_positions(context.positions)
        position_key = self._position_key(signal)

        if signal.action in {SignalAction.CLOSE, SignalAction.CLOSE_LONG, SignalAction.CLOSE_SHORT}:
            positions.pop(position_key, None)
            return PortfolioContext(positions=positions)

        positions[position_key] = self._build_position_state(signal)
        return PortfolioContext(positions=positions)

    def _copy_positions(self, positions: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value.copy() if isinstance(value, dict) else value
            for key, value in positions.items()
        }

    def _position_key(self, signal: SignalPayload) -> str:
        return f"{signal.market_type.value}:{signal.symbol}"

    def _build_position_state(self, signal: SignalPayload) -> dict[str, Any]:
        side = "LONG" if signal.action == SignalAction.OPEN_LONG else "SHORT"
        return {
            "symbol": signal.symbol,
            "market_type": signal.market_type.value,
            "order_type": signal.order_type.value,
            "action": signal.action.value,
            "side": side,
            "amount": float(signal.amount or 0.0),
            "entry": signal.entry,
            "status": signal.status.value if signal.status else SignalStatus.RECEIVED.value,
            "generated_timestamp": signal.generated_timestamp,
            "signal": signal.model_copy(deep=True),
        }