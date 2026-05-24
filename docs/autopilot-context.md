# Autopilot (SDK-only) — Context & Implementation Notes

Mục tiêu: thu thập thông tin cần thiết để hiện thực một "autopilot" trong SDK — chỉ dành cho việc phát sinh và gửi signal lên backend; không triển khai execution hoặc order placement.

1) Phạm vi
- SDK-only: module chạy trong bot / demo scripts, chịu trách nhiệm tạo và gửi `Signal` tới backend.
- Không bao gồm: executor, matching engine, hoặc bất kỳ thành phần đặt lệnh nào.

2) Giả định nền tảng
- Backend chấp nhận payload theo `signal-contract-v2.json` (file trong `signal-core-backend/docs`).
- SDK hiện có: `QuantSignalClient`, `SignalPayload` (Pydantic), `generate_hmac_signature`, `NetworkClient`.

3) Input / Output
- Input:
  - `market_data_provider() -> Mapping[str, Any]` (tick/ohlcv snapshot).
  - `strategy.on_market_data(tick)` trả về:
    - `SignalPayload` (Pydantic) hoặc
    - `Mapping` phù hợp với `signal-contract-v2`.
- Output:
  - HTTP POST tới `POST /api/v1/signals` với header `X-Bot-Api-Key`, `X-Timestamp` (khi dùng `send_payload_with_bot_key`) và `X-Signature` (khi signer secret cấu hình).

4) Yêu cầu dữ liệu (mapping giữa SDK hiện tại và contract v2)
- Bắt buộc: `signalId`, `botId`, `symbol`, `action`, `marketType`, `orderType`, `generatedTimestamp`.
- SDK `SignalPayload` hiện dùng fields: `side`, `action`, `symbol`, `tp`, `sl`, `confidence_score`, `timestamp`, `metadata`.
- Hành động cần thực hiện trước khi triển khai: đưa `SignalPayload` map rõ ràng sang contract v2 (đổi/hoán tên field), hoặc cho phép strategy trả raw dict theo contract.

5) Reliability & Error Handling
- Retries: client hiện raise_for_status(); tests should assert idempotency behavior.
- Logging: autopilot should log exceptions and continue loop (no crash).

6) Tests / Acceptance Criteria
- Unit tests: simulate `market_data_provider` with deterministic ticks and a strategy that emits one signal; assert `QuantSignalClient.post_json` called with payload matching `signal-contract-v2` (or `SignalPayload` serialized equivalently).
- Integration smoke: optional local test posting to a dev backend endpoint (dev only).

7) Security / Signing
- Use `generate_hmac_signature` when `signer_secret` configured.
- Ensure canonicalization (sorted keys) used if backend expects canonical JSON for signature.

8) Next steps (implementation checklist)
- Decide mapping strategy: extend `SignalPayload` or require strategies to return contract dicts.
- Add small function-runner `autopilot.start(...)` (function-based) in `src/quant_signal_sdk/autopilot.py` (implementation later).
- Add unit tests under `bot-framework-python/tests/test_autopilot.py`.

Contact/Notes:
- Người yêu cầu: developer muốn "autopilot" nghĩa là tự động gửi signal (không phải hệ thống execution).
