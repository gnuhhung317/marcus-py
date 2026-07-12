# Quant Signal SDK

This repository now contains the developer SDK package: `quant_signal_sdk`.

> 📖 **New:** See [DEVELOPER.md](DEVELOPER.md) for a complete step-by-step guide — writing strategies, backtesting with real data, uploading results to Marcus backend, and running live bots with telemetry.

Local executor client code has been moved to `local-executor-client` in the workspace.

## Features
- Pydantic signal models and enums (`SignalPayload`, `SignalSide`, `SignalAction`).
- Retry-enabled HTTP transport (`NetworkClient`) using `requests` and `urllib3.Retry`.
- Optional HMAC SHA-256 payload signing helper (`generate_hmac_signature`).
- High-level API client (`QuantSignalClient`) for authenticated signal submission.
- Minimal `BaseStrategy` contract for strategy inheritance.
- Optional exchange-agnostic CCXT market-data downloader for OHLCV, symbol discovery, and funding history.
- Optional in-memory portfolio backtest runner with OHLCV replay and execution-policy enforcement.
- Optional backtest publishing client for uploading completed `BacktestReport` objects to the backend.
- Pluggable dry-run sync helpers (`StateSyncer`, `HttpDryRunSyncer`, `WebSocketDryRunSyncer`, `FileSyncer`).
- Dedicated operational telemetry client (`TelemetryClient`) separate from dry-run PnL/state sync.

## Quickstart
```python
from quant_signal_sdk import ExecutionPolicies, MarketType, OrderType, QuantSignalClient, SignalAction, SignalPayload

client = QuantSignalClient(
    base_url="https://api.example.com",
    api_key="your-api-key",
    default_bot_id="bot_123",
    signer_secret="optional-signing-secret",
)

signal = SignalPayload(
    action=SignalAction.OPEN_LONG,
    symbol="BTCUSDT",
    market_type=MarketType.SPOT,
    order_type=OrderType.MARKET,
    entry=70000,
    amount=0.01,
    stop_loss=68500,
    take_profit=72000,
    metadata={"strategy": "trend_v1", "confidence_score": 0.84},
    policies=ExecutionPolicies(max_size_percent=0.1, cancel_order_after=1711976400),
)

result = client.send_signal(signal)
print(result)
```

Transport contract notes:
- Canonical published symbol format is `BTCUSDT`.
- Supported transport actions are `OPEN_LONG`, `OPEN_SHORT`, `CLOSE_LONG`, `CLOSE_SHORT`, and `UPDATE_TP_SL`.
- `ExecutionPolicies` serializes `maxSizePercent`, `cancelOrderAfter`, and `closePositionAfter` on the wire.
- `SignalAction.CLOSE` remains a deprecated SDK compatibility enum, but `QuantSignalClient` rejects it before transport.

## Backtest

Create a `my_bot.py` file that exports a strategy class or `STRATEGY` object with `on_event(...)`, then run:

```bash
quant-sdk backtest --bot-file my_bot.py --data-csv candles.csv --initial-cash 1000
```

The backtest engine replays OHLCV candles, queues signals for the next tick, and prints a simple portfolio summary when the run completes. The runner is symbol-aware: it keeps a per-symbol quote registry, marks positions from each symbol's latest quote, and only fills orders when the matching symbol has an executable quote.

If your OHLCV data is stored as Parquet (recommended per `GUIDE_DATA.md`), point the CLI at the Parquet file or directory. Example using the dataset layout in `GUIDE_DATA.md`:

```powershell
python -m quant_signal_sdk.cli backtest --bot-file my_bot.py \
    --data-parquet "D:\Code\Projects\self-projects\macd-overlay - Copy\data\ohlcv\BTCUSDT.parquet" \
    --timestamp-column timestamp --initial-cash 1000 --output-dir backtest_output_parquet --export-html
```

The CLI accepts either `--data-csv` (legacy) or `--data-parquet` (preferred). When a directory is passed to `--data-parquet` the first `*.parquet` file is used.

Composite backtests are supported in v1, but executable composite legs must provide leg-specific OHLC such as `spot_open/high/low/close` and `futures_open/high/low/close`. A composite payload that only carries `*_close` values is sufficient for marking equity, but not for executing market or limit orders on that leg. Upstream loaders must keep composite timestamps aligned to avoid look-ahead bias.

