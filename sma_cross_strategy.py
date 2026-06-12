from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from quant_signal_sdk.models import MarketType, OrderType, SignalAction, SignalPayload
from quant_signal_sdk.runtime.interfaces import BaseStrategy, MarketEvent, PortfolioContext


class SmaCrossStrategy(BaseStrategy):
    def __init__(self, short_window: int = 5, long_window: int = 15) -> None:
        self.short_window = short_window
        self.long_window = long_window
        self.prices: list[float] = []
        self.position_active = False

    def on_event(self, event: MarketEvent, context: PortfolioContext) -> list[SignalPayload]:
        # Extract closing price
        close_val = event.payload.get("close")
        if close_val is None:
            return []

        try:
            close = float(close_val)
        except (ValueError, TypeError):
            return []

        if close <= 0.0:
            return []

        self.prices.append(close)

        # Maintain history size to avoid memory growth
        if len(self.prices) > 200:
            self.prices.pop(0)

        if len(self.prices) < self.long_window:
            return []

        # Calculate Simple Moving Averages
        short_sma = sum(self.prices[-self.short_window:]) / self.short_window
        long_sma = sum(self.prices[-self.long_window:]) / self.long_window

        # Get Symbol
        raw_symbol = event.payload.get("symbol") or "BTCUSDT"
        symbol = str(raw_symbol).replace("/", "").replace("-", "").replace("_", "").upper()

        signals: list[SignalPayload] = []

        # Check for crossover
        if short_sma > long_sma and not self.position_active:
            self.position_active = True
            equity = context.equity if context.equity > 0 else 10000.0
            target_notional = equity * 0.95
            self.last_amount = target_notional / close
            signals.append(
                self._build_signal(
                    action=SignalAction.OPEN_LONG,
                    timestamp=event.timestamp,
                    symbol=symbol,
                    entry=close,
                    amount=self.last_amount,
                )
            )
        elif short_sma < long_sma and self.position_active:
            self.position_active = False
            position_key = f"SPOT:{symbol}"
            position = context.positions.get(position_key)
            if position:
                amount = float(position.get("quantity") or position.get("amount") or getattr(self, "last_amount", 1.0))
            else:
                amount = getattr(self, "last_amount", 1.0)
            signals.append(
                self._build_signal(
                    action=SignalAction.CLOSE_LONG,
                    timestamp=event.timestamp,
                    symbol=symbol,
                    entry=close,
                    amount=amount,
                )
            )

        return signals

    def _build_signal(
        self,
        action: SignalAction,
        timestamp: datetime,
        symbol: str,
        entry: float,
        amount: float = 1.0,
    ) -> SignalPayload:
        return SignalPayload(
            signal_id=f"smacross-{action.value.lower()}-{symbol}-{int(timestamp.timestamp())}",
            bot_id="sma-cross-bot",
            action=action,
            symbol=symbol,
            market_type=MarketType.SPOT,
            order_type=OrderType.MARKET,
            entry=entry,
            amount=amount,
            generated_timestamp=timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc),
            timeframe="1h",
            metadata={
                "strategy": "sma_crossover",
                "short_window": self.short_window,
                "long_window": self.long_window,
            },
        )


STRATEGY = SmaCrossStrategy()
