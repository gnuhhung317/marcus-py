# ARCHITECTURE.md - Marcus Trading Bot Framework

## 1. Mental Model: The "Plug-and-Play" Architecture
This repository is designed as a **Hexagonal Architecture** (Ports and Adapters) for algorithmic trading. The core logic (Strategy) is isolated from external concerns (Data Feeds and Signal Delivery).

### Core Domains
- **Signal Engine**: Standardizes trading intent via `SignalPayload`.
- **Runtime Engine**: Manages state (`PortfolioContext`) and orchestration via `Runner`.
- **Connectors**: External interfaces for REST APIs (`QuantSignalClient`) and Exchanges (`CCXTClient`).

---

## 2. Lifecycle & Core Flow: The Journey of a Signal
The most important entity in this system is the **`SignalPayload`**. Its lifecycle follows a strict path to ensure reliability:

1.  **Ingestion**: `BaseFeed` (e.g., `OhlcvReplayFeed`) streams `MarketEvent` objects.
2.  **Transformation (Decision)**: `BaseStrategy.on_event` receives the event and a snapshot of the `PortfolioContext`. It emits one or more `SignalPayload` objects.
3.  **Boundary Guard (Validation)**: 
    - `SignalTranslator` validates timeframe integrity (checks for data gaps).
    - `RiskManager` (optional) calculates SL/TP and validates position sizing.
4.  **Egress (Dispatch)**: 
    - **Backtest**: Handled by `PortfolioBacktestRunner` which matches orders against subsequent candles.
    - **Live**: `QuantSignalClient` signs the payload (HMAC-SHA256) and sends it via REST to the backend.

## 2.5 Dual Lifecycle Publishing

The SDK now treats bot history as two separate pipelines:

1.  **Historical backtest**: `PortfolioBacktestRunner` produces a `BacktestReport`, and `BacktestUploadClient` can upload it to `/api/v1/bots/{botId}/backtest-results`.
2.  **Live dry-run**: `Runner` delegates state persistence and sync to a pluggable `StateSyncer` implementation. `HttpDryRunSyncer` is the default REST transport, but `WebSocketDryRunSyncer` and `FileSyncer` are also available.
3.  **Operational telemetry**: `TelemetryClient` is reserved for non-PnL metrics such as latency, CPU, and heartbeats.

This split keeps transport concerns out of the core `Runner` loop and lets the backend merge historical and out-of-sample data later.

---

## 3. In/Out Boundaries
- **Input Protocols**:
    - **Live**: Typically WebSocket or REST (via `BaseFeed` implementations).
    - **Backtest**: CSV/DataFrame via `OhlcvReplayFeed`.
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
- **Network Library**: The `NetworkClient` is tightly coupled to the `requests` library. 
    - *Status*: Acceptable for now, but a protocol-based abstraction is already in place to allow swapping for `httpx` or `aiohttp` in the future.
- **CCXT Dependency**: CCXT is handled as an optional "extra", which is a good design choice to keep the core SDK lightweight.

---

## 6. Module Responsibility Map
| Module | Responsibility |
| :--- | :--- |
| `models.py` | Defines the "Contract" (Schemas & Enums). |
| `translator.py` | Data integrity gatekeeper and payload serializer. |
| `runner.py` | The main loop orchestrating Feed -> Strategy -> Dispatcher. |
| `backtest.py` | High-fidelity local exchange simulation. |
| `backtest_upload.py` | Batch upload of `BacktestReport` to backend historical storage. |
| `sync.py` | Dry-run state tracking and pluggable sync transports. |
| `telemetry.py` | Operational telemetry client, separate from dry-run PnL/state sync. |
| `client.py` | Secure network communication (Signing & POST). |
| `ccxt_client.py` | Optional market data adapter. |
