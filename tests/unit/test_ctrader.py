"""cTrader Open API READ-ONLY client + adapter — Auth-Flow, Trendbar-Dekodierung, kein Order-Pfad.

Kein Netz: ``FakeWS`` skriptet die JSON-Antworten je ``payloadType``; Token-Endpoint via respx.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from trading_agent.core.enums import ProviderHealth, Timeframe
from trading_agent.data.providers import ctrader as ct
from trading_agent.data.providers.ctrader import (
    CTraderAdapter,
    CTraderAuthError,
    CTraderClient,
    authorize_url,
    exchange_code,
)
from trading_agent.security.secrets import Secret


class FakeWS:
    """Skriptet cTrader-JSON-Antworten. Kennt App-Auth, Account-Auth, Symbols, Trendbars, Spots."""

    def __init__(
        self, *, symbols: dict[str, int], trendbars: list[dict], spots: list[dict]
    ) -> None:
        self._symbols = symbols
        self._trendbars = trendbars
        self._spots = spots
        self._out: asyncio.Queue[str] = asyncio.Queue()
        self.closed = False
        self.sent: list[dict] = []

    async def send(self, data: str) -> None:
        msg = json.loads(data)
        self.sent.append(msg)
        pt = msg.get("payloadType")
        mid = msg.get("clientMsgId")
        if pt == ct.PT_HEARTBEAT:
            return
        if pt == ct.PT_APP_AUTH_REQ:
            await self._reply(mid, ct.PT_APP_AUTH_RES, {})
        elif pt == ct.PT_ACCOUNT_AUTH_REQ:
            await self._reply(mid, ct.PT_ACCOUNT_AUTH_RES, {"ctidTraderAccountId": 42})
        elif pt == ct.PT_GET_ACCOUNTS_REQ:
            await self._reply(
                mid, ct.PT_GET_ACCOUNTS_RES, {"ctidTraderAccount": [{"ctidTraderAccountId": 42}]}
            )
        elif pt == ct.PT_SYMBOLS_LIST_REQ:
            await self._reply(
                mid,
                ct.PT_SYMBOLS_LIST_RES,
                {"symbol": [{"symbolId": v, "symbolName": k} for k, v in self._symbols.items()]},
            )
        elif pt == ct.PT_GET_TRENDBARS_REQ:
            await self._reply(mid, ct.PT_GET_TRENDBARS_RES, {"trendbar": self._trendbars})
        elif pt == ct.PT_SUBSCRIBE_SPOTS_REQ:
            await self._reply(mid, ct.PT_SUBSCRIBE_SPOTS_RES, {})
            for s in self._spots:
                await self._out.put(json.dumps({"payloadType": ct.PT_SPOT_EVENT, "payload": s}))
        elif pt == ct.PT_UNSUBSCRIBE_SPOTS_REQ:
            await self._reply(mid, 2130, {})

    async def _reply(self, mid: str, pt: int, payload: dict) -> None:
        await self._out.put(json.dumps({"clientMsgId": mid, "payloadType": pt, "payload": payload}))

    async def recv(self) -> str:
        return await self._out.get()

    async def close(self) -> None:
        self.closed = True


def _client(fake: FakeWS, *, account_id: int = 42) -> CTraderClient:
    return CTraderClient(
        client_id=Secret("cid", name="CTRADER_CLIENT_ID"),
        client_secret=Secret("csec", name="CTRADER_CLIENT_SECRET"),
        access_token=Secret("tok", name="CTRADER_ACCESS_TOKEN"),
        account_id=account_id,
        demo=True,
        connect_fn=lambda _url: _ready(fake),
        heartbeat_s=999.0,
    )


async def _ready(fake: FakeWS) -> FakeWS:
    return fake


# --------------------------------------------------------------------------- OAuth


def test_authorize_url_uses_accounts_scope() -> None:
    url = authorize_url("APP123", "http://localhost/", scope="accounts")
    assert url.startswith("https://openapi.ctrader.com/apps/auth?")
    assert "scope=accounts" in url and "response_type=code" in url
    assert "client_id=APP123" in url


@respx.mock
async def test_exchange_code_ok() -> None:
    respx.post("https://openapi.ctrader.com/apps/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "AT",
                "refresh_token": "RT",
                "expires_in": 2628000,
                "token_type": "bearer",
            },
        )
    )
    tok = await exchange_code(
        client_id="c", client_secret="s", code="xyz", redirect_uri="http://localhost/"
    )
    assert tok.access_token == "AT" and tok.refresh_token == "RT"


@respx.mock
async def test_exchange_code_error_is_raised_not_faked() -> None:
    respx.post("https://openapi.ctrader.com/apps/token").mock(
        return_value=httpx.Response(
            200, json={"errorCode": "INVALID_GRANT", "description": "bad code"}
        )
    )
    with pytest.raises(CTraderAuthError, match="bad code"):
        await exchange_code(
            client_id="c", client_secret="s", code="x", redirect_uri="http://localhost/"
        )


# --------------------------------------------------------------------------- client


async def test_connect_does_app_and_account_auth() -> None:
    fake = FakeWS(symbols={"EURUSD": 1}, trendbars=[], spots=[])
    c = _client(fake)
    await c.connect()
    types = [m["payloadType"] for m in fake.sent]
    assert ct.PT_APP_AUTH_REQ in types and ct.PT_ACCOUNT_AUTH_REQ in types
    await c.aclose()
    assert fake.closed


async def test_list_accounts_without_account_auth() -> None:
    fake = FakeWS(symbols={}, trendbars=[], spots=[])
    c = _client(fake, account_id=0)
    await c.connect(authenticate_account=False)
    accounts = await c.list_accounts()
    await c.aclose()
    assert accounts == [42]
    assert ct.PT_ACCOUNT_AUTH_REQ not in [m["payloadType"] for m in fake.sent]


async def test_get_trendbars_decodes_prices_and_time() -> None:
    # low=1.08000 -> 108_000 (×1e5); deltaOpen=+50 -> 1.08050, deltaHigh=+120, deltaClose=+30
    open_dt = datetime(2024, 6, 4, 8, 0, tzinfo=UTC)
    minute = int(open_dt.timestamp()) // 60
    tb = [
        {
            "low": 108_000,
            "deltaOpen": 50,
            "deltaHigh": 120,
            "deltaClose": 30,
            "volume": 250,
            "utcTimestampInMinutes": minute,
        }
    ]
    fake = FakeWS(symbols={"EURUSD": 1}, trendbars=tb, spots=[])
    c = _client(fake)
    await c.connect()
    bars = await c.get_trendbars(
        1,
        Timeframe.M5,
        open_dt - timedelta(hours=1),
        open_dt + timedelta(hours=1),
        instrument_label="EURUSD",
    )
    await c.aclose()
    assert len(bars) == 1
    b = bars[0]
    assert b.open == pytest.approx(1.08050)
    assert b.high == pytest.approx(1.08120)
    assert b.close == pytest.approx(1.08030)
    assert b.low == pytest.approx(1.08000)
    assert b.open_time == open_dt and b.source == "ctrader"


async def test_spot_snapshot_waits_for_bid_and_ask() -> None:
    spots = [
        {"symbolId": 1, "bid": 108050},  # nur bid
        {"symbolId": 1, "ask": 108060},  # jetzt komplett
    ]
    fake = FakeWS(symbols={"EURUSD": 1}, trendbars=[], spots=spots)
    c = _client(fake)
    await c.connect()
    snap = await c.spot_snapshot([1], timeout_s=2.0)
    await c.aclose()
    assert snap[1][0] == pytest.approx(1.0805)
    assert snap[1][1] == pytest.approx(1.0806)


async def test_error_response_raises_auth_error() -> None:
    class ErrWS(FakeWS):
        async def send(self, data: str) -> None:
            msg = json.loads(data)
            if msg.get("payloadType") == ct.PT_APP_AUTH_REQ:
                await self._reply(
                    msg["clientMsgId"],
                    ct.PT_OA_ERROR_RES,
                    {"errorCode": "CH_CLIENT_AUTH_FAILURE", "description": "bad app creds"},
                )

    fake = ErrWS(symbols={}, trendbars=[], spots=[])
    c = _client(fake)
    with pytest.raises(CTraderAuthError, match="bad app creds"):
        await c.connect()
    await c.aclose()


# --------------------------------------------------------------------------- adapter


def test_adapter_unavailable_without_credentials(monkeypatch) -> None:
    for v in ct._ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    a = CTraderAdapter(allow_keychain=False)
    st = a.status()
    assert st.health is ProviderHealth.UNAVAILABLE
    assert a.info.credentials.read_only is True
    assert "CTRADER_CLIENT_ID" in st.detail


def test_adapter_has_no_order_methods() -> None:
    from trading_agent.execution.brokers.base import BrokerAdapter

    a = CTraderAdapter(allow_keychain=False)
    assert not isinstance(a, BrokerAdapter)
    assert not hasattr(a, "submit")
    assert not hasattr(a, "cancel")
    # kein Order-Nachrichtentyp im Modul
    assert not any("NEW_ORDER" in n or "NewOrder" in n for n in dir(ct))


async def test_adapter_fetch_ohlcv_via_fake_ws(monkeypatch) -> None:
    for v in ct._ENV_VARS:
        monkeypatch.setenv(v, "x")
    monkeypatch.setenv("CTRADER_ACCOUNT_ID", "42")
    open_dt = datetime(2024, 6, 4, 8, 0, tzinfo=UTC)
    minute = int(open_dt.timestamp()) // 60
    tb = [
        {
            "low": 234_500_000,
            "deltaOpen": 100_000,
            "deltaHigh": 200_000,
            "deltaClose": 50_000,
            "volume": 10,
            "utcTimestampInMinutes": minute,
        }
    ]
    fake = FakeWS(symbols={"XAUUSD": 41}, trendbars=tb, spots=[])
    a = CTraderAdapter(allow_keychain=False, connect_fn=lambda _u: _ready(fake))
    bars = await a.fetch_ohlcv(
        "XAUUSD", Timeframe.M5, open_dt - timedelta(hours=1), open_dt + timedelta(hours=1)
    )
    await a.aclose()
    assert len(bars) == 1 and bars[0].instrument == "XAUUSD"
    assert bars[0].open == pytest.approx(2346.0)
    assert a.status().health is not ProviderHealth.UNAVAILABLE
