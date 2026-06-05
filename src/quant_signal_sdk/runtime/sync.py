from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from ..models import SignalAction, SignalPayload
from .dry_run import DryRunSyncClient
from .telemetry import TelemetryClient
from .dry_run_store import DryRunClosedTradeSnapshot, DryRunPortfolioSnapshot, DryRunPositionSnapshot, DryRunStateSnapshot, SQLiteDryRunStore
from .interfaces import PortfolioContext


logger = logging.getLogger(__name__)


class StateSyncer(Protocol):
    def sync(self, context: PortfolioContext, force: bool = False) -> None:
        ...


class WebSocketTransport(Protocol):
    def send(self, payload: str) -> None:
        ...


class DryRunStateTracker:
    """Maintains paper-trading state outside Runner core."""

    def __init__(self, store: SQLiteDryRunStore) -> None:
        self._store = store

    @property
    def store(self) -> SQLiteDryRunStore:
        return self._store

    def recover_context(self) -> PortfolioContext | None:
        state = self._store.load_state()
        return state.to_context() if state is not None else None

    def replace_state(self, state: DryRunStateSnapshot) -> None:
        self._store.replace_state(state)

    def on_signal_applied(self, signal: SignalPayload, context: PortfolioContext) -> None:
        position_key = f"{signal.market_type.value}:{signal.symbol}"
        signal_timestamp = signal.generated_timestamp or context.timestamp or datetime.now(timezone.utc)
        if signal.action in {SignalAction.OPEN_LONG, SignalAction.OPEN_SHORT}:
            self._store.upsert_position(self._position_from_signal(signal, context, position_key, signal_timestamp))
        elif signal.action in {SignalAction.CLOSE, SignalAction.CLOSE_LONG, SignalAction.CLOSE_SHORT}:
            existing_position = self._store.get_position(position_key)
            if existing_position is not None:
                self._store.remove_position(position_key)
                self._store.upsert_closed_trade(self._closed_trade_from_signal(existing_position, signal, signal_timestamp))
        self._store.save_portfolio(self.portfolio_from_context(context, signal_timestamp))

    def load_state_or_context(self, context: PortfolioContext) -> DryRunStateSnapshot:
        state = self._store.load_state()
        if state is not None:
            return state
        state = DryRunStateSnapshot(
            portfolio=self.portfolio_from_context(context),
            positions=[],
            closed_trades=[],
        )
        self._store.replace_state(state)
        return state

    def portfolio_from_context(self, context: PortfolioContext, timestamp: datetime | None = None) -> DryRunPortfolioSnapshot:
        snapshot_at = timestamp or context.timestamp or datetime.now(timezone.utc)
        return DryRunPortfolioSnapshot(
            timestamp=snapshot_at,
            cash=float(context.cash),
            equity=float(context.equity),
            realized_pnl=float(context.realized_pnl),
            unrealized_pnl=float(context.unrealized_pnl),
            total_fees=float(context.total_fees),
        )

    def _position_from_signal(
        self,
        signal: SignalPayload,
        context: PortfolioContext,
        position_key: str,
        timestamp: datetime,
    ) -> DryRunPositionSnapshot:
        side = "LONG" if signal.action == SignalAction.OPEN_LONG else "SHORT"
        position = context.positions.get(position_key) or {}
        return DryRunPositionSnapshot(
            position_id=position_key,
            symbol=signal.symbol,
            market_type=signal.market_type.value,
            side=side,
            quantity=float(signal.amount or position.get("amount") or 0.0),
            entry_price=float(signal.entry or position.get("entry") or 0.0),
            current_price=float(signal.entry or position.get("current_price") or position.get("entry") or 0.0),
            unrealized_pnl=float(position.get("unrealized_pnl") or context.unrealized_pnl),
            opened_at=timestamp,
            source_signal_id=signal.signal_id,
        )

    def _closed_trade_from_signal(
        self,
        position: DryRunPositionSnapshot,
        signal: SignalPayload,
        exit_timestamp: datetime,
    ) -> DryRunClosedTradeSnapshot:
        exit_price = float(signal.entry or position.current_price or position.entry_price)
        if position.side == "LONG":
            pnl = (exit_price - position.entry_price) * position.quantity
        else:
            pnl = (position.entry_price - exit_price) * position.quantity
        return DryRunClosedTradeSnapshot(
            trade_id=self._trade_id(position, signal, exit_timestamp),
            symbol=position.symbol,
            market_type=position.market_type,
            side=position.side,
            quantity=position.quantity,
            entry_price=position.entry_price,
            exit_price=exit_price,
            pnl=pnl,
            fees=0.0,
            entry_timestamp=position.opened_at,
            exit_timestamp=exit_timestamp,
            entry_signal_id=position.source_signal_id,
            exit_signal_id=signal.signal_id,
        )

    def _trade_id(self, position: DryRunPositionSnapshot, signal: SignalPayload, exit_timestamp: datetime) -> str:
        if position.source_signal_id and signal.signal_id:
            return f"trade_{position.source_signal_id}_{signal.signal_id}"
        return f"{position.position_id}_{position.opened_at.isoformat()}_{exit_timestamp.isoformat()}"


