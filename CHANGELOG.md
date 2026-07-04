# Changelog

## [0.1.1] - 2026-06-01

### Added
- GitHub Actions trusted publishing workflow for automatic PyPI uploads on GitHub Release publish events.
- README release instructions for configuring PyPI trusted publisher setup.

## [0.1.2] - 2026-06-04

### Added
- `BacktestUploadClient` support for uploading `BacktestReport` batches to `POST /api/v1/bots/{botId}/backtest-results`.
- Pluggable `StateSyncer` implementations for live dry-run sync, including `HttpDryRunSyncer`, `WebSocketDryRunSyncer`, and `FileSyncer`.
- Dedicated `TelemetryClient` for operational metrics, separate from dry-run PnL/state sync.

### Changed
- `Runner` now delegates dry-run persistence and sync through the state sync layer instead of owning transport logic directly.
- SDK lifecycle docs now describe the full flow as historical backtest -> live dry-run -> operational telemetry.

### Fixed
- Relaxed `pandas`, `numpy`, and `ccxt` dependency constraints in `pyproject.toml` to support newer Python versions (such as Python 3.12) out of the box.

## [Unreleased]

### Changed
- Prepared the SDK for `0.2.0` by slimming the root public API, moving heavy dependencies into extras, and centralizing HTTP auth helpers.
- Updated dry-run REST wiring to prefer `create_dry_run_syncer(...)`, which returns the state syncer and runner callback together.

## [0.1.0] - 2026-04-24

### Added
- Optional `market-data` extra for `ccxt`, with runtime guard and install instructions.
- `QuantSignalClient` support for bot registration and bot-key payload submission.
- SDK README install guidance and build/test commands.

### Fixed
- Pyright typing issues for `NetworkClient` protocol and test mocks.
- `SignalPayload` symbol normalization and validation behavior.
- CI workflow Python version matrix quoting.

### Tested
- `PYTHONPATH=src python -m pytest -q` is the source-tree test command.
