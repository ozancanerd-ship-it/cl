"""Bybit **Account**-Adapter (v5, **Bybit EU / EEA**) — strikt READ-ONLY (HMAC-SHA256).

Host: ``https://api.bybit.eu`` (offizieller EEA-Endpunkt laut
https://bybit-exchange.github.io/docs/v5/guide). **Keine** Demo-/Testnet-Hosts.

Nur lesende Endpunkte: API-Key-Info (inkl. Berechtigungen!), Wallet-Balance, offene Orders,
Positionen, Transaktions-Log. **Kein** ``submit()``/``cancel()`` — dieser Adapter ist **kein**
``BrokerAdapter``. Kein Withdraw-Pfad.

**Bybit-„Read-Only"-Key:** Bybit listet in ``permissions`` weiterhin die Datendomänen, für die
der Key Lesezugriff hat (z. B. ``Spot: ["SpotTrade"]``), setzt aber ``readOnly = 1``. Das
``readOnly``-Flag ist bindend — ein solcher Key kann **nicht** handeln, egal was in
``permissions`` steht. ``assert_read_only()`` wertet daher primär das Flag aus.

Signierung (Bybit v5):

    prehash  = timestamp + api_key + recv_window + payload
               payload = queryString (GET) | rawBody (POST)
    X-BAPI-SIGN = hex( HMAC-SHA256( secret, prehash ) )

Header: ``X-BAPI-API-KEY``, ``X-BAPI-TIMESTAMP`` (ms), ``X-BAPI-RECV-WINDOW``,
``X-BAPI-SIGN``, ``X-BAPI-SIGN-TYPE: 2``.

**Read-only-Nachweis:** ``GET /v5/user/query-api`` gibt die Rechte des Keys *direkt* zurück
(``readOnly`` + ``permissions``). ``assert_read_only()`` bestätigt, wenn ``readOnly == 1``
(bindend) **und** kein ``Withdraw`` gesetzt ist — bzw. bei ``readOnly == 0``, wenn weder eine
Trade-Gruppe belegt noch ``Withdraw`` gesetzt ist. Kein Dry-Run-Order nötig.

Credentials aus ``security.secrets`` (ENV → macOS-Keychain), nie im Code, nie ins Log.
Ohne Credentials: ``status() == UNAVAILABLE``, Calls werfen ``BybitAuthError``.
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
from trading_agent.data.health import HealthTracker
from trading_agent.data.interfaces import ProviderStatus
from trading_agent.data.providers.adapter_base import AdapterInfo, CredentialSpec, LiveDataAdapter
from trading_agent.security.secrets import Secret, get_secret, missing_secrets, redact
from trading_agent.utils.logging import get_logger

_log = get_logger("bybit_account")

_BASE_URL = "https://api.bybit.eu"  # Bybit EU / EEA — kein .com, kein Demo/Testnet
_KEY_ENV = "BYBIT_API_KEY"
_SECRET_ENV = "BYBIT_API_SECRET"
_RECV_WINDOW = "5000"

#: Nur diese v5-Pfade darf der Adapter aufrufen — alle rein lesend.
_ALLOWED_PATHS: frozenset[str] = frozenset(
    {
        "/v5/user/query-api",
        "/v5/account/wallet-balance",
        "/v5/account/info",
        "/v5/account/transaction-log",
        "/v5/position/list",
        "/v5/order/realtime",
        "/v5/order/history",
        "/v5/execution/list",
    }
)

#: retCode-Werte, die auf ein Key-/Signatur-/Berechtigungsproblem hindeuten.
_AUTH_RET_CODES: frozenset[int] = frozenset({10003, 10004, 10005, 10007, 10010, 33004})

#: Bybit-Permission-Gruppen mit Handels-Scope. Bei ``readOnly=1`` sind sie reine LESE-Domänen;
#: nur bei ``readOnly=0`` bedeutet ein Eintrag hier tatsächlich Handelsrecht.
_TRADE_PERMISSION_GROUPS: tuple[str, ...] = (
    "ContractTrade",
    "Spot",
    "Options",
    "Derivatives",
    "CopyTrading",
    "BlockTrade",
    "Earn",
)


class BybitAccountError(RuntimeError):
    """Basisklasse. **Kein Fallback auf erfundene Daten.**"""


class BybitAuthError(BybitAccountError):
    """Key/Secret fehlt, ist ungültig, falsch signiert, abgelaufen oder Berechtigung fehlt."""


class BybitAPIError(BybitAccountError):
    """Sonstiger Bybit-API-Fehler (Rate-Limit, temporär, Parameter)."""


def sign_v5(timestamp: str, api_key: str, recv_window: str, payload: str, secret: str) -> str:
    """Bybit-v5-Signatur: ``hex(HMAC-SHA256(secret, timestamp+api_key+recv_window+payload))``."""
    prehash = f"{timestamp}{api_key}{recv_window}{payload}"
    return hmac.new(secret.encode(), prehash.encode(), hashlib.sha256).hexdigest()


class BybitPrivateClient:
    """Signierender Client für Bybit-v5-**Lese**-Endpunkte. Retry mit frischem Timestamp."""

    def __init__(
        self,
        api_key: Secret,
        api_secret: Secret,
        *,
        base_url: str = _BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
        timeout_s: float = 15.0,
        recv_window: str = _RECV_WINDOW,
        max_attempts: int = 3,
        min_spacing_s: float = 0.25,
    ) -> None:
        self._key = api_key
        self._secret = api_secret
        self._clock = clock or SystemClock()
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_s,
            transport=transport,
            headers={"User-Agent": "trading-agent/0.1 (read-only account)"},
        )
        self._recv = recv_window
        self._max_attempts = max_attempts
        self._min_spacing = min_spacing_s
        self._last_call = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    def _redact(self, text: str) -> str:
        return redact(text, self._key, self._secret)

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Signierter GET auf einen der erlaubten Lese-Pfade. Gibt ``result`` zurück."""
        if path not in _ALLOWED_PATHS:
            raise BybitAccountError(f"Pfad {path!r} ist im READ-ONLY-Adapter nicht erlaubt")
        if not (self._key.present and self._secret.present):
            raise BybitAuthError(
                f"{_KEY_ENV}/{_SECRET_ENV} nicht gesetzt — Bybit-Account ist NOT_AVAILABLE."
            )
        query = urllib.parse.urlencode(sorted((params or {}).items()))
        last_exc: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            await self._space()
            ts = str(int(time.time() * 1000))
            sign = sign_v5(ts, self._key.reveal(), self._recv, query, self._secret.reveal())
            headers = {
                "X-BAPI-API-KEY": self._key.reveal(),
                "X-BAPI-TIMESTAMP": ts,
                "X-BAPI-RECV-WINDOW": self._recv,
                "X-BAPI-SIGN": sign,
                "X-BAPI-SIGN-TYPE": "2",
            }
            url = f"{path}?{query}" if query else path
            try:
                resp = await self._http.get(url, headers=headers)
            except httpx.HTTPError as exc:
                last_exc = BybitAPIError(self._redact(f"{type(exc).__name__}: {exc}"))
                _log.warning("bybit private transport error", extra={"attempt": attempt})
                continue
            try:
                payload = resp.json()
            except ValueError as exc:
                last_exc = BybitAPIError(f"HTTP {resp.status_code}: keine JSON-Antwort")
                if resp.status_code < 500:
                    raise last_exc from exc
                continue

            ret_code = int(payload.get("retCode", -1))
            if ret_code == 0:
                result: dict[str, Any] = payload.get("result") or {}
                return result
            ret_msg = str(payload.get("retMsg", ""))
            # 10002 = Zeitstempel außerhalb recv_window ⇒ mit frischem ts erneut versuchen
            if ret_code == 10002 and attempt < self._max_attempts:
                last_exc = BybitAuthError(f"retCode 10002: {ret_msg}")
                continue
            detail = self._redact(f"retCode {ret_code}: {ret_msg}")
            if ret_code in _AUTH_RET_CODES:
                raise BybitAuthError(detail)
            raise BybitAPIError(detail)
        raise last_exc or BybitAPIError("bybit private call fehlgeschlagen")

    async def _space(self) -> None:
        dt = time.monotonic() - self._last_call
        if dt < self._min_spacing:
            await asyncio.sleep(self._min_spacing - dt)
        self._last_call = time.monotonic()


