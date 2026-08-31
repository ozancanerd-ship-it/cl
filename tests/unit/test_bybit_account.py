"""Bybit **Account**-Adapter (READ-ONLY, v5) — Signierer, private REST, Read-only-Nachweis.

Kein Netz: Signatur gegen einen gepinnten Vektor, alle REST-Calls via respx.
Kein Order-Pfad — der Adapter hat kein ``submit``/``cancel``.
"""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from trading_agent.core.enums import ProviderHealth
from trading_agent.data.providers.bybit_account import (
    BybitAccountAdapter,
    BybitAccountError,
    BybitAPIError,
    BybitAuthError,
    BybitPrivateClient,
    sign_v5,
)
from trading_agent.security.secrets import get_secret

_SECRET = base64.b64encode(b"bybit-test-secret-bytes-0123456789").decode()


def _ok(result: dict) -> httpx.Response:
    return httpx.Response(200, json={"retCode": 0, "retMsg": "OK", "result": result, "time": 1})


def _err(code: int, msg: str) -> httpx.Response:
    return httpx.Response(200, json={"retCode": code, "retMsg": msg, "result": {}, "time": 1})


def _creds(monkeypatch) -> None:
    monkeypatch.setenv("BYBIT_API_KEY", "PUBKEY")
    monkeypatch.setenv("BYBIT_API_SECRET", _SECRET)


# --------------------------------------------------------------------------- signer


def test_sign_v5_pinned_vector() -> None:
    sig = sign_v5("1700000000000", "mykey", "5000", "accountType=UNIFIED", "mysecretkey")
    assert sig == "ed4fc08de8a94382d720af884c1d43b7a37626f8c2d23b297112086331ae9dce"


def test_sign_v5_prehash_order() -> None:
    import hashlib
    import hmac

    ts, key, rw, payload, secret = "111", "kk", "5000", "a=1&b=2", "sss"
    expect = hmac.new(
        secret.encode(), f"{ts}{key}{rw}{payload}".encode(), hashlib.sha256
    ).hexdigest()
    assert sign_v5(ts, key, rw, payload, secret) == expect


# --------------------------------------------------------------------------- adapter contract


def test_adapter_unavailable_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("BYBIT_API_KEY", raising=False)
    monkeypatch.delenv("BYBIT_API_SECRET", raising=False)
    a = BybitAccountAdapter(allow_keychain=False)
    st = a.status()
    assert st.health is ProviderHealth.UNAVAILABLE
    assert "BYBIT_API_KEY" in st.detail and "BYBIT_API_SECRET" in st.detail
    assert a.info.credentials.read_only is True


def test_adapter_has_no_order_methods() -> None:
    from trading_agent.execution.brokers.base import BrokerAdapter

    a = BybitAccountAdapter(allow_keychain=False)
    assert not isinstance(a, BrokerAdapter)
    assert not hasattr(a, "submit")
    assert not hasattr(a, "cancel")


async def test_call_without_creds_raises_auth_error() -> None:
    a = BybitAccountAdapter(allow_keychain=False)
    with pytest.raises(BybitAuthError):
        await a.get_wallet_balance()
    await a.aclose()


async def test_private_client_rejects_non_allowlisted_path(monkeypatch) -> None:
    _creds(monkeypatch)
    key = get_secret("BYBIT_API_KEY", allow_keychain=False)
    sec = get_secret("BYBIT_API_SECRET", allow_keychain=False)
    client = BybitPrivateClient(key, sec)
    with pytest.raises(BybitAccountError):
        await client.get("/v5/order/create", {})
    await client.aclose()


# --------------------------------------------------------------------------- private REST (respx)


@respx.mock
async def test_signs_headers_and_parses(monkeypatch) -> None:
    _creds(monkeypatch)
    route = respx.get("https://api.bybit.eu/v5/account/wallet-balance").mock(
        return_value=_ok(
            {
                "list": [
                    {
                        "totalEquity": "1234.5",
                        "totalWalletBalance": "1200.0",
                        "coin": [
                            {"coin": "USDT", "walletBalance": "1200.0"},
                            {"coin": "BTC", "walletBalance": "0"},
                        ],
                    }
                ]
            }
        )
    )
    a = BybitAccountAdapter(allow_keychain=False)
    summary = await a.get_wallet_balance()
    await a.aclose()
    assert summary.equity == 1234.5 and summary.balance == 1200.0
    assert summary.nonzero_balances == {"USDT": 1200.0}
    req = route.calls.last.request
    assert req.headers["X-BAPI-API-KEY"] == "PUBKEY"
    assert req.headers["X-BAPI-SIGN"] and req.headers["X-BAPI-SIGN-TYPE"] == "2"
    assert _SECRET not in str(dict(req.headers))


