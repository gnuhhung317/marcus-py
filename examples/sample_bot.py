"""Example SMA bot moved to examples/ for documentation and demos.

Usage:
    python examples/sample_bot.py --base-url http://localhost:8080 --bot-id demo-sma

This script is the same as the demo used during development but is not exported
as part of the runtime SDK public API.
"""
import argparse
from quant_signal_sdk.ccxt_client import CCXTClient, close_prices_from_ohlcv
from quant_signal_sdk.strategy import SimpleSmaStrategy
from quant_signal_sdk.client import QuantSignalClient


def register_bot_via_client(client: QuantSignalClient, bot_id: str, name: str | None = None, auth_token: str | None = None, bot_api_key: str | None = None):
    payload = {
        "botId": bot_id,
        "name": name or bot_id,
        "description": "Sample SMA strategy bot",
    }
    return client.register_bot(payload, auth_token=auth_token, bot_api_key=bot_api_key)


def send_signal_via_client(client: QuantSignalClient, payload: dict, bot_api_key: str | None = None):
    if bot_api_key:
        return client.send_payload_with_bot_key(payload, bot_api_key=bot_api_key)
    return client.send_payload(payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--bot-id", required=True)
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument("--short", type=int, default=5)
    parser.add_argument("--long", type=int, default=15)
    parser.add_argument("--auth-token", default=None, help="Bearer auth token for web user")
    parser.add_argument("--bot-api-key", default=None, help="Bot API key; sent as X-Bot-Api-Key header")
    args = parser.parse_args()

    # Build auth headers: prefer auth token (Bearer) then bot API key header
    headers = {}
    if args.auth_token:
        headers["Authorization"] = f"Bearer {args.auth_token}"
    if args.bot_api_key:
        headers["X-Bot-Api-Key"] = args.bot_api_key

    sdk_client = QuantSignalClient(base_url=args.base_url, api_key="")

    print(f"Registering bot '{args.bot_id}' at {args.base_url} ...")
    try:
        resp = register_bot_via_client(sdk_client, args.bot_id, auth_token=args.auth_token, bot_api_key=args.bot_api_key)
        print("Registered:", resp)
    except Exception as e:
        print("Register failed (continuing if bot exists):", e)

    client = CCXTClient(exchange_id=args.exchange)
    try:
        ohlcv = client.fetch_ohlcv(args.symbol, timeframe=args.timeframe, limit=args.long + 10)
        closes = close_prices_from_ohlcv(ohlcv)
    except Exception as e:
        print("Market data fetch failed:", e)
        return

    strat = SimpleSmaStrategy(short_window=args.short, long_window=args.long)
    payload = strat.generate_signal_payload(args.bot_id, closes)
    if payload is None:
        print("Insufficient data to generate signal")
        return

    print("Sending signal:", payload)
    try:
        result = send_signal_via_client(sdk_client, payload, bot_api_key=args.bot_api_key)
        print("Signal result:", result)
    except Exception as e:
        print("Failed to send signal:", e)


if __name__ == "__main__":
    main()
