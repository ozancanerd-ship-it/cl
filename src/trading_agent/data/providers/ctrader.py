"""cTrader Open API (Spotware) — **READ-ONLY** Markt­daten für Pepperstone (FX + XAUUSD).

Cloud-tauglich, reines Netzprotokoll, **keine Windows-/Terminal-Abhängigkeit**. JSON über
Secure-WebSocket (``wss://{live|demo}.ctraderapi.com:5036``).

Auth (dreistufig)
-----------------
1. **OAuth2** — App auf https://openapi.ctrader.com registrieren → ``CTRADER_CLIENT_ID`` /
   ``CTRADER_CLIENT_SECRET``. Nutzer autorisiert im Browser (Scope **``accounts``** = nur
   Konto-Daten lesen, **kein** ``trading``) → ``code`` → Token-Tausch → ``CTRADER_ACCESS_TOKEN``
   (+ ``CTRADER_REFRESH_TOKEN``).
2. **ProtoOAApplicationAuthReq** (clientId, clientSecret).
3. **ProtoOAAccountAuthReq** (ctidTraderAccountId = ``CTRADER_ACCOUNT_ID``, accessToken).

Danach: ``ProtoOASymbolsListReq`` (numerische ``symbolId`` je Konto auflösen) +
``ProtoOAGetTrendbarsReq`` (OHLC-Historie) + ``ProtoOASubscribeSpotsReq`` (Live Bid/Ask).
Heartbeat alle 10 s.

Preise: alle cTrader-Open-API-Preise sind **× 100 000** (Integer) — hier durch ``_PRICE_SCALE``
zurückskaliert. Trendbar: ``low`` absolut, ``deltaOpen/High/Close`` relativ zu ``low``.
``utcTimestampInMinutes × 60`` = Epoch-Sekunden (UTC) des Bar-Beginns.

Sicherheit
----------
Dieser Adapter ruft **ausschließlich** Marktdaten-/Konto-Lese-Nachrichten auf — **niemals**
``ProtoOANewOrderReq`` / ``ProtoOAClosePositionReq`` o. Ä. Kein ``submit``/``cancel``.
Credentials nur über ``security.secrets`` (ENV → Keychain), nie im Code, nie ins Log.
Ohne Credentials: ``status() == UNAVAILABLE``, Calls werfen ``CTraderUnavailable``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from trading_agent.core.clock import Clock, SystemClock
from trading_agent.core.enums import DataKind, ProviderHealth, Timeframe
from trading_agent.core.models import OHLCV, Quote
from trading_agent.core.time import bar_close_time, ensure_utc, is_aligned, to_epoch_ms
from trading_agent.data.health import HealthTracker
from trading_agent.data.interfaces import ProviderStatus
from trading_agent.data.providers.adapter_base import AdapterInfo, CredentialSpec, LiveDataAdapter
from trading_agent.security.secrets import Secret, get_secret, missing_secrets, redact
from trading_agent.utils.logging import get_logger

_log = get_logger("ctrader")

_TOKEN_URL = "https://openapi.ctrader.com/apps/token"
_AUTH_URL = "https://openapi.ctrader.com/apps/auth"
_WS_LIVE = "wss://live.ctraderapi.com:5036"
_WS_DEMO = "wss://demo.ctraderapi.com:5036"
_PRICE_SCALE = 100_000.0

# ProtoOAPayloadType (JSON-Envelope: {"clientMsgId","payloadType","payload"})
PT_APP_AUTH_REQ = 2100
PT_APP_AUTH_RES = 2101
PT_ACCOUNT_AUTH_REQ = 2102
PT_ACCOUNT_AUTH_RES = 2103
PT_GET_ACCOUNTS_REQ = 2149
PT_GET_ACCOUNTS_RES = 2150
PT_SYMBOLS_LIST_REQ = 2114
PT_SYMBOLS_LIST_RES = 2115
PT_SUBSCRIBE_SPOTS_REQ = 2127
PT_SUBSCRIBE_SPOTS_RES = 2128
PT_UNSUBSCRIBE_SPOTS_REQ = 2129
PT_SPOT_EVENT = 2131
PT_GET_TRENDBARS_REQ = 2137
PT_GET_TRENDBARS_RES = 2138
PT_OA_ERROR_RES = 2142
PT_HEARTBEAT = 51
PT_PROTO_ERROR = 50

_TRENDBAR_PERIOD: dict[Timeframe, int] = {
    Timeframe.M1: 1,
    Timeframe.M5: 5,
    Timeframe.M15: 7,
    Timeframe.M30: 8,
    Timeframe.H1: 9,
    Timeframe.H4: 10,
    Timeframe.D1: 12,
}

# kanonisch → cTrader-Symbolname (Broker-abhängig; die numerische symbolId wird zur Laufzeit
# über ProtoOASymbolsListReq aufgelöst).
DEFAULT_SYMBOL_MAP: dict[str, str] = {
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
    "USDJPY": "USDJPY",
    "AUDUSD": "AUDUSD",
    "USDCHF": "USDCHF",
    "USDCAD": "USDCAD",
    "XAUUSD": "XAUUSD",
}


class CTraderError(RuntimeError):
    """Basisklasse. **Kein Fallback auf erfundene Daten.**"""


class CTraderUnavailable(CTraderError):
    """Credentials fehlen oder Verbindung nicht möglich."""


class CTraderAuthError(CTraderError):
    """OAuth-/App-/Account-Authentifizierung fehlgeschlagen."""


# --------------------------------------------------------------------------------- OAuth


@dataclass(frozen=True, slots=True)
class TokenBundle:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "bearer"


def authorize_url(client_id: str, redirect_uri: str, *, scope: str = "accounts") -> str:
    """Browser-URL für die Nutzer-Autorisierung. ``scope='accounts'`` = **nur lesen**."""
    from urllib.parse import urlencode

    q = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "response_type": "code",
        }
    )
    return f"{_AUTH_URL}?{q}"


async def exchange_code(
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> TokenBundle:
    """Authorization-Code → Access-/Refresh-Token."""
    return await _token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        transport=transport,
    )


async def refresh_access_token(
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> TokenBundle:
    return await _token_request(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        transport=transport,
    )


async def _token_request(
    data: dict[str, str], *, transport: httpx.AsyncBaseTransport | None
) -> TokenBundle:
    async with httpx.AsyncClient(timeout=20.0, transport=transport) as c:
        r = await c.post(_TOKEN_URL, data=data)
    try:
        payload = r.json()
    except ValueError as exc:
        raise CTraderAuthError(f"Token-Endpoint: HTTP {r.status_code}, keine JSON-Antwort") from exc
    if payload.get("errorCode") or "access_token" not in payload:
        msg = payload.get("description") or payload.get("errorCode") or f"HTTP {r.status_code}"
        raise CTraderAuthError(
            f"Token-Tausch fehlgeschlagen: {redact(str(msg), data.get('client_secret', ''))}"
        )
    return TokenBundle(
        access_token=str(payload["access_token"]),
        refresh_token=str(payload.get("refresh_token", "")),
        expires_in=int(payload.get("expires_in", 0)),
        token_type=str(payload.get("token_type", "bearer")),
    )


# --------------------------------------------------------------------------------- WS client


class AsyncWSConn(Protocol):  # struktureller Typ für eine WS-Verbindung
    async def send(self, data: str) -> None: ...  # pragma: no cover
    async def recv(self) -> str | bytes: ...  # pragma: no cover
    async def close(self) -> None: ...  # pragma: no cover


ConnectFn = Callable[[str], Awaitable["AsyncWSConn"]]


@dataclass(frozen=True, slots=True)
class CTraderSymbol:
    symbol_id: int
    name: str


class CTraderClient:
    """Minimaler READ-ONLY JSON-WS-Client. Öffnet die Verbindung, authentifiziert App + Konto,
    liest Symbole / Trendbars / Spot-Quotes. **Kein Order-Nachrichtentyp.**"""

    def __init__(
        self,
        *,
        client_id: Secret,
        client_secret: Secret,
        access_token: Secret,
        account_id: int,
        demo: bool = True,
        connect_fn: ConnectFn | None = None,
        clock: Clock | None = None,
        heartbeat_s: float = 10.0,
        request_timeout_s: float = 20.0,
    ) -> None:
        self._cid = client_id
        self._csec = client_secret
        self._token = access_token
        self.account_id = account_id
        self.url = _WS_DEMO if demo else _WS_LIVE
        self._connect = connect_fn or _default_ws_connect
        self._clock = clock or SystemClock()
        self._hb = heartbeat_s
        self._timeout = request_timeout_s
        self._conn: AsyncWSConn | None = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._spots: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._tasks: list[asyncio.Task[None]] = []
        self._closed = False

    # ---- Verbindung / Auth -------------------------------------------------
    async def connect(self, *, authenticate_account: bool = True) -> None:
        """WS öffnen + App-Auth. Mit ``authenticate_account=True`` zusätzlich Account-Auth
        (braucht ``account_id``). Für die Konto-Auflösung im Link-Flow: ``False``."""
        self._conn = await self._connect(self.url)
        self._tasks.append(asyncio.create_task(self._recv_loop()))
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))
        await self._send(
            PT_APP_AUTH_REQ,
            {"clientId": self._cid.reveal(), "clientSecret": self._csec.reveal()},
            expect=PT_APP_AUTH_RES,
        )
        if authenticate_account:
            await self._send(
                PT_ACCOUNT_AUTH_REQ,
                {"ctidTraderAccountId": self.account_id, "accessToken": self._token.reveal()},
                expect=PT_ACCOUNT_AUTH_RES,
            )
        _log.info("ctrader connected", extra={"account_id": self.account_id, "url": self.url})

    async def aclose(self) -> None:
        self._closed = True
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        if self._conn is not None:
            with contextlib.suppress(Exception):
                await self._conn.close()
            self._conn = None

    # ---- Nachrichten -----------------------------------------------------
    async def _send(
        self, payload_type: int, payload: dict[str, Any], *, expect: int | None = None
    ) -> dict[str, Any]:
        if self._conn is None:
            raise CTraderError("nicht verbunden — connect() zuerst aufrufen")
        msg_id = uuid.uuid4().hex
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = fut
        frame = {"clientMsgId": msg_id, "payloadType": payload_type, "payload": payload}
        await self._conn.send(json.dumps(frame))
        try:
            res = await asyncio.wait_for(fut, self._timeout)
        finally:
            self._pending.pop(msg_id, None)
        pt = int(res.get("payloadType", 0))
        body = res.get("payload") or {}
        if pt in (PT_OA_ERROR_RES, PT_PROTO_ERROR):
            code = body.get("errorCode", "?")
            desc = body.get("description", "")
            raise CTraderAuthError(f"cTrader error {code}: {desc}")
        if expect is not None and pt != expect:
            raise CTraderError(f"unerwartete Antwort payloadType={pt} (erwartet {expect})")
        return body

    async def _recv_loop(self) -> None:
        assert self._conn is not None
        try:
            while not self._closed:
                raw = await self._conn.recv()
                text = raw.decode() if isinstance(raw, bytes) else raw
                msg = json.loads(text)
                pt = int(msg.get("payloadType", 0))
                if pt == PT_HEARTBEAT:
                    continue
                if pt == PT_SPOT_EVENT:
                    await self._spots.put(msg.get("payload") or {})
                    continue
                msg_id = msg.get("clientMsgId")
                fut = self._pending.get(msg_id) if msg_id else None
                if fut is not None and not fut.done():
                    fut.set_result(msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(CTraderError(f"WS-Verbindung verloren: {exc}"))

    async def _heartbeat_loop(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(self._hb)
                if self._conn is not None:
                    frame = {"payloadType": PT_HEARTBEAT, "payload": {}}
                    with contextlib.suppress(Exception):
                        await self._conn.send(json.dumps(frame))
        except asyncio.CancelledError:
            raise

    # ---- READ-ONLY Abfragen -------------------------------------------
    async def list_accounts(self) -> list[int]:
        body = await self._send(
            PT_GET_ACCOUNTS_REQ, {"accessToken": self._token.reveal()}, expect=PT_GET_ACCOUNTS_RES
        )
        out: list[int] = []
        for a in body.get("ctidTraderAccount", []):
            aid = a.get("ctidTraderAccountId")
            if aid is not None:
                out.append(int(aid))
        return out

    async def symbols(self) -> dict[str, int]:
        body = await self._send(
            PT_SYMBOLS_LIST_REQ,
            {"ctidTraderAccountId": self.account_id, "includeArchivedSymbols": False},
            expect=PT_SYMBOLS_LIST_RES,
        )
        return {
            str(s.get("symbolName", "")).upper(): int(s["symbolId"])
            for s in body.get("symbol", [])
            if "symbolId" in s
        }

    async def get_trendbars(
        self,
        symbol_id: int,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
        *,
        instrument_label: str = "",
    ) -> list[OHLCV]:
        period = _TRENDBAR_PERIOD.get(timeframe)
        if period is None:
            raise CTraderError(f"cTrader: Timeframe {timeframe} nicht unterstützt")
        start, end = ensure_utc(start), ensure_utc(end)
        body = await self._send(
            PT_GET_TRENDBARS_REQ,
            {
                "ctidTraderAccountId": self.account_id,
                "symbolId": symbol_id,
                "period": period,
                "fromTimestamp": to_epoch_ms(start),
                "toTimestamp": to_epoch_ms(end),
            },
            expect=PT_GET_TRENDBARS_RES,
        )
        label = (instrument_label or str(symbol_id)).upper()
        out: list[OHLCV] = []
        now = ensure_utc(self._clock.now())
        for tb in body.get("trendbar", []):
            low = int(tb["low"]) / _PRICE_SCALE
            o = (int(tb["low"]) + int(tb.get("deltaOpen", 0))) / _PRICE_SCALE
            h = (int(tb["low"]) + int(tb.get("deltaHigh", 0))) / _PRICE_SCALE
            c = (int(tb["low"]) + int(tb.get("deltaClose", 0))) / _PRICE_SCALE
            open_time = datetime.fromtimestamp(int(tb["utcTimestampInMinutes"]) * 60, tz=UTC)
            if not is_aligned(open_time, timeframe):
                continue
            close_time = bar_close_time(open_time, timeframe)
            if close_time > now or not (start <= open_time < end):
                continue
            out.append(
                OHLCV(
                    instrument=label,
                    timeframe=timeframe,
                    open_time=open_time,
                    close_time=close_time,
                    open=o,
                    high=max(h, o, c),
                    low=min(low, o, c),
                    close=c,
                    volume=float(tb.get("volume", 0)),
                    source="ctrader",
                    ingested_at=now,
                )
            )
        out.sort(key=lambda b: b.open_time)
        return out

    async def spot_snapshot(
        self, symbol_ids: list[int], *, timeout_s: float = 15.0
    ) -> dict[int, tuple[float, float]]:
        """Abonniert die Symbole, wartet je Symbol auf das erste ``ProtoOASpotEvent`` mit
        Bid **und** Ask, meldet sich wieder ab. Kein Dauer-Stream."""
        await self._send(
            PT_SUBSCRIBE_SPOTS_REQ,
            {"ctidTraderAccountId": self.account_id, "symbolId": symbol_ids},
            expect=PT_SUBSCRIBE_SPOTS_RES,
        )
        got: dict[int, tuple[float, float]] = {}
        last: dict[int, dict[str, float]] = {}
        deadline = asyncio.get_event_loop().time() + timeout_s
        try:
            while len(got) < len(symbol_ids):
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    break
                try:
                    ev = await asyncio.wait_for(self._spots.get(), remaining)
                except TimeoutError:
                    break
                sid = int(ev.get("symbolId", 0))
                cur = last.setdefault(sid, {})
                if "bid" in ev:
                    cur["bid"] = int(ev["bid"]) / _PRICE_SCALE
                if "ask" in ev:
                    cur["ask"] = int(ev["ask"]) / _PRICE_SCALE
                if "bid" in cur and "ask" in cur and sid in symbol_ids:
                    got[sid] = (cur["bid"], cur["ask"])
        finally:
            with contextlib.suppress(Exception):
                await self._send(
                    PT_UNSUBSCRIBE_SPOTS_REQ,
                    {"ctidTraderAccountId": self.account_id, "symbolId": symbol_ids},
                )
        return got


async def _default_ws_connect(url: str) -> AsyncWSConn:  # pragma: no cover — braucht Netz
    import websockets

    conn = await websockets.connect(url, max_size=8 * 1024 * 1024)
    return conn


# --------------------------------------------------------------------------------- Adapter

_ENV_VARS = (
    "CTRADER_CLIENT_ID",
    "CTRADER_CLIENT_SECRET",
    "CTRADER_ACCESS_TOKEN",
    "CTRADER_ACCOUNT_ID",
)


class CTraderAdapter(LiveDataAdapter):
    """READ-ONLY cTrader-Markt­daten (FX + XAUUSD). Kein ``submit``/``cancel``."""

    def __init__(
        self,
        *,
        symbol_map: dict[str, str] | None = None,
        demo: bool = True,
        service: str = "trading-agent",
        allow_keychain: bool = True,
        connect_fn: ConnectFn | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.symbol_map = {**DEFAULT_SYMBOL_MAP, **(symbol_map or {})}
        self.demo = demo
        self.service = service
        self.allow_keychain = allow_keychain
        self._connect_fn = connect_fn
        self._clock: Clock = clock or SystemClock()
        self._client: CTraderClient | None = None
        self._symbol_ids: dict[str, int] = {}
        super().__init__(
            AdapterInfo(
                name="ctrader",
                asset_classes=("forex", "gold"),
                data_kinds=(DataKind.OHLCV, DataKind.QUOTE),
                modes=("historical", "stream"),
                credentials=CredentialSpec(
                    provider="ctrader",
                    env_vars=_ENV_VARS,
                    read_only=True,
                    note="OAuth2 Scope 'accounts' (nur lesen). Kein Trading-Scope, keine Orders.",
                ),
                redistribution_allowed=False,
                note="READ-ONLY: Symbols/Trendbars/Spot. Kein Order-Nachrichtentyp im Adapter.",
            )
        )
        self._health = HealthTracker("ctrader", clock=self._clock)

    # ---- Credentials / Zustand -----------------------------------------
    def missing_credentials(self) -> list[str]:
        return missing_secrets(_ENV_VARS, service=self.service, allow_keychain=self.allow_keychain)

    def credentials_ok(self) -> bool:
        return not self.missing_credentials()

    def status(self) -> ProviderStatus:
        if not self.credentials_ok():
            return ProviderStatus(
                provider="ctrader",
                health=ProviderHealth.UNAVAILABLE,
                checked_at=datetime.now(UTC),
                detail=f"Credentials fehlen: {', '.join(self.missing_credentials())}",
            )
        return self._health.status()

    def to_provider_symbol(self, canonical: str) -> str:
        return self.symbol_map.get(canonical.upper(), canonical.upper())

    def _secret(self, name: str) -> Secret:
        return get_secret(name, service=self.service, allow_keychain=self.allow_keychain)

    async def _ensure_client(self) -> CTraderClient:
        if self._client is not None:
            return self._client
        missing = self.missing_credentials()
        if missing:
            raise CTraderUnavailable(
                f"cTrader NOT_AVAILABLE — fehlende ENV: {', '.join(missing)}. Keine Simulation."
            )
        acc = self._secret("CTRADER_ACCOUNT_ID").reveal()
        try:
            account_id = int(acc)
        except ValueError as exc:
            raise CTraderUnavailable("CTRADER_ACCOUNT_ID ist keine Zahl") from exc
        client = CTraderClient(
            client_id=self._secret("CTRADER_CLIENT_ID"),
            client_secret=self._secret("CTRADER_CLIENT_SECRET"),
            access_token=self._secret("CTRADER_ACCESS_TOKEN"),
            account_id=account_id,
            demo=self.demo,
            connect_fn=self._connect_fn,
            clock=self._clock,
        )
        try:
            await client.connect()
        except CTraderError as exc:
            self._health.record_failure(str(exc))
            with contextlib.suppress(Exception):
                await client.aclose()
            raise
        self._client = client
        self._symbol_ids = await client.symbols()
        return client

    async def _symbol_id(self, instrument: str) -> int:
        await self._ensure_client()
        name = self.to_provider_symbol(instrument)
        sid = self._symbol_ids.get(name.upper())
        if sid is None:
            raise CTraderError(f"cTrader: Symbol {name!r} im Konto nicht gefunden")
        return sid

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---- READ-ONLY -----------------------------------------------------
    async def fetch_ohlcv(
        self, instrument: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[OHLCV]:
        client = await self._ensure_client()
        sid = await self._symbol_id(instrument)
        try:
            bars = await client.get_trendbars(
                sid, timeframe, start, end, instrument_label=instrument
            )
        except CTraderError as exc:
            self._health.record_failure(str(exc))
            raise
        self._health.record_success(latency_ms=1.0)
        return bars

    async def fetch_quote(self, instrument: str) -> Quote:
        client = await self._ensure_client()
        sid = await self._symbol_id(instrument)
        try:
            snap = await client.spot_snapshot([sid])
        except CTraderError as exc:
            self._health.record_failure(str(exc))
            raise
        if sid not in snap:
            self._health.record_failure("kein Spot-Event erhalten")
            raise CTraderError(f"cTrader: kein Bid/Ask für {instrument} erhalten")
        bid, ask = snap[sid]
        now = ensure_utc(self._clock.now())
        self._health.record_success(latency_ms=1.0)
        return Quote(
            instrument=instrument.upper(),
            ts=now,
            bid=bid,
            ask=ask,
            source="ctrader",
            ingested_at=now,
        )


__all__ = [
    "DEFAULT_SYMBOL_MAP",
    "AsyncWSConn",
    "CTraderAdapter",
    "CTraderAuthError",
    "CTraderClient",
    "CTraderError",
    "CTraderSymbol",
    "CTraderUnavailable",
    "TokenBundle",
    "authorize_url",
    "exchange_code",
    "refresh_access_token",
]