@respx.mock
async def test_open_orders_and_positions_zero(monkeypatch) -> None:
    _creds(monkeypatch)
    respx.get("https://api.bybit.eu/v5/order/realtime").mock(return_value=_ok({"list": []}))
    respx.get("https://api.bybit.eu/v5/position/list").mock(
        return_value=_ok({"list": [{"symbol": "BTCUSDT", "size": "0"}]})
    )
    a = BybitAccountAdapter(allow_keychain=False)
    oo = await a.get_open_orders()
    pos = await a.get_positions()
    await a.aclose()
    assert oo["count"] == 0 and pos["count"] == 0


@respx.mock
async def test_invalid_key_maps_to_auth_error(monkeypatch) -> None:
    _creds(monkeypatch)
    respx.get("https://api.bybit.eu/v5/account/wallet-balance").mock(
        return_value=_err(10003, "API key is invalid")
    )
    a = BybitAccountAdapter(allow_keychain=False)
    with pytest.raises(BybitAuthError):
        await a.get_wallet_balance()
    await a.aclose()


@respx.mock
async def test_generic_ret_code_maps_to_api_error(monkeypatch) -> None:
    _creds(monkeypatch)
    respx.get("https://api.bybit.eu/v5/account/wallet-balance").mock(
        return_value=_err(10006, "too many visits")
    )
    a = BybitAccountAdapter(allow_keychain=False)
    with pytest.raises(BybitAPIError):
        await a.get_wallet_balance()
    await a.aclose()


@respx.mock
async def test_timestamp_error_retries(monkeypatch) -> None:
    _creds(monkeypatch)
    respx.get("https://api.bybit.eu/v5/account/wallet-balance").mock(
        side_effect=[_err(10002, "recv_window"), _ok({"list": []})]
    )
    a = BybitAccountAdapter(allow_keychain=False)
    summary = await a.get_wallet_balance()
    await a.aclose()
    assert summary.equity is None  # leere Liste -> keine Zahlen, aber kein Fehler


# --------------------------------------------------------------------------- read-only assertion


@respx.mock
async def test_assert_read_only_confirmed_readonly_flag(monkeypatch) -> None:
    _creds(monkeypatch)
    respx.get("https://api.bybit.eu/v5/user/query-api").mock(
        return_value=_ok(
            {"readOnly": 1, "permissions": {"ContractTrade": [], "Wallet": []}, "ips": []}
        )
    )
    a = BybitAccountAdapter(allow_keychain=False)
    proof = await a.assert_read_only()
    await a.aclose()
    assert proof.confirmed is True


@respx.mock
async def test_assert_read_only_confirmed_when_readonly_flag_despite_listed_scopes(
    monkeypatch,
) -> None:
    """Bybit-EU-„Read-Only"-Key: ``readOnly=1`` ist bindend, auch wenn ``Spot``/``Derivatives``
    als LESE-Domänen in ``permissions`` stehen."""
    _creds(monkeypatch)
    respx.get("https://api.bybit.eu/v5/user/query-api").mock(
        return_value=_ok(
            {
                "readOnly": 1,
                "permissions": {
                    "Spot": ["SpotTrade"],
                    "Derivatives": ["DerivativesTrade"],
                    "ContractTrade": [],
                    "Wallet": [],
                },
                "ips": [],
            }
        )
    )
    a = BybitAccountAdapter(allow_keychain=False)
    proof = await a.assert_read_only()
    await a.aclose()
    assert proof.confirmed is True
    assert "readOnly=1" in proof.detail


@respx.mock
async def test_assert_read_only_raises_when_readwrite_with_trade(monkeypatch) -> None:
    _creds(monkeypatch)
    respx.get("https://api.bybit.eu/v5/user/query-api").mock(
        return_value=_ok(
            {"readOnly": 0, "permissions": {"ContractTrade": ["Order", "Position"]}, "ips": []}
        )
    )
    a = BybitAccountAdapter(allow_keychain=False)
    with pytest.raises(BybitAccountError, match="NICHT read-only"):
        await a.assert_read_only()
    await a.aclose()


@respx.mock
async def test_assert_read_only_raises_on_withdraw(monkeypatch) -> None:
    _creds(monkeypatch)
    respx.get("https://api.bybit.eu/v5/user/query-api").mock(
        return_value=_ok(
            {"readOnly": 1, "permissions": {"Wallet": ["AccountTransfer", "Withdraw"]}, "ips": []}
        )
    )
    a = BybitAccountAdapter(allow_keychain=False)
    with pytest.raises(BybitAccountError, match="Withdraw"):
        await a.assert_read_only()
    await a.aclose()


@respx.mock
async def test_secret_never_leaks_into_error(monkeypatch) -> None:
    _creds(monkeypatch)
    respx.get("https://api.bybit.eu/v5/account/wallet-balance").mock(
        return_value=_err(10004, f"error sign, params: {_SECRET}")
    )
    a = BybitAccountAdapter(allow_keychain=False)
    with pytest.raises(BybitAccountError) as ei:
        await a.get_wallet_balance()
    await a.aclose()
    assert _SECRET not in str(ei.value)
