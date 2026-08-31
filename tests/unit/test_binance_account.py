"""Binance **Account**-Adapter (READ-ONLY) — Signierer, private REST, Read-only-Nachweis.

Kein Netz: Signatur gegen Binances **offiziellen Testvektor**, alle REST-Calls via respx.
Kein Order-Pfad — der Adapter hat kein ``submit``/``cancel``.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from trading_agent.core.enums import ProviderHealth
from trading_agent.data.providers.binance_account import (
    BinanceAccountAdapter,
    BinanceAccountError,
    BinanceAPIError,
    BinanceAuthError,
    BinancePrivateClient,
    sign_query,
)
from trading_agent.security.secrets import get_secret

_B = "https://api.binance.com"


def _creds(monkeypatch) -> None:
    monkeypatch.setenv("BINANCE_API_KEY", "PUBKEY")
    monkeypatch.setenv("BINANCE_API_SECRET", "SEKRIT")


# --------------------------------------------------------------------------- signer


def test_sign_query_matches_binance_official_vector() -> None:
    query = (
        "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1"
        "&recvWindow=5000&timestamp=1499827319559"
    )
    secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
    assert sign_query(query, secret) == (
        "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"
    )


# --------------------------------------------------------------------------- adapter contract


def test_adapter_unavailable_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("BINANCE_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_API_SECRET", raising=False)
    a = BinanceAccountAdapter(allow_keychain=False)
    st = a.status()
    assert st.health is ProviderHealth.UNAVAILABLE
    assert "BINANCE_API_KEY" in st.detail
    assert a.info.credentials.read_only is True


def test_adapter_has_no_order_methods() -> None:
    from trading_agent.execution.brokers.base import BrokerAdapter

    a = BinanceAccountAdapter(allow_keychain=False)
    assert not isinstance(a, BrokerAdapter)
    assert not hasattr(a, "submit")
    assert not hasattr(a, "cancel")


async def test_private_client_rejects_non_allowlisted_path(monkeypatch) -> None:
    _creds(monkeypatch)
    key = get_secret("BINANCE_API_KEY", allow_keychain=False)
    sec = get_secret("BINANCE_API_SECRET", allow_keychain=False)
    client = BinancePrivateClient(key, sec)
    with pytest.raises(BinanceAccountError):
        await client.signed_get("/api/v3/order", {})
    await client.aclose()


# --------------------------------------------------------------------------- private REST (respx)


@respx.mock
async def test_signed_get_adds_headers_and_signature(monkeypatch) -> None:
    _creds(monkeypatch)
    route = respx.get(url__regex=rf"{_B}/api/v3/account.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "canTrade": True,
                "canWithdraw": False,
                "accountType": "SPOT",
                "balances": [
                    {"asset": "USDT", "free": "500.0", "locked": "0"},
                    {"asset": "BTC", "free": "0", "locked": "0"},
                ],
            },
        )
    )
    a = BinanceAccountAdapter(allow_keychain=False)
    acct = await a.get_spot_balances()
    await a.aclose()
    assert acct.nonzero_balances == {"USDT": 500.0}
    req = route.calls.last.request
    assert req.headers["X-MBX-APIKEY"] == "PUBKEY"
    assert "signature=" in str(req.url)
    assert "SEKRIT" not in str(req.url)


@respx.mock
async def test_api_restrictions_parsed(monkeypatch) -> None:
    _creds(monkeypatch)
    respx.get(url__regex=rf"{_B}/sapi/v1/account/apiRestrictions.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "enableReading": True,
                "enableWithdrawals": False,
                "enableInternalTransfer": False,
                "permitsUniversalTransfer": False,
                "enableSpotAndMarginTrading": False,
                "enableFutures": False,
                "ipRestrict": True,
            },
        )
    )
    a = BinanceAccountAdapter(allow_keychain=False)
    proof = await a.assert_read_only()
    await a.aclose()
    assert proof.confirmed is True
    assert "withdraw=NEIN" in proof.detail and "nur Reading" in proof.detail


@respx.mock
async def test_assert_read_only_raises_on_withdraw(monkeypatch) -> None:
    _creds(monkeypatch)
    respx.get(url__regex=rf"{_B}/sapi/v1/account/apiRestrictions.*").mock(
        return_value=httpx.Response(200, json={"enableReading": True, "enableWithdrawals": True})
    )
    a = BinanceAccountAdapter(allow_keychain=False)
    with pytest.raises(BinanceAccountError, match="Withdraw"):
        await a.assert_read_only()
    await a.aclose()


@respx.mock
async def test_assert_read_only_confirms_but_flags_trading(monkeypatch) -> None:
    _creds(monkeypatch)
    respx.get(url__regex=rf"{_B}/sapi/v1/account/apiRestrictions.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "enableReading": True,
                "enableWithdrawals": False,
                "enableSpotAndMarginTrading": True,
            },
        )
    )
    a = BinanceAccountAdapter(allow_keychain=False)
    proof = await a.assert_read_only()
    await a.aclose()
    assert proof.confirmed is True
    assert "Trading aktiv" in proof.detail


@respx.mock
async def test_invalid_key_maps_to_auth_error(monkeypatch) -> None:
    _creds(monkeypatch)
    respx.get(url__regex=rf"{_B}/api/v3/account.*").mock(
        return_value=httpx.Response(401, json={"code": -2015, "msg": "Invalid API-key."})
    )
    a = BinanceAccountAdapter(allow_keychain=False)
    with pytest.raises(BinanceAuthError):
        await a.get_spot_balances()
    await a.aclose()


@respx.mock
async def test_generic_error_maps_to_api_error(monkeypatch) -> None:
    _creds(monkeypatch)
    respx.get(url__regex=rf"{_B}/api/v3/openOrders.*").mock(
        return_value=httpx.Response(429, json={"code": -1003, "msg": "Too much request weight."})
    )
    a = BinanceAccountAdapter(allow_keychain=False)
    with pytest.raises(BinanceAPIError):
        await a.get_open_orders()
    await a.aclose()


@respx.mock
async def test_secret_never_leaks_into_error(monkeypatch) -> None:
    _creds(monkeypatch)
    respx.get(url__regex=rf"{_B}/api/v3/account.*").mock(
        return_value=httpx.Response(400, json={"code": -1022, "msg": "Signature for SEKRIT"})
    )
    a = BinanceAccountAdapter(allow_keychain=False)
    with pytest.raises(BinanceAccountError) as ei:
        await a.get_spot_balances()
    await a.aclose()
    assert "SEKRIT" not in str(ei.value)
