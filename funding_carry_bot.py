from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from quant_signal_sdk.models import MarketType, OrderType, SignalAction, SignalPayload
from quant_signal_sdk.runtime.interfaces import BaseStrategy, MarketEvent, PortfolioContext


@dataclass(slots=True)
class FundingCarryConfig:
    top_k: int = 5
    target_notional: float = 100.0
    hold_hours: int = 168
    min_funding_rate: float = 0.0
    score_col: str = "predicted_score"
    rebalance_mode: str = "fixed"
    fee_rate: float = 0.0015
    alpha_threshold: float = 0.0
    bot_id: str = "funding-carry-bot"
    order_type: OrderType = OrderType.LIMIT


class FundingCarryStrategy(BaseStrategy):
    def __init__(self, config: FundingCarryConfig | None = None) -> None:
        self._config = config or FundingCarryConfig()
        self._current_timestamp: datetime | None = None
        self._score_buffer: dict[str, float] = {}
        self._last_scores: dict[str, float] = {}
        self._target_symbols: set[str] = set()
        self._rebalance_allowed = True
        # analytics counters
        self._rebalance_events: int = 0
        self._coin_swaps: int = 0
        self._unique_symbols_traded: set[str] = set()

    def on_event(self, event: MarketEvent, context: PortfolioContext) -> list[SignalPayload]:
        if self._current_timestamp is None:
            self._current_timestamp = event.timestamp

        if event.timestamp > self._current_timestamp:
            self._roll_timestamp(event.timestamp, context)

        symbol = self._extract_symbol(event.payload)
        if symbol:
            score = self._safe_float(event.payload.get(self._config.score_col))
            self._score_buffer[symbol] = score

        return self._maybe_trade(event, context)

    def _roll_timestamp(self, new_timestamp: datetime, context: PortfolioContext) -> None:
        prev_targets = set(self._target_symbols)
        self._last_scores = dict(self._score_buffer)
        self._score_buffer = {}
        if self._last_scores:
            ranked = sorted(self._last_scores.items(), key=lambda item: item[1], reverse=True)
            self._target_symbols = {symbol for symbol, _ in ranked[: self._config.top_k]}
        else:
            self._target_symbols = set()

        self._rebalance_allowed = self._should_rebalance(new_timestamp, context, self._target_symbols)
        # analytics: count target changes and rebalance events
        if prev_targets != self._target_symbols:
            diff = prev_targets.symmetric_difference(self._target_symbols)
            self._coin_swaps += len(diff)
            if self._rebalance_allowed:
                self._rebalance_events += 1

        self._current_timestamp = new_timestamp

    def _should_rebalance(self, now: datetime, context: PortfolioContext, targets: set[str]) -> bool:
        if not context.positions:
            return True
        if self._config.hold_hours <= 0:
            return True

        avg_hold = self._average_hold_hours(now, context)
        if avg_hold < float(self._config.hold_hours):
            return False

        # If the current future symbols are already a subset of targets, no rebalance needed
        current_symbols = self._current_future_symbols(context)
        if current_symbols and targets and current_symbols.issubset(targets):
            return False

        if self._config.rebalance_mode.lower() != "adaptive":
            return True

        current_symbols = self._current_future_symbols(context)
        if not current_symbols or not targets:
            return False

        current_scores = [self._last_scores.get(symbol, 0.0) for symbol in current_symbols]
        target_scores = [self._last_scores.get(symbol, 0.0) for symbol in targets]
        avg_current = sum(current_scores) / len(current_scores) if current_scores else 0.0
        avg_target = sum(target_scores) / len(target_scores) if target_scores else 0.0
        alpha_gain = avg_target - avg_current
        cost_to_switch = self._config.fee_rate * 2.0
        return alpha_gain > (cost_to_switch + self._config.alpha_threshold)

    def _maybe_trade(self, event: MarketEvent, context: PortfolioContext) -> list[SignalPayload]:
        symbol = self._extract_symbol(event.payload)
        if not symbol:
            return []

        current_symbols = self._current_future_symbols(context)
        if self._rebalance_allowed:
            target_symbols = self._target_symbols
        else:
            target_symbols = current_symbols

        funding_rate = self._safe_float(event.payload.get("funding_rate"))
        price = self._extract_price(event.payload)
        if price <= 0.0:
            return []

        signals: list[SignalPayload] = []
        if symbol in target_symbols and symbol not in current_symbols:
            if funding_rate >= self._config.min_funding_rate:
                signals.extend(self._build_open_signals(event, symbol, price, funding_rate))
        elif symbol not in target_symbols and symbol in current_symbols and self._rebalance_allowed:
            signals.extend(self._build_close_signals(event, symbol, price, funding_rate))

        # track unique traded symbols for analytics
        for s in signals:
            base = self._normalize_symbol(s.symbol)
            if base:
                self._unique_symbols_traded.add(base)

        return signals

    def _build_open_signals(self, event: MarketEvent, symbol: str, price: float, funding_rate: float) -> list[SignalPayload]:
        amount = self._position_amount(price)
        if amount <= 0.0:
            return []

        spot_symbol = self._normalize_symbol(event.payload.get("spot_symbol") or symbol)
        futures_symbol = self._normalize_symbol(event.payload.get("futures_symbol") or symbol)
        timestamp = self._ensure_utc(event.timestamp)
        metadata = {
            "strategy": "funding_carry",
            "funding_rate": funding_rate,
            "score": self._last_scores.get(symbol, 0.0),
            "rebalance_mode": self._config.rebalance_mode,
        }
        return [
            self._signal(
                symbol=spot_symbol,
                market_type=MarketType.SPOT,
                action=SignalAction.OPEN_LONG,
                amount=amount,
                price=price,
                timestamp=timestamp,
                metadata={**metadata, "leg": "spot"},
            ),
            self._signal(
                symbol=futures_symbol,
                market_type=MarketType.FUTURE,
                action=SignalAction.OPEN_SHORT,
                amount=amount,
                price=price,
                timestamp=timestamp,
                metadata={**metadata, "leg": "futures"},
            ),
        ]

    def _build_close_signals(self, event: MarketEvent, symbol: str, price: float, funding_rate: float) -> list[SignalPayload]:
        amount = self._position_amount(price)
        if amount <= 0.0:
            return []

        spot_symbol = self._normalize_symbol(event.payload.get("spot_symbol") or symbol)
        futures_symbol = self._normalize_symbol(event.payload.get("futures_symbol") or symbol)
        timestamp = self._ensure_utc(event.timestamp)
        metadata = {
            "strategy": "funding_carry",
            "funding_rate": funding_rate,
            "score": self._last_scores.get(symbol, 0.0),
            "rebalance_mode": self._config.rebalance_mode,
        }
        return [
            self._signal(
                symbol=spot_symbol,
                market_type=MarketType.SPOT,
                action=SignalAction.CLOSE_LONG,
                amount=amount,
                price=price,
                timestamp=timestamp,
                metadata={**metadata, "leg": "spot"},
            ),
            self._signal(
                symbol=futures_symbol,
                market_type=MarketType.FUTURE,
                action=SignalAction.CLOSE_SHORT,
                amount=amount,
                price=price,
                timestamp=timestamp,
                metadata={**metadata, "leg": "futures"},
            ),
        ]

    def _signal(
        self,
        *,
        symbol: str,
        market_type: MarketType,
        action: SignalAction,
        amount: float,
        price: float,
        timestamp: datetime,
        metadata: dict[str, Any],
    ) -> SignalPayload:
        return SignalPayload(
            signal_id=f"funding-{action.value.lower()}-{symbol}-{int(timestamp.timestamp())}",
            bot_id=self._config.bot_id,
            action=action,
            symbol=symbol,
            market_type=market_type,
            order_type=self._config.order_type,
            entry=price,
            amount=amount,
            generated_timestamp=timestamp,
            metadata=metadata,
        )

    def _position_amount(self, price: float) -> float:
        if price <= 0.0:
            return 0.0
        return float(self._config.target_notional / price)

    def _average_hold_hours(self, now: datetime, context: PortfolioContext) -> float:
        holds: list[float] = []
        for position in context.positions.values():
            if not isinstance(position, dict):
                continue
            if position.get("market_type") != MarketType.FUTURE.value:
                continue
            opened_at = position.get("opened_at") or position.get("generated_timestamp")
            if not isinstance(opened_at, datetime):
                continue
            opened_ts = opened_at if opened_at.tzinfo else opened_at.replace(tzinfo=timezone.utc)
            holds.append((now - opened_ts).total_seconds() / 3600.0)
        if not holds:
            return 0.0
        return sum(holds) / len(holds)

    def _current_future_symbols(self, context: PortfolioContext) -> set[str]:
        symbols: set[str] = set()
        for key, position in context.positions.items():
            if not isinstance(position, dict):
                continue
            if position.get("market_type") != MarketType.FUTURE.value:
                continue
            symbol = self._normalize_symbol(position.get("symbol") or key.split(":")[-1])
            if symbol:
                symbols.add(symbol)
        return symbols

    @staticmethod
    def _extract_symbol(payload: dict[str, Any]) -> str:
        raw = payload.get("symbol") or payload.get("futures_symbol") or payload.get("spot_symbol") or ""
        return FundingCarryStrategy._normalize_symbol(raw)

    @staticmethod
    def _extract_price(payload: dict[str, Any]) -> float:
        for key in ("futures_close", "spot_close", "close"):
            value = payload.get(key)
            if value is None:
                continue
            price = FundingCarryStrategy._safe_float(value)
            if price > 0.0:
                return price
        return 0.0

    @staticmethod
    def _safe_float(value: Any) -> float:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return 0.0
        if result != result:
            return 0.0
        return result

    @staticmethod
    def _normalize_symbol(value: Any) -> str:
        if value is None:
            return ""
        return str(value).replace("/", "").replace("-", "").replace("_", "").split(":")[0].upper()

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


STRATEGY = FundingCarryStrategy()
