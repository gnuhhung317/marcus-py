from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .._http import build_auth_headers, response_json_or_empty
from ..signing import canonical_json_bytes
from .dry_run_store import DryRunClosedTradeSnapshot, DryRunPortfolioSnapshot, DryRunPositionSnapshot, DryRunStateSnapshot


@dataclass(frozen=True, slots=True)
class DryRunSyncConfig:
    base_url: str
    bot_id: str
    api_key: str
    signer_secret: str | None = None
    sqlite_path: str = ".quant_signal_sdk/dry_run.sqlite3"
    sync_interval_seconds: float = 3600.0
    timeout_seconds: float = 10.0


class DryRunSyncClient:
    def __init__(self, config: DryRunSyncConfig, session: requests.Session | None = None) -> None:
        self._config = config
        self._session = session or requests.Session()

    @property
    def sqlite_path(self) -> Path:
        return Path(self._config.sqlite_path)

    def fetch_latest(self) -> DryRunStateSnapshot | None:
        response = self._session.get(
            self._url("/dry-run/latest"),
            headers=self._headers(body=b""),
            timeout=self._config.timeout_seconds,
        )
        if response.status_code == 204:
            return None
        response.raise_for_status()
        payload = response.json()
        if not payload:
            return None
        return self._parse_state(payload)

    def push_snapshot(self, state: DryRunStateSnapshot) -> dict[str, Any]:
        payload = {
            "portfolio": {
                "timestamp": state.portfolio.timestamp.isoformat(),
                "cash": state.portfolio.cash,
                "equity": state.portfolio.equity,
                "realizedPnl": state.portfolio.realized_pnl,
                "unrealizedPnl": state.portfolio.unrealized_pnl,
                "totalFees": state.portfolio.total_fees,
            },
            "positions": [
                {
                    "positionId": position.position_id,
                    "symbol": position.symbol,
                    "marketType": position.market_type,
                    "side": position.side,
                    "quantity": position.quantity,
                    "entryPrice": position.entry_price,
                    "currentPrice": position.current_price,
                    "unrealizedPnl": position.unrealized_pnl,
                    "openedAt": position.opened_at.isoformat(),
                    "sourceSignalId": position.source_signal_id,
                }
                for position in state.positions
            ],
            "closedTrades": [
                {
                    "tradeId": trade.trade_id,
                    "symbol": trade.symbol,
                    "marketType": trade.market_type,
                    "side": trade.side,
                    "quantity": trade.quantity,
                    "entryPrice": trade.entry_price,
                    "exitPrice": trade.exit_price,
                    "pnl": trade.pnl,
                    "fees": trade.fees,
                    "entryTimestamp": trade.entry_timestamp.isoformat(),
                    "exitTimestamp": trade.exit_timestamp.isoformat(),
                    "entrySignalId": trade.entry_signal_id,
                    "exitSignalId": trade.exit_signal_id,
                }
                for trade in state.closed_trades
            ],
        }
        body = canonical_json_bytes(payload)
        response = self._session.post(
            self._url("/dry-run/sync"),
            headers=self._headers(body=body),
            data=body,
            timeout=self._config.timeout_seconds,
        )
        return response_json_or_empty(response)

    def _parse_state(self, payload: dict[str, Any]) -> DryRunStateSnapshot:
        portfolio = payload["portfolio"]
        positions = payload.get("positions") or []
        closed_trades = payload.get("closedTrades") or payload.get("closed_trades") or []
        return DryRunStateSnapshot(
            portfolio=DryRunPortfolioSnapshot(
                timestamp=self._parse_timestamp(portfolio["timestamp"]),
                cash=float(portfolio.get("cash") or 0.0),
                equity=float(portfolio.get("equity") or 0.0),
                realized_pnl=float(portfolio.get("realizedPnl") or portfolio.get("realized_pnl") or 0.0),
                unrealized_pnl=float(portfolio.get("unrealizedPnl") or portfolio.get("unrealized_pnl") or 0.0),
                total_fees=float(portfolio.get("totalFees") or portfolio.get("total_fees") or 0.0),
            ),
            positions=[
                DryRunPositionSnapshot(
                    position_id=str(position["positionId"]),
                    symbol=str(position["symbol"]),
                    market_type=str(position["marketType"]),
                    side=str(position["side"]),
                    quantity=float(position["quantity"]),
                    entry_price=float(position["entryPrice"]),
                    current_price=float(position["currentPrice"]),
                    unrealized_pnl=float(position.get("unrealizedPnl") or 0.0),
                    opened_at=self._parse_timestamp(position["openedAt"]),
                    source_signal_id=position.get("sourceSignalId"),
                    status=str(position.get("status") or "OPEN"),
                )
                for position in positions
            ],
            closed_trades=[
                DryRunClosedTradeSnapshot(
                    trade_id=str(trade["tradeId"]),
                    symbol=str(trade["symbol"]),
                    market_type=str(trade["marketType"]),
                    side=str(trade["side"]),
                    quantity=float(trade["quantity"]),
                    entry_price=float(trade["entryPrice"]),
                    exit_price=float(trade["exitPrice"]),
                    pnl=float(trade.get("pnl") or 0.0),
                    fees=float(trade.get("fees") or 0.0),
                    entry_timestamp=self._parse_timestamp(trade["entryTimestamp"]),
                    exit_timestamp=self._parse_timestamp(trade["exitTimestamp"]),
                    entry_signal_id=trade.get("entrySignalId"),
                    exit_signal_id=trade.get("exitSignalId"),
                )
                for trade in closed_trades
            ],
        )

    def _url(self, suffix: str) -> str:
        return f"{self._config.base_url.rstrip('/')}/api/v1/bots/{self._config.bot_id}{suffix}"

    def _headers(self, *, body: bytes) -> dict[str, str]:
        return build_auth_headers(
            api_key=self._config.api_key,
            body=body,
            signer_secret=self._config.signer_secret,
        )

    def _parse_timestamp(self, value: Any):
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        from datetime import datetime
        return datetime.fromisoformat(text)


# Backward-compatible name for dry-run state transport only. Telemetry has its
# own client in telemetry.py and is intentionally no longer an alias here.
BotDryRunClient = DryRunSyncClient
