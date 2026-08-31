"""Binance **Account**-Adapter — strikt READ-ONLY (signierte REST, HMAC-SHA256).

Nur lesende Endpunkte: API-Key-Berechtigungen, Spot-Guthaben, offene Orders, Serverzeit.
**Kein** ``submit()``/``cancel()`` — kein ``BrokerAdapter``. Kein Withdraw-/Transfer-Pfad.

Signierung (Binance):

    query      = urlencode(params + {timestamp, recvWindow})
    signature  = hex( HMAC-SHA256( secret, query ) )
    → GET {base}{path}?{query}&signature={signature}   Header: X-MBX-APIKEY: <key>

**Read-only-Nachweis:** ``GET /sapi/v1/account/apiRestrictions`` gibt die Rechte des Keys
*direkt* zurück (``enableReading`` / ``enableWithdrawals`` / ``enableSpotAndMarginTrading`` /
``enableFutures`` / ``permitsUniversalTransfer`` …). ``assert_read_only()`` bestätigt nur, wenn
**weder Withdraw noch Transfer** aktiv ist und meldet Trading-Flags explizit.

Credentials aus ``security.secrets`` (ENV → macOS-Keychain), nie im Code, nie ins Log.
Ohne Credentials: ``status() == UNAVAILABLE``, Calls werfen ``BinanceAuthError``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from trading_agent.core.clock import Clock, SystemClock
from trading_agent.core.enums import ProviderHealth
from trading_agent.core.time import parse_timestamp
from trading_agent.data.health import HealthTracker
from trading_agent.data.interfaces import ProviderStatus
from trading_agent.data.providers.adapter_base import AdapterInfo, CredentialSpec, LiveDataAdapter
from trading_agent.security.secrets import Secret, get_secret, missing_secrets, redact
from trading_agent.utils.logging import get_logger

_log = get_logger("binance_account")

_SPOT_BASE = "https://api.binance.com"
_KEY_ENV = "BINANCE_API_KEY"
_SECRET_ENV = "BINANCE_API_SECRET"
_RECV_WINDOW = "5000"

#: Nur diese signierten Pfade darf der Adapter aufrufen — alle rein lesend.
_ALLOWED_PATHS: frozenset[str] = frozenset(
    {
        "/sapi/v1/account/apiRestrictions",
        "/api/v3/account",
        "/api/v3/openOrders",
        "/api/v3/myTrades",
        "/api/v3/allOrders",
    }
)

#: Binance-Fehlercodes für Key-/Signatur-/Berechtigungsprobleme.
_AUTH_ERROR_CODES: frozenset[int] = frozenset({-2014, -2015, -1022, -1099, -2008, -1002})


def sign_query(query: str, secret: str) -> str:
    """Binance-Signatur: ``hex(HMAC-SHA256(secret, query))``."""
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


class BinanceAccountError(RuntimeError):
    """Basisklasse. **Kein Fallback auf erfundene Daten.**"""


class BinanceAuthError(BinanceAccountError):
    """Key/Secret fehlt, ist ungültig, falsch signiert oder hat die Berechtigung nicht."""


class BinanceAPIError(BinanceAccountError):
    """Sonstiger Binance-API-Fehler (Rate-Limit, temporär, Parameter)."""


class BinancePrivateClient:
    """Signierender Client für Binance-**Lese**-Endpunkte. Retry mit frischem Timestamp."""

    def __init__(
        self,
        api_key: Secret,
        api_secret: Secret,
        *,
        base_url: str = _SPOT_BASE,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
        timeout_s: float = 15.0,
        recv_window: str = _RECV_WINDOW,
        max_attempts: int = 3,
        min_spacing_s: float = 0.2,
    ) -> None:
        self._key = api_key
        self._secret = api_secret
        self._clock = clock or SystemClock()
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_s,
            transport=transport,
            headers={
                "User-Agent": "trading-agent/0.1 (read-only account)",
                "X-MBX-APIKEY": api_key.reveal() if api_key.present else "",
            },
        )
        self._recv = recv_window
        self._max_attempts = max_attempts
        self._min_spacing = min_spacing_s
        self._last_call = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    def _redact(self, text: str) -> str:
        return redact(text, self._key, self._secret)

    async def signed_get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if path not in _ALLOWED_PATHS:
            raise BinanceAccountError(f"Pfad {path!r} ist im READ-ONLY-Adapter nicht erlaubt")
        if not (self._key.present and self._secret.present):
            raise BinanceAuthError(
                f"{_KEY_ENV}/{_SECRET_ENV} nicht gesetzt — Binance-Account ist NOT_AVAILABLE."
            )
        last_exc: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            await self._space()
            base_params = dict(params or {})
            base_params["timestamp"] = str(int(time.time() * 1000))
            base_params["recvWindow"] = self._recv
            query = urllib.parse.urlencode(base_params)
            sig = sign_query(query, self._secret.reveal())
            try:
                resp = await self._http.get(f"{path}?{query}&signature={sig}")
            except httpx.HTTPError as exc:
                last_exc = BinanceAPIError(self._redact(f"{type(exc).__name__}: {exc}"))
                _log.warning("binance private transport error", extra={"attempt": attempt})
                continue
            try:
                payload = resp.json()
            except ValueError as exc:
                last_exc = BinanceAPIError(f"HTTP {resp.status_code}: keine JSON-Antwort")
                if resp.status_code < 500:
                    raise last_exc from exc
                continue

            if isinstance(payload, dict) and "code" in payload and "msg" in payload:
                code = int(payload["code"])
                detail = self._redact(f"code {code}: {payload['msg']}")
                if code == -1021 and attempt < self._max_attempts:  # Timestamp/recvWindow
                    last_exc = BinanceAuthError(detail)
                    continue
                if code in _AUTH_ERROR_CODES:
                    raise BinanceAuthError(detail)
                raise BinanceAPIError(detail)
            return payload
        raise last_exc or BinanceAPIError("binance private call fehlgeschlagen")

    async def _space(self) -> None:
        dt = time.monotonic() - self._last_call
        if dt < self._min_spacing:
            await asyncio.sleep(self._min_spacing - dt)
        self._last_call = time.monotonic()


@dataclass(frozen=True, slots=True)
class ApiPermissions:
    can_read: bool
    can_withdraw: bool
    can_internal_transfer: bool
    can_universal_transfer: bool
    can_spot_margin_trade: bool
    can_futures_trade: bool
    can_margin: bool
    ip_restricted: bool
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class AccountSummary:
    can_trade: bool
    can_withdraw: bool
    account_type: str
    nonzero_balances: dict[str, float]


@dataclass(frozen=True, slots=True)
class ReadOnlyProof:
    confirmed: bool
    detail: str
    probed_with: str = "GET /sapi/v1/account/apiRestrictions"


class BinanceAccountAdapter(LiveDataAdapter):
    """READ-ONLY Binance-Konto (Spot). Kein ``submit``/``cancel``. Kein Withdraw/Transfer."""

    def __init__(
        self,
        *,
        key_env: str = _KEY_ENV,
        secret_env: str = _SECRET_ENV,
        base_url: str = _SPOT_BASE,
        service: str = "trading-agent",
        allow_keychain: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.key_env = key_env
        self.secret_env = secret_env
        self.base_url = base_url
        self.service = service
        self.allow_keychain = allow_keychain
        self._transport = transport
        self._clock: Clock = clock or SystemClock()
        self._client: BinancePrivateClient | None = None
        super().__init__(
            AdapterInfo(
                name="binance_account",
                asset_classes=("crypto", "altcoin", "gold"),
                data_kinds=(),
                modes=("stream",),
                credentials=CredentialSpec(
                    provider="binance",
                    env_vars=(self.key_env, self.secret_env),
                    read_only=True,
                    note="Binance-API-Key nur mit 'Enable Reading'. KEINE Trade-/Withdraw-Rechte.",
                ),
                redistribution_allowed=False,
                note="READ-ONLY Account-Query. Kein submit/cancel im Adapter, kein Withdraw-Pfad.",
            ),
        )
        self._health = HealthTracker("binance_account", clock=self._clock)

    # ---- Credentials / Zustand -------------------------------------------
    def missing_credentials(self) -> list[str]:
        return missing_secrets(
            (self.key_env, self.secret_env),
            service=self.service,
            allow_keychain=self.allow_keychain,
        )

    def credentials_ok(self) -> bool:
        return not self.missing_credentials()

    def status(self) -> ProviderStatus:
        if not self.credentials_ok():
            return ProviderStatus(
                provider="binance_account",
                health=ProviderHealth.UNAVAILABLE,
                checked_at=datetime.now(UTC),
                detail=f"Credentials fehlen: {', '.join(self.missing_credentials())}",
            )
        return self._health.status()

    def _private(self) -> BinancePrivateClient:
        if self._client is None:
            key = get_secret(self.key_env, service=self.service, allow_keychain=self.allow_keychain)
            sec = get_secret(
                self.secret_env, service=self.service, allow_keychain=self.allow_keychain
            )
            if not (key.present and sec.present):
                raise BinanceAuthError(
                    f"{self.key_env}/{self.secret_env} nicht gesetzt — Binance NOT_AVAILABLE."
                )
            self._client = BinancePrivateClient(
                key, sec, base_url=self.base_url, transport=self._transport, clock=self._clock
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            result = await self._private().signed_get(path, params)
        except BinanceAccountError as exc:
            self._health.record_failure(str(exc))
            raise
        self._health.record_success(latency_ms=1.0)
        return result

    # ---- READ-ONLY Abfragen --------------------------------------------
    async def server_time(self) -> tuple[datetime, float]:
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=12.0, transport=self._transport
        ) as c:
            r = await c.get("/api/v3/time")
            r.raise_for_status()
            server = parse_timestamp(int(r.json()["serverTime"]))
        return server, (server - self._clock.now()).total_seconds()

    async def get_api_permissions(self) -> ApiPermissions:
        raw = await self._get("/sapi/v1/account/apiRestrictions")

        def flag(key: str) -> bool:
            return bool(raw.get(key, False))

        return ApiPermissions(
            can_read=flag("enableReading"),
            can_withdraw=flag("enableWithdrawals"),
            can_internal_transfer=flag("enableInternalTransfer"),
            can_universal_transfer=flag("permitsUniversalTransfer"),
            can_spot_margin_trade=flag("enableSpotAndMarginTrading"),
            can_futures_trade=flag("enableFutures"),
            can_margin=flag("enableMargin"),
            ip_restricted=flag("ipRestrict"),
            raw=dict(raw),
        )

    async def get_spot_balances(self) -> AccountSummary:
        raw = await self._get("/api/v3/account")
        coins = {
            b["asset"]: float(b["free"]) + float(b["locked"])
            for b in raw.get("balances", [])
            if float(b.get("free", 0)) + float(b.get("locked", 0)) != 0.0
        }
        return AccountSummary(
            can_trade=bool(raw.get("canTrade", False)),
            can_withdraw=bool(raw.get("canWithdraw", False)),
            account_type=str(raw.get("accountType", "SPOT")),
            nonzero_balances=coins,
        )

    async def get_open_orders(self) -> dict[str, Any]:
        raw = await self._get("/api/v3/openOrders")
        orders = raw if isinstance(raw, list) else []
        return {"count": len(orders), "orders": orders}

    async def assert_read_only(self) -> ReadOnlyProof:
        """Prüft die Key-Rechte direkt. ``confirmed`` ⇒ weder Withdraw noch Transfer aktiv."""
        try:
            perms = await self.get_api_permissions()
        except BinanceAuthError as exc:
            return ReadOnlyProof(confirmed=False, detail=f"apiRestrictions nicht lesbar: {exc}")
        if perms.can_withdraw or perms.can_internal_transfer or perms.can_universal_transfer:
            raise BinanceAccountError(
                "WARNUNG: der API-Key hat Withdraw-/Transfer-Rechte — "
                f"withdraw={perms.can_withdraw} internal_transfer={perms.can_internal_transfer} "
                f"universal_transfer={perms.can_universal_transfer}. "
                "Key im Binance-Portal bearbeiten und diese Rechte entfernen."
            )
        trade_flags = []
        if perms.can_spot_margin_trade:
            trade_flags.append("spot/margin trade")
        if perms.can_futures_trade:
            trade_flags.append("futures trade")
        note = "nur Reading" if not trade_flags else f"Trading aktiv: {', '.join(trade_flags)}"
        return ReadOnlyProof(
            confirmed=True,
            detail=f"withdraw=NEIN, transfer=NEIN; {note}; ip_restricted={perms.ip_restricted}",
        )


__all__ = [
    "AccountSummary",
    "ApiPermissions",
    "BinanceAPIError",
    "BinanceAccountAdapter",
    "BinanceAccountError",
    "BinanceAuthError",
    "BinancePrivateClient",
    "ReadOnlyProof",
    "sign_query",
]
