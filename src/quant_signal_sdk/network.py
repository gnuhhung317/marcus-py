from __future__ import annotations

from typing import Any, Protocol

import requests
from requests import Response, Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from .signing import canonical_json_bytes

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

    def post_bytes(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: bytes,
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

    def post_bytes(
        self,
        *,
        url: str,
        headers: dict[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> Response:
        return self._session.post(
            url,
            headers=headers,
            data=body,
            timeout=timeout_seconds,
        )

    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        json_body: dict[str, Any],
        timeout_seconds: float,
    ) -> Response:
        body = canonical_json_bytes(json_body)
        return self.post_bytes(
            url=url,
            headers=headers,
            body=body,
            timeout_seconds=timeout_seconds,
        )
