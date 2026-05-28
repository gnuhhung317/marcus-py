from datetime import datetime, timezone
import time
import threading
from uuid import uuid4
from typing import Any, Mapping

from .models import SignalPayload
from .network import NetworkClient, NetworkClientProtocol
from .signing import generate_hmac_signature


class QuantSignalClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        default_bot_id: str | None = None,
        endpoint_path: str = "/api/v1/signals",
        timeout_seconds: float = 10.0,
        signer_secret: str | None = None,
        signature_header: str = "X-Signature",
        network_client: NetworkClientProtocol | None = None,
        bot_api_key_header: str = "X-Bot-Api-Key",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._endpoint_path = endpoint_path if endpoint_path.startswith("/") else f"/{endpoint_path}"
        self._timeout_seconds = timeout_seconds
        self._api_key = api_key
        self._default_bot_id = default_bot_id
        self._signer_secret = signer_secret
        self._signature_header = signature_header
        self._bot_api_key_header = bot_api_key_header
        self._network_client = network_client or NetworkClient()

    def set_signer_secret(self, secret: str | None) -> None:
        """Set or clear the signer secret used for HMAC signing of requests.

        Prefer this public API over mutating the private attribute `_signer_secret`.
        """
        self._signer_secret = secret

    def get_signer_secret(self) -> str | None:
        """Return the currently configured signer secret, if any.

        This accessor is primarily useful for tests and for callers that need
        to confirm the client will sign requests.
        """
        return self._signer_secret

    def set_default_bot_id(self, bot_id: str | None) -> None:
        """Set or clear the default bot id used when a payload omits botId."""
        self._default_bot_id = bot_id

    def get_default_bot_id(self) -> str | None:
        """Return the client-context bot id, if one has been configured."""
        return self._default_bot_id

    def send_signal(self, signal: SignalPayload) -> dict[str, Any]:
        if signal.signal_id is None or not str(signal.signal_id).strip():
            signal.signal_id = str(uuid4())

        if signal.bot_id is None or not str(signal.bot_id).strip():
            if self._default_bot_id is None or not self._default_bot_id.strip():
                raise ValueError("bot_id is required or a default_bot_id must be configured")
            signal.bot_id = self._default_bot_id

        payload = signal.model_dump(mode="json", by_alias=True, exclude_none=True)
        return self.send_payload(payload)

    def send_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        json_payload = dict(payload)
        if "signalId" not in json_payload and "signal_id" not in json_payload:
            json_payload["signalId"] = str(uuid4())
        elif "signal_id" in json_payload and "signalId" not in json_payload:
            json_payload["signalId"] = json_payload.pop("signal_id")

        if "botId" not in json_payload and "bot_id" not in json_payload:
            if self._default_bot_id:
                json_payload["botId"] = self._default_bot_id
        elif "bot_id" in json_payload and "botId" not in json_payload:
            json_payload["botId"] = json_payload.pop("bot_id")

        if "generatedTimestamp" not in json_payload and "generated_timestamp" not in json_payload:
            json_payload["generatedTimestamp"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        elif "generated_timestamp" in json_payload and "generatedTimestamp" not in json_payload:
            ts_val = json_payload.pop("generated_timestamp")
            if isinstance(ts_val, datetime):
                json_payload["generatedTimestamp"] = ts_val.isoformat().replace("+00:00", "Z")
            else:
                json_payload["generatedTimestamp"] = str(ts_val)

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
        timestamp = str(int(time.time() * 1000))
        headers = {
            "Content-Type": "application/json",
            self._bot_api_key_header: self._api_key,
            "X-Timestamp": timestamp,
        }
        if self._signer_secret:
            headers[self._signature_header] = generate_hmac_signature(payload, self._signer_secret, timestamp=timestamp)
        return headers

    def send_payload_with_bot_key(self, payload: Mapping[str, Any], bot_api_key: str, timeout_seconds: float | None = None) -> dict[str, Any]:
        json_payload = dict(payload)
        if "signalId" not in json_payload and "signal_id" not in json_payload:
            json_payload["signalId"] = str(uuid4())
        elif "signal_id" in json_payload and "signalId" not in json_payload:
            json_payload["signalId"] = json_payload.pop("signal_id")

        if "botId" not in json_payload and "bot_id" not in json_payload:
            if self._default_bot_id:
                json_payload["botId"] = self._default_bot_id
        elif "bot_id" in json_payload and "botId" not in json_payload:
            json_payload["botId"] = json_payload.pop("bot_id")

        if "generatedTimestamp" not in json_payload and "generated_timestamp" not in json_payload:
            json_payload["generatedTimestamp"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        elif "generated_timestamp" in json_payload and "generatedTimestamp" not in json_payload:
            ts_val = json_payload.pop("generated_timestamp")
            if isinstance(ts_val, datetime):
                json_payload["generatedTimestamp"] = ts_val.isoformat().replace("+00:00", "Z")
            else:
                json_payload["generatedTimestamp"] = str(ts_val)

        timestamp = str(int(time.time() * 1000))
        headers = {
            "Content-Type": "application/json",
            self._bot_api_key_header: bot_api_key,
            "X-Timestamp": timestamp,
        }
        if self._signer_secret:
            headers[self._signature_header] = generate_hmac_signature(json_payload, self._signer_secret, timestamp=timestamp)
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

    def send_heartbeat(self, bot_id: str | None = None, bot_api_key: str | None = None) -> dict[str, Any]:
        """Send a heartbeat to the server for a specific bot."""
        target_bot_id = bot_id or self._default_bot_id
        if not target_bot_id:
            raise ValueError("bot_id is required or a default_bot_id must be configured")

        target_api_key = bot_api_key or self._api_key
        if not target_api_key:
            raise ValueError("bot_api_key is required or an api_key must be configured")

        url = f"{self._base_url}/api/v1/bots/{target_bot_id}/heartbeat"
        
        timestamp = str(int(time.time() * 1000))
        headers = {
            "Content-Type": "application/json",
            self._bot_api_key_header: target_api_key,
            "X-Timestamp": timestamp,
        }

        if self._signer_secret:
            headers[self._signature_header] = generate_hmac_signature({}, self._signer_secret, timestamp=timestamp)

        response = self._network_client.post_json(
            url=url,
            headers=headers,
            json_body={},
            timeout_seconds=self._timeout_seconds,
        )
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

    def start_heartbeat_loop(self, interval_seconds: float = 300.0, bot_id: str | None = None, bot_api_key: str | None = None) -> None:
        """Start a background daemon thread that periodically sends heartbeats to the server."""
        target_bot_id = bot_id or self._default_bot_id
        if not target_bot_id:
            raise ValueError("bot_id is required or a default_bot_id must be configured")

        target_api_key = bot_api_key or self._api_key
        if not target_api_key:
            raise ValueError("bot_api_key is required or an api_key must be configured")

        if hasattr(self, "_heartbeat_stop_event") and self._heartbeat_stop_event and not self._heartbeat_stop_event.is_set():
            return

        self._heartbeat_stop_event = threading.Event()

        def _loop():
            try:
                self.send_heartbeat(bot_id=target_bot_id, bot_api_key=target_api_key)
            except Exception as e:
                import sys
                print(f"Error sending heartbeat: {e}", file=sys.stderr)

            while not self._heartbeat_stop_event.wait(interval_seconds):
                try:
                    self.send_heartbeat(bot_id=target_bot_id, bot_api_key=target_api_key)
                except Exception as e:
                    import sys
                    print(f"Error sending heartbeat: {e}", file=sys.stderr)

        self._heartbeat_thread = threading.Thread(target=_loop, daemon=True)
        self._heartbeat_thread.start()

    def stop_heartbeat_loop(self) -> None:
        """Stop the background heartbeat thread if it is running."""
        if hasattr(self, "_heartbeat_stop_event") and self._heartbeat_stop_event:
            self._heartbeat_stop_event.set()
        if hasattr(self, "_heartbeat_thread") and self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=5.0)
