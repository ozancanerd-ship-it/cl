"""Kraken **Account**-Adapter — strikt READ-ONLY (private REST, HMAC-SHA512).

Nur ``Query``-Endpunkte: Guthaben, Trade-Balance, offene/geschlossene Orders, offene Positionen,
Ledger. **Kein** ``submit()``/``cancel()`` — dieser Adapter ist **kein** ``BrokerAdapter`` und
kann keine Order auslösen. Kein Withdraw-Pfad.

Signierung (Kraken-Spezifikation):

    postdata   = urlencode(data)  # inkl. nonce
    sha        = SHA256( nonce_str + postdata )
    message    = urlpath.encode() + sha
    API-Sign   = base64( HMAC-SHA512( base64decode(secret), message ) )

Header: ``API-Key`` (öffentlicher Key) + ``API-Sign``. ``nonce`` ist streng monoton steigend
(Mikrosekunden + Zähler-Guard); jeder Retry bekommt eine **neue** Nonce.

Credentials kommen aus ``security.secrets.get_secret`` (ENV → macOS-Keychain), nie aus dem Code,
nie ins Log (``Secret`` redigiert sich selbst). Ohne Credentials: ``status() == UNAVAILABLE``,
alle Calls werfen ``KrakenAuthError`` — **nichts wird simuliert**.
"""

from __future__ import annotations

import asyncio
import base64
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

_log = get_logger("kraken_account")

_BASE_URL = "https://api.kraken.com"
_KEY_ENV = "KRAKEN_API_KEY"
_SECRET_ENV = "KRAKEN_API_SECRET"

#: Endpunkte, die dieser Adapter aufrufen darf. Alles andere ist per Konstruktion ausgeschlossen.
_ALLOWED_METHODS: frozenset[str] = frozenset(
    {
        "Balance",
        "BalanceEx",
        "TradeBalance",
        "OpenOrders",
        "ClosedOrders",
        "OpenPositions",
        "Ledgers",
        "QueryLedgers",
        "TradesHistory",
        "QueryOrders",
        "QueryTrades",
    }
)

#: Kraken-Fehlercodes, die auf ein Key-/Berechtigungs-/Signaturproblem hindeuten.
_AUTH_ERROR_MARKERS: tuple[str, ...] = (
    "EAPI:Invalid key",
    "EAPI:Invalid signature",
    "EAPI:Invalid nonce",
    "EGeneral:Permission denied",
    "EAPI:Bad request",
    "EAuth",
)


class KrakenAccountError(RuntimeError):
    """Basisklasse. **Kein Fallback auf erfundene Daten.**"""


class KrakenAuthError(KrakenAccountError):
    """Key/Secret fehlt, ist ungültig, falsch signiert oder hat die Berechtigung nicht."""


class KrakenAPIError(KrakenAccountError):
    """Sonstiger Kraken-API-Fehler (Rate-Limit, temporär, Parameter)."""


def sign_request(urlpath: str, data: dict[str, Any], secret_b64: str) -> str:
    """Kraken ``API-Sign`` für einen privaten Request. ``data`` muss ``nonce`` enthalten."""
    postdata = urllib.parse.urlencode(data)
    encoded = (str(data["nonce"]) + postdata).encode()
    message = urlpath.encode() + hashlib.sha256(encoded).digest()
    mac = hmac.new(base64.b64decode(secret_b64), message, hashlib.sha512)
    return base64.b64encode(mac.digest()).decode()


class _NonceFactory:
    """Streng monoton steigende Nonce (µs-Auflösung + Zähler-Guard je Prozess/Key)."""

    def __init__(self) -> None:
        self._last = 0

    def next(self) -> int:
        n = max(int(time.time() * 1_000_000), self._last + 1)
        self._last = n
        return n


