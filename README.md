# Quant Signal SDK

This repository now contains only the developer SDK package: `quant_signal_sdk`.

Local executor client code has been moved to `local-executor-client` in the workspace.

## Features
- Pydantic signal models and enums (`SignalPayload`, `SignalSide`, `SignalAction`).
- Retry-enabled HTTP transport (`NetworkClient`) using `requests` and `urllib3.Retry`.
- Optional HMAC SHA-256 payload signing helper (`generate_hmac_signature`).
- High-level API client (`QuantSignalClient`) for authenticated signal submission.
- Minimal `BaseStrategy` contract for strategy inheritance.
- In-memory portfolio backtest runner with OHLCV replay and execution-policy enforcement.

## Quickstart
```python
from quant_signal_sdk import QuantSignalClient, SignalPayload

client = QuantSignalClient(
    base_url="https://api.example.com",
    api_key="your-api-key",
    signer_secret="optional-signing-secret",
)

signal = SignalPayload(
    side="LONG",
    action="OPEN_LONG",
    symbol="BTCUSDT",
    tp=72000,
    sl=68500,
    confidence_score=0.84,
    metadata={"strategy": "trend_v1"},
)

result = client.send_signal(signal)
print(result)
```

## Backtest

Create a `my_bot.py` file that exports a strategy class or `STRATEGY` object with `on_event(...)`, then run:

```bash
quant-sdk backtest --bot-file my_bot.py --data-csv candles.csv --initial-cash 1000
```

The backtest engine replays OHLCV candles, queues signals for the next tick, and prints a simple portfolio summary when the run completes.

If your OHLCV data is stored as Parquet (recommended per `GUIDE_DATA.md`), point the CLI at the Parquet file or directory. Example using the dataset layout in `GUIDE_DATA.md`:

```powershell
python -m quant_signal_sdk.cli backtest --bot-file my_bot.py \
    --data-parquet "D:\Code\Projects\self-projects\macd-overlay - Copy\data\ohlcv\BTCUSDT.parquet" \
    --timestamp-column timestamp --initial-cash 1000 --output-dir backtest_output_parquet --export-html
```

The CLI accepts either `--data-csv` (legacy) or `--data-parquet` (preferred). When a directory is passed to `--data-parquet` the first `*.parquet` file is used.

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

Install optional market-data helpers:

```bash
pip install -e .[market-data]
```

Install both dev and market-data extras together:

```bash
pip install -e .[dev,market-data]
```

After publishing to PyPI, users can install the released package with:

```bash
pip install quant-signal-sdk
```

## Tests
```bash
python -m unittest discover -s tests -v
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
