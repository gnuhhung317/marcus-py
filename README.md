# Quant Signal SDK

This repository now contains only the developer SDK package: `quant_signal_sdk`.

Local executor client code has been moved to `local-executor-client` in the workspace.

## Features
- Pydantic signal models and enums (`SignalPayload`, `SignalSide`, `SignalAction`).
- Retry-enabled HTTP transport (`NetworkClient`) using `requests` and `urllib3.Retry`.
- Optional HMAC SHA-256 payload signing helper (`generate_hmac_signature`).
- High-level API client (`QuantSignalClient`) for authenticated signal submission.
- Minimal `BaseStrategy` contract for strategy inheritance.

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

## Tests
```bash
python -m unittest discover -s tests -v
```

## Build
```bash
python -m build --sdist --wheel
```

## Contract Fixtures
- SDK fixtures are versioned under `tests/fixtures/contracts`.
- SDK compatibility checks are in `tests/test_contract_compatibility_sdk.py`.
