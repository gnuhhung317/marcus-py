"""Funding arbitrage example with a single shared strategy for live and backtest.

The module keeps the reusable strategy in ``quant_signal_sdk.core_strategy`` and
uses thin adapters here for:

1. live market-data fetching + signal dispatch
2. backtest replay + report export + Marcus upload

Legacy helpers are preserved for compatibility with existing tests, but the
main execution path now routes through ``FundingArbitrageStrategy``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from quant_signal_sdk.ccxt_client import CCXTClient
from quant_signal_sdk.client import QuantSignalClient
from quant_signal_sdk.cli import export_backtest_results
from quant_signal_sdk.core_strategy import FundingArbitrageConfig, FundingArbitrageStrategy
from quant_signal_sdk.models import MarginMode, MarketType, OrderType, SignalAction, SignalPayload
from quant_signal_sdk.runtime.adapters import CronTrigger, DataFrameFeed, LiveHTTPDispatcher, MockDispatcher, ScheduledRESTFeed
from quant_signal_sdk.runtime.backtest import BacktestConfig, PortfolioBacktestRunner
from quant_signal_sdk.runtime.backtest_upload import BacktestUploadClient, BacktestUploadConfig
from quant_signal_sdk.runtime.interfaces import PortfolioContext
from quant_signal_sdk.runtime.runner import Runner


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
        # Prefer the canonical 168h feature, then fall back to the older 24h field.
        score_column = next(
            (column for column in ("sum_funding_168h", "sum_funding_24h", "funding_rate") if column in feature_df.columns),
            None,
        )
        feature_df = feature_df.copy()
        if score_column is None:
            feature_df["predicted_score"] = 0.0
            return feature_df
        feature_df["predicted_score"] = feature_df[score_column].astype(float)
        return feature_df


def build_arbitrage_fetcher(
    *,
    exchange_id: str,
    spot_symbol: str,
    futures_symbol: str,
    timeframe: str,
    limit: int = 200,
) -> Callable[[], pd.DataFrame]:
    """Return a zero-arg fetcher the SDK can call on schedule."""

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

        return pd.DataFrame(
            [
                {
                    "timestamp": datetime.now(timezone.utc),
                    "spot_symbol": spot_symbol,
                    "futures_symbol": futures_symbol,
                    "spot_open": float(latest_spot["open"]),
                    "spot_high": float(latest_spot["high"]),
                    "spot_low": float(latest_spot["low"]),
                    "spot_close": float(latest_spot["close"]),
                    "futures_open": float(latest_futures["open"]),
                    "futures_high": float(latest_futures["high"]),
                    "futures_low": float(latest_futures["low"]),
                    "futures_close": float(latest_futures["close"]),
                    "open": float(latest_futures["open"]),
                    "high": float(latest_futures["high"]),
                    "low": float(latest_futures["low"]),
                    "close": float(latest_futures["close"]),
                    "funding_rate": funding_rate,
                    "volume": float(latest_futures["volume"]),
                }
            ]
        )

    return my_arbitrage_fetcher


def build_replay_feed(replay_csv: str) -> DataFrameFeed:
    """Use the same decision logic against a file-backed DataFrame."""

    return DataFrameFeed(_load_dataframe_source(replay_csv), timestamp_col="timestamp")


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
    futures_symbol: str | None = None,
) -> None:
    spot_norm = _normalize_symbol_for_api(symbol)
    future_norm = _normalize_symbol_for_api(futures_symbol or symbol)
    act = action.upper()

    if act == "OPEN":
        spot_action = SignalAction.OPEN_LONG
        future_action = SignalAction.OPEN_SHORT
    else:
        spot_action = SignalAction.CLOSE_LONG
        future_action = SignalAction.CLOSE_SHORT

    spot_signal = SignalPayload(
        action=spot_action,
        symbol=spot_norm,
        market_type=MarketType.SPOT,
        order_type=OrderType.MARKET,
        amount=amount,
        metadata={"strategy": "funding_arbitrage", "leg": "spot"},
    )

    future_signal = SignalPayload(
        action=future_action,
        symbol=future_norm,
        market_type=MarketType.FUTURE,
        order_type=OrderType.MARKET,
        amount=amount,
        leverage=leverage,
        margin_mode=MarginMode(margin_mode) if margin_mode else None,
        metadata={"strategy": "funding_arbitrage", "leg": "futures"},
    )

    client.send_signal(spot_signal)
    client.send_signal(future_signal)


def fetch_arbitrage_candidates(exchange: Any) -> Dict[str, Any]:
    markets = getattr(exchange, "markets", {}) or {}
    try:
        funding = exchange.fetch_funding_rates() or {}
    except Exception:
        funding = {}

    candidates: Dict[str, Any] = {}
    for symbol, info in markets.items():
        is_swap = bool(info.get("swap") or ":" in symbol)
        if not is_swap:
            continue

        spot_symbol = symbol.split(":")[0]
        spot_info = markets.get(spot_symbol)
        if not spot_info or not spot_info.get("spot"):
            continue

        funding_payload = (
            funding.get(symbol)
            or funding.get(symbol.replace("/", ""))
            or funding.get(symbol.split(":")[0])
            or {}
        )
        fr_val = funding_payload.get("fundingRate") if isinstance(funding_payload, dict) else None
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
    # Legacy compatibility path for tests and older examples.
    for event in feed.stream():
        snapshot = pd.DataFrame([event.payload])
        scores = evaluate_arbitrage_snapshot(engineer, bot, snapshot)
        if should_open_trade(scores, threshold):
            dispatch_arbitrage_orders(
                client=client,
                symbol=str(event.payload.get("futures_symbol") or event.payload.get("spot_symbol") or "BTC/USDT:USDT"),
                futures_symbol=str(event.payload.get("futures_symbol") or event.payload.get("spot_symbol") or "BTC/USDT:USDT"),
                action="OPEN",
                amount=trade_amount,
            )


def _build_strategy(args: argparse.Namespace) -> FundingArbitrageStrategy:
    config = FundingArbitrageConfig(
        target_notional=args.target_notional,
        min_hold_hours=args.min_hold_hours,
        open_funding_threshold=args.open_funding_threshold,
        close_funding_threshold=args.close_funding_threshold,
        leverage=args.leverage,
        margin_mode=MarginMode(args.margin_mode.upper()),
    )
    return FundingArbitrageStrategy(bot_id=args.bot_id, config=config)


def _load_dataframe_source(source: str) -> pd.DataFrame:
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Data source not found: {source}")

    if path.is_dir():
        candidates = sorted(path.glob("*.parquet"))
        if not candidates:
            raise ValueError(f"No parquet files found in directory: {source}")
        path = candidates[0]

    if path.suffix.lower() in {".parquet", ".pq"}:
        frame = pd.read_parquet(path)
    else:
        frame = pd.read_csv(path)

    if "timestamp" in frame.columns:
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
        frame = frame.dropna(subset=["timestamp"])

    return frame


def _build_live_runner(args: argparse.Namespace) -> Runner:
    client = QuantSignalClient(
        base_url=args.base_url,
        api_key=args.bot_api_key or "",
        default_bot_id=args.bot_id,
        signer_secret=args.bot_signer_secret,
    )
    strategy = _build_strategy(args)
    fetcher = build_arbitrage_fetcher(
        exchange_id=args.exchange,
        spot_symbol=args.spot_symbol,
        futures_symbol=args.futures_symbol or f"{args.spot_symbol}:USDT",
        timeframe=args.timeframe,
    )
    feed = ScheduledRESTFeed(trigger=CronTrigger(args.schedule_timeframe), fetcher=fetcher)
    dispatcher = LiveHTTPDispatcher(client, bot_api_key=args.bot_api_key)
    return Runner(feed=feed, strategy=strategy, dispatcher=dispatcher)


def _build_backtest_runner(args: argparse.Namespace) -> PortfolioBacktestRunner:
    source = args.backtest_csv or args.replay_csv or args.backtest_parquet
    if not source:
        raise ValueError("Backtest mode requires one of --backtest-csv, --backtest-parquet, or --replay-csv")

    dataframe = _load_dataframe_source(source)
    feed = DataFrameFeed(dataframe, timestamp_col="timestamp")
    strategy = _build_strategy(args)
    config = BacktestConfig(
        initial_cash=args.initial_cash,
        maker_fee_rate=args.maker_fee,
        taker_fee_rate=args.taker_fee,
        slippage_rate=args.slippage,
        default_max_size_percent=args.default_max_size_percent,
    )
    return PortfolioBacktestRunner(feed=feed, strategy=strategy, config=config)


def _upload_backtest_report(report: Any, args: argparse.Namespace) -> dict[str, Any]:
    client = BacktestUploadClient(
        BacktestUploadConfig(
            base_url=args.backend_url,
            bot_id=args.bot_id,
            api_key=args.api_key or "",
            signer_secret=args.signer_secret,
            run_name=args.run_name,
        )
    )
    return client.push_backtest_report(report)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Funding arbitrage example with a shared strategy for live and backtest")
    parser.add_argument("--mode", choices=("live", "backtest"), default="live")
    parser.add_argument("--bot-id", required=True, help="Bot id used in emitted SignalPayloads")

    parser.add_argument("--base-url", default="https://marcus-api.tromoi.xyz", help="Backend base URL")
    parser.add_argument("--bot-api-key", default=None, help="Runtime bot API key")
    parser.add_argument("--bot-signer-secret", default=None, help="Runtime bot HMAC signer secret")

    parser.add_argument("--spot-symbol", default="BTC/USDT", help="Spot symbol for live feed")
    parser.add_argument("--futures-symbol", default=None, help="Futures symbol for live feed")
    parser.add_argument("--exchange", default="binance", help="CCXT exchange id")
    parser.add_argument("--timeframe", default="1h", help="Candle timeframe")
    parser.add_argument("--schedule-timeframe", default="1h", help="Polling cadence for live mode")

    parser.add_argument("--target-notional", type=float, default=10.0, help="Target notional per leg")
    parser.add_argument("--min-hold-hours", type=float, default=8.0, help="Minimum hold duration before close")
    parser.add_argument("--open-funding-threshold", type=float, default=0.0, help="Funding threshold to open")
    parser.add_argument("--close-funding-threshold", type=float, default=0.0, help="Funding threshold to close")
    parser.add_argument("--leverage", type=int, default=1, help="Future leverage")
    parser.add_argument("--margin-mode", default="CROSS", choices=("CROSS", "ISOLATED"), help="Future margin mode")

    parser.add_argument("--backtest-csv", default=None, help="Replay a CSV file through DataFrameFeed for backtests")
    parser.add_argument("--backtest-parquet", default=None, help="Replay a Parquet file or directory for backtests")
    parser.add_argument("--replay-csv", default=None, help="Legacy alias for --backtest-csv")
    parser.add_argument("--initial-cash", type=float, default=0.0, help="Starting cash for backtests")
    parser.add_argument("--maker-fee", type=float, default=0.0, help="Maker fee rate")
    parser.add_argument("--taker-fee", type=float, default=0.0, help="Taker fee rate")
    parser.add_argument("--slippage", type=float, default=0.0, help="Slippage rate")
    parser.add_argument("--default-max-size-percent", type=float, default=None, help="Optional max size percent clamp")
    parser.add_argument("--output-dir", default="backtest_output", help="Directory for CSV/HTML exports")
    parser.add_argument("--export-html", action="store_true", help="Write a static HTML tear sheet")
    parser.add_argument("--upload-backtest", action="store_true", help="Upload the completed backtest report to Marcus backend")
    parser.add_argument("--backend-url", default="https://marcus-api.tromoi.xyz", help="Marcus backend base URL")
    parser.add_argument("--api-key", default=None, help="Bot API key for backtest upload")
    parser.add_argument("--signer-secret", default=None, help="Optional bot signer secret for backtest upload")
    parser.add_argument("--run-name", default=None, help="Optional display name for this backtest run")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    backtest_source = args.backtest_csv or args.backtest_parquet or args.replay_csv
    mode = args.mode
    if backtest_source:
        mode = "backtest"

    if mode == "backtest":
        runner = _build_backtest_runner(args)
        report = runner.run()
        export_backtest_results(report, output_dir=args.output_dir, export_html=args.export_html)
        print(f"cash={report.context.cash:.8f}")
        print(f"realized_pnl={report.context.realized_pnl:.8f}")
        print(f"unrealized_pnl={report.context.unrealized_pnl:.8f}")
        print(f"equity={report.context.equity:.8f}")
        print(f"fills={len(report.fills)}")

        if args.upload_backtest:
            response = _upload_backtest_report(report, args)
            print(f"Upload successful. Response: {response}")
            print("View at: https://marcus-ui.tromoi.xyz/terminal/leaderboard")
        return

    runner = _build_live_runner(args)
    runner.run()


if __name__ == "__main__":
    main()
