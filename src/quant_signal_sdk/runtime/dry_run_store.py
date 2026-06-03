from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .interfaces import PortfolioContext


@dataclass(frozen=True, slots=True)
class DryRunPortfolioSnapshot:
    timestamp: datetime
    cash: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    total_fees: float


@dataclass(frozen=True, slots=True)
class DryRunPositionSnapshot:
    position_id: str
    symbol: str
    market_type: str
    side: str
    quantity: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    opened_at: datetime
    source_signal_id: str | None = None
    status: str = "OPEN"


@dataclass(frozen=True, slots=True)
class DryRunClosedTradeSnapshot:
    trade_id: str
    symbol: str
    market_type: str
    side: str
    quantity: float
    entry_price: float
    exit_price: float
    pnl: float
    fees: float
    entry_timestamp: datetime
    exit_timestamp: datetime
    entry_signal_id: str | None = None
    exit_signal_id: str | None = None


@dataclass(frozen=True, slots=True)
class DryRunStateSnapshot:
    portfolio: DryRunPortfolioSnapshot
    positions: list[DryRunPositionSnapshot]
    closed_trades: list[DryRunClosedTradeSnapshot]

    def to_context(self) -> PortfolioContext:
        positions = {
            position.position_id: {
                "position_id": position.position_id,
                "symbol": position.symbol,
                "market_type": position.market_type,
                "side": position.side,
                "amount": position.quantity,
                "entry": position.entry_price,
                "current_price": position.current_price,
                "unrealized_pnl": position.unrealized_pnl,
                "opened_at": position.opened_at,
                "source_signal_id": position.source_signal_id,
                "status": position.status,
            }
            for position in self.positions
        }
        return PortfolioContext(
            positions=positions,
            cash=self.portfolio.cash,
            open_orders={},
            realized_pnl=self.portfolio.realized_pnl,
            unrealized_pnl=self.portfolio.unrealized_pnl,
            total_fees=self.portfolio.total_fees,
            equity=self.portfolio.equity,
            timestamp=self.portfolio.timestamp,
        )


class SQLiteDryRunStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def has_state(self) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(1) AS count FROM portfolio_snapshot").fetchone()
        return bool(row and row["count"])

    def save_portfolio(self, snapshot: DryRunPortfolioSnapshot) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO portfolio_snapshot
                (timestamp, cash, equity, realized_pnl, unrealized_pnl, total_fees)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    self._format_dt(snapshot.timestamp),
                    snapshot.cash,
                    snapshot.equity,
                    snapshot.realized_pnl,
                    snapshot.unrealized_pnl,
                    snapshot.total_fees,
                ),
            )
            conn.commit()

    def upsert_position(self, position: DryRunPositionSnapshot) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO positions
                (position_id, symbol, market_type, side, quantity, entry_price, current_price, unrealized_pnl, opened_at, source_signal_id, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    position.position_id,
                    position.symbol,
                    position.market_type,
                    position.side,
                    position.quantity,
                    position.entry_price,
                    position.current_price,
                    position.unrealized_pnl,
                    self._format_dt(position.opened_at),
                    position.source_signal_id,
                    position.status,
                ),
            )
            conn.commit()

    def get_position(self, position_id: str) -> DryRunPositionSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT position_id, symbol, market_type, side, quantity, entry_price, current_price, unrealized_pnl, opened_at, source_signal_id, status
                FROM positions
                WHERE position_id = ?
                """,
                (position_id,),
            ).fetchone()
        return self._position_from_row(row) if row else None

    def remove_position(self, position_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM positions WHERE position_id = ?", (position_id,))
            conn.commit()

    def upsert_closed_trade(self, trade: DryRunClosedTradeSnapshot) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO closed_trades
                (trade_id, symbol, market_type, side, quantity, entry_price, exit_price, pnl, fees, entry_timestamp, exit_timestamp, entry_signal_id, exit_signal_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade.trade_id,
                    trade.symbol,
                    trade.market_type,
                    trade.side,
                    trade.quantity,
                    trade.entry_price,
                    trade.exit_price,
                    trade.pnl,
                    trade.fees,
                    self._format_dt(trade.entry_timestamp),
                    self._format_dt(trade.exit_timestamp),
                    trade.entry_signal_id,
                    trade.exit_signal_id,
                ),
            )
            conn.commit()

    def replace_state(self, state: DryRunStateSnapshot) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM portfolio_snapshot")
            conn.execute("DELETE FROM positions")
            conn.execute("DELETE FROM closed_trades")
            conn.execute(
                """
                INSERT INTO portfolio_snapshot
                (timestamp, cash, equity, realized_pnl, unrealized_pnl, total_fees)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    self._format_dt(state.portfolio.timestamp),
                    state.portfolio.cash,
                    state.portfolio.equity,
                    state.portfolio.realized_pnl,
                    state.portfolio.unrealized_pnl,
                    state.portfolio.total_fees,
                ),
            )
            for position in state.positions:
                conn.execute(
                    """
                    INSERT INTO positions
                    (position_id, symbol, market_type, side, quantity, entry_price, current_price, unrealized_pnl, opened_at, source_signal_id, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        position.position_id,
                        position.symbol,
                        position.market_type,
                        position.side,
                        position.quantity,
                        position.entry_price,
                        position.current_price,
                        position.unrealized_pnl,
                        self._format_dt(position.opened_at),
                        position.source_signal_id,
                        position.status,
                    ),
                )
            for trade in state.closed_trades:
                conn.execute(
                    """
                    INSERT INTO closed_trades
                    (trade_id, symbol, market_type, side, quantity, entry_price, exit_price, pnl, fees, entry_timestamp, exit_timestamp, entry_signal_id, exit_signal_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trade.trade_id,
                        trade.symbol,
                        trade.market_type,
                        trade.side,
                        trade.quantity,
                        trade.entry_price,
                        trade.exit_price,
                        trade.pnl,
                        trade.fees,
                        self._format_dt(trade.entry_timestamp),
                        self._format_dt(trade.exit_timestamp),
                        trade.entry_signal_id,
                        trade.exit_signal_id,
                    ),
                )
            conn.commit()

    def load_state(self) -> DryRunStateSnapshot | None:
        with self._connect() as conn:
            portfolio_row = conn.execute(
                """
                SELECT timestamp, cash, equity, realized_pnl, unrealized_pnl, total_fees
                FROM portfolio_snapshot
                ORDER BY timestamp DESC
                LIMIT 1
                """
            ).fetchone()
            if portfolio_row is None:
                return None
            position_rows = conn.execute(
                """
                SELECT position_id, symbol, market_type, side, quantity, entry_price, current_price, unrealized_pnl, opened_at, source_signal_id, status
                FROM positions
                ORDER BY opened_at ASC
                """
            ).fetchall()
            trade_rows = conn.execute(
                """
                SELECT trade_id, symbol, market_type, side, quantity, entry_price, exit_price, pnl, fees, entry_timestamp, exit_timestamp, entry_signal_id, exit_signal_id
                FROM closed_trades
                ORDER BY exit_timestamp ASC
                """
            ).fetchall()
        return DryRunStateSnapshot(
            portfolio=self._portfolio_from_row(portfolio_row),
            positions=[self._position_from_row(row) for row in position_rows],
            closed_trades=[self._trade_from_row(row) for row in trade_rows],
        )

    def snapshot_payload(self) -> dict[str, Any] | None:
        state = self.load_state()
        if state is None:
            return None
        return {
            "portfolio": {
                "timestamp": self._format_dt(state.portfolio.timestamp),
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
                    "openedAt": self._format_dt(position.opened_at),
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
                    "entryTimestamp": self._format_dt(trade.entry_timestamp),
                    "exitTimestamp": self._format_dt(trade.exit_timestamp),
                    "entrySignalId": trade.entry_signal_id,
                    "exitSignalId": trade.exit_signal_id,
                }
                for trade in state.closed_trades
            ],
        }

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS portfolio_snapshot (
                    timestamp TEXT PRIMARY KEY,
                    cash REAL NOT NULL,
                    equity REAL NOT NULL,
                    realized_pnl REAL NOT NULL,
                    unrealized_pnl REAL NOT NULL,
                    total_fees REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    position_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    market_type TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    current_price REAL NOT NULL,
                    unrealized_pnl REAL NOT NULL,
                    opened_at TEXT NOT NULL,
                    source_signal_id TEXT,
                    status TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS closed_trades (
                    trade_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    market_type TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    pnl REAL NOT NULL,
                    fees REAL NOT NULL,
                    entry_timestamp TEXT NOT NULL,
                    exit_timestamp TEXT NOT NULL,
                    entry_signal_id TEXT,
                    exit_signal_id TEXT
                )
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def _portfolio_from_row(self, row: sqlite3.Row) -> DryRunPortfolioSnapshot:
        return DryRunPortfolioSnapshot(
            timestamp=self._parse_dt(row["timestamp"]),
            cash=float(row["cash"]),
            equity=float(row["equity"]),
            realized_pnl=float(row["realized_pnl"]),
            unrealized_pnl=float(row["unrealized_pnl"]),
            total_fees=float(row["total_fees"]),
        )

    def _position_from_row(self, row: sqlite3.Row) -> DryRunPositionSnapshot:
        return DryRunPositionSnapshot(
            position_id=str(row["position_id"]),
            symbol=str(row["symbol"]),
            market_type=str(row["market_type"]),
            side=str(row["side"]),
            quantity=float(row["quantity"]),
            entry_price=float(row["entry_price"]),
            current_price=float(row["current_price"]),
            unrealized_pnl=float(row["unrealized_pnl"]),
            opened_at=self._parse_dt(row["opened_at"]),
            source_signal_id=row["source_signal_id"],
            status=str(row["status"]),
        )

    def _trade_from_row(self, row: sqlite3.Row) -> DryRunClosedTradeSnapshot:
        return DryRunClosedTradeSnapshot(
            trade_id=str(row["trade_id"]),
            symbol=str(row["symbol"]),
            market_type=str(row["market_type"]),
            side=str(row["side"]),
            quantity=float(row["quantity"]),
            entry_price=float(row["entry_price"]),
            exit_price=float(row["exit_price"]),
            pnl=float(row["pnl"]),
            fees=float(row["fees"]),
            entry_timestamp=self._parse_dt(row["entry_timestamp"]),
            exit_timestamp=self._parse_dt(row["exit_timestamp"]),
            entry_signal_id=row["entry_signal_id"],
            exit_signal_id=row["exit_signal_id"],
        )

    def _format_dt(self, value: datetime) -> str:
        return value.isoformat()

    def _parse_dt(self, value: str) -> datetime:
        text = value
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
