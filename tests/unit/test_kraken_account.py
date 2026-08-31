"""Kraken **Account**-Adapter (READ-ONLY) — Signierer, Secrets, private REST, Sicherheits-Assertion.

Kein Netz: HMAC-Signierung gegen Krakens **offiziellen Testvektor**, alle REST-Calls via respx.
Kein Order-Pfad — der Adapter hat kein ``submit``/``cancel``.
"""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from trading_agent.core.enums import ProviderHealth
from trading_agent.data.providers.kraken_account import (
    KrakenAccountAdapter,
    KrakenAccountError,
    KrakenAPIError,
    KrakenAuthError,
    KrakenPrivateClient,
    sign_request,
)
from trading_agent.security.secrets import Secret, get_secret, missing_secrets, redact

_TEST_SECRET = base64.b64encode(b"kraken-test-secret-bytes-0123456789").decode()


# --------------------------------------------------------------------------- signer


def test_sign_request_matches_kraken_official_vector() -> None:
    data = {
        "nonce": 1616492376594,
        "ordertype": "limit",
        "pair": "XBTUSD",
        "price": 37500,
        "type": "buy",
        "volume": 1.25,
    }
    secret = (
        "kQH5HW/8p1uGOVjbgWA7FunAmGO8lsSUXNsu3eow76sz84Q18fWxnyRzBHCd3pd5nE9qa99HAZtuZuj6F1huXg=="
    )
    sig = sign_request("/0/private/AddOrder", data, secret)
    assert sig == (
        "4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ=="
    )


# --------------------------------------------------------------------------- secrets


def test_secret_repr_is_redacted() -> None:
    s = Secret("super-secret-value", name="KRAKEN_API_KEY")
    assert "super-secret-value" not in repr(s)
    assert "super-secret-value" not in str(s)
    assert "redacted" in repr(s)
    assert s.reveal() == "super-secret-value"
    assert s.present and bool(s)


def test_get_secret_env_and_missing(monkeypatch) -> None:
    monkeypatch.setenv("KRAKEN_API_KEY", "abc")
    monkeypatch.delenv("KRAKEN_API_SECRET", raising=False)
    assert get_secret("KRAKEN_API_KEY", allow_keychain=False).reveal() == "abc"
    assert not get_secret("KRAKEN_API_SECRET", allow_keychain=False).present
    assert missing_secrets(("KRAKEN_API_KEY", "KRAKEN_API_SECRET"), allow_keychain=False) == [
        "KRAKEN_API_SECRET"
    ]


def test_redact_strips_known_secrets() -> None:
    msg = "call failed with key=abc123 and sign=xyz789"
    out = redact(msg, "abc123", Secret("xyz789"))
    assert "abc123" not in out and "xyz789" not in out


# --------------------------------------------------------------------------- adapter contract


def test_adapter_unavailable_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("KRAKEN_API_KEY", raising=False)
    monkeypatch.delenv("KRAKEN_API_SECRET", raising=False)
    a = KrakenAccountAdapter(allow_keychain=False)
    st = a.status()
    assert st.health is ProviderHealth.UNAVAILABLE
    assert "KRAKEN_API_KEY" in st.detail and "KRAKEN_API_SECRET" in st.detail
    assert a.info.credentials.read_only is True
    assert a.missing_credentials() == ["KRAKEN_API_KEY", "KRAKEN_API_SECRET"]


def test_adapter_has_no_order_methods() -> None:
    from trading_agent.execution.brokers.base import BrokerAdapter

    a = KrakenAccountAdapter(allow_keychain=False)
    assert not isinstance(a, BrokerAdapter)
    assert not hasattr(a, "submit")
    assert not hasattr(a, "cancel")


async def test_call_without_creds_raises_auth_error() -> None:
    a = KrakenAccountAdapter(allow_keychain=False)
    with pytest.raises(KrakenAuthError):
        await a.get_balances()
    await a.aclose()


# --------------------------------------------------------------------------- private REST (respx)


def _creds(monkeypatch) -> None:
    monkeypatch.setenv("KRAKEN_API_KEY", "PUBLICKEY")
    monkeypatch.setenv("KRAKEN_API_SECRET", _TEST_SECRET)


@respx.mock
async def test_private_client_signs_and_sends_headers(monkeypatch) -> None:
    _creds(monkeypatch)
    route = respx.post("https://api.kraken.com/0/private/Balance").mock(
        return_value=httpx.Response(200, json={"error": [], "result": {"ZEUR": "100.0"}})
    )
    a = KrakenAccountAdapter(allow_keychain=False)
    bal = await a.get_balances()
    await a.aclose()
    assert bal == {"ZEUR": 100.0}
    req = route.calls.last.request
    assert req.headers["API-Key"] == "PUBLICKEY"
    assert req.headers["API-Sign"]  # nicht leer
    assert b"nonce=" in req.content
    # Secret darf NIE im Request-Body oder in Headern stehen
    assert _TEST_SECRET not in req.content.decode()
    assert _TEST_SECRET not in dict(req.headers).values()


