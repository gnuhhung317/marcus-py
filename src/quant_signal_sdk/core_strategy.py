from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .interfaces import BaseStrategy, MarketEvent, PortfolioContext
from .models import MarginMode, MarketType, OrderType, SignalAction, SignalPayload


@dataclass(slots=True)
class FundingArbitrageConfig:
    target_notional: float = 10.0
    min_hold_hours: float = 8.0
    open_funding_threshold: float = 0.0
    close_funding_threshold: float = 0.0
    leverage: int = 1
    margin_mode: MarginMode = MarginMode.CROSS


class FundingArbitrageStrategy(BaseStrategy):
    def __init__(self, bot_id: str, config: FundingArbitrageConfig | None = None) -> None:
        self._bot_id = bot_id
        self._config = config or FundingArbitrageConfig()

    def on_event(self, event: MarketEvent, context: PortfolioContext) -> list[SignalPayload]:
        pair = self._extract_pair(event.payload)
        if pair is None:
            return []

        spot_symbol = self._normalize_symbol(pair["spot_symbol"])
        futures_symbol = self._normalize_symbol(pair["futures_symbol"])
        latest_price = self._extract_price(event.payload)
        funding_rate = self._extract_funding_rate(event.payload)

        spot_key = self._position_key(MarketType.SPOT, spot_symbol)
        futures_key = self._position_key(MarketType.FUTURE, futures_symbol)
        has_spot = spot_key in context.positions
        has_futures = futures_key in context.positions
        has_position = has_spot or has_futures

        if has_position:
            if self._should_close(event, context, spot_key, futures_key, funding_rate):
                return self._build_close_signals(spot_symbol, futures_symbol, latest_price, funding_rate, event.timestamp)
            return []

        if funding_rate <= self._config.open_funding_threshold:
            return []

        if latest_price <= 0.0:
            return []

        return self._build_open_signals(spot_symbol, futures_symbol, latest_price, funding_rate, event.timestamp)

    def _should_close(
        self,
        event: MarketEvent,
        context: PortfolioContext,
        spot_key: str,
        futures_key: str,
        funding_rate: float,
    ) -> bool:
        position = context.positions.get(futures_key) or context.positions.get(spot_key)
        if not isinstance(position, dict):
            return funding_rate <= self._config.close_funding_threshold

        opened_at = position.get("generated_timestamp") or position.get("opened_at")
        if isinstance(opened_at, datetime):
            opened_ts = opened_at if opened_at.tzinfo else opened_at.replace(tzinfo=timezone.utc)
            held_hours = (event.timestamp - opened_ts).total_seconds() / 3600.0
            if held_hours < self._config.min_hold_hours and funding_rate > self._config.close_funding_threshold:
                return False

        return funding_rate <= self._config.close_funding_threshold

    def _build_open_signals(
        self,
        spot_symbol: str,
        futures_symbol: str,
        price: float,
        funding_rate: float,
        timestamp: datetime,
    ) -> list[SignalPayload]:
        amount = self._position_amount(price)
        metadata = self._metadata(spot_symbol, futures_symbol, funding_rate, price)
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
                leverage=self._config.leverage,
                margin_mode=self._config.margin_mode,
                metadata={**metadata, "leg": "futures"},
            ),
        ]

    def _build_close_signals(
        self,
        spot_symbol: str,
        futures_symbol: str,
        price: float,
        funding_rate: float,
        timestamp: datetime,
    ) -> list[SignalPayload]:
        amount = self._position_amount(price)
        metadata = self._metadata(spot_symbol, futures_symbol, funding_rate, price)
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
                leverage=self._config.leverage,
                margin_mode=self._config.margin_mode,
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
        leverage: int | None = None,
        margin_mode: MarginMode | None = None,
    ) -> SignalPayload:
        return SignalPayload(
            bot_id=self._bot_id,
            action=action,
            symbol=symbol,
            market_type=market_type,
            order_type=OrderType.MARKET,
            amount=amount,
            entry=price,
            leverage=leverage,
            margin_mode=margin_mode,
            generated_timestamp=timestamp,
            metadata=metadata,
        )

    def _extract_pair(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        spot_symbol = str(payload.get("spot_symbol") or "").strip()
        futures_symbol = str(payload.get("futures_symbol") or "").strip()
        if not spot_symbol or not futures_symbol:
            return None

        return {
            "spot_symbol": spot_symbol,
            "futures_symbol": futures_symbol,
        }

    def _extract_price(self, payload: dict[str, Any]) -> float:
        for key in ("futures_close", "spot_close"):
            raw_value = payload.get(key)
            if raw_value is None:
                continue
            try:
                price = float(raw_value)
            except (TypeError, ValueError):
                continue
            if price > 0.0:
                return price
        return 0.0

    def _extract_funding_rate(self, payload: dict[str, Any]) -> float:
        raw_value = payload.get("funding_rate", 0.0)
        try:
            return float(raw_value)
        except (TypeError, ValueError):
            return 0.0
        return 0.0

    def _position_amount(self, price: float) -> float:
        if price <= 0.0:
            return 0.0
        return float(self._config.target_notional / price)

    def _metadata(self, spot_symbol: str, futures_symbol: str, funding_rate: float, price: float) -> dict[str, Any]:
        return {
            "strategy": "funding_arbitrage",
            "pair_symbol": futures_symbol,
            "spot_symbol": spot_symbol,
            "futures_symbol": futures_symbol,
            "funding_rate": funding_rate,
            "reference_price": price,
        }

    def _position_key(self, market_type: MarketType, symbol: str) -> str:
        return f"{market_type.value}:{self._normalize_symbol(symbol)}"

    def _normalize_symbol(self, symbol: str) -> str:
        return symbol.replace("/", "").replace("_", "").replace("-", "").split(":")[0].upper()