# Changelog

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