class HttpDryRunSyncer:
    def __init__(self, client: DryRunSyncClient, tracker: DryRunStateTracker, interval: float = 3600.0) -> None:
        self._client = client
        self._tracker = tracker
        self._interval = interval
        self._last_sync_monotonic = 0.0

    def recover_context(self) -> PortfolioContext | None:
        local_context = self._tracker.recover_context()
        if local_context is not None:
            return local_context
        latest = self._client.fetch_latest()
        if latest is None:
            return None
        self._tracker.replace_state(latest)
        return latest.to_context()

    def sync(self, context: PortfolioContext, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_sync_monotonic < self._interval:
            return
        try:
            self._client.push_snapshot(self._tracker.load_state_or_context(context))
            self._last_sync_monotonic = time.monotonic()
        except Exception:
            logger.exception("Dry-run sync failed")


class WebSocketDryRunSyncer:
    def __init__(self, transport: WebSocketTransport, tracker: DryRunStateTracker, interval: float = 60.0) -> None:
        self._transport = transport
        self._tracker = tracker
        self._interval = interval
        self._last_sync_monotonic = 0.0

    def sync(self, context: PortfolioContext, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_sync_monotonic < self._interval:
            return
        state = self._tracker.load_state_or_context(context)
        payload = {
            "type": "dry_run_state",
            "portfolio": {
                "timestamp": state.portfolio.timestamp.isoformat(),
                "cash": state.portfolio.cash,
                "equity": state.portfolio.equity,
                "realizedPnl": state.portfolio.realized_pnl,
                "unrealizedPnl": state.portfolio.unrealized_pnl,
                "totalFees": state.portfolio.total_fees,
            },
            "positions": [asdict(position) for position in state.positions],
            "closedTrades": [asdict(trade) for trade in state.closed_trades],
        }
        self._transport.send(json.dumps(payload, default=str, separators=(",", ":")))
        self._last_sync_monotonic = time.monotonic()


class FileSyncer:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def sync(self, context: PortfolioContext, force: bool = False) -> None:
        payload = asdict(context)
        payload["timestamp"] = context.timestamp.isoformat() if context.timestamp else None
        self._path.write_text(json.dumps(payload, default=str, indent=2), encoding="utf-8")


class NoopSyncer:
    def sync(self, context: PortfolioContext, force: bool = False) -> None:
        return None


class TelemetrySyncer(Protocol):
    def report(self, context: PortfolioContext, force: bool = False) -> None:
        ...


class HttpTelemetrySyncer:
    def __init__(self, client: TelemetryClient, interval: float = 60.0) -> None:
        self._client = client
        self._interval = interval
        self._last_report_monotonic = 0.0

    def report(self, context: PortfolioContext, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_report_monotonic < self._interval:
            return
        try:
            self._client.push_telemetry(
                equity=float(context.equity),
                realized_pnl=float(context.realized_pnl),
                unrealized_pnl=float(context.unrealized_pnl),
                metrics={},
            )
            self._last_report_monotonic = time.monotonic()
        except Exception:
            logger.exception("Telemetry report failed")


class NoopTelemetrySyncer:
    def report(self, context: PortfolioContext, force: bool = False) -> None:
        return None


__all__ = [
    "StateSyncer",
    "WebSocketTransport",
    "DryRunStateTracker",
    "HttpDryRunSyncer",
    "WebSocketDryRunSyncer",
    "FileSyncer",
    "NoopSyncer",
    "TelemetrySyncer",
    "HttpTelemetrySyncer",
    "NoopTelemetrySyncer",
]