The v1 portfolio engine uses one shared cash ledger across spot, future, and margin positions. That is intentional for sizing and cross-asset accounting, but it does not simulate isolated-margin liquidation buckets or wallet transfers.

### Publish backtest results

The backtest CLI still exports local CSV/HTML/JSON artifacts, but it can also upload the completed report to the backend as a historical run:

```bash
quant-sdk backtest --bot-file my_bot.py --data-csv candles.csv --initial-cash 1000 \
  --upload-backtest \
  --backend-url https://api.example.com \
  --bot-id bot_123 \
  --api-key <bot-api-key> \
  --signer-secret <optional-signing-secret>
```

The upload is batch-only. Equity history and closed trades are stored as `HISTORICAL`, while live dry-run sync continues through `/api/v1/bots/{botId}/dry-run/sync`.

### Dry-run and telemetry

For live paper trading, use the factory so the runner receives both the state syncer and the callback that persists applied signals:

```python
from quant_signal_sdk.runtime.dry_run import DryRunSyncConfig
from quant_signal_sdk.runtime.runner import Runner
from quant_signal_sdk.runtime.sync import create_dry_run_syncer

dry_run = create_dry_run_syncer(
    DryRunSyncConfig(
        base_url="https://api.example.com",
        bot_id="bot_123",
        api_key="your-bot-api-key",
        signer_secret="optional-signing-secret",
    )
)

runner = Runner(
    feed,
    strategy,
    dispatcher,
    after_signal_applied=dry_run.after_signal_applied,
    state_syncer=dry_run.state_syncer,
)
```

Directly composing `DryRunSyncClient`, `DryRunStateTracker`, and `HttpDryRunSyncer` is still supported for advanced usage. The factory is preferred because it avoids syncing empty position state.

Operational telemetry is separate. Use `TelemetryClient` for metrics like CPU, latency, and heartbeat-style signals instead of reusing the dry-run transport.

## Requirements
- Python 3.10+

## Setup
Install the library for local development:

```bash
pip install -e .
```

Install with SDK development tools:

```bash
pip install -e .[dev]
```

Install optional backtest helpers:

```bash
pip install -e .[backtest]
```

Install optional market-data helpers:

```bash
pip install -e .[market-data]
```

Install dev, backtest, and market-data extras together:

```bash
pip install -e .[dev,backtest,market-data]
```

For multi-exchange downloads, use the generic downloader:

```python
from quant_signal_sdk.ccxt_client import ExchangeDataDownloader

downloader = ExchangeDataDownloader(exchange_id="binance", market_type="swap")
frame = downloader.fetch_ohlcv_frame("BTC/USDT:USDT", timeframe="1h", since="2024-01-01", paginate=True)
symbols = downloader.list_symbols(quote_asset="USDT", market_type="swap")
```

Or use the CLI:

```bash
quant-sdk install-ohlcv --exchange binance --symbols BTC/USDT --data-root data
quant-sdk install-data --exchange binance --symbols BTC/USDT --data-root data
```

After publishing to PyPI, users can install the released package with:

```bash
pip install quant-signal-sdk
```

## Tests
```bash
PYTHONPATH=src python -m pytest -q
```

## Release
Build the distribution and validate the artifacts before upload:

```bash
python -m build --sdist --wheel
python -m twine check dist/*
```

If you use GitHub Actions trusted publishing, the workflow in `.github/workflows/publish-pypi.yml` publishes automatically when you create a GitHub Release. Configure the PyPI trusted publisher once for this repository, then stop using API tokens for uploads.

## Example: register -> signer secret behavior

When registering a bot via the example `examples/sample_bot.py`, the backend returns
both an `apiKey` (runtime API key) and a `rawSecret` (signer secret). The example
attaches the returned `rawSecret` to the `QuantSignalClient` as the `signer_secret`
when the user did not already supply one. This ensures subsequent signal POSTs
include the required `X-Timestamp` and `X-Signature` headers that the server
validates.

If you prefer to manage signing secrets yourself, pass `--bot-signer-secret` to
the example and the returned `rawSecret` will not overwrite it.


## Build
```bash
python -m build --sdist --wheel
```

## Contract Fixtures
- SDK fixtures are versioned under `tests/fixtures/contracts`.
- SDK compatibility checks are in `tests/test_contract_compatibility_sdk.py`.
