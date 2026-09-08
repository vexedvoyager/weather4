from unittest.mock import MagicMock, patch

from src.kalshi_client import KalshiClient


def _make_client():
    # Minimal RSA key so KalshiAuth can initialize without hitting the network.
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    return KalshiClient("fake-key-id", pem)


def test_429_triggers_retry_then_succeeds():
    client = _make_client()

    rate_limited_resp = MagicMock(status_code=429)
    success_resp = MagicMock(status_code=200)
    success_resp.raise_for_status = MagicMock()
    success_resp.json.return_value = {"markets": []}

    with patch("src.kalshi_client.requests.request", side_effect=[rate_limited_resp, success_resp]) as mock_req, \
         patch("src.kalshi_client.time.sleep") as mock_sleep:
        result = client._request("GET", "/markets")

    assert result == {"markets": []}
    assert mock_req.call_count == 2
    mock_sleep.assert_called_once()


def test_429_exhausts_retries_and_raises():
    client = _make_client()

    rate_limited_resp = MagicMock(status_code=429)
    rate_limited_resp.raise_for_status.side_effect = Exception("429 Too Many Requests")

    with patch("src.kalshi_client.requests.request", return_value=rate_limited_resp) as mock_req, \
         patch("src.kalshi_client.time.sleep"):
        try:
            client._request("GET", "/markets")
            assert False, "should have raised after exhausting retries"
        except Exception as e:
            assert "429" in str(e)

    from src.kalshi_client import MAX_RETRIES
    assert mock_req.call_count == MAX_RETRIES + 1


def test_non_429_error_does_not_retry():
    client = _make_client()

    error_resp = MagicMock(status_code=500)
    error_resp.raise_for_status.side_effect = Exception("500 Server Error")

    with patch("src.kalshi_client.requests.request", return_value=error_resp) as mock_req:
        try:
            client._request("GET", "/markets")
            assert False, "should have raised immediately"
        except Exception:
            pass

    assert mock_req.call_count == 1  # no retry loop for non-429 errors


def test_success_on_first_try_does_not_sleep():
    client = _make_client()

    success_resp = MagicMock(status_code=200)
    success_resp.raise_for_status = MagicMock()
    success_resp.json.return_value = {"ok": True}

    with patch("src.kalshi_client.requests.request", return_value=success_resp), \
         patch("src.kalshi_client.time.sleep") as mock_sleep:
        result = client._request("GET", "/markets")

    assert result == {"ok": True}
    mock_sleep.assert_not_called()


def test_get_markets_by_series_defaults_to_open_status_not_active():
    """
    Regression test for a real, confirmed bug: status="active" caused a
    uniform 400 Bad Request across every city in a live Forecast Refresh
    run. Kalshi's own documented example uses status="open" - confirmed
    correct, and this test locks in that default so it can't silently
    drift back to the wrong value.
    """
    client = _make_client()
    success_resp = MagicMock(status_code=200)
    success_resp.raise_for_status = MagicMock()
    success_resp.json.return_value = {"markets": []}

    with patch("src.kalshi_client.requests.request", return_value=success_resp) as mock_req:
        client.get_markets_by_series("KXHIGHCHI")

    _, kwargs = mock_req.call_args
    assert kwargs["params"]["status"] == "open"
    assert kwargs["params"]["status"] != "active"
