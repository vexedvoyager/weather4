"""
Shared setup used by both src/forecast_refresh.py and src/price_check.py,
so the two-tier scan design doesn't duplicate client/logging setup.
"""
import logging
import os

from src.kalshi_client import KalshiClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def build_client() -> KalshiClient:
    key_id = os.environ.get("KALSHI_KEY_ID")
    private_key_pem = os.environ.get("KALSHI_PRIVATE_KEY")
    if not key_id or not private_key_pem:
        raise RuntimeError(
            "KALSHI_KEY_ID and KALSHI_PRIVATE_KEY must be set as environment "
            "variables (GitHub Actions secrets in production). Market-data "
            "reads require these even in paper mode; orders are only ever "
            "placed in live mode."
        )
    return KalshiClient(key_id, private_key_pem)
