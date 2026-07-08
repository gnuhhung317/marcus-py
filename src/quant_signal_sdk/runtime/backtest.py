from __future__ import annotations

import copy
import logging
import math
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..models import ExecutionPolicies, MarketType, OrderType, SignalAction, SignalPayload
from .interfaces import BaseFeed, BaseStrategy, MarketEvent, PortfolioContext

if TYPE_CHECKING:
    import pandas as pd


logger = logging.getLogger(__name__)

_TIMEFRAME_PATTERN = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)


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
    position_key: str
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
    clamped_orders: int = 0


@dataclass(frozen=True, slots=True)
class BacktestReport:
    context: PortfolioContext
    fills: list[BacktestFill] = field(default_factory=list)
    orders: list[BacktestOrder] = field(default_factory=list)
    equity_history: list[EquityPoint] = field(default_factory=list)
    candle_history: list[dict[str, Any]] = field(default_factory=list)
    closed_trades: list[ClosedTrade] = field(default_factory=list)
    metrics: BacktestMetrics | None = None


@dataclass(frozen=True, slots=True)
class QuoteSnapshot:
    symbol: str
    market_type: str
    position_key: str
    timestamp: datetime
    close: float
    open: float | None = None
    high: float | None = None
    low: float | None = None

    @property
    def executable(self) -> bool:
        return self.open is not None and self.high is not None and self.low is not None


