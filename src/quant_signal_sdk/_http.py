from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from .signing import canonical_json_bytes, sign_bytes, generate_hmac_signature, generate_hmac_signature_bytes

def timestamp_ms() -> str:
    return str(int(time.time() * 1000))

def canonical_json_text(payload: Mapping[str, Any]) -> str:
    return canonical_json_bytes(payload).decode("utf-8")

def build_auth_headers(
    *,
    api_key: str,
    payload: Mapping[str, Any] | None = None,
    body: bytes | None = None,
    signer_secret: str | None = None,
    timestamp: str | None = None,
    bot_api_key_header: str = "X-Bot-Api-Key",
    signature_header: str = "X-Signature",
    content_type: str = "application/json",
    content_encoding: str | None = None,
    extra_headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    request_timestamp = timestamp or timestamp_ms()
    headers = {
        "Content-Type": content_type,
        bot_api_key_header: api_key,
        "X-Timestamp": request_timestamp,
    }
    if content_encoding:
        headers["Content-Encoding"] = content_encoding
    if extra_headers:
        headers.update(dict(extra_headers))

    if signer_secret:
        if body is not None:
            headers[signature_header] = sign_bytes(body, signer_secret, timestamp=request_timestamp)
        else:
            body_bytes = canonical_json_bytes(payload or {})
            headers[signature_header] = sign_bytes(body_bytes, signer_secret, timestamp=request_timestamp)
    return headers

def response_json_or_empty(response: Any) -> dict[str, Any]:
    response.raise_for_status()
    if not getattr(response, "content", None):
        return {}
    return response.json()
