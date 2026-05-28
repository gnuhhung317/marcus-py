#!/usr/bin/env python3
"""Event-driven funding arbitrage example.

This entrypoint demonstrates the new kernel wiring:
Feed -> Strategy -> Dispatcher -> Runner.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant_signal_sdk import (
    FundingArbitrageConfig,
    FundingArbitrageStrategy,
    LiveHTTPDispatcher,
    LiveRESTFeed,
    MockDispatcher,
    ParquetReplayFeed,
    QuantSignalClient,
    PortfolioContext,
    SignalPayload,
    Runner,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Event-driven funding arbitrage example")
    parser.add_argument("--bot-id", required=True, help="Bot id used in emitted SignalPayloads")
    parser.add_argument("--base-url", default="http://localhost:8080", help="Backend base URL")
    parser.add_argument("--bot-api-key", default=None, help="Runtime bot API key")
    parser.add_argument("--bot-signer-secret", default=None, help="Runtime bot HMAC signer secret")
    parser.add_argument("--spot-symbol", default="BTC/USDT", help="Spot symbol for live feed")
    parser.add_argument("--futures-symbol", default=None, help="Futures symbol for live feed")
    parser.add_argument("--exchange", default="binance", help="CCXT exchange id")
    parser.add_argument("--timeframe", default="1h", help="Candle timeframe")
    parser.add_argument("--interval-seconds", type=float, default=60.0, help="Polling interval in live mode")
    parser.add_argument("--target-notional", type=float, default=10.0, help="Target notional per leg")
    parser.add_argument("--min-hold-hours", type=float, default=8.0, help="Minimum hold duration before close")
    parser.add_argument("--replay-csv", default=None, help="Optional replay file path loaded through pandas")
    parser.add_argument("--use-mock-dispatcher", action="store_true", help="Collect signals locally instead of posting them")
    parser.add_argument("--ledger-csv", default=None, help="Optional CSV path for MockDispatcher output")
    parser.add_argument("--state-json", default="funding_arb_portfolio.json", help="Local portfolio snapshot path")
    return parser


def load_initial_context(state_path: str | None) -> PortfolioContext:
    if not state_path:
        return PortfolioContext()

    path = Path(state_path)
    if not path.exists():
        return PortfolioContext()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return PortfolioContext()

    positions = raw.get("positions") or raw.get("active_positions") or {}
    hydrated_positions: dict[str, dict[str, Any]] = {}
    fallback_timestamp = raw.get("updated_at") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    for symbol, value in positions.items():
        if isinstance(value, dict):
            hydrated_positions[str(symbol)] = {
                **value,
                "generated_timestamp": value.get("generated_timestamp") or value.get("entered_at") or fallback_timestamp,
            }
        else:
            hydrated_positions[str(symbol)] = {
                "amount": float(value),
                "generated_timestamp": fallback_timestamp,
            }

    return PortfolioContext(positions=hydrated_positions)


def dump_context_snapshot(state_path: str, context: PortfolioContext) -> None:
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "active_positions": context.positions,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=4, default=str), encoding="utf-8")


def build_runner(args: argparse.Namespace) -> tuple[Runner, object]:
    config = FundingArbitrageConfig(
        target_notional=args.target_notional,
        min_hold_hours=args.min_hold_hours,
    )
    strategy = FundingArbitrageStrategy(bot_id=args.bot_id, config=config)
    initial_context = load_initial_context(args.state_json)

    if args.replay_csv:
        import pandas as pd

        dataframe = pd.read_csv(args.replay_csv)
        feed = ParquetReplayFeed(dataframe)
    else:
        feed = LiveRESTFeed(
            spot_symbol=args.spot_symbol,
            futures_symbol=args.futures_symbol,
            exchange_id=args.exchange,
            timeframe=args.timeframe,
            interval_seconds=args.interval_seconds,
        )

    if args.use_mock_dispatcher:
        dispatcher = MockDispatcher()
    else:
        client = QuantSignalClient(
            base_url=args.base_url,
            api_key=args.bot_api_key or "",
            default_bot_id=args.bot_id,
            signer_secret=args.bot_signer_secret,
        )
        dispatcher = LiveHTTPDispatcher(client, bot_api_key=args.bot_api_key)

    def after_signal_applied(_: SignalPayload, context: PortfolioContext) -> None:
        if args.state_json:
            dump_context_snapshot(args.state_json, context)

    runner = Runner(
        feed=feed,
        strategy=strategy,
        dispatcher=dispatcher,
        initial_context=initial_context,
        after_signal_applied=after_signal_applied,
    )
    return runner, dispatcher


def main() -> None:
    args = build_parser().parse_args()
    runner, dispatcher = build_runner(args)
    runner.run()

    if isinstance(dispatcher, MockDispatcher):
        print(f"Captured {len(dispatcher.ledger)} signals")
        if args.ledger_csv:
            dispatcher.export_csv(args.ledger_csv)
            print(f"Ledger exported to {args.ledger_csv}")


if __name__ == "__main__":
    main()