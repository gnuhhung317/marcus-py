from __future__ import annotations

import json
from typing import Any, Protocol

import requests
from requests import Response, Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

RETRY_STATUS_CODES = (500, 502, 503, 504)


class NetworkClientProtocol(Protocol):
    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        json_body: dict[str, Any],
        timeout_seconds: float,
    ) -> Any:
        ...


class NetworkClient:
    def __init__(
        self,
        *,
        retries: int = 3,
        backoff_factor: float = 0.5,
        session: Session | None = None,
    ) -> None:
        self._session = session or requests.Session()
        retry_policy = Retry(
            total=retries,
            read=retries,
            connect=retries,
            backoff_factor=backoff_factor,
            status_forcelist=RETRY_STATUS_CODES,
            allowed_methods=frozenset(["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_policy)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        json_body: dict[str, Any],
        timeout_seconds: float,
    ) -> Response:
        canonical_body = json.dumps(json_body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return self._session.post(
            url,
            headers=headers,
            data=canonical_body,
            timeout=timeout_seconds,
        )
