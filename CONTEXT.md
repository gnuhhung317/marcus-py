# CONTEXT — bot-framework-python (L1 Service)

> **Parent**: [CONTEXT_MAP.md](../CONTEXT_MAP.md) | **Changelog**: [CONTEXT_CHANGELOG.md](CONTEXT_CHANGELOG.md)
> **Role**: Developer SDK — Plug-and-play Python framework for quantitative developers to build and deploy trading bots

---

## Service Identity

| Property | Value |
|----------|-------|
| Stack | Python 3.11+, asyncio, Pydantic, WebSocket |
| Package | `quant_signal_sdk` (published to PyPI) |
| Install | `pip install -e .[dev,backtest,market-data]` |
| Test | `PYTHONPATH=src python -m pytest -q` (from `bot-framework-python/` dir) |
| Scope | Developer-side SDK (NOT executor runtime) |

### ⚠️ Scope Clarification
- `quant_signal_sdk` = **developer-side SDK** (this service)
- `local_executor` = **trader runtime** (separate service: `local-executor-client/`)
- Do NOT implement executor-only changes when SDK work is requested.

---

## Architecture & Philosophy

> "Developers should only care about their alpha. We handle the rest."

**Convention over Configuration** by default, with escape hatches for power users.

### Core Abstractions
1. **Bot** — Base class developers extend. Provides lifecycle hooks.
2. **Signal** — Standardized output (LONG, SHORT, etc.) with confidence, TP/SL.
3. **DataProvider** — Interface for market data (Live: WebSocket/REST, Backtest: CSV/DB).
4. **Transport** — Handles WebSocket connection, auth, heartbeat, retry, rate limiting.

### Strategy Lifecycle Hooks
- `on_bar_close(symbol, timeframe, ohlcv)` — Scanning Alpha (multi-timeframe)
- `on_tick(symbol, price, ticker)` — Monitoring & execution (trailing stop)

---

## Module Structure

```
bot-framework-python/
├── src/quant_signal_sdk/
│   ├── __init__.py
│   ├── cli.py                # CLI entry point
│   ├── data_loader.py        # Market data loading utilities
│   ├── funding_pipeline.py   # Funding rate pipeline
│   ├── runtime/
│   │   ├── __init__.py
│   │   ├── interfaces.py     # Core interfaces (Bot, DataProvider, etc.)
│   │   ├── backtest.py       # Backtesting engine
│   │   └── adapters.py       # Transport adapters
│   └── ...
├── examples/
│   └── funding_arbitrage_bot.py  # Example bot implementation
├── tests/
│   └── test_funding_arbitrage_bot.py
├── models/                   # Pre-trained ML models
├── scripts/                  # Utility scripts
├── pyproject.toml            # Package metadata & dependencies
├── funding_carry_backtest.py # Standalone backtest script
├── funding_carry_bot.py      # Standalone bot script
├── my_bot.py                 # Quick-start bot example
└── backtest_output_*/        # Backtest results (CSV, metrics)
```

---

## Canonical Bot Setup

```python
from quant_signal_sdk import BaseStrategy, MarketType, OrderType, SignalAction, SignalPayload

class MyTrendBot(BaseStrategy):
    def on_event(self, event, context):
        if self.detect_uptrend(event.payload):
            return [SignalPayload(
                action=SignalAction.OPEN_LONG,
                symbol="BTCUSDT",
                market_type=MarketType.SPOT,
                order_type=OrderType.MARKET,
                amount=0.01,
                metadata={"confidence": 0.85},
            )]
        return []

    def detect_uptrend(self, tick):
        return tick["sma_20"] > tick["sma_50"]
```

---

## Framework Handles Internally

- WebSocket connection with exponential-backoff reconnect
- Signal schema validation (Pydantic) and JSON serialization
- Authentication handshake with central CMS
- Heartbeat and liveness signaling
- Rate limiting and backpressure
- Graceful shutdown with pending signal flush
- Async-first runtime with dual sync/async handler adapters
- Structured logging with rotation
- Health metrics exposure

---

## Non-Negotiable Constraints

- DO NOT leak/log developer secrets, strategy internals, or raw credentials
- DO NOT couple strategy logic to transport or infrastructure
- DO NOT introduce breaking SDK changes without migration guidance
- Enforce semantic versioning with 6-month deprecation window

---

## Contract Alignment (with signal-core-backend)

- **AsyncAPI** is the primary contract authority
- Generate Python transport/message bindings from AsyncAPI specs
- For contract or credential-policy changes → consult with backend (marcus-domain-first) first
- Reconcile SDK and backend contracts before finalizing changes

---

## Important Gotchas

1. **Test runner**: Use `PYTHONPATH=src python -m pytest -q` from the `bot-framework-python/` directory so tests run against the working tree.

2. **Package install locally**: `pip install -e .[dev,backtest,market-data]`

3. **Install from GitHub**: Use `subdirectory=bot-framework-python` URL for monorepo installs.

4. **Funding carry**: `funding_carry_backtest.py` and `funding_carry_bot.py` are standalone scripts, not part of the SDK package.

---

## Bot Lifecycle Pipeline

1. **Historical backtest**: `PortfolioBacktestRunner` generates a `BacktestReport`; `BacktestUploadClient` uploads it to `POST /api/v1/bots/{botId}/backtest-results`.
2. **Live dry-run**: `Runner` delegates portfolio sync to `StateSyncer` implementations. Prefer `create_dry_run_syncer(...)` for REST dry-run wiring so the persistence callback and HTTP syncer are configured together.
3. **Operational telemetry**: `TelemetryClient` is reserved for CPU, latency, heartbeat, and similar non-PnL metrics. `BotTelemetryClient` remains a compatibility alias.
4. **Separation rule**: Dry-run state and telemetry are different payloads. Keep their clients and transports separate when adding new runtime features.

---

## Key Data Schemas

### Signal Payload (Bot → Backend)
```json
{
  "signal_id": "uuid",
  "bot_id": "bot_alpha_v1",
  "symbol": "BTC/USDT",
  "timeframe": "1h",
  "action": "OPEN_LONG",
  "params": { "entry_price": 45000.0, "atr_value": 120.5 },
  "metadata": {
    "analysis_context": { "d1_trend": "BULLISH", "h4_rsi": 32.5 },
    "logic_version": "2.0"
  }
}
```

---

> **Update Trigger**: When changing public SDK API, signal schema, transport protocol, or adding new bot hooks → update this CONTEXT.md and append to CONTEXT_CHANGELOG.md
