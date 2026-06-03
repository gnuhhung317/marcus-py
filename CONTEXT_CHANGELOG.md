# CONTEXT CHANGELOG — bot-framework-python

> Track all significant context changes. Newest entries at top.
> Rules: See [CONTEXT_RULES.md](../CONTEXT_RULES.md)

---

<!-- Entry template:
## [YYYY-MM-DD] <short-title>
**Agent**: <agent-name>
**Type**: feature | fix | architecture | contract | gotcha
**What Changed**: <one-line summary>
**Why**: <reason>
**Impact**: <affected services/files>
**Action Required**: <migration/awareness>
-->

## [2026-06-03] Dual-Pipeline Bot Lifecycle Docs
**Agent**: codex
**Type**: architecture
**What Changed**: Documented backtest upload, pluggable dry-run sync, and separate telemetry handling in the SDK context docs.
**Why**: The SDK now supports uploading historical backtests and syncing live paper-trading state independently.
**Impact**: bot-framework-python CONTEXT.md, changelog, and runtime docs.
**Action Required**: Use `BacktestUploadClient` for batch uploads and `StateSyncer` for live sync; keep telemetry separate.

## [2026-06-02] Context Map System Created
**Agent**: system-setup
**Type**: architecture
**What Changed**: Created L1 CONTEXT.md for bot-framework-python as part of layered Context Map system
**Why**: Enable consistent agent onboarding and context preservation across sessions
**Impact**: No code changes — documentation only
**Action Required**: None — future changes should append entries here