@respx.mock
async def test_trade_balance_and_open_orders(monkeypatch) -> None:
    _creds(monkeypatch)
    respx.post("https://api.kraken.com/0/private/Balance").mock(
        return_value=httpx.Response(
            200, json={"error": [], "result": {"ZEUR": "250.5", "XXBT": "0.0"}}
        )
    )
    respx.post("https://api.kraken.com/0/private/TradeBalance").mock(
        return_value=httpx.Response(
            200, json={"error": [], "result": {"e": "260.0", "tb": "250.5", "mf": "260.0"}}
        )
    )
    respx.post("https://api.kraken.com/0/private/OpenOrders").mock(
        return_value=httpx.Response(200, json={"error": [], "result": {"open": {}}})
    )
    respx.post("https://api.kraken.com/0/private/OpenPositions").mock(
        return_value=httpx.Response(200, json={"error": [], "result": {}})
    )
    a = KrakenAccountAdapter(allow_keychain=False)
    summary = await a.get_trade_balance()
    oo = await a.get_open_orders()
    op = await a.get_open_positions()
    await a.aclose()
    assert summary.equity == 260.0 and summary.balance == 250.5 and summary.currency == "EUR"
    assert summary.nonzero_balances == {"ZEUR": 250.5}
    assert oo["count"] == 0 and op["count"] == 0
    assert a.status().health is not ProviderHealth.UNAVAILABLE


@respx.mock
async def test_invalid_key_maps_to_auth_error(monkeypatch) -> None:
    _creds(monkeypatch)
    respx.post("https://api.kraken.com/0/private/Balance").mock(
        return_value=httpx.Response(200, json={"error": ["EAPI:Invalid key"], "result": {}})
    )
    a = KrakenAccountAdapter(allow_keychain=False)
    with pytest.raises(KrakenAuthError):
        await a.get_balances()
    await a.aclose()
    assert a.status().health in (ProviderHealth.DEGRADED, ProviderHealth.UNAVAILABLE)


@respx.mock
async def test_generic_api_error(monkeypatch) -> None:
    _creds(monkeypatch)
    respx.post("https://api.kraken.com/0/private/Balance").mock(
        return_value=httpx.Response(
            200, json={"error": ["EOrder:Rate limit exceeded"], "result": {}}
        )
    )
    a = KrakenAccountAdapter(allow_keychain=False)
    with pytest.raises(KrakenAPIError):
        await a.get_balances()
    await a.aclose()


@respx.mock
async def test_invalid_nonce_retries_with_new_nonce(monkeypatch) -> None:
    _creds(monkeypatch)
    responses = [
        httpx.Response(200, json={"error": ["EAPI:Invalid nonce"], "result": {}}),
        httpx.Response(200, json={"error": [], "result": {"ZEUR": "1.0"}}),
    ]
    respx.post("https://api.kraken.com/0/private/Balance").mock(side_effect=responses)
    a = KrakenAccountAdapter(allow_keychain=False)
    bal = await a.get_balances()
    await a.aclose()
    assert bal == {"ZEUR": 1.0}


# --------------------------------------------------------------------------- read-only assertion


@respx.mock
async def test_assert_read_only_confirmed_on_permission_denied(monkeypatch) -> None:
    _creds(monkeypatch)
    respx.post("https://api.kraken.com/0/private/AddOrder").mock(
        return_value=httpx.Response(
            200, json={"error": ["EGeneral:Permission denied"], "result": {}}
        )
    )
    a = KrakenAccountAdapter(allow_keychain=False)
    proof = await a.assert_read_only()
    await a.aclose()
    assert proof.confirmed is True
    assert "Permission denied" in proof.detail


@respx.mock
async def test_assert_read_only_raises_if_key_can_trade(monkeypatch) -> None:
    _creds(monkeypatch)
    respx.post("https://api.kraken.com/0/private/AddOrder").mock(
        return_value=httpx.Response(
            200, json={"error": [], "result": {"descr": {"order": "buy 0.0001 XBTUSD @ limit 1"}}}
        )
    )
    a = KrakenAccountAdapter(allow_keychain=False)
    with pytest.raises(KrakenAccountError, match="Order-Rechte"):
        await a.assert_read_only()
    await a.aclose()


@respx.mock
async def test_secret_never_leaks_into_error(monkeypatch) -> None:
    _creds(monkeypatch)
    # Kraken echot manchmal Teile des Requests — hier simuliert im Fehlertext
    respx.post("https://api.kraken.com/0/private/Balance").mock(
        return_value=httpx.Response(
            200, json={"error": [f"EGeneral:Internal {_TEST_SECRET}"], "result": {}}
        )
    )
    a = KrakenAccountAdapter(allow_keychain=False)
    with pytest.raises(KrakenAccountError) as ei:
        await a.get_balances()
    await a.aclose()
    assert _TEST_SECRET not in str(ei.value)


async def test_private_client_rejects_non_readonly_method(monkeypatch) -> None:
    _creds(monkeypatch)
    key = get_secret("KRAKEN_API_KEY", allow_keychain=False)
    sec = get_secret("KRAKEN_API_SECRET", allow_keychain=False)
    client = KrakenPrivateClient(key, sec)
    with pytest.raises(KrakenAccountError):
        await client.call("Withdraw", {"amount": "1"})
    await client.aclose()