@dataclass(frozen=True, slots=True)
class ApiKeyInfo:
    read_only: bool
    permissions: dict[str, list[str]]
    ip_allowlist: list[str]
    expires_at: str | None
    trade_permissions: list[str]
    can_withdraw: bool


@dataclass(frozen=True, slots=True)
class AccountSummary:
    equity: float | None
    balance: float | None
    currency: str
    nonzero_balances: dict[str, float]


@dataclass(frozen=True, slots=True)
class ReadOnlyProof:
    confirmed: bool
    detail: str
    probed_with: str = "GET /v5/user/query-api (Rechte des Keys)"


class BybitAccountAdapter(LiveDataAdapter):
    """READ-ONLY Bybit-Konto (v5). Kein ``submit``/``cancel``. Kein Withdraw."""

    def __init__(
        self,
        *,
        key_env: str = _KEY_ENV,
        secret_env: str = _SECRET_ENV,
        base_url: str = _BASE_URL,
        account_type: str = "UNIFIED",
        service: str = "trading-agent",
        allow_keychain: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.key_env = key_env
        self.secret_env = secret_env
        self.base_url = base_url
        self.account_type = account_type
        self.service = service
        self.allow_keychain = allow_keychain
        self._transport = transport
        self._clock: Clock = clock or SystemClock()
        self._client: BybitPrivateClient | None = None
        super().__init__(
            AdapterInfo(
                name="bybit_account",
                asset_classes=("crypto", "altcoin"),
                data_kinds=(),
                modes=("stream",),
                credentials=CredentialSpec(
                    provider="bybit",
                    env_vars=(self.key_env, self.secret_env),
                    read_only=True,
                    note=(
                        "Bybit-API-Key im Modus 'Read-Only'. KEINE Trade-, KEINE Withdraw-Rechte."
                    ),
                ),
                redistribution_allowed=False,
                note="READ-ONLY Account-Query. Kein submit/cancel im Adapter, kein Withdraw-Pfad.",
            ),
        )
        self._health = HealthTracker("bybit_account", clock=self._clock)

    # ---- Credentials / Zustand -------------------------------------------
    def credentials_ok(self) -> bool:
        return not self.missing_credentials()

    def missing_credentials(self) -> list[str]:
        return missing_secrets(
            (self.key_env, self.secret_env),
            service=self.service,
            allow_keychain=self.allow_keychain,
        )

    def status(self) -> ProviderStatus:
        if not self.credentials_ok():
            return ProviderStatus(
                provider="bybit_account",
                health=ProviderHealth.UNAVAILABLE,
                checked_at=datetime.now(UTC),
                detail=f"Credentials fehlen: {', '.join(self.missing_credentials())}",
            )
        return self._health.status()

    def _private(self) -> BybitPrivateClient:
        if self._client is None:
            key = get_secret(self.key_env, service=self.service, allow_keychain=self.allow_keychain)
            sec = get_secret(
                self.secret_env, service=self.service, allow_keychain=self.allow_keychain
            )
            if not (key.present and sec.present):
                raise BybitAuthError(
                    f"{self.key_env}/{self.secret_env} nicht gesetzt — Bybit-Account NOT_AVAILABLE."
                )
            self._client = BybitPrivateClient(
                key,
                sec,
                base_url=self.base_url,
                transport=self._transport,
                clock=self._clock,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            result = await self._private().get(path, params)
        except BybitAccountError as exc:
            self._health.record_failure(str(exc))
            raise
        self._health.record_success(latency_ms=1.0)
        return result

    # ---- READ-ONLY Abfragen --------------------------------------------
    async def server_time(self) -> tuple[datetime, float]:
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=12.0, transport=self._transport
        ) as c:
            r = await c.get("/v5/market/time")
            r.raise_for_status()
            nano = int(r.json()["result"]["timeNano"])
        server = datetime.fromtimestamp(nano / 1e9, tz=UTC)
        return server, (server - self._clock.now()).total_seconds()

    async def get_api_key_info(self) -> ApiKeyInfo:
        result = await self._get("/v5/user/query-api")
        perms: dict[str, list[str]] = {
            k: list(v) for k, v in (result.get("permissions") or {}).items()
        }
        trade = sorted({p for grp in _TRADE_PERMISSION_GROUPS for p in perms.get(grp, [])})
        wallet = perms.get("Wallet", [])
        return ApiKeyInfo(
            read_only=str(result.get("readOnly")) in ("1", "True", "true"),
            permissions=perms,
            ip_allowlist=list(result.get("ips") or []),
            expires_at=result.get("expiredAt") or None,
            trade_permissions=trade,
            can_withdraw=any("ithdraw" in p for p in wallet),
        )

    async def get_wallet_balance(self) -> AccountSummary:
        result = await self._get("/v5/account/wallet-balance", {"accountType": self.account_type})
        rows = result.get("list") or []
        if not rows:
            return AccountSummary(None, None, "USD", {})
        acct = rows[0]
        coins = {
            c["coin"]: float(c.get("walletBalance") or 0.0)
            for c in acct.get("coin", [])
            if float(c.get("walletBalance") or 0.0) != 0.0
        }
        return AccountSummary(
            equity=_f(acct.get("totalEquity")),
            balance=_f(acct.get("totalWalletBalance")),
            currency="USD",
            nonzero_balances=coins,
        )

    async def get_open_orders(self, *, category: str = "linear") -> dict[str, Any]:
        result = await self._get(
            "/v5/order/realtime", {"category": category, "settleCoin": "USDT", "limit": 50}
        )
        orders = result.get("list") or []
        return {"count": len(orders), "orders": orders}

    async def get_positions(self, *, category: str = "linear") -> dict[str, Any]:
        result = await self._get(
            "/v5/position/list", {"category": category, "settleCoin": "USDT", "limit": 50}
        )
        positions = [p for p in (result.get("list") or []) if float(p.get("size") or 0.0) != 0.0]
        return {"count": len(positions), "positions": positions}

    async def get_transaction_log(self, *, limit: int = 5) -> list[dict[str, Any]]:
        result = await self._get(
            "/v5/account/transaction-log", {"accountType": self.account_type, "limit": limit}
        )
        return list(result.get("list") or [])[:limit]

    async def assert_read_only(self) -> ReadOnlyProof:
        """Prüft die Rechte des Keys direkt (``GET /v5/user/query-api``).

        Bybit-Logik: ``readOnly == 1`` ist **bindend** — ein solcher Key kann nicht handeln
        und nicht auszahlen, unabhängig davon, welche Datendomänen (``Spot``/``Derivatives``…)
        in ``permissions`` zum Lesen freigeschaltet sind. ``confirmed`` gilt daher, wenn:

        * ``readOnly == 1``  **und**  kein ``Withdraw`` in den Wallet-Rechten, **oder**
        * ``readOnly == 0``  aber **weder** eine Trade-Gruppe belegt **noch** ``Withdraw``.

        Sonst → ``BybitAccountError`` (Key kann handeln/auszahlen)."""
        try:
            info = await self.get_api_key_info()
        except BybitAuthError as exc:
            return ReadOnlyProof(confirmed=False, detail=f"query-api nicht lesbar: {exc}")

        if info.can_withdraw:
            raise BybitAccountError(
                "WARNUNG: der API-Key hat Withdraw-Rechte (Wallet). "
                "Key im Bybit-Portal bearbeiten und Withdraw entfernen."
            )
        if not info.read_only and info.trade_permissions:
            raise BybitAccountError(
                "WARNUNG: der API-Key ist NICHT read-only (readOnly=0) und hat Trade-Rechte "
                f"{info.trade_permissions}. Key auf 'Read-Only' umstellen."
            )
        note = (
            "readOnly=1 (bindend — Key kann nicht handeln/auszahlen)"
            if info.read_only
            else "readOnly=0, aber keine Trade-/Withdraw-Rechte"
        )
        read_scopes = sorted(k for k, v in info.permissions.items() if v)
        return ReadOnlyProof(
            confirmed=True,
            detail=f"{note}; Lese-Domänen: {read_scopes or '(nur Konto)'}",
        )


def _f(v: object) -> float | None:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


__all__ = [
    "AccountSummary",
    "ApiKeyInfo",
    "BybitAPIError",
    "BybitAccountAdapter",
    "BybitAccountError",
    "BybitAuthError",
    "BybitPrivateClient",
    "ReadOnlyProof",
    "sign_v5",
]
