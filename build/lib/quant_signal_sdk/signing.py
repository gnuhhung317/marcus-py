from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Mapping


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def generate_hmac_signature(payload: Mapping[str, Any], secret: str, timestamp: str | None = None) -> str:
    message_text = _canonical_json(payload)
    if timestamp is not None:
        message_text = f"{timestamp}\n{message_text}"
    message = message_text.encode("utf-8")
    key = secret.encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()
