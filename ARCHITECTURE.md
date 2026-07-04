# ARCHITECTURE.md - Marcus Trading Bot Framework

## 1. Mental Model: Layered SDK Architecture
This repository is designed as a layered developer SDK. The package root exposes only the core signal contract, signing helper, HTTP client, and runtime interfaces. Backtesting, market data, and ML/research helpers live in optional submodules and extras.

### Core Domains
- **Core Signal SDK**: Standardizes trading intent via `SignalPayload` and submits it with `QuantSignalClient`.
- **Runtime Engine**: Manages dry-run state (`PortfolioContext`) and orchestration via `Runner`.
- **Backtest Layer**: Provides `PortfolioBacktestRunner` and report upload helpers behind optional Pandas-backed functionality.
- **Market Data Layer**: Provides CCXT-backed downloaders through the `market-data` extra.
- **ML/Research Layer**: Provides ranker and funding pipeline utilities through the `ml` extra.

---

## 2. Lifecycle & Core Flow: The Journey of a Signal
The most important entity in this system is the **`SignalPayload`**. Its lifecycle follows a strict path to ensure reliability:

1.  **Ingestion**: `BaseFeed` (e.g., `OhlcvReplayFeed`) streams `MarketEvent` objects.
2.  **Transformation (Decision)**: `BaseStrategy.on_event` receives the event and a snapshot of the `PortfolioContext`. It emits one or more `SignalPayload` objects.
3.  **Boundary Guard (Validation)**: 
    - `SignalTranslator` validates timeframe integrity (checks for data gaps).
    - `RiskManager` (optional) calculates SL/TP and validates position sizing.
4.  **Egress (Dispatch)**: 
    - **Backtest**: Handled by `PortfolioBacktestRunner`, which keeps a symbol-aware quote registry, matches orders only against the relevant asset's subsequent quote updates, and supports both flat and composite event shapes.
    - **Live**: `QuantSignalClient` signs the payload (HMAC-SHA256) and sends it via REST to the backend.

## 2.5 Dual Lifecycle Publishing

The SDK now treats bot history as two separate pipelines:

1.  **Historical backtest**: `PortfolioBacktestRunner` produces a `BacktestReport`, and `BacktestUploadClient` can upload it to `/api/v1/bots/{botId}/backtest-results`.
2.  **Live dry-run**: `Runner` delegates state persistence and sync to a pluggable `StateSyncer` implementation. `HttpDryRunSyncer` is the default REST transport, but `WebSocketDryRunSyncer` and `FileSyncer` are also available.
3.  **Operational telemetry**: `TelemetryClient` is reserved for non-PnL metrics such as latency, CPU, and heartbeats.

This split keeps transport concerns out of the core `Runner` loop and lets the backend merge historical and out-of-sample data later. `Runner` local position state should be treated as dry-run state, not confirmed exchange execution truth.

---

## 3. In/Out Boundaries
- **Input Protocols**:
    - **Live**: Typically WebSocket or REST (via `BaseFeed` implementations).
    - **Backtest**: CSV/DataFrame via `OhlcvReplayFeed`.
      Flat events use root OHLC plus `symbol`; composite events require leg-specific OHLC for any leg that should be executable.
- **Output Contracts**:
    - **REST API**: JSON-over-HTTP. Authenticated via `X-Bot-Api-Key` and `X-Signature` (HMAC).
    - **Schema**: Strictly enforced by Pydantic models in `models.py`.

---

## 4. Architectural Highlights (The "Aha" Moments)
- **Immutable State Snapshots**: The `Runner` creates a snapshot of `PortfolioContext` before passing it to the strategy. This prevents a strategy from accidentally mutating the global state mid-calculation.
- **Contract-Driven Development**: The system uses `tests/fixtures/contracts/` to ensure the Python models always match the backend's expectations.
- **Timeframe Safety**: The `SignalTranslator` will **abort** signal generation if it detects data gaps, preventing "ghost signals" based on stale data.

---

## 5. Critique & Optimization (The Reality Check)

### Current Bottlenecks
- **Synchronous I/O**: The `Runner` is still synchronous, but network sync is no longer embedded in the core loop. If a sync transport is slow, it can be swapped independently through `StateSyncer`.
    - *Optimization*: Move to `asyncio` for the `Runner` and feed/strategy loop if the execution model ever needs higher throughput.
- **State Persistence**: The `PortfolioContext` is currently in-memory. If the process crashes, the bot "forgets" its positions unless the strategy implements its own persistence (e.g., SQLite or Redis).
    - *Status*: Dry-run persistence now lives outside `Runner` via `DryRunStateTracker`; a more distributed store can be introduced without changing the loop itself.

### Coupling
- **Network Library**: The SDK uses `requests` underneath `NetworkClient`.
    - *Status*: Authentication headers, canonical JSON, HMAC signing, and empty-response handling are centralized so endpoint clients do not drift.
- **CCXT Dependency**: CCXT is handled as an optional `market-data` extra so the core SDK remains lightweight.

---

## 6. Module Responsibility Map
| Module | Responsibility |
| :--- | :--- |
| `models.py` | Defines the "Contract" (Schemas & Enums). |
| `_http.py` | Shared HTTP authentication, canonical body, and response helpers. |
| `translator.py` | Data integrity gatekeeper and payload serializer. |
| `runner.py` | The main loop orchestrating Feed -> Strategy -> Dispatcher. |
| `backtest.py` | High-fidelity local exchange simulation. |
| `backtest_upload.py` | Batch upload of `BacktestReport` to backend historical storage. |
| `sync.py` | Dry-run state tracking and pluggable sync transports. |
| `telemetry.py` | Operational telemetry client, separate from dry-run PnL/state sync. |
| `client.py` | Secure network communication (Signing & POST). |
| `ccxt_client.py` | Optional market data adapter. |
