from __future__ import annotations

import math
import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from ..models import ExecutionPolicies, OrderType, SignalAction, SignalPayload
from .interfaces import BaseFeed, BaseStrategy, MarketEvent, PortfolioContext

if TYPE_CHECKING:
    import pandas as pd


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_cash: float = 0.0
    maker_fee_rate: float = 0.0
    taker_fee_rate: float = 0.0
    slippage_rate: float = 0.0
    default_max_size_percent: float | None = None


@dataclass(slots=True)
class BacktestOrder:
    order_id: str
    signal: SignalPayload
    symbol: str
    market_type: str
    order_type: OrderType
    action: SignalAction
    side: str
    quantity: float
    limit_price: float | None
    created_at: datetime
    eligible_at: datetime
    cancel_after: datetime | None
    status: str = "OPEN"
    filled_quantity: float = 0.0
    fill_price: float | None = None
    fee_paid: float = 0.0


@dataclass(frozen=True, slots=True)
class BacktestFill:
    order_id: str
    signal_id: str | None
    symbol: str
    market_type: str
    action: SignalAction
    side: str
    quantity: float
    price: float
    fee: float
    timestamp: datetime
    fee_type: str


@dataclass(frozen=True, slots=True)
class EquityPoint:
    timestamp: datetime
    cash: float
    unrealized_pnl: float
    realized_pnl: float
    total_fees: float
    equity: float


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    symbol: str
    market_type: str
    side: str
    entry_timestamp: datetime
    exit_timestamp: datetime
    quantity: float
    entry_price: float
    exit_price: float
    entry_fees: float
    exit_fees: float
    pnl: float
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    total_return: float
    annualized_return: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    win_rate: float
    profit_factor: float
    time_in_market: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    gross_profit: float
    gross_loss: float
    final_equity: float
    rebalance_events: int = 0
    coin_swaps: int = 0
    unique_symbols_traded: int = 0


@dataclass(frozen=True, slots=True)
class BacktestReport:
    context: PortfolioContext
    fills: list[BacktestFill] = field(default_factory=list)
    orders: list[BacktestOrder] = field(default_factory=list)
    equity_history: list[EquityPoint] = field(default_factory=list)
    candle_history: list[dict[str, Any]] = field(default_factory=list)
    closed_trades: list[ClosedTrade] = field(default_factory=list)
    metrics: BacktestMetrics | None = None


class OhlcvReplayFeed(BaseFeed):
    def __init__(self, dataframe: "pd.DataFrame", timestamp_column: str | None = None) -> None:
        self._dataframe = dataframe
        self._timestamp_column = timestamp_column

    def stream(self) -> Iterator[MarketEvent]:
        columns = list(self._dataframe.columns)
        required = {"open", "high", "low", "close"}
        missing = required.difference({str(column).lower() for column in columns})
        if missing:
            raise ValueError(f"ohlcv replay requires columns: {', '.join(sorted(missing))}")

        timestamp_index = self._timestamp_position(columns)
        for row in self._dataframe.itertuples(index=True, name=None):
            timestamp = self._extract_timestamp(row, timestamp_index)
            payload = self._extract_payload(row, columns, timestamp_index)
            yield MarketEvent(timestamp=timestamp, payload=payload)

    def _timestamp_position(self, columns: list[str]) -> int | None:
        if self._timestamp_column is None:
            return None
        try:
            return columns.index(self._timestamp_column) + 1
        except ValueError:
            return None

    def _extract_timestamp(self, row: tuple[Any, ...], timestamp_index: int | None) -> datetime:
        if timestamp_index is not None and timestamp_index < len(row):
            return self._coerce_timestamp(row[timestamp_index])

        index_value = row[0]
        if isinstance(index_value, datetime):
            return index_value if index_value.tzinfo else index_value.replace(tzinfo=timezone.utc)
        if hasattr(index_value, "to_pydatetime"):
            value = index_value.to_pydatetime()
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc)

    def _extract_payload(self, row: tuple[Any, ...], columns: list[str], timestamp_index: int | None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for position, column_name in enumerate(columns, start=1):
            if timestamp_index is not None and position == timestamp_index:
                continue
            value = row[position]
            if value is not None:
                payload[str(column_name)] = value
        return payload

    def _coerce_timestamp(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, (int, float)):
            if value > 1e11:
                return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class PortfolioBacktestRunner:
    def __init__(
        self,
        *,
        feed: BaseFeed,
        strategy: BaseStrategy,
        config: BacktestConfig | None = None,
        initial_context: PortfolioContext | None = None,
    ) -> None:
        self._feed = feed
        self._strategy = strategy
        self._config = config or BacktestConfig()
        self._context = initial_context or PortfolioContext(cash=self._config.initial_cash, equity=self._config.initial_cash)
        self._pending_orders: list[BacktestOrder] = []
        self._open_orders: dict[str, BacktestOrder] = {}
        self._order_history: list[BacktestOrder] = []
        self._fills: list[BacktestFill] = []
        self._equity_history: list[EquityPoint] = []
        self._candle_history: list[dict[str, Any]] = []

    @property
    def context(self) -> PortfolioContext:
        return self._context

    def run(self) -> BacktestReport:
        for event in self._feed.stream():
            self._process_event(event)
        return BacktestReport(
            context=self._context,
            fills=list(self._fills),
            orders=list(self._order_history),
            equity_history=list(self._equity_history),
            candle_history=list(self._candle_history),
            closed_trades=self._derive_closed_trades(),
            metrics=self._compute_metrics(),
        )

    def _process_event(self, event: MarketEvent) -> None:
        candle = self._extract_candle(event.payload)
        self._candle_history.append({
            "timestamp": event.timestamp,
            "open": candle["open"],
            "high": candle["high"],
            "low": candle["low"],
            "close": candle["close"],
            "payload": dict(event.payload),
        })
        self._materialize_pending_orders(event.timestamp)
        self._sweep_expired_orders(event.timestamp)
        self._match_market_orders(event.timestamp, candle)
        self._match_limit_orders(event.timestamp, candle)
        self._mark_to_market(event.timestamp, candle)
        self._equity_history.append(
            EquityPoint(
                timestamp=event.timestamp,
                cash=self._context.cash,
                unrealized_pnl=self._context.unrealized_pnl,
                realized_pnl=self._context.realized_pnl,
                total_fees=self._context.total_fees,
                equity=self._context.equity,
            )
        )
        self._invoke_strategy(event)

    def _invoke_strategy(self, event: MarketEvent) -> None:
        signals = self._strategy.on_event(event, self._snapshot_context())
        for signal in signals:
            self._admit_signal(signal, event)

    def _admit_signal(self, signal: SignalPayload, event: MarketEvent) -> None:
        reference_price = self._reference_price(signal, event.payload)
        notional = self._signal_notional(signal, reference_price)
        max_size_percent = self._resolve_max_size_percent(signal.policies)
        if max_size_percent is not None:
            max_notional = self._context.cash * max_size_percent
            if notional > max_notional:
                logger.warning(
                    "rejecting signal due to max_size_percent signalId=%s symbol=%s notional=%.8f limit=%.8f",
                    signal.signal_id,
                    signal.symbol,
                    notional,
                    max_notional,
                )
                return

        order = self._build_order(signal=signal, reference_price=reference_price, created_at=event.timestamp)
        self._order_history.append(order)
        self._pending_orders.append(order)

    def _materialize_pending_orders(self, current_timestamp: datetime) -> None:
        if not self._pending_orders:
            return
        for order in self._pending_orders:
            order.eligible_at = current_timestamp
            self._open_orders[order.order_id] = order
        self._pending_orders.clear()

    def _sweep_expired_orders(self, current_timestamp: datetime) -> None:
        for order_id, order in list(self._open_orders.items()):
            if order.cancel_after is not None and current_timestamp > order.cancel_after:
                order.status = "CANCELED"
                del self._open_orders[order_id]

    def _match_market_orders(self, current_timestamp: datetime, candle: dict[str, float]) -> None:
        for order_id, order in list(self._open_orders.items()):
            if order.order_type != OrderType.MARKET:
                continue
            fill_price = self._apply_slippage(candle["open"], order.side)
            self._fill_order(order_id, order, fill_price, current_timestamp, fee_type="TAKER")

    def _match_limit_orders(self, current_timestamp: datetime, candle: dict[str, float]) -> None:
        for order_id, order in list(self._open_orders.items()):
            if order.order_type != OrderType.LIMIT or order.limit_price is None:
                continue
            if not self._limit_touched(order, candle):
                continue
            self._fill_order(order_id, order, order.limit_price, current_timestamp, fee_type="MAKER")

    def _mark_to_market(self, current_timestamp: datetime, candle: dict[str, float]) -> None:
        unrealized = 0.0
        for position in self._context.positions.values():
            if not isinstance(position, dict):
                continue
            quantity = float(position.get("quantity") or position.get("amount") or 0.0)
            entry_price = float(position.get("average_entry_price") or position.get("entry_price") or 0.0)
            side = str(position.get("side") or "LONG").upper()
            if quantity == 0.0 or entry_price <= 0.0:
                continue
            if side == "SHORT" or float(position.get("net_quantity") or 0.0) < 0.0:
                unrealized += (entry_price - candle["close"]) * abs(quantity)
            else:
                unrealized += (candle["close"] - entry_price) * abs(quantity)

        self._context = PortfolioContext(
            positions=self._copy_mapping(self._context.positions),
            cash=self._context.cash,
            open_orders=self._copy_mapping(self._open_orders),
            realized_pnl=self._context.realized_pnl,
            unrealized_pnl=unrealized,
            total_fees=self._context.total_fees,
            equity=self._context.cash + unrealized,
            timestamp=current_timestamp,
        )

    def _fill_order(self, order_id: str, order: BacktestOrder, price: float, current_timestamp: datetime, fee_type: str) -> None:
        fee_rate = self._config.taker_fee_rate if fee_type == "TAKER" else self._config.maker_fee_rate
        fee = abs(order.quantity * price) * fee_rate
        quantity = abs(order.quantity)
        signed_quantity = quantity if order.side == "BUY" else -quantity
        self._apply_fill(order, price, fee, signed_quantity)
        order.status = "FILLED"
        order.filled_quantity = quantity
        order.fill_price = price
        order.fee_paid = fee
        self._fills.append(
            BacktestFill(
                order_id=order.order_id,
                signal_id=order.signal.signal_id,
                symbol=order.symbol,
                market_type=order.market_type,
                action=order.action,
                side=order.side,
                quantity=quantity,
                price=price,
                fee=fee,
                timestamp=current_timestamp,
                fee_type=fee_type,
            )
        )
        self._open_orders.pop(order_id, None)

    def _apply_fill(self, order: BacktestOrder, price: float, fee: float, signed_quantity: float) -> None:
        position_key = f"{order.market_type}:{order.symbol}"
        position = self._context.positions.get(position_key)
        cash = self._context.cash
        realized_pnl = self._context.realized_pnl
        total_fees = self._context.total_fees + fee

        if position is None:
            cash, realized_pnl = self._open_new_position(position_key, order, price, fee, signed_quantity, cash, realized_pnl)
        else:
            cash, realized_pnl = self._update_existing_position(position_key, position, order, price, fee, signed_quantity, cash, realized_pnl)

        self._context = PortfolioContext(
            positions=self._copy_mapping(self._context.positions),
            cash=cash,
            open_orders=self._copy_mapping(self._open_orders),
            realized_pnl=realized_pnl,
            unrealized_pnl=self._context.unrealized_pnl,
            total_fees=total_fees,
            equity=cash + self._context.unrealized_pnl,
            timestamp=self._context.timestamp,
        )

    def _open_new_position(
        self,
        position_key: str,
        order: BacktestOrder,
        price: float,
        fee: float,
        signed_quantity: float,
        cash: float,
        realized_pnl: float,
    ) -> tuple[float, float]:
        side = "LONG" if signed_quantity > 0 else "SHORT"
        quantity = abs(signed_quantity)
        position = {
            "symbol": order.symbol,
            "market_type": order.market_type,
            "side": side,
            "quantity": quantity,
            "amount": quantity,
            "net_quantity": signed_quantity,
            "average_entry_price": price,
            "entry_price": price,
            "opening_fee": fee,
            "opened_at": order.created_at,
            "generated_timestamp": order.signal.generated_timestamp,
            "signal": order.signal.model_copy(deep=True),
        }
        self._context.positions[position_key] = position
        if side == "LONG":
            cash -= quantity * price + fee
        else:
            cash += quantity * price - fee
        return cash, realized_pnl

    def _update_existing_position(
        self,
        position_key: str,
        position: dict[str, Any],
        order: BacktestOrder,
        price: float,
        fee: float,
        signed_quantity: float,
        cash: float,
        realized_pnl: float,
    ) -> tuple[float, float]:
        existing_quantity = float(position.get("net_quantity") or position.get("quantity") or 0.0)
        existing_side = "LONG" if existing_quantity >= 0 else "SHORT"
        entry_price = float(position.get("average_entry_price") or position.get("entry_price") or 0.0)
        opening_fee = float(position.get("opening_fee") or 0.0)
        closing_quantity = abs(signed_quantity)

        if existing_side == "LONG" and signed_quantity > 0:
            total_quantity = abs(existing_quantity) + closing_quantity
            weighted_price = ((entry_price * abs(existing_quantity)) + (price * closing_quantity)) / total_quantity
            position["quantity"] = total_quantity
            position["amount"] = total_quantity
            position["net_quantity"] = total_quantity
            position["average_entry_price"] = weighted_price
            position["entry_price"] = weighted_price
            position["opening_fee"] = opening_fee + fee
            cash -= closing_quantity * price + fee
            return cash, realized_pnl

        if existing_side == "SHORT" and signed_quantity < 0:
            total_quantity = abs(existing_quantity) + closing_quantity
            weighted_price = ((entry_price * abs(existing_quantity)) + (price * closing_quantity)) / total_quantity
            position["quantity"] = total_quantity
            position["amount"] = total_quantity
            position["net_quantity"] = -total_quantity
            position["average_entry_price"] = weighted_price
            position["entry_price"] = weighted_price
            position["opening_fee"] = opening_fee + fee
            cash += closing_quantity * price - fee
            return cash, realized_pnl

        close_quantity = min(abs(existing_quantity), closing_quantity)
        if existing_side == "LONG":
            cash += close_quantity * price - fee
            realized_pnl += (price - entry_price) * close_quantity - opening_fee - fee
        else:
            cash -= close_quantity * price + fee
            realized_pnl += (entry_price - price) * close_quantity - opening_fee - fee

        remaining_quantity = abs(existing_quantity) - close_quantity
        if remaining_quantity <= 0:
            self._context.positions.pop(position_key, None)
            return cash, realized_pnl

        if existing_side == "LONG":
            position["quantity"] = remaining_quantity
            position["amount"] = remaining_quantity
            position["net_quantity"] = remaining_quantity
        else:
            position["quantity"] = remaining_quantity
            position["amount"] = remaining_quantity
            position["net_quantity"] = -remaining_quantity
        position["opening_fee"] = opening_fee
        return cash, realized_pnl

    def _build_order(self, *, signal: SignalPayload, reference_price: float, created_at: datetime) -> BacktestOrder:
        order_id = signal.signal_id or f"order_{uuid.uuid4()}"
        side = self._signal_side(signal.action)
        return BacktestOrder(
            order_id=order_id,
            signal=signal.model_copy(deep=True),
            symbol=signal.symbol,
            market_type=signal.market_type.value,
            order_type=signal.order_type,
            action=signal.action,
            side=side,
            quantity=abs(float(signal.amount or 0.0)),
            limit_price=float(signal.entry if signal.entry is not None else reference_price),
            created_at=created_at,
            eligible_at=created_at,
            cancel_after=self._resolve_cancel_after(signal.policies),
        )

    def _signal_side(self, action: SignalAction) -> str:
        if action in {SignalAction.OPEN_LONG, SignalAction.CLOSE_SHORT}:
            return "BUY"
        if action in {SignalAction.OPEN_SHORT, SignalAction.CLOSE_LONG}:
            return "SELL"
        return "BUY"

    def _reference_price(self, signal: SignalPayload, payload: dict[str, Any]) -> float:
        if signal.entry is not None:
            return float(signal.entry)
        candle = self._extract_candle(payload)
        return candle["close"]

    def _signal_notional(self, signal: SignalPayload, reference_price: float) -> float:
        return abs(float(signal.amount or 0.0)) * reference_price

    def _resolve_max_size_percent(self, policies: ExecutionPolicies | None) -> float | None:
        if policies is not None and policies.max_size_percent is not None:
            return float(policies.max_size_percent)
        if self._config.default_max_size_percent is not None:
            return float(self._config.default_max_size_percent)
        return None

    def _resolve_cancel_after(self, policies: ExecutionPolicies | None) -> datetime | None:
        if policies is None or policies.cancel_order_after is None:
            return None
        return datetime.fromtimestamp(float(policies.cancel_order_after), tz=timezone.utc)

    def _extract_candle(self, payload: dict[str, Any]) -> dict[str, float]:
        if {"open", "high", "low", "close"}.issubset(payload.keys()):
            return {
                "open": float(payload["open"]),
                "high": float(payload["high"]),
                "low": float(payload["low"]),
                "close": float(payload["close"]),
            }
        ohlcv = payload.get("ohlcv")
        if isinstance(ohlcv, (list, tuple)) and len(ohlcv) >= 5:
            return {"open": float(ohlcv[1]), "high": float(ohlcv[2]), "low": float(ohlcv[3]), "close": float(ohlcv[4])}
        raise ValueError("MarketEvent payload must contain OHLCV candle fields")

    def _apply_slippage(self, price: float, side: str) -> float:
        slippage = self._config.slippage_rate
        if side == "BUY":
            return price * (1.0 + slippage)
        return price * (1.0 - slippage)

    def _limit_touched(self, order: BacktestOrder, candle: dict[str, float]) -> bool:
        price = float(order.limit_price or 0.0)
        return candle["low"] <= price <= candle["high"]

    def _snapshot_context(self) -> PortfolioContext:
        return PortfolioContext(
            positions=self._copy_mapping(self._context.positions),
            cash=self._context.cash,
            open_orders=self._copy_mapping(self._open_orders),
            realized_pnl=self._context.realized_pnl,
            unrealized_pnl=self._context.unrealized_pnl,
            total_fees=self._context.total_fees,
            equity=self._context.equity,
            timestamp=self._context.timestamp,
        )

    def _copy_mapping(self, mapping: dict[str, Any]) -> dict[str, Any]:
        copied: dict[str, Any] = {}
        for key, value in mapping.items():
            copied[key] = value.copy() if isinstance(value, dict) else value
        return copied

    def _derive_closed_trades(self) -> list[ClosedTrade]:
        by_key: dict[str, list[dict[str, Any]]] = {}
        closed_trades: list[ClosedTrade] = []

        for fill in self._fills:
            key = f"{fill.market_type}:{fill.symbol}"
            queue = by_key.setdefault(key, [])
            side = "LONG" if fill.action in {SignalAction.OPEN_LONG, SignalAction.CLOSE_SHORT} else "SHORT"
            direction = 1 if side == "LONG" else -1
            signed_qty = direction * fill.quantity

            if not queue:
                queue.append(
                    {
                        "side": side,
                        "quantity": fill.quantity,
                        "entry_price": fill.price,
                        "entry_timestamp": fill.timestamp,
                        "entry_fees": fill.fee,
                    }
                )
                continue

            current_side = queue[0]["side"]
            current_net = sum(item["quantity"] for item in queue)

            if current_side == side:
                queue.append(
                    {
                        "side": side,
                        "quantity": fill.quantity,
                        "entry_price": fill.price,
                        "entry_timestamp": fill.timestamp,
                        "entry_fees": fill.fee,
                    }
                )
                continue

            remaining = fill.quantity
            exit_fees_remaining = fill.fee

            while remaining > 0 and queue:
                entry = queue[0]
                close_qty = min(entry["quantity"], remaining)
                proportion = close_qty / entry["quantity"] if entry["quantity"] else 0.0
                entry_fees = entry["entry_fees"] * proportion
                exit_fees = exit_fees_remaining * (close_qty / remaining) if remaining else 0.0
                if entry["side"] == "LONG":
                    pnl = (fill.price - entry["entry_price"]) * close_qty - entry_fees - exit_fees
                else:
                    pnl = (entry["entry_price"] - fill.price) * close_qty - entry_fees - exit_fees
                closed_trades.append(
                    ClosedTrade(
                        symbol=fill.symbol,
                        market_type=fill.market_type,
                        side=entry["side"],
                        entry_timestamp=entry["entry_timestamp"],
                        exit_timestamp=fill.timestamp,
                        quantity=close_qty,
                        entry_price=entry["entry_price"],
                        exit_price=fill.price,
                        entry_fees=entry_fees,
                        exit_fees=exit_fees,
                        pnl=pnl,
                        duration_seconds=(fill.timestamp - entry["entry_timestamp"]).total_seconds(),
                    )
                )
                remaining -= close_qty
                exit_fees_remaining -= exit_fees
                if close_qty >= entry["quantity"]:
                    queue.pop(0)
                else:
                    entry["quantity"] -= close_qty
                    entry["entry_fees"] -= entry_fees

            if remaining > 0:
                queue.insert(
                    0,
                    {
                        "side": side,
                        "quantity": remaining,
                        "entry_price": fill.price,
                        "entry_timestamp": fill.timestamp,
                        "entry_fees": exit_fees_remaining,
                    },
                )

            if current_net == 0:
                by_key[key] = queue

        return closed_trades

    def _compute_metrics(self) -> BacktestMetrics:
        if not self._equity_history:
            final_equity = self._context.equity
            return BacktestMetrics(
                total_return=0.0,
                annualized_return=0.0,
                sharpe_ratio=0.0,
                sortino_ratio=0.0,
                max_drawdown=0.0,
                win_rate=0.0,
                profit_factor=0.0,
                time_in_market=0.0,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                gross_profit=0.0,
                gross_loss=0.0,
                final_equity=final_equity,
                rebalance_events=getattr(self._strategy, "_rebalance_events", 0),
                coin_swaps=getattr(self._strategy, "_coin_swaps", 0),
                unique_symbols_traded=len(getattr(self._strategy, "_unique_symbols_traded", set())),
            )

        equities = [point.equity for point in self._equity_history]
        running_max = equities[0]
        max_drawdown = 0.0
        for equity in equities:
            running_max = max(running_max, equity)
            if running_max > 0:
                drawdown = (running_max - equity) / running_max
                max_drawdown = max(max_drawdown, drawdown)

        returns: list[float] = []
        for previous, current in zip(equities, equities[1:]):
            if previous > 0:
                returns.append((current - previous) / previous)

        mean_return = sum(returns) / len(returns) if returns else 0.0
        variance = sum((value - mean_return) ** 2 for value in returns) / (len(returns) - 1) if len(returns) > 1 else 0.0
        std_dev = math.sqrt(variance) if variance > 0 else 0.0
        downside_returns = [value for value in returns if value < 0]
        downside_variance = (
            sum((value - (sum(downside_returns) / len(downside_returns))) ** 2 for value in downside_returns) / (len(downside_returns) - 1)
            if len(downside_returns) > 1
            else 0.0
        )
        downside_dev = math.sqrt(downside_variance) if downside_variance > 0 else 0.0

        total_trades = len(self._derive_closed_trades())
        winning_trades = sum(1 for trade in self._derive_closed_trades() if trade.pnl > 0)
        losing_trades = sum(1 for trade in self._derive_closed_trades() if trade.pnl < 0)
        gross_profit = sum(trade.pnl for trade in self._derive_closed_trades() if trade.pnl > 0)
        gross_loss = abs(sum(trade.pnl for trade in self._derive_closed_trades() if trade.pnl < 0))
        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        else:
            profit_factor = math.inf if gross_profit > 0 else 0.0
        win_rate = winning_trades / total_trades if total_trades > 0 else 0.0
        sharpe_ratio = (mean_return / std_dev) * math.sqrt(252.0) if std_dev > 0 else 0.0
        if downside_dev > 0:
            sortino_ratio = (mean_return / downside_dev) * math.sqrt(252.0)
        else:
            sortino_ratio = math.inf if mean_return > 0 else 0.0
        total_duration = (self._equity_history[-1].timestamp - self._equity_history[0].timestamp).total_seconds()
        time_in_market_seconds = sum(trade.duration_seconds for trade in self._derive_closed_trades())
        time_in_market = time_in_market_seconds / total_duration if total_duration > 0 else 0.0
        final_equity = equities[-1]
        total_return = (final_equity - self._config.initial_cash) / self._config.initial_cash if self._config.initial_cash > 0 else 0.0
        annualized_return = total_return

        return BacktestMetrics(
            total_return=total_return,
            annualized_return=annualized_return,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            max_drawdown=max_drawdown,
            win_rate=win_rate,
            profit_factor=profit_factor,
            time_in_market=time_in_market,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            final_equity=final_equity,
            rebalance_events=getattr(self._strategy, "_rebalance_events", 0),
            coin_swaps=getattr(self._strategy, "_coin_swaps", 0),
            unique_symbols_traded=len(getattr(self._strategy, "_unique_symbols_traded", set())),
        )