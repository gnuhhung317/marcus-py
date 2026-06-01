"""Funding arbitrage example with explicit separation between data fetching and decision logic.

This module is intentionally structured to show three distinct layers:

1. A user-owned fetcher that gathers market data and returns a DataFrame.
2. A user-owned decision layer that turns the latest snapshot into a trade decision.
3. SDK wiring that schedules the fetcher and dispatches signals through the client.

Swap `ScheduledRESTFeed` with `DataFrameFeed` to reuse the same decision logic for backtests.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict

import pandas as pd

from quant_signal_sdk.ccxt_client import CCXTClient
from quant_signal_sdk.client import QuantSignalClient
from quant_signal_sdk.models import MarginMode, MarketType, OrderType, SignalAction, SignalPayload
from quant_signal_sdk.runtime.adapters import CronTrigger, DataFrameFeed, ScheduledRESTFeed


class QuantFeatureEngineer:
    def calculate_features(self, symbol: str, ohlcv: pd.DataFrame, funding: pd.DataFrame) -> pd.Series:
        # Simple, deterministic feature set sufficient for tests and snapshots.
        roc_close_24h = 0.0
        if len(ohlcv) >= 25:
            prev = ohlcv["close"].shift(24).iloc[-1]
            if prev and prev != 0:
                roc_close_24h = (ohlcv["close"].iloc[-1] - prev) / prev

        volatility_series = ohlcv["close"].pct_change().rolling(window=min(24, len(ohlcv))).std()
        volatility_24h = self._safe_float(volatility_series.iloc[-1] if not volatility_series.empty else 0.0)

        funding_series = funding.get("funding_rate", funding.get("fundingRate", pd.Series(dtype=float)))
        sum_funding_168h = self._safe_float(funding_series.tail(168).sum())
        mean_funding_168h = self._safe_float(funding_series.tail(168).mean())

        return pd.Series(
            {
                "roc_close_24h": roc_close_24h,
                "volatility_24h": volatility_24h,
                "sum_funding_168h": sum_funding_168h,
                "mean_funding_168h": mean_funding_168h,
            }
        )

    @staticmethod
    def _safe_float(value: Any) -> float:
        if value is None:
            return 0.0
        try:
            result = float(value)
        except (TypeError, ValueError):
            return 0.0
        if pd.isna(result):
            return 0.0
        return result


class ArbitrageBot:
    def __init__(self, model_paths: list[str] | None = None, features: list[str] | None = None) -> None:
        self.model_paths = model_paths or []
        self.features = features or []

    def predict_scores(self, feature_df: pd.DataFrame) -> pd.DataFrame:
        # Simple heuristic: rank by `sum_funding_24h` if present, otherwise funding_rate
        if "sum_funding_24h" in feature_df.columns:
            feature_df = feature_df.copy()
            feature_df["predicted_score"] = feature_df["sum_funding_24h"].astype(float)
            return feature_df

        feature_df = feature_df.copy()
        feature_df["predicted_score"] = feature_df.get("funding_rate", 0.0).astype(float)
        return feature_df


def build_arbitrage_fetcher(
    *,
    exchange_id: str,
    spot_symbol: str,
    futures_symbol: str,
    timeframe: str,
    limit: int = 200,
) -> Callable[[], pd.DataFrame]:
    """Return a zero-arg fetcher the SDK can call on schedule.

    The fetcher owns the data gathering concern. It can be replaced without
    changing decision logic or SDK wiring.
    """

    client = CCXTClient(exchange_id=exchange_id)

    def my_arbitrage_fetcher() -> pd.DataFrame:
        spot_rows = client.fetch_ohlcv(spot_symbol, timeframe=timeframe, limit=limit)
        futures_rows = client.fetch_ohlcv(futures_symbol, timeframe=timeframe, limit=limit)

        spot_frame = pd.DataFrame(spot_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        futures_frame = pd.DataFrame(futures_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])

        funding_rate = 0.0
        fetch_funding_rates = getattr(client.exchange, "fetch_funding_rates", None)
        if callable(fetch_funding_rates):
            try:
                funding_rates = fetch_funding_rates() or {}
                funding_payload = funding_rates.get(futures_symbol) or funding_rates.get(futures_symbol.replace("/", ""), {})
                funding_rate = float(funding_payload.get("fundingRate", 0.0))
            except Exception:
                funding_rate = 0.0

        latest_spot = spot_frame.iloc[-1]
        latest_futures = futures_frame.iloc[-1]
        timestamp_value = datetime.now(timezone.utc)

        return pd.DataFrame(
            [
                {
                    "timestamp": timestamp_value,
                    "spot_symbol": spot_symbol,
                    "futures_symbol": futures_symbol,
                    "close": float(latest_futures["close"]),
                    "spot_close": float(latest_spot["close"]),
                    "futures_close": float(latest_futures["close"]),
                    "funding_rate": funding_rate,
                    "volume": float(latest_futures["volume"]),
                }
            ]
        )

    return my_arbitrage_fetcher


def build_replay_feed(replay_csv: str) -> DataFrameFeed:
    """Use the same decision logic against a file-backed DataFrame."""

    return DataFrameFeed(pd.read_csv(replay_csv))


def should_open_trade(scores: pd.DataFrame, threshold: float) -> bool:
    if scores.empty or "predicted_score" not in scores.columns:
        return False
    try:
        score = float(scores["predicted_score"].iloc[0])
    except (TypeError, ValueError):
        return False
    if pd.isna(score):
        return False
    return score > threshold


def evaluate_arbitrage_snapshot(
    engineer: QuantFeatureEngineer,
    bot: ArbitrageBot,
    snapshot: pd.DataFrame,
) -> pd.DataFrame:
    features = engineer.calculate_features("BTCUSDT", snapshot, snapshot)
    feature_frame = pd.DataFrame([features])
    if "funding_rate" in snapshot.columns and not snapshot.empty:
        feature_frame["funding_rate"] = float(snapshot["funding_rate"].iloc[-1])
    if "spot_close" in snapshot.columns and "futures_close" in snapshot.columns and not snapshot.empty:
        spot_close = float(snapshot["spot_close"].iloc[-1])
        futures_close = float(snapshot["futures_close"].iloc[-1])
        if spot_close > 0:
            feature_frame["spread_bps"] = ((futures_close - spot_close) / spot_close) * 10000.0
    return bot.predict_scores(feature_frame)


@dataclass
class StateManager:
    filepath: str

    def load_portfolio(self) -> Dict[str, Any]:
        if not os.path.exists(self.filepath):
            return {}
        try:
            with open(self.filepath, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception:
            return {}
        # stored as mapping symbol->amount
        return payload or {}

    def save_portfolio(self, portfolio: Dict[str, Any]) -> None:
        d = os.path.dirname(self.filepath)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(self.filepath, "w", encoding="utf-8") as fh:
            json.dump(portfolio, fh)


def _normalize_symbol_for_api(symbol: str) -> str:
    # Expect forms like 'BTC/USDT:USDT' -> 'BTCUSDT'
    base = symbol.split(":")[0]
    return re.sub(r"[^A-Za-z0-9]", "", base).upper()


def dispatch_arbitrage_orders(
    client: QuantSignalClient,
    *,
    symbol: str,
    action: str,
    amount: float,
    leverage: int | None = None,
    margin_mode: str | None = None,
) -> None:
    norm = _normalize_symbol_for_api(symbol)
    act = action.upper()

    if act == "OPEN":
        spot_action = SignalAction.OPEN_LONG
        future_action = SignalAction.OPEN_SHORT
    else:
        spot_action = SignalAction.CLOSE_LONG
        future_action = SignalAction.CLOSE_SHORT

    spot_signal = SignalPayload(
        action=spot_action,
        symbol=norm,
        market_type=MarketType.SPOT,
        order_type=OrderType.MARKET,
        amount=amount,
        metadata={"strategy": "funding_arbitrage", "leg": "spot"},
    )

    future_signal = SignalPayload(
        action=future_action,
        symbol=norm,
        market_type=MarketType.FUTURE,
        order_type=OrderType.MARKET,
        amount=amount,
        leverage=leverage,
        margin_mode=MarginMode(margin_mode) if margin_mode else None,
        metadata={"strategy": "funding_arbitrage", "leg": "futures"},
    )

    # Prefer structured send_signal which validates and serializes the model
    client.send_signal(spot_signal)
    client.send_signal(future_signal)


def fetch_arbitrage_candidates(exchange: Any) -> Dict[str, Any]:
    # Build a map of swap/perpetual markets that also have a spot counterpart
    markets = getattr(exchange, "markets", {}) or {}
    funding = {}
    try:
        funding = exchange.fetch_funding_rates() or {}
    except Exception:
        funding = {}

    candidates: Dict[str, Any] = {}
    for symbol, info in markets.items():
        # prefer explicit swap flag, but accept naming convention with ':'
        is_swap = bool(info.get("swap") or ":" in symbol)
        if not is_swap:
            continue

        spot_symbol = symbol.split(":")[0]
        spot_info = markets.get(spot_symbol)
        if not spot_info or not spot_info.get("spot"):
            continue

        fr = funding.get(symbol) or funding.get(symbol.replace("/", "/")) or {}
        fr_val = fr.get("fundingRate") if isinstance(fr, dict) else None
        try:
            fr_f = float(fr_val) if fr_val is not None else 0.0
        except (TypeError, ValueError):
            fr_f = 0.0

        candidates[symbol] = {
            "spot_symbol": spot_symbol,
            "funding_rate": fr_f,
        }

    return candidates


def run_event_loop(
    *,
    feed: DataFrameFeed | ScheduledRESTFeed,
    client: QuantSignalClient,
    engineer: QuantFeatureEngineer,
    bot: ArbitrageBot,
    trade_amount: float,
    threshold: float,
) -> None:
    for event in feed.stream():
        snapshot = pd.DataFrame([event.payload])
        scores = evaluate_arbitrage_snapshot(engineer, bot, snapshot)
        if should_open_trade(scores, threshold):
            dispatch_arbitrage_orders(
                client=client,
                symbol=str(event.payload.get("futures_symbol") or event.payload.get("spot_symbol") or "BTC/USDT:USDT"),
                action="OPEN",
                amount=trade_amount,
            )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Funding arbitrage bot example with separated fetch/decision layers")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--bot-id", required=True)
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--futures-symbol", default=None, help="Defaults to <symbol>:USDT")
    parser.add_argument("--timeframe", default="1h", help="Candle timeframe used by the fetcher")
    parser.add_argument("--schedule-timeframe", default="1h", help="Trigger cadence for ScheduledRESTFeed")
    parser.add_argument("--trade-threshold", type=float, default=0.05)
    parser.add_argument("--trade-amount", type=float, default=0.1)
    parser.add_argument("--replay-csv", default=None, help="Replay a CSV file through DataFrameFeed for backtests")
    parser.add_argument("--bot-api-key", default=None)
    parser.add_argument("--bot-signer-secret", default=None)
    parser.add_argument("--auth-token", default=None)
    args = parser.parse_args()

    client = QuantSignalClient(base_url=args.base_url, api_key=args.bot_api_key or "", default_bot_id=args.bot_id, signer_secret=args.bot_signer_secret)
    engineer = QuantFeatureEngineer()
    bot = ArbitrageBot(model_paths=[], features=[])
    futures_symbol = args.futures_symbol or f"{args.symbol}:USDT"

    if args.replay_csv:
        feed: DataFrameFeed | ScheduledRESTFeed = build_replay_feed(args.replay_csv)
    else:
        fetcher = build_arbitrage_fetcher(
            exchange_id=args.exchange,
            spot_symbol=args.symbol,
            futures_symbol=futures_symbol,
            timeframe=args.timeframe,
        )
        feed = ScheduledRESTFeed(trigger=CronTrigger(args.schedule_timeframe), fetcher=fetcher)

    # The SDK handles timing and event delivery; only the fetcher and decision logic are user-owned.
    run_event_loop(
        feed=feed,
        client=client,
        engineer=engineer,
        bot=bot,
        trade_amount=args.trade_amount,
        threshold=args.trade_threshold,
    )


if __name__ == "__main__":
    main()
