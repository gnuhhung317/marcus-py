from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..models import SignalAction, SignalPayload, SignalStatus
from .interfaces import BaseDispatcher, BaseFeed, BaseStrategy, PortfolioContext
from .sync import StateSyncer


logger = logging.getLogger(__name__)


class Runner:
    def __init__(
        self,
        feed: BaseFeed,
        strategy: BaseStrategy,
        dispatcher: BaseDispatcher,
        initial_context: PortfolioContext | None = None,
        after_signal_applied: Callable[[SignalPayload, PortfolioContext], None] | None = None,
        state_syncer: StateSyncer | None = None,
    ) -> None:
        self._feed = feed
        self._strategy = strategy
        self._dispatcher = dispatcher
        self._explicit_initial_context = initial_context is not None
        self._context = initial_context or PortfolioContext()
        self._after_signal_applied = after_signal_applied
        self._state_syncer = state_syncer

    @property
    def context(self) -> PortfolioContext:
        return self._context

    def run(self) -> PortfolioContext:
        self._recover_context()
        logger.info(
            "runner started feed=%s strategy=%s dispatcher=%s",
            self._feed.__class__.__name__,
            self._strategy.__class__.__name__,
            self._dispatcher.__class__.__name__,
        )
        for event in self._feed.stream():
            signals = self._strategy.on_event(event, self._snapshot_context())
            for signal in signals:
                try:
                    self._dispatcher.dispatch(signal)
                except Exception:
                    logger.exception("Dispatch failed for signalId=%s botId=%s symbol=%s", signal.signal_id, signal.bot_id, signal.symbol)
                    continue

                self._context = self._apply_signal(self._context, signal)
                logger.info(
                    "signal applied signalId=%s action=%s symbol=%s marketType=%s positions=%s",
                    signal.signal_id,
                    signal.action.value,
                    signal.symbol,
                    signal.market_type.value,
                    len(self._context.positions),
                )
                if self._after_signal_applied is not None:
                    self._after_signal_applied(signal, self._context)
                if self._state_syncer is not None:
                    self._state_syncer.sync(self._context)
        if self._state_syncer is not None:
            self._state_syncer.sync(self._context, force=True)
        return self._context

    def _recover_context(self) -> None:
        if self._explicit_initial_context:
            return
        recover_context = getattr(self._state_syncer, "recover_context", None)
        if not callable(recover_context):
            return
        try:
            latest = recover_context()
        except Exception:
            logger.exception("Dry-run recovery failed")
            return
        if latest is None:
            return
        self._context = latest

    def _parse_timestamp(self, value: Any) -> datetime | None:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    def _snapshot_context(self) -> PortfolioContext:
        return PortfolioContext(
            positions=self._copy_positions(self._context.positions),
            cash=self._context.cash,
            open_orders=self._copy_positions(self._context.open_orders),
            realized_pnl=self._context.realized_pnl,
            unrealized_pnl=self._context.unrealized_pnl,
            total_fees=self._context.total_fees,
            equity=self._context.equity,
            timestamp=self._context.timestamp,
        )

    def _apply_signal(self, context: PortfolioContext, signal: SignalPayload) -> PortfolioContext:
        positions = self._copy_positions(context.positions)
        position_key = self._position_key(signal)

        if signal.action in {SignalAction.CLOSE, SignalAction.CLOSE_LONG, SignalAction.CLOSE_SHORT}:
            positions.pop(position_key, None)
            return PortfolioContext(
                positions=positions,
                cash=context.cash,
                open_orders=self._copy_positions(context.open_orders),
                realized_pnl=context.realized_pnl,
                unrealized_pnl=context.unrealized_pnl,
                total_fees=context.total_fees,
                equity=context.equity,
                timestamp=signal.generated_timestamp,
            )

        positions[position_key] = self._build_position_state(signal)
        return PortfolioContext(
            positions=positions,
            cash=context.cash,
            open_orders=self._copy_positions(context.open_orders),
            realized_pnl=context.realized_pnl,
            unrealized_pnl=context.unrealized_pnl,
            total_fees=context.total_fees,
            equity=context.equity,
            timestamp=signal.generated_timestamp,
        )

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
            "position_id": self._position_key(signal),
            "symbol": signal.symbol,
            "market_type": signal.market_type.value,
            "order_type": signal.order_type.value,
            "action": signal.action.value,
            "side": side,
            "amount": float(signal.amount or 0.0),
            "entry": float(signal.entry or 0.0),
            "current_price": float(signal.entry or 0.0),
            "unrealized_pnl": 0.0,
            "opened_at": signal.generated_timestamp,
            "source_signal_id": signal.signal_id,
            "status": signal.status.value if signal.status else SignalStatus.RECEIVED.value,
            "generated_timestamp": signal.generated_timestamp,
            "signal": signal.model_copy(deep=True),
        }