class KrakenPrivateClient:
    """Dünner, signierender POST-Client für ``/0/private/*``. Retry mit **neuer** Nonce."""

    def __init__(
        self,
        api_key: Secret,
        api_secret: Secret,
        *,
        base_url: str = _BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
        timeout_s: float = 15.0,
        max_attempts: int = 3,
        min_spacing_s: float = 0.4,
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
        self._max_attempts = max_attempts
        self._min_spacing = min_spacing_s
        self._nonce = _NonceFactory()
        self._last_call = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    def _redact(self, text: str) -> str:
        return redact(text, self._key, self._secret)

    async def call(self, method: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Ruft ``/0/private/<method>`` auf und gibt ``result`` zurück. Wirft bei Fehler."""
        if method not in _ALLOWED_METHODS and method != "AddOrder":
            raise KrakenAccountError(f"Methode {method!r} ist im READ-ONLY-Adapter nicht erlaubt")
        if not (self._key.present and self._secret.present):
            raise KrakenAuthError(
                f"{_KEY_ENV}/{_SECRET_ENV} nicht gesetzt — Kraken-Account ist NOT_AVAILABLE."
            )
        urlpath = f"/0/private/{method}"
        last_exc: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            await self._space()
            body = dict(data or {})
            body["nonce"] = self._nonce.next()
            headers = {
                "API-Key": self._key.reveal(),
                "API-Sign": sign_request(urlpath, body, self._secret.reveal()),
                "Content-Type": "application/x-www-form-urlencoded",
            }
            try:
                resp = await self._http.post(urlpath, data=body, headers=headers)
            except httpx.HTTPError as exc:
                last_exc = KrakenAPIError(self._redact(f"{type(exc).__name__}: {exc}"))
                _log.warning("kraken private transport error", extra={"attempt": attempt})
                continue
            try:
                payload = resp.json()
            except ValueError as exc:
                last_exc = KrakenAPIError(f"HTTP {resp.status_code}: keine JSON-Antwort")
                if resp.status_code < 500:
                    raise last_exc from exc
                continue

            errors = payload.get("error") or []
            if errors:
                joined = "; ".join(str(e) for e in errors)
                if (
                    any(m in joined for m in ("EAPI:Invalid nonce",))
                    and attempt < self._max_attempts
                ):
                    last_exc = KrakenAuthError(joined)
                    continue  # neue Nonce im nächsten Versuch
                if any(m in joined for m in _AUTH_ERROR_MARKERS):
                    raise KrakenAuthError(self._redact(joined))
                raise KrakenAPIError(self._redact(joined))
            result: dict[str, Any] = payload.get("result", {})
            return result
        raise last_exc or KrakenAPIError("kraken private call fehlgeschlagen")

    async def _space(self) -> None:
        dt = time.monotonic() - self._last_call
        if dt < self._min_spacing:
            await asyncio.sleep(self._min_spacing - dt)
        self._last_call = time.monotonic()


@dataclass(frozen=True, slots=True)
class AccountSummary:
    equity: float | None
    balance: float | None
    free_margin: float | None
    margin_level_pct: float | None
    currency: str
    nonzero_balances: dict[str, float]


@dataclass(frozen=True, slots=True)
class ReadOnlyProof:
    """Ergebnis der Sicherheits-Assertion. ``confirmed`` ⇒ der Key kann **nicht** handeln."""

    confirmed: bool
    detail: str
    probed_with: str = "AddOrder validate=true (platziert nie eine Order)"


class KrakenAccountAdapter(LiveDataAdapter):
    """READ-ONLY Kraken-Konto. Kein ``submit``/``cancel``. Kein Withdraw."""

    def __init__(
        self,
        *,
        key_env: str = _KEY_ENV,
        secret_env: str = _SECRET_ENV,
        base_url: str = _BASE_URL,
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
        self._client: KrakenPrivateClient | None = None
        super().__init__(
            AdapterInfo(
                name="kraken_account",
                asset_classes=("crypto", "altcoin"),
                data_kinds=(),
                modes=("stream",),
                credentials=CredentialSpec(
                    provider="kraken",
                    env_vars=(self.key_env, self.secret_env),
                    read_only=True,
                    note=(
                        "Kraken-API-Key mit NUR Query-Rechten (Funds/Orders/Ledger). "
                        "KEINE Order-, KEINE Withdraw-Rechte."
                    ),
                ),
                redistribution_allowed=False,
                note="READ-ONLY Account-Query. Kein submit/cancel im Adapter, kein Withdraw-Pfad.",
            ),
        )
        self._health = HealthTracker("kraken_account", clock=self._clock)

    # ---- Credentials / Zustand ---------------------------------------------
    def _secrets(self) -> tuple[Secret, Secret]:
        key = get_secret(self.key_env, service=self.service, allow_keychain=self.allow_keychain)
        sec = get_secret(self.secret_env, service=self.service, allow_keychain=self.allow_keychain)
        return key, sec

    def credentials_ok(self) -> bool:
        return not missing_secrets(
            (self.key_env, self.secret_env),
            service=self.service,
            allow_keychain=self.allow_keychain,
        )

    def missing_credentials(self) -> list[str]:
        return missing_secrets(
            (self.key_env, self.secret_env),
            service=self.service,
            allow_keychain=self.allow_keychain,
        )

    def status(self) -> ProviderStatus:
        if not self.credentials_ok():
            return ProviderStatus(
                provider="kraken_account",
                health=ProviderHealth.UNAVAILABLE,
                checked_at=datetime.now(UTC),
                detail=f"Credentials fehlen: {', '.join(self.missing_credentials())}",
            )
        return self._health.status()

    def _private(self) -> KrakenPrivateClient:
        if self._client is None:
            key, sec = self._secrets()
            if not (key.present and sec.present):
                raise KrakenAuthError(
                    f"{self.key_env}/{self.secret_env} nicht gesetzt — Kraken-Account NOT_AVAILABLE."
                )
            self._client = KrakenPrivateClient(
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

    async def _call(self, method: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            result = await self._private().call(method, data)
        except KrakenAccountError as exc:
            self._health.record_failure(str(exc))
            raise
        self._health.record_success(latency_ms=1.0)
        return result

    # ---- READ-ONLY Abfragen ----------------------------------------------
    async def server_time(self) -> tuple[datetime, float]:
        """Öffentliche Kraken-Serverzeit + Clock-Skew (Sekunden, positiv = Server voraus)."""
        async with httpx.AsyncClient(
            base_url=self.base_url, timeout=12.0, transport=self._transport
        ) as c:
            r = await c.get("/0/public/Time")
            r.raise_for_status()
            unix = int(r.json()["result"]["unixtime"])
        server = datetime.fromtimestamp(unix, tz=UTC)
        skew = (server - self._clock.now()).total_seconds()
        return server, skew

    async def get_balances(self) -> dict[str, float]:
        result = await self._call("Balance")
        return {k: float(v) for k, v in result.items() if float(v) != 0.0}

    async def get_trade_balance(self, *, asset: str = "ZEUR") -> AccountSummary:
        result = await self._call("TradeBalance", {"asset": asset})
        balances = {k: float(v) for k, v in (await self._call("Balance")).items() if float(v) != 0}
        ml = result.get("ml")
        return AccountSummary(
            equity=_f(result.get("e")),
            balance=_f(result.get("tb")),
            free_margin=_f(result.get("mf")),
            margin_level_pct=_f(ml) if ml not in (None, "") else None,
            currency=asset.lstrip("Z") or asset,
            nonzero_balances=balances,
        )

    async def get_open_orders(self) -> dict[str, Any]:
        result = await self._call("OpenOrders")
        orders = result.get("open", {})
        return {"count": len(orders), "orders": orders}

    async def get_open_positions(self) -> dict[str, Any]:
        result = await self._call("OpenPositions")
        return {"count": len(result), "positions": result}

    async def get_closed_orders(self, *, limit: int = 5) -> dict[str, Any]:
        result = await self._call("ClosedOrders")
        closed = result.get("closed", {})
        items = list(closed.items())[:limit]
        return {"count_total": result.get("count", len(closed)), "sample": dict(items)}

    async def get_ledgers(self, *, limit: int = 5) -> list[dict[str, Any]]:
        result = await self._call("Ledgers")
        ledger = result.get("ledger", {})
        rows = sorted(ledger.values(), key=lambda e: float(e.get("time", 0.0)), reverse=True)
        return rows[:limit]

    async def assert_read_only(self) -> ReadOnlyProof:
        """Sicherheits-Assertion: beweist, dass der Key **nicht** handeln kann.

        Ruft ``AddOrder`` mit ``validate=true`` (Krakens offizieller Dry-Run — **platziert
        niemals eine Order**). Erwartet ``EGeneral:Permission denied``.

        * Permission denied  → ``confirmed=True``  (Key ist handelsunfähig — gewünscht).
        * Erfolg / Validierung ok → **``KrakenAccountError``** (der Key HAT Order-Rechte → abbrechen).
        """
        probe = {
            "pair": "XBTUSD",
            "type": "buy",
            "ordertype": "limit",
            "price": "1",
            "volume": "0.0001",
            "validate": "true",
        }
        try:
            await self._private().call("AddOrder", probe)
        except KrakenAuthError as exc:
            if "Permission denied" in str(exc):
                return ReadOnlyProof(
                    confirmed=True, detail="EGeneral:Permission denied — Key kann nicht handeln"
                )
            return ReadOnlyProof(confirmed=False, detail=f"unerwarteter Auth-Fehler: {exc}")
        except KrakenAPIError as exc:
            # z. B. Rate-Limit — nicht eindeutig, aber jedenfalls keine Order platziert
            return ReadOnlyProof(confirmed=False, detail=f"nicht eindeutig ({exc})")
        raise KrakenAccountError(
            "WARNUNG: AddOrder(validate=true) wurde akzeptiert — der API-Key HAT Order-Rechte. "
            "Key sofort widerrufen und ohne 'Create & modify orders' neu anlegen."
        )


def _f(v: object) -> float | None:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


__all__ = [
    "AccountSummary",
    "KrakenAPIError",
    "KrakenAccountAdapter",
    "KrakenAccountError",
    "KrakenAuthError",
    "KrakenPrivateClient",
    "ReadOnlyProof",
    "sign_request",
]
