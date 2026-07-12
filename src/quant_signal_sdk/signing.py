from __future__ import annotations

import gzip
import hashlib
import hmac
import json
from typing import Any, Mapping

def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

def sign_bytes(body: bytes, secret: str, timestamp: str | None = None) -> str:
    message = body if timestamp is None else timestamp.encode("utf-8") + b"\n" + body
    key = secret.encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()

def generate_hmac_signature(payload: Mapping[str, Any], secret: str, timestamp: str | None = None) -> str:
    body = canonical_json_bytes(payload)
    return sign_bytes(body, secret, timestamp)

def generate_hmac_signature_bytes(payload: bytes, secret: str, timestamp: str | None = None) -> str:
    return sign_bytes(payload, secret, timestamp)

def gzip_bytes(payload_bytes: bytes, *, compresslevel: int = 6) -> bytes:
    return gzip.compress(payload_bytes, compresslevel=compresslevel)
