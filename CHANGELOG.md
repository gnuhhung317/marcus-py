# Changelog

## [0.1.1] - 2026-06-01

### Added
- GitHub Actions trusted publishing workflow for automatic PyPI uploads on GitHub Release publish events.
- README release instructions for configuring PyPI trusted publisher setup.

## [Unreleased]

### Added
- `BacktestUploadClient` support for uploading `BacktestReport` batches to `POST /api/v1/bots/{botId}/backtest-results`.
- Pluggable `StateSyncer` implementations for live dry-run sync, including `HttpDryRunSyncer`, `WebSocketDryRunSyncer`, and `FileSyncer`.
- Dedicated `TelemetryClient` for operational metrics, separate from dry-run PnL/state sync.

### Changed
- `Runner` now delegates dry-run persistence and sync through the state sync layer instead of owning transport logic directly.
- SDK lifecycle docs now describe the full flow as historical backtest -> live dry-run -> operational telemetry.

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
- `python -m unittest discover -s tests -v` passed successfully.
