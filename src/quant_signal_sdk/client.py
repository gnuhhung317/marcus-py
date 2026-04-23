from __future__ import annotations

from typing import Any, Mapping

from .models import SignalPayload
from .network import NetworkClient
from .signing import generate_hmac_signature


class QuantSignalClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        endpoint_path: str = "/api/v1/signals",
        timeout_seconds: float = 10.0,
        signer_secret: str | None = None,
        signature_header: str = "X-Signature",
        api_key_header: str = "X-API-Key",
        network_client: NetworkClient | None = None,
        bot_api_key_header: str = "X-Bot-Api-Key",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._endpoint_path = endpoint_path if endpoint_path.startswith("/") else f"/{endpoint_path}"
        self._timeout_seconds = timeout_seconds
        self._api_key = api_key
        self._api_key_header = api_key_header
        self._signer_secret = signer_secret
        self._signature_header = signature_header
        self._bot_api_key_header = bot_api_key_header
        self._network_client = network_client or NetworkClient()

    def send_signal(self, signal: SignalPayload) -> dict[str, Any]:
        payload = signal.model_dump(mode="json", exclude_none=True)
        return self.send_payload(payload)

    def send_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        json_payload = dict(payload)
        headers = self._build_headers(json_payload)
        response = self._network_client.post_json(
            url=self._build_url(),
            headers=headers,
            json_body=json_payload,
            timeout_seconds=self._timeout_seconds,
        )
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

    def _build_url(self) -> str:
        return f"{self._base_url}{self._endpoint_path}"

    def _build_headers(self, payload: Mapping[str, Any]) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            self._api_key_header: self._api_key,
        }
        if self._signer_secret:
            headers[self._signature_header] = generate_hmac_signature(payload, self._signer_secret)
        return headers

    def send_payload_with_bot_key(self, payload: Mapping[str, Any], bot_api_key: str, timeout_seconds: float | None = None) -> dict[str, Any]:
        json_payload = dict(payload)
        headers = {
            "Content-Type": "application/json",
            self._bot_api_key_header: bot_api_key,
        }
        if self._signer_secret:
            headers[self._signature_header] = generate_hmac_signature(json_payload, self._signer_secret)
        response = self._network_client.post_json(
            url=self._build_url(),
            headers=headers,
            json_body=json_payload,
            timeout_seconds=timeout_seconds or self._timeout_seconds,
        )
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

    def register_bot(self, bot_payload: Mapping[str, Any], auth_token: str | None = None, bot_api_key: str | None = None) -> dict[str, Any]:
        """Register a bot via POST /api/v1/bots. Sends either Authorization or bot API key header when provided."""
        json_payload = dict(bot_payload)
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        if bot_api_key:
            headers[self._bot_api_key_header] = bot_api_key

        response = self._network_client.post_json(
            url=f"{self._base_url}/api/v1/bots",
            headers=headers,
            json_body=json_payload,
            timeout_seconds=self._timeout_seconds,
        )
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()
