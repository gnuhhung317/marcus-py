from __future__ import annotations

from datetime import datetime, timezone

from quant_signal_sdk.models import MarketType, OrderType, SignalAction, SignalPayload
from quant_signal_sdk.runtime.interfaces import BaseStrategy, MarketEvent, PortfolioContext


class SampleBacktestBot(BaseStrategy):
    def __init__(self) -> None:
        self._opened = False
        self._closed = False

    def on_event(self, event: MarketEvent, context: PortfolioContext) -> list[SignalPayload]:
        if not self._opened:
            self._opened = True
            return [self._build_signal(SignalAction.OPEN_LONG, event.timestamp, entry=float(event.payload["close"]))]

        if self._opened and not self._closed and context.positions:
            self._closed = True
            return [self._build_signal(SignalAction.CLOSE_LONG, event.timestamp, entry=float(event.payload["close"]))]

        return []

    def _build_signal(self, action: SignalAction, timestamp: datetime, *, entry: float) -> SignalPayload:
        return SignalPayload(
            signal_id=f"sample-{action.value.lower()}-{int(timestamp.timestamp())}",
            bot_id="sample-backtest-bot",
            action=action,
            symbol="BTCUSDT",
            market_type=MarketType.SPOT,
            order_type=OrderType.MARKET,
            entry=entry,
            amount=1.0,
            generated_timestamp=timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc),
            timeframe="15m",
            metadata={"strategy": "sample_backtest"},
        )


STRATEGY = SampleBacktestBot()