# Developer Guide — quant-signal-sdk

This guide shows you how to use the SDK to develop, backtest, and deploy trading strategies.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      Your Strategy                              │
│  class MyBot(BaseStrategy):                                     │
│    def on_event(self, event, context) -> list[SignalPayload]:   │
│        ... decide to trade based on event.payload ...           │
└────────────┬──────────────────────────┬─────────────────────────┘
             │                          │
    Feed provides MarketEvent    Context has positions/cash
             │                          │
┌────────────▼──────────┐   ┌──────────▼──────────────────┐
│       BaseFeed        │   │     PortfolioContext         │
│  stream() → events    │   │  positions, cash, equity,   │
│  (DataFrameFeed,      │   │  realized_pnl, total_fees   │
│   OhlcvReplayFeed,    │   └─────────────────────────────┘
│   ScheduledRESTFeed)  │
└───────────────────────┘
```

Two runners consume this interface:

| Runner | Use case | PnL | Source |
|--------|----------|-----|--------|
| `PortfolioBacktestRunner` | Historical simulation | Calculated in-memory | `runtime/backtest.py` |
| `Runner` | Live execution | Tracked by dispatcher + telemetry | `runtime/runner.py` |

## Prerequisites

```bash
# Install SDK
cd bot-framework-python
pip install -e .

# For ML features (funding pipeline, ranker)
pip install -e ".[ml]"

# Verify
quant-sdk --help
```

---

## Pattern 1: Single-symbol Backtest

**Goal:** Test a strategy against historical OHLCV data and see PnL, Sharpe, drawdown.

### 1. Write your strategy

```python
# my_bot.py
from quant_signal_sdk.models import SignalAction, SignalPayload, MarketType, OrderType
from quant_signal_sdk.runtime.interfaces import BaseStrategy

class MyBot(BaseStrategy):
    """Simple trend-following bot: buy when close > 50000, sell when close < 40000."""

    def __init__(self):
        self._in_position = False

    def on_event(self, event, context):
        close = event.payload.get("close", 0)

        if not self._in_position and close > 50000:
            self._in_position = True
            return [SignalPayload(
                action=SignalAction.OPEN_LONG,
                symbol="BTCUSDT",
                market_type=MarketType.SPOT,
                order_type=OrderType.MARKET,
                amount=0.01,
            )]

        if self._in_position and close < 40000:
            self._in_position = False
            return [SignalPayload(
                action=SignalAction.CLOSE_LONG,
                symbol="BTCUSDT",
                market_type=MarketType.SPOT,
                order_type=OrderType.MARKET,
                amount=0.01,
            )]

        return []
```

### 2. Write the backtest script

```python
# backtest_my_bot.py
import pandas as pd
from my_bot import MyBot
from quant_signal_sdk.runtime.adapters import DataFrameFeed
from quant_signal_sdk.runtime.backtest import PortfolioBacktestRunner, BacktestConfig
from quant_signal_sdk.cli import export_backtest_results

# Step 1: Load your data (any source — CSV, Parquet, exchange, database)
df = pd.read_parquet("D:\\data\\ohlcv\\BTCUSDT.parquet")
feed = DataFrameFeed(df, timestamp_col="timestamp")

# Step 2: Configure backtest parameters
config = BacktestConfig(
    initial_cash=10000.0,    # Starting capital in USD
    maker_fee_rate=0.0,      # Maker fee (limit orders)
    taker_fee_rate=0.001,    # Taker fee (market orders) — 0.1%
    slippage_rate=0.001,     # Slippage per trade — 0.1%
)

# Step 3: Run
runner = PortfolioBacktestRunner(feed=feed, strategy=MyBot(), config=config)
report = runner.run()

# Step 4: Export results
export_backtest_results(report, output_dir="backtest_results/btc_trend", export_html=True)

# Step 5: Print summary
print(f"Initial cash:    {config.initial_cash:.2f}")
print(f"Final equity:    {report.context.equity:.2f}")
print(f"Realized PnL:    {report.context.realized_pnl:.2f}")
print(f"Total fees:      {report.context.total_fees:.4f}")
print(f"Trades:          {report.metrics.total_trades}")
print(f"Win rate:        {report.metrics.win_rate:.1%}")
print(f"Sharpe ratio:    {report.metrics.sharpe_ratio:.2f}")
print(f"Max drawdown:    {report.metrics.max_drawdown:.1%}")
print(f"Profit factor:   {report.metrics.profit_factor:.2f}")
```

### 3. Run

```bash
python backtest_my_bot.py
```

Output:
```
Initial cash:    10000.00
Final equity:    10423.50
Realized PnL:    423.50
Total fees:      10.23
Trades:          5
Win rate:        60.0%
Sharpe ratio:    1.23
Max drawdown:    5.2%
Profit factor:   1.85
```

Open `backtest_results/btc_trend/tearsheet.html` in your browser to see equity curve + trades.

---

## Pattern 2: Multi-symbol Portfolio Backtest

**Goal:** Run a strategy across multiple symbols simultaneously.

### Key concepts

- `PortfolioBacktestRunner` tracks positions per `market_type:symbol` key
- Each `MarketEvent.payload` must include a `symbol` field
- Strategy reads `event.payload.get("symbol")` to identify which asset this event belongs to
- `PortfolioContext.positions` holds all open positions across symbols

### Write a multi-symbol strategy

```python
# multi_symbol_bot.py
from quant_signal_sdk.models import SignalAction, SignalPayload, MarketType, OrderType
from quant_signal_sdk.runtime.interfaces import BaseStrategy

class MultiSymbolBot(BaseStrategy):
    def __init__(self, threshold=0.01):
        self._threshold = threshold

    def on_event(self, event, context):
        symbol = event.payload.get("symbol", "UNKNOWN")
        close = event.payload.get("close", 0)
        funding = event.payload.get("funding_rate", 0)
        position_key = f"FUTURE:{symbol}"
        has_position = position_key in context.positions

        # Open short when funding rate is high (negative carry trade)
        if not has_position and funding > self._threshold:
            return [SignalPayload(
                action=SignalAction.OPEN_SHORT,
                symbol=symbol,
                market_type=MarketType.FUTURE,
                order_type=OrderType.MARKET,
                amount=100.0,  # $100 notional per symbol
            )]

        # Close when funding drops
        if has_position and funding < self._threshold * 0.5:
            return [SignalPayload(
                action=SignalAction.CLOSE_SHORT,
                symbol=symbol,
                market_type=MarketType.FUTURE,
                order_type=OrderType.MARKET,
                amount=100.0,
            )]

        return []
```

### Load multi-symbol data

```python
# backtest_portfolio.py
from multi_symbol_bot import MultiSymbolBot
from quant_signal_sdk.runtime.adapters import DataFrameFeed
from quant_signal_sdk.runtime.backtest import PortfolioBacktestRunner, BacktestConfig

# Option A: Use funding_pipeline to build a multi-symbol DataFrame
from quant_signal_sdk.funding_pipeline import resolve_data_paths, build_master_df

paths = resolve_data_paths("D:\\data")
master_df = build_master_df(paths, ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
# master_df has columns: timestamp, open, high, low, close, volume,
#                        funding_rate, sum_open_interest, symbol, ...

feed = DataFrameFeed(master_df, timestamp_col="timestamp")
runner = PortfolioBacktestRunner(
    feed=feed,
    strategy=MultiSymbolBot(threshold=0.0005),
    config=BacktestConfig(initial_cash=50000.0, taker_fee_rate=0.0004),
)
report = runner.run()
```

### Run

```bash
python backtest_portfolio.py
```

---

## Pattern 3: Upload Backtest Results to Marcus Backend

**Goal:** After running a backtest locally, upload the metrics to the Marcus platform so they appear on the leaderboard.

### Prerequisites

You need a bot registered on Marcus:

```bash
# Bot credentials (get these from provisioning or the dashboard)
BOT_ID="bot_abc123"
API_KEY="sk_..."
BACKEND_URL="https://marcus-api.tromoi.xyz"
```

### Upload from Python

```python
# upload_backtest.py
from quant_signal_sdk.runtime.backtest import PortfolioBacktestRunner, BacktestConfig
from quant_signal_sdk.runtime.backtest_upload import BacktestUploadClient, BacktestUploadConfig
from quant_signal_sdk.runtime.adapters import DataFrameFeed
from my_bot import MyBot
import pandas as pd

# 1. Run backtest (or load from disk)
df = pd.read_parquet("D:\\data\\ohlcv\\BTCUSDT.parquet")
runner = PortfolioBacktestRunner(
    feed=DataFrameFeed(df, timestamp_col="timestamp"),
    strategy=MyBot(),
    config=BacktestConfig(initial_cash=10000.0),
)
report = runner.run()

# 2. Upload to backend
client = BacktestUploadClient(BacktestUploadConfig(
    base_url="https://marcus-api.tromoi.xyz",
    bot_id="bot_abc123",
    api_key="sk_...",
    run_name="My Backtest v1",
))
response = client.push_backtest_report(report)
print("Upload response:", response)
```

### Upload via CLI

```bash
# First run backtest and save results
python backtest_my_bot.py   # exports to backtest_results/btc_trend/

# Then upload the results directory
quant-sdk upload backtest_results/btc_trend/ \
  --bot-id bot_abc123 \
  --api-key sk_... \
  --backend-url https://marcus-api.tromoi.xyz \
  --run-name "BTC Trend v1"
```

The CLI reads `metrics.json` and `equity_curve.csv` from the directory and posts them to `POST /api/v1/bots/{id}/backtest-results`.

### Verify on UI

Open `https://marcus-ui.tromoi.xyz/terminal/leaderboard` in your browser. You should see:
- Your backtest run name
- Sharpe, Max Drawdown, Win Rate, Profit Factor
- Equity curve chart
- Closed trades list

---

## Pattern 4: Live Bot

**Goal:** Run a strategy live, dispatching signals to the backend.

### Key concepts

- `Runner` = feed + strategy + dispatcher (not backtest engine)
- `BaseDispatcher` sends signals to the outside world (e.g., `LiveHTTPDispatcher`)
- Telemetry and dry-run state sync are **separate** from the runner (see sub-sections below)

```python
# live_bot.py
from quant_signal_sdk.client import QuantSignalClient
from quant_signal_sdk.runtime.runner import Runner
from quant_signal_sdk.runtime.adapters import ScheduledRESTFeed, CronTrigger, LiveHTTPDispatcher
from my_bot import MyBot

BOT_ID = "bot_abc123"
API_KEY = "sk_..."
BASE_URL = "https://marcus-api.tromoi.xyz"

# 1. Feed — fetches market data on schedule
def fetch_market_data():
    """Return a DataFrame with latest OHLCV. SDK calls this on every tick."""
    import pandas as pd
    return pd.DataFrame([{"timestamp": pd.Timestamp.utcnow(), "close": 50000}])

feed = ScheduledRESTFeed(
    trigger=CronTrigger("1h"),
    fetcher=fetch_market_data,
)

# 2. Dispatcher — sends signals to Marcus backend
client = QuantSignalClient(base_url=BASE_URL, api_key=API_KEY, default_bot_id=BOT_ID)
dispatcher = LiveHTTPDispatcher(client=client)

# 3. Run — infinite loop
runner = Runner(feed=feed, strategy=MyBot(), dispatcher=dispatcher)
print("Runner started. Press Ctrl+C to stop.")
runner.run()
```

---

### Pattern 4A: Telemetry (Native Runner Integration)

`TelemetryClient` is wrapped by `HttpTelemetrySyncer` to report operational metrics (equity, realized PnL, unrealized PnL, etc.) natively in the `Runner` core loop. It automatically handles throttling based on the configured `interval`.

```python
from quant_signal_sdk.runtime.telemetry import TelemetryClient, TelemetryConfig
from quant_signal_sdk.runtime.sync import HttpTelemetrySyncer

telemetry_client = TelemetryClient(TelemetryConfig(
    base_url="https://marcus-api.tromoi.xyz",
    bot_id="bot_abc123",
    api_key="sk_...",
))

# Create telemetry syncer to report every 60 seconds (throttled automatically)
telemetry_syncer = HttpTelemetrySyncer(client=telemetry_client, interval=60.0)

# Pass it directly to the Runner constructor:
runner = Runner(
    feed=feed,
    strategy=MyBot(),
    dispatcher=dispatcher,
    telemetry_syncer=telemetry_syncer,
)
runner.run()
```

POSTs to `POST /api/v1/bots/{id}/telemetry` under the hood. If the runner stops running or fails to report telemetry within the expected heartbeat window, the UI shows the bot as **offline**.

---

### Pattern 4B: Dry-run State Sync (separate transport)

Dry-run sync tracks live paper-trading positions on the backend. It is **separate** from telemetry and from the runner. Three transports available:

```python
from quant_signal_sdk.runtime.dry_run import DryRunSyncClient, DryRunSyncConfig
from quant_signal_sdk.runtime.sync import HttpDryRunSyncer, WebSocketDryRunSyncer, FileSyncer

# Option 1: REST-based sync
syncer = HttpDryRunSyncer(DryRunSyncClient(DryRunSyncConfig(
    base_url="https://marcus-api.tromoi.xyz",
    bot_id="bot_abc123",
    api_key="sk_...",
)))

# Option 2: WebSocket-based sync
syncer = WebSocketDryRunSyncer(
    transport=WebSocketTransport("wss://marcus-api.tromoi.xyz/ws/dry-run"),
    config=DryRunSyncConfig(...),
)

# Option 3: File-based sync (local only)
syncer = FileSyncer(filepath="/state/dry_run_state.json")

# Use with Runner:
runner = Runner(
    feed=feed,
    strategy=MyBot(),
    dispatcher=dispatcher,
    state_syncer=syncer,                 # syncs ledger context after each signal
    telemetry_syncer=telemetry_syncer,   # syncs operational status periodically
)
runner.run()
```

The `StateSyncer` contract keeps state sync outside `Runner` so you can swap REST ↔ WebSocket ↔ file without changing strategy code.

---

## Data Sources — Implementing your own Feed

The SDK provides 3 built-in feeds, but you can implement your own:

| Built-in Feed | Constructor | Use case |
|--------------|-------------|----------|
| `DataFrameFeed` | `DataFrameFeed(df, timestamp_col)` | Backtest with any DataFrame |
| `OhlcvReplayFeed` | `OhlcvReplayFeed(df, timestamp_col)` | Auto-normalize OHLCV columns |
| `ScheduledRESTFeed` | `ScheduledRESTFeed(trigger, fetcher)` | Live data from exchange API |

### Custom Feed — implement `BaseFeed`

```python
from collections.abc import Iterator
from datetime import datetime
from quant_signal_sdk.runtime.interfaces import BaseFeed, MarketEvent

class MyDatabaseFeed(BaseFeed):
    """Feed from any source — just yield MarketEvent objects."""

    def __init__(self, connection_string, query):
        self._conn = connection_string
        self._query = query

    def stream(self) -> Iterator[MarketEvent]:
        rows = self._execute_query()
        for row in rows:
            yield MarketEvent(
                timestamp=datetime.fromisoformat(row["timestamp"]),
                payload=dict(row),  # open, high, low, close, volume, ...
            )

    def _execute_query(self):
        # ... your DB logic ...
        pass
```

Then use it anywhere a `BaseFeed` is expected:

```python
runner = PortfolioBacktestRunner(
    feed=MyDatabaseFeed("sqlite:///prices.db", "SELECT * FROM btc_1h"),
    strategy=MyBot(),
    config=BacktestConfig(initial_cash=10000.0),
)
```

---

## API Reference Quick Links

| Component | File | Key Classes |
|-----------|------|-------------|
| Backtest engine | `src/quant_signal_sdk/runtime/backtest.py` | `PortfolioBacktestRunner`, `BacktestConfig`, `BacktestReport`, `BacktestMetrics` |
| Upload client | `src/quant_signal_sdk/runtime/backtest_upload.py` | `BacktestUploadClient`, `BacktestUploadConfig` |
| Feeds | `src/quant_signal_sdk/runtime/adapters.py` | `DataFrameFeed`, `OhlcvReplayFeed`, `ScheduledRESTFeed` |
| Live runner | `src/quant_signal_sdk/runtime/runner.py` | `Runner` |
| Telemetry | `src/quant_signal_sdk/runtime/telemetry.py` | `TelemetryClient`, `TelemetryConfig` |
| Interfaces | `src/quant_signal_sdk/runtime/interfaces.py` | `BaseFeed`, `BaseStrategy`, `BaseDispatcher`, `PortfolioContext` |
| Data loader | `src/quant_signal_sdk/data_loader.py` | `BundleLoader`, `BundleManifest` |
| Funding pipeline | `src/quant_signal_sdk/funding_pipeline.py` | `resolve_data_paths`, `load_symbol_frame`, `build_master_df` |
| Signal client | `src/quant_signal_sdk/client.py` | `QuantSignalClient` |
| Models | `src/quant_signal_sdk/models.py` | `SignalPayload`, `SignalAction`, `MarketType`, `OrderType` |

## Troubleshooting

| Symptom | Cause | Solution |
|---------|-------|----------|
| No fills in backtest | Data has no OHLCV columns | Ensure payload has `open`, `high`, `low`, `close` |
| All orders canceled | `cancel_after` is set | Check `ExecutionPolicies.cancel_order_after` |
| Negative equity | Fees + slippage exceed capital | Reduce `taker_fee_rate` or `slippage_rate` |
| Upload returns 401 | Invalid API key | Verify `bot_id` and `api_key` from provisioner |
| Telemetry not visible | Wrong bot_id | Check `bot_id` matches what was provisioned |
| Multi-symbol not working | Missing `symbol` in payload | Ensure `event.payload` has a `symbol` field |