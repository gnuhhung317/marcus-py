from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from ..models import SignalAction, SignalPayload, SignalStatus
from .interfaces import BaseDispatcher, BaseFeed, BaseStrategy, PortfolioContext
from .telemetry import BotTelemetryClient, TelemetryConfig


logger = logging.getLogger(__name__)


class Runner:
    def __init__(
        self,
        feed: BaseFeed,
        strategy: BaseStrategy,
        dispatcher: BaseDispatcher,
        initial_context: PortfolioContext | None = None,
        after_signal_applied: Callable[[SignalPayload, PortfolioContext], None] | None = None,
        telemetry_config: TelemetryConfig | None = None,
        telemetry_client: BotTelemetryClient | None = None,
        telemetry_sync_interval: float | None = None,
    ) -> None:
        self._feed = feed
        self._strategy = strategy
        self._dispatcher = dispatcher
        self._explicit_initial_context = initial_context is not None
        self._context = initial_context or PortfolioContext()
        self._after_signal_applied = after_signal_applied
        self._telemetry_config = telemetry_config
        if telemetry_config and telemetry_sync_interval is not None:
            self._telemetry_config = TelemetryConfig(
                base_url=telemetry_config.base_url,
                bot_id=telemetry_config.bot_id,
                api_key=telemetry_config.api_key,
                signer_secret=telemetry_config.signer_secret,
                sync_interval_seconds=telemetry_sync_interval,
                timeout_seconds=telemetry_config.timeout_seconds,
            )
        self._telemetry_client = telemetry_client or (BotTelemetryClient(self._telemetry_config) if self._telemetry_config else None)
        self._last_telemetry_sync_monotonic = 0.0

    @property
    def context(self) -> PortfolioContext:
        return self._context

    def run(self) -> PortfolioContext:
        self._recover_telemetry_context()
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
                self._sync_telemetry_if_due()
        self._sync_telemetry(force=True)
        return self._context

    def _recover_telemetry_context(self) -> None:
        if self._telemetry_client is None or self._explicit_initial_context:
            return
        try:
            latest = self._telemetry_client.fetch_latest()
        except Exception:
            logger.exception("Telemetry recovery failed")
            return
        if not latest:
            return
        self._context = PortfolioContext(
            positions=self._copy_positions(self._context.positions),
            cash=self._context.cash,
            open_orders=self._copy_positions(self._context.open_orders),
            realized_pnl=float(latest.get("realizedPnl") or latest.get("realized_pnl") or 0.0),
            unrealized_pnl=float(latest.get("unrealizedPnl") or latest.get("unrealized_pnl") or 0.0),
            total_fees=self._context.total_fees,
            equity=float(latest.get("equity") or 0.0),
            timestamp=self._parse_timestamp(latest.get("timestamp")),
        )

    def _sync_telemetry_if_due(self) -> None:
        if self._telemetry_config is None:
            return
        now = time.monotonic()
        if now - self._last_telemetry_sync_monotonic < self._telemetry_config.sync_interval_seconds:
            return
        self._sync_telemetry(force=True)

    def _sync_telemetry(self, *, force: bool = False) -> None:
        if self._telemetry_client is None:
            return
        if not force and self._telemetry_config is not None:
            now = time.monotonic()
            if now - self._last_telemetry_sync_monotonic < self._telemetry_config.sync_interval_seconds:
                return
        try:
            self._telemetry_client.push_context(self._context)
            self._last_telemetry_sync_monotonic = time.monotonic()
        except Exception:
            logger.exception("Telemetry sync failed")

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
                timestamp=context.timestamp,
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
            timestamp=context.timestamp,
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
