"""
Kalshi API authentication: RSA-PSS request signing.

Kalshi requires each request to be signed with your private key. The
signature covers: timestamp + HTTP method + request path.

Your API key ID and private key come from your Kalshi account settings
(Settings > API Keys). NEVER commit your private key to git. It is read
from environment variables / GitHub Actions secrets — see README.md.
"""
import base64
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


class KalshiAuth:
    def __init__(self, key_id: str, private_key_pem: str):
        self.key_id = key_id
        self._private_key = serialization.load_pem_private_key(
            private_key_pem.encode() if isinstance(private_key_pem, str) else private_key_pem,
            password=None,
        )

    def sign_request(self, method: str, path: str) -> dict:
        """
        Returns the headers Kalshi requires on every authenticated request.
        `path` should be the request path only (e.g. "/trade-api/v2/portfolio/positions"),
        not the full URL.
        """
        timestamp_ms = str(int(time.time() * 1000))
        message = f"{timestamp_ms}{method.upper()}{path}".encode()

        signature = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        signature_b64 = base64.b64encode(signature).decode()

        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-SIGNATURE": signature_b64,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        }
