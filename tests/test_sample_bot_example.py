from __future__ import annotations

import unittest

from quant_signal_sdk.client import QuantSignalClient


class SampleBotExampleTest(unittest.TestCase):
    def test_register_returned_raw_secret_attaches_to_client_when_none(self) -> None:
        # Simulate a client created without a signer_secret
        client = QuantSignalClient(base_url="http://localhost:8080", api_key="", signer_secret=None)

        # Simulate server response containing rawSecret but no apiKey
        returned_raw_secret = "sk_example_123"

        # Example logic: attach returned_raw_secret when client has no signer_secret
        if returned_raw_secret and not client.get_signer_secret():
            client.set_signer_secret(returned_raw_secret)

        self.assertEqual(client.get_signer_secret(), returned_raw_secret)


if __name__ == "__main__":
    unittest.main()