@dataclass(frozen=True, slots=True)
class PositionLot:
    quantity: float
    entry_price: float
    entry_timestamp: datetime
    entry_fees: float


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
        self._closed_trades: list[ClosedTrade] = []
        self._quote_registry: dict[str, QuoteSnapshot] = {}
        self._last_quote_timestamp: dict[str, datetime] = {}
        self._quote_cadence_seconds: dict[str, float] = {}
        self._clamped_orders = 0
        self._close_lag_seconds: list[float] = []

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
            closed_trades=list(self._closed_trades),
            metrics=self._compute_metrics(),
        )

    def _process_event(self, event: MarketEvent) -> None:
        updated_quotes = self._extract_quotes(event)
        self._update_quote_registry(updated_quotes)
        self._record_candle_history(event, updated_quotes)
        self._materialize_pending_orders(event.timestamp)
        self._sweep_expired_orders(event.timestamp)
        self._sweep_close_position_deadlines(event.timestamp, updated_quotes)
        self._match_orders(event.timestamp, updated_quotes)
        self._mark_to_market(event.timestamp)
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
        self._prune_quote_tracking()

    def _invoke_strategy(self, event: MarketEvent) -> None:
        signals = self._strategy.on_event(event, self._snapshot_context())
        for signal in signals:
            self._admit_signal(signal, event)

    def _extract_quotes(self, event: MarketEvent) -> dict[str, QuoteSnapshot]:
        payload = event.payload
        quotes: dict[str, QuoteSnapshot] = {}
        composite_prefixes = sorted(
            {
                key[:-7]
                for key in payload
                if key.endswith("_symbol") and key not in {"symbol"}
            }
        )

        if composite_prefixes:
            for prefix in composite_prefixes:
                raw_symbol = payload.get(f"{prefix}_symbol")
                symbol = self._normalize_symbol(raw_symbol)
                if not symbol:
                    continue
                market_type = self._resolve_market_type(payload.get(f"{prefix}_market_type"), prefix=prefix)
                close_value = payload.get(f"{prefix}_close")
                if close_value is None:
                    continue
                try:
                    close_price = float(close_value)
                except (TypeError, ValueError):
                    continue

                open_value = payload.get(f"{prefix}_open")
                high_value = payload.get(f"{prefix}_high")
                low_value = payload.get(f"{prefix}_low")
                open_price = self._safe_float(open_value)
                high_price = self._safe_float(high_value)
                low_price = self._safe_float(low_value)

                snapshot = QuoteSnapshot(
                    symbol=symbol,
                    market_type=market_type,
                    position_key=self._position_key(market_type, symbol),
                    timestamp=event.timestamp,
                    close=close_price,
                    open=open_price,
                    high=high_price,
                    low=low_price,
                )
                quotes[snapshot.position_key] = snapshot
            return quotes

        root_candle = self._extract_root_candle(payload)
        if root_candle is None:
            return quotes

        position_keys = self._resolve_flat_event_keys(payload)
        for position_key in position_keys:
            market_type, symbol = position_key.split(":", maxsplit=1)
            quotes[position_key] = QuoteSnapshot(
                symbol=symbol,
                market_type=market_type,
                position_key=position_key,
                timestamp=event.timestamp,
                open=root_candle["open"],
                high=root_candle["high"],
                low=root_candle["low"],
                close=root_candle["close"],
            )
        return quotes

    def _resolve_flat_event_keys(self, payload: dict[str, Any]) -> list[str]:
        raw_symbol = payload.get("symbol")
        symbol = self._normalize_symbol(raw_symbol)
        if symbol:
            market_type = self._resolve_market_type(payload.get("market_type"))
            return [self._position_key(market_type, symbol)]

        known = self._known_position_keys()
        if len(known) == 1:
            return sorted(known)
        return []

    def _known_position_keys(self) -> set[str]:
        known = set(self._context.positions.keys())
        known.update(order.position_key for order in self._open_orders.values())
        known.update(order.position_key for order in self._pending_orders)
        known.update(self._quote_registry.keys())
        return known

    def _update_quote_registry(self, updated_quotes: dict[str, QuoteSnapshot]) -> None:
        for position_key, snapshot in updated_quotes.items():
            previous_timestamp = self._last_quote_timestamp.get(position_key)
            if previous_timestamp is not None:
                cadence = (snapshot.timestamp - previous_timestamp).total_seconds()
                if cadence > 0:
                    current = self._quote_cadence_seconds.get(position_key)
                    if current is None or cadence < current:
                        self._quote_cadence_seconds[position_key] = cadence
            self._last_quote_timestamp[position_key] = snapshot.timestamp
            self._quote_registry[position_key] = snapshot

    def _prune_quote_tracking(self) -> None:
        active_keys = set(self._context.positions.keys())
        active_keys.update(order.position_key for order in self._open_orders.values())
        active_keys.update(order.position_key for order in self._pending_orders)
        for position_key in list(self._last_quote_timestamp.keys()):
            if position_key in active_keys:
                continue
            self._last_quote_timestamp.pop(position_key, None)
            self._quote_cadence_seconds.pop(position_key, None)

    def _record_candle_history(self, event: MarketEvent, updated_quotes: dict[str, QuoteSnapshot]) -> None:
        payload = dict(event.payload)
        if not updated_quotes:
            root_candle = self._extract_root_candle(event.payload)
            if root_candle is not None:
                self._candle_history.append(
                    {
                        "timestamp": event.timestamp,
                        "open": root_candle["open"],
                        "high": root_candle["high"],
                        "low": root_candle["low"],
                        "close": root_candle["close"],
                        "payload": payload,
                        "executable": False,
                    }
                )
                return
            self._candle_history.append({"timestamp": event.timestamp, "payload": payload})
            return

        for snapshot in updated_quotes.values():
            price = snapshot.close
            self._candle_history.append(
                {
                    "timestamp": event.timestamp,
                    "symbol": snapshot.symbol,
                    "market_type": snapshot.market_type,
                    "open": snapshot.open if snapshot.open is not None else price,
                    "high": snapshot.high if snapshot.high is not None else price,
                    "low": snapshot.low if snapshot.low is not None else price,
                    "close": price,
                    "payload": payload,
                    "executable": snapshot.executable,
                }
            )

    def _admit_signal(self, signal: SignalPayload, event: MarketEvent) -> None:
        reference_price = self._reference_price(signal, event.payload)
        order = self._build_order(signal=signal, reference_price=reference_price, created_at=event.timestamp)
        order.quantity = self._apply_size_policy(order, reference_price)
        if order.quantity <= 0.0:
            return
        self._order_history.append(order)
        self._pending_orders.append(order)

    def _apply_size_policy(self, order: BacktestOrder, reference_price: float) -> float:
        max_size_percent = self._resolve_max_size_percent(order.signal.policies)
        if max_size_percent is None:
            return order.quantity

        existing_position = self._context.positions.get(order.position_key)
        existing_quantity = 0.0
        if isinstance(existing_position, dict):
            existing_quantity = float(existing_position.get("net_quantity") or existing_position.get("quantity") or 0.0)

        signed_order_quantity = order.quantity if order.side == "BUY" else -order.quantity
        close_quantity = 0.0
        opening_quantity = order.quantity
        if existing_quantity != 0.0 and (existing_quantity > 0) != (signed_order_quantity > 0):
            close_quantity = min(abs(existing_quantity), order.quantity)
            opening_quantity = max(order.quantity - abs(existing_quantity), 0.0)

        if opening_quantity <= 0.0:
            return order.quantity

        if reference_price <= 0.0:
            return order.quantity

        available_cash = max(self._context.cash, 0.0)
        cap_notional = available_cash * max_size_percent
        allowed_open_quantity = cap_notional / reference_price if cap_notional > 0 else 0.0
        if allowed_open_quantity >= opening_quantity:
            return order.quantity

        adjusted_quantity = close_quantity + max(allowed_open_quantity, 0.0)
        self._clamped_orders += 1
        logger.warning(
            "clamping order due to max_size_percent signalId=%s symbol=%s requested=%.8f adjusted=%.8f openQuantity=%.8f allowed=%.8f",
            order.signal.signal_id,
            order.symbol,
            order.quantity,
            adjusted_quantity,
            opening_quantity,
            allowed_open_quantity,
        )
        return adjusted_quantity

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

    def _sweep_close_position_deadlines(self, current_timestamp: datetime, updated_quotes: dict[str, QuoteSnapshot]) -> None:
        for position_key, position in list(self._context.positions.items()):
            if not isinstance(position, dict):
                continue
            close_after = position.get("close_position_after")
            if not isinstance(close_after, datetime) or current_timestamp <= close_after:
                continue
            quote = updated_quotes.get(position_key)
            if quote is None or not quote.executable:
                continue

            lag_seconds = (current_timestamp - close_after).total_seconds()
            self._close_lag_seconds.append(lag_seconds)
            threshold_seconds = self._close_lag_threshold_seconds(position_key, position)
            if lag_seconds > threshold_seconds:
                logger.warning(
                    "close_position_after executed with lag symbol=%s marketType=%s lagSeconds=%.2f thresholdSeconds=%.2f",
                    position.get("symbol"),
                    position.get("market_type"),
                    lag_seconds,
                    threshold_seconds,
                )

            self._cancel_orders_for_position(position_key)
            self._execute_synthetic_close(position_key, position, quote, current_timestamp)

    def _cancel_orders_for_position(self, position_key: str) -> None:
        for order_id, order in list(self._open_orders.items()):
            if order.position_key != position_key:
                continue
            order.status = "CANCELED"
            del self._open_orders[order_id]

        remaining_pending: list[BacktestOrder] = []
        for order in self._pending_orders:
            if order.position_key == position_key:
                order.status = "CANCELED"
                continue
            remaining_pending.append(order)
        self._pending_orders = remaining_pending

    def _execute_synthetic_close(
        self,
        position_key: str,
        position: dict[str, Any],
        quote: QuoteSnapshot,
        current_timestamp: datetime,
    ) -> None:
        net_quantity = float(position.get("net_quantity") or position.get("quantity") or 0.0)
        quantity = abs(net_quantity)
        if quantity <= 0.0:
            return

        action = SignalAction.CLOSE_LONG if net_quantity > 0 else SignalAction.CLOSE_SHORT
        signal = self._synthetic_close_signal(position, action, quantity, current_timestamp)
        order = BacktestOrder(
            order_id=f"deadline_close_{uuid.uuid4()}",
            signal=signal,
            symbol=str(position.get("symbol") or ""),
            market_type=str(position.get("market_type") or MarketType.SPOT.value),
            position_key=position_key,
            order_type=OrderType.MARKET,
            action=action,
            side=self._signal_side(action),
            quantity=quantity,
            limit_price=quote.close,
            created_at=current_timestamp,
            eligible_at=current_timestamp,
            cancel_after=None,
        )
        self._order_history.append(order)
        execution_price = self._apply_slippage(float(quote.open or quote.close), order.side)
        self._fill_order(order.order_id, order, execution_price, current_timestamp, fee_type="TAKER")

    def _match_orders(self, current_timestamp: datetime, updated_quotes: dict[str, QuoteSnapshot]) -> None:
        for position_key, quote in updated_quotes.items():
            if not quote.executable:
                continue

            market_orders = [
                (order_id, order)
                for order_id, order in self._open_orders.items()
                if order.position_key == position_key and order.order_type == OrderType.MARKET
            ]
            for order_id, order in list(market_orders):
                fill_price = self._apply_slippage(float(quote.open or quote.close), order.side)
                self._fill_order(order_id, order, fill_price, current_timestamp, fee_type="TAKER")

            limit_orders = [
                (order_id, order)
                for order_id, order in self._open_orders.items()
                if order.position_key == position_key and order.order_type == OrderType.LIMIT
            ]
            for order_id, order in list(limit_orders):
                if order.limit_price is None or not self._limit_touched(order, quote):
                    continue
                self._fill_order(order_id, order, float(order.limit_price), current_timestamp, fee_type="MAKER")

    def _mark_to_market(self, current_timestamp: datetime) -> None:
        signed_market_value, unrealized = self._portfolio_valuation()

        self._context = PortfolioContext(
            positions=self._copy_mapping(self._context.positions),
            cash=self._context.cash,
            open_orders=self._copy_mapping(self._open_orders),
            realized_pnl=self._context.realized_pnl,
            unrealized_pnl=unrealized,
            total_fees=self._context.total_fees,
            equity=self._context.cash + signed_market_value,
            timestamp=current_timestamp,
        )

    def _portfolio_valuation(self) -> tuple[float, float]:
        signed_market_value = 0.0
        unrealized = 0.0

        for position_key, position in self._context.positions.items():
            if not isinstance(position, dict):
                continue

            net_quantity = self._position_net_quantity(position)
            quantity = abs(net_quantity)
            entry_price = float(position.get("average_entry_price") or position.get("entry_price") or 0.0)
            if quantity == 0.0 or entry_price <= 0.0:
                continue

            mark_price = self._position_mark_price(position_key, entry_price)
            if mark_price <= 0.0:
                continue

            direction = 1.0 if net_quantity > 0.0 else -1.0
            signed_market_value += direction * quantity * mark_price
            if direction > 0.0:
                unrealized += (mark_price - entry_price) * quantity
            else:
                unrealized += (entry_price - mark_price) * quantity

        return signed_market_value, unrealized

    def _position_net_quantity(self, position: dict[str, Any]) -> float:
        raw_net_quantity = position.get("net_quantity")
        if raw_net_quantity is not None:
            return float(raw_net_quantity)

        quantity = float(position.get("quantity") or position.get("amount") or 0.0)
        side = str(position.get("side") or "LONG").upper()
        return -abs(quantity) if side == "SHORT" else abs(quantity)

    def _position_mark_price(self, position_key: str, fallback_price: float) -> float:
        quote = self._quote_registry.get(position_key)
        if quote is not None:
            return quote.close
        return fallback_price

    def _fill_order(self, order_id: str, order: BacktestOrder, price: float, current_timestamp: datetime, fee_type: str) -> None:
        fee_rate = self._config.taker_fee_rate if fee_type == "TAKER" else self._config.maker_fee_rate
        fee = abs(order.quantity * price) * fee_rate
        quantity = abs(order.quantity)
        signed_quantity = quantity if order.side == "BUY" else -quantity
        self._apply_fill(order, price, fee, signed_quantity, current_timestamp)
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

    def _apply_fill(
        self,
        order: BacktestOrder,
        price: float,
        fee: float,
        signed_quantity: float,
        current_timestamp: datetime,
    ) -> None:
        position = self._context.positions.get(order.position_key)
        cash = self._context.cash
        realized_pnl = self._context.realized_pnl
        total_fees = self._context.total_fees + fee

        if position is None:
            cash, realized_pnl = self._open_new_position(
                order.position_key,
                order,
                price,
                fee,
                signed_quantity,
                cash,
                realized_pnl,
            )
        else:
            cash, realized_pnl = self._update_existing_position(
                order.position_key,
                position,
                order,
                price,
                fee,
                signed_quantity,
                cash,
                realized_pnl,
                current_timestamp,
            )

        signed_market_value, unrealized = self._portfolio_valuation()

        self._context = PortfolioContext(
            positions=self._copy_mapping(self._context.positions),
            cash=cash,
            open_orders=self._copy_mapping(self._open_orders),
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized,
            total_fees=total_fees,
            equity=cash + signed_market_value,
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
            "close_position_after": self._resolve_close_after(order.signal.policies),
            "timeframe": order.signal.timeframe,
            "signal": order.signal.model_copy(deep=True),
            "lots": [
                PositionLot(quantity=quantity, entry_price=price, entry_timestamp=order.created_at, entry_fees=fee)
            ],
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
        current_timestamp: datetime,
    ) -> tuple[float, float]:
        existing_quantity = float(position.get("net_quantity") or position.get("quantity") or 0.0)
        existing_side = "LONG" if existing_quantity >= 0 else "SHORT"
        order_side = "LONG" if signed_quantity >= 0 else "SHORT"
        trade_quantity = abs(signed_quantity)

        if existing_side == order_side:
            lots = list(self._position_lots(position))
            lots.append(PositionLot(quantity=trade_quantity, entry_price=price, entry_timestamp=order.created_at, entry_fees=fee))
            position["lots"] = lots
            if existing_side == "LONG":
                cash -= trade_quantity * price + fee
            else:
                cash += trade_quantity * price - fee
            position["signal"] = order.signal.model_copy(deep=True)
            position["close_position_after"] = self._merge_close_deadlines(
                position.get("close_position_after"),
                self._resolve_close_after(order.signal.policies),
            )
            if position.get("timeframe") is None and order.signal.timeframe is not None:
                position["timeframe"] = order.signal.timeframe
            self._refresh_position_summary(position, existing_sign=1 if existing_side == "LONG" else -1)
            return cash, realized_pnl

        close_quantity = min(abs(existing_quantity), trade_quantity)
        residual_open_quantity = max(trade_quantity - abs(existing_quantity), 0.0)
        close_fee = fee * (close_quantity / trade_quantity) if trade_quantity > 0 else 0.0
        residual_open_fee = fee - close_fee

        if existing_side == "LONG":
            cash += close_quantity * price - close_fee
        else:
            cash -= close_quantity * price + close_fee
        realized_pnl += self._close_lots(position, close_quantity, price, close_fee, current_timestamp)

        remaining_lots = position.get("lots") or []
        if remaining_lots:
            self._refresh_position_summary(position, existing_sign=1 if existing_side == "LONG" else -1)
            return cash, realized_pnl

        self._context.positions.pop(position_key, None)
        if residual_open_quantity <= 0.0:
            return cash, realized_pnl

        residual_signed_quantity = residual_open_quantity if signed_quantity > 0 else -residual_open_quantity
        return self._open_new_position(
            position_key,
            order,
            price,
            residual_open_fee,
            residual_signed_quantity,
            cash,
            realized_pnl,
        )

    def _close_lots(
        self,
        position: dict[str, Any],
        close_quantity: float,
        exit_price: float,
        exit_fee_total: float,
        current_timestamp: datetime,
    ) -> float:
        lots = list(self._position_lots(position))
        remaining = close_quantity
        realized = 0.0
        exit_fee_remaining = exit_fee_total
        position_side = str(position.get("side") or "LONG").upper()
        updated_lots: list[PositionLot] = []

        for lot in lots:
            if remaining <= 0.0:
                updated_lots.append(lot)
                continue
            lot_quantity = lot.quantity
            if lot_quantity <= 0.0:
                continue

            consumed_quantity = min(lot_quantity, remaining)
            proportion = consumed_quantity / lot_quantity if lot_quantity else 0.0
            entry_fee = lot.entry_fees * proportion
            exit_fee = exit_fee_remaining * (consumed_quantity / remaining) if remaining else 0.0
            entry_price = lot.entry_price
            entry_timestamp = lot.entry_timestamp

            if position_side == "LONG":
                pnl = (exit_price - entry_price) * consumed_quantity - entry_fee - exit_fee
            else:
                pnl = (entry_price - exit_price) * consumed_quantity - entry_fee - exit_fee

            self._closed_trades.append(
                ClosedTrade(
                    symbol=str(position.get("symbol") or ""),
                    market_type=str(position.get("market_type") or ""),
                    side=position_side,
                    entry_timestamp=entry_timestamp,
                    exit_timestamp=current_timestamp,
                    quantity=consumed_quantity,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    entry_fees=entry_fee,
                    exit_fees=exit_fee,
                    pnl=pnl,
                    duration_seconds=(current_timestamp - entry_timestamp).total_seconds(),
                )
            )
            realized += pnl
            remaining -= consumed_quantity
            exit_fee_remaining -= exit_fee

            if consumed_quantity < lot_quantity:
                updated_lots.append(replace(lot, quantity=lot_quantity - consumed_quantity, entry_fees=lot.entry_fees - entry_fee))

        position["lots"] = updated_lots

        return realized

    def _refresh_position_summary(self, position: dict[str, Any], *, existing_sign: int) -> None:
        lots = self._position_lots(position)
        total_quantity = sum(lot.quantity for lot in lots)
        total_fees = sum(lot.entry_fees for lot in lots)
        weighted_notional = sum(lot.quantity * lot.entry_price for lot in lots)
        average_entry = weighted_notional / total_quantity if total_quantity > 0 else 0.0
        if total_quantity <= 0.0:
            return

        position["quantity"] = total_quantity
        position["amount"] = total_quantity
        position["net_quantity"] = total_quantity if existing_sign > 0 else -total_quantity
        position["average_entry_price"] = average_entry
        position["entry_price"] = average_entry
        position["opening_fee"] = total_fees
        position["side"] = "LONG" if existing_sign > 0 else "SHORT"
        first_lot = lots[0]
        position["opened_at"] = first_lot.entry_timestamp
        position["generated_timestamp"] = first_lot.entry_timestamp

    def _position_lots(self, position: dict[str, Any]) -> list[PositionLot]:
        raw_lots = position.get("lots") or []
        lots: list[PositionLot] = []
        changed = False
        for raw_lot in raw_lots:
            if isinstance(raw_lot, PositionLot):
                lots.append(raw_lot)
                continue
            changed = True
            lot_quantity = float(raw_lot.get("quantity") or 0.0) if isinstance(raw_lot, dict) else 0.0
            lot_entry_price = float(raw_lot.get("entry_price") or 0.0) if isinstance(raw_lot, dict) else 0.0
            lot_entry_timestamp = raw_lot.get("entry_timestamp") if isinstance(raw_lot, dict) else None
            if not isinstance(lot_entry_timestamp, datetime):
                lot_entry_timestamp = position.get("opened_at")
            if not isinstance(lot_entry_timestamp, datetime):
                lot_entry_timestamp = position.get("generated_timestamp")
            if not isinstance(lot_entry_timestamp, datetime):
                lot_entry_timestamp = datetime.now(timezone.utc)
            lot_entry_fees = float(raw_lot.get("entry_fees") or 0.0) if isinstance(raw_lot, dict) else 0.0
            lots.append(
                PositionLot(
                    quantity=lot_quantity,
                    entry_price=lot_entry_price,
                    entry_timestamp=lot_entry_timestamp,
                    entry_fees=lot_entry_fees,
                )
            )
        if changed:
            position["lots"] = lots
        return lots

    def _build_order(self, *, signal: SignalPayload, reference_price: float, created_at: datetime) -> BacktestOrder:
        order_id = signal.signal_id or f"order_{uuid.uuid4()}"
        side = self._signal_side(signal.action)
        market_type = signal.market_type.value
        symbol = self._normalize_symbol(signal.symbol)
        return BacktestOrder(
            order_id=order_id,
            signal=signal.model_copy(deep=True),
            symbol=symbol,
            market_type=market_type,
            position_key=self._position_key(market_type, symbol),
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

        position_key = self._position_key(signal.market_type.value, signal.symbol)
        quote = self._quote_registry.get(position_key)
        if quote is not None:
            return quote.close

        leg_price = self._leg_close_from_payload(payload, signal)
        if leg_price is not None:
            return leg_price

        candle = self._extract_root_candle(payload)
        if candle is not None:
            return candle["close"]

        raise ValueError(f"No reference price available for signal symbol={signal.symbol} marketType={signal.market_type.value}")

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

    def _resolve_close_after(self, policies: ExecutionPolicies | None) -> datetime | None:
        if policies is None or policies.close_position_after is None:
            return None
        return datetime.fromtimestamp(float(policies.close_position_after), tz=timezone.utc)

    def _extract_root_candle(self, payload: dict[str, Any]) -> dict[str, float] | None:
        if {"open", "high", "low", "close"}.issubset(payload.keys()):
            return {
                "open": float(payload["open"]),
                "high": float(payload["high"]),
                "low": float(payload["low"]),
                "close": float(payload["close"]),
            }
        ohlcv = payload.get("ohlcv")
        if isinstance(ohlcv, (list, tuple)) and len(ohlcv) >= 5:
            return {
                "open": float(ohlcv[1]),
                "high": float(ohlcv[2]),
                "low": float(ohlcv[3]),
                "close": float(ohlcv[4]),
            }
        return None

    def _leg_close_from_payload(self, payload: dict[str, Any], signal: SignalPayload) -> float | None:
        for prefix in {key[:-7] for key in payload if key.endswith("_symbol") and key != "symbol"}:
            if self._normalize_symbol(payload.get(f"{prefix}_symbol")) != self._normalize_symbol(signal.symbol):
                continue
            market_type = self._resolve_market_type(payload.get(f"{prefix}_market_type"), prefix=prefix)
            if market_type != signal.market_type.value:
                continue
            close_value = self._safe_float(payload.get(f"{prefix}_close"))
            if close_value is not None:
                return close_value
        return None

    def _apply_slippage(self, price: float, side: str) -> float:
        slippage = self._config.slippage_rate
        if side == "BUY":
            return price * (1.0 + slippage)
        return price * (1.0 - slippage)

    def _limit_touched(self, order: BacktestOrder, quote: QuoteSnapshot) -> bool:
        if order.limit_price is None or quote.high is None or quote.low is None:
            return False
        price = float(order.limit_price)
        return quote.low <= price <= quote.high

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
        return copy.deepcopy(mapping)

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
                clamped_orders=self._clamped_orders,
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

        total_trades = len(self._closed_trades)
        winning_trades = sum(1 for trade in self._closed_trades if trade.pnl > 0)
        losing_trades = sum(1 for trade in self._closed_trades if trade.pnl < 0)
        gross_profit = sum(trade.pnl for trade in self._closed_trades if trade.pnl > 0)
        gross_loss = abs(sum(trade.pnl for trade in self._closed_trades if trade.pnl < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (math.inf if gross_profit > 0 else 0.0)
        win_rate = winning_trades / total_trades if total_trades > 0 else 0.0
        sharpe_ratio = (mean_return / std_dev) * math.sqrt(252.0) if std_dev > 0 else 0.0
        sortino_ratio = (mean_return / downside_dev) * math.sqrt(252.0) if downside_dev > 0 else (math.inf if mean_return > 0 else 0.0)
        total_duration = (self._equity_history[-1].timestamp - self._equity_history[0].timestamp).total_seconds()
        time_in_market_seconds = sum(trade.duration_seconds for trade in self._closed_trades)
        time_in_market = time_in_market_seconds / total_duration if total_duration > 0 else 0.0
        final_equity = equities[-1]
        total_return = (final_equity - self._config.initial_cash) / self._config.initial_cash if self._config.initial_cash > 0 else 0.0
        annualized_return = total_return

        traded_symbols = {fill.symbol for fill in self._fills}
        unique_symbols_traded = len(traded_symbols) if traded_symbols else len(getattr(self._strategy, "_unique_symbols_traded", set()))

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
            unique_symbols_traded=unique_symbols_traded,
            clamped_orders=self._clamped_orders,
        )

    def _synthetic_close_signal(
        self,
        position: dict[str, Any],
        action: SignalAction,
        quantity: float,
        timestamp: datetime,
    ) -> SignalPayload:
        source_signal = position.get("signal")
        bot_id = source_signal.bot_id if isinstance(source_signal, SignalPayload) else None
        symbol = str(position.get("symbol") or "")
        market_type = self._resolve_market_type(position.get("market_type"))
        timeframe = position.get("timeframe")
        return SignalPayload(
            signal_id=f"synthetic-close-{uuid.uuid4()}",
            bot_id=bot_id,
            action=action,
            symbol=symbol,
            market_type=MarketType(market_type),
            order_type=OrderType.MARKET,
            amount=quantity,
            generated_timestamp=timestamp,
            timeframe=str(timeframe) if timeframe is not None else None,
            metadata={"reason": "close_position_after"},
        )

    def _close_lag_threshold_seconds(self, position_key: str, position: dict[str, Any]) -> float:
        timeframe = position.get("timeframe")
        if isinstance(timeframe, str):
            parsed = self._timeframe_seconds(timeframe)
            if parsed is not None:
                return parsed
        cadence = self._quote_cadence_seconds.get(position_key)
        if cadence is not None and cadence > 0:
            return cadence
        return 3600.0

    def _timeframe_seconds(self, timeframe: str) -> float | None:
        match = _TIMEFRAME_PATTERN.match(timeframe)
        if match is None:
            return None
        quantity = int(match.group(1))
        unit = match.group(2).lower()
        unit_seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
        return float(quantity * unit_seconds)

    def _merge_close_deadlines(self, current: Any, candidate: datetime | None) -> datetime | None:
        if candidate is None:
            return current if isinstance(current, datetime) else None
        if isinstance(current, datetime):
            return min(current, candidate)
        return candidate

    def _resolve_market_type(self, raw_market_type: Any, prefix: str | None = None) -> str:
        if isinstance(raw_market_type, MarketType):
            return raw_market_type.value
        if raw_market_type is not None:
            text = str(raw_market_type).strip().upper()
            if text in {MarketType.SPOT.value, MarketType.FUTURE.value, MarketType.MARGIN.value}:
                return text
        if prefix is not None:
            lowered = prefix.lower()
            if lowered.startswith("spot"):
                return MarketType.SPOT.value
            if lowered.startswith("future") or lowered.startswith("futures") or lowered.startswith("swap"):
                return MarketType.FUTURE.value
            if lowered.startswith("margin"):
                return MarketType.MARGIN.value
        return MarketType.SPOT.value

    def _position_key(self, market_type: str, symbol: str) -> str:
        return f"{self._resolve_market_type(market_type)}:{self._normalize_symbol(symbol)}"

    def _normalize_symbol(self, symbol: Any) -> str:
        if symbol is None:
            return ""
        return str(symbol).replace("/", "").replace("-", "").replace("_", "").split(":")[0].upper()

    def _safe_float(self, value: Any) -> float | None:
        if value is None:
            return None
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return result
