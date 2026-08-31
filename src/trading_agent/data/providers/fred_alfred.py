"""FRED / ALFRED — **Point-in-Time** Makrodaten über echte Vintages.

ALFRED (*ArchivaL* FRED) gibt für jede Serie die **Vintage-Historie** zurück: welcher Wert
wann *erstmals* veröffentlicht wurde und jede spätere Revision, jeweils mit ``realtime_start``.
Das ist echtes Point-in-Time — **keine Näherung**:

* ``MacroEvent.available_time`` = ``realtime_start`` der Vintage (Erstveröffentlichung bzw.
  Revisions-Zeitpunkt).
* ``MacroEvent.revision``      = laufende Nummer der Vintage (0 = Erstwert).

Die Engine darf zu einem Replay-``cutoff`` nur Vintages mit ``available_time <= cutoff`` sehen
(``MarketDataRepository.read_macro`` erzwingt das).

**Release-Termine** (``fred/releases/dates``) sind reproduzierbare historische Fakten. *Wann* ein
künftiger Termin angekündigt wurde, gibt FRED nicht her — dafür wird konservativ
``scheduled - announce_lead_days`` (Default 365 T) angenommen und **im Feld ``note`` markiert**.
Damit sieht der Backtest nie ein Release, das damals nicht absehbar war; er sieht es nur evtl.
*früher* als real angekündigt (konservativ Richtung Vorsicht).

Ohne ``FRED_API_KEY`` (kostenlos: https://fred.stlouisfed.org/docs/api/api_key.html):
``status()`` == ``UNAVAILABLE``, ``fetch_macro`` / ``release_calendar`` werfen
``AdapterUnavailable`` — **nichts wird erfunden, nichts simuliert**.
"""

from __future__ import annotations

import dataclasses
import os
from datetime import UTC, datetime, timedelta

import httpx

from trading_agent.core.enums import DataKind, NewsImpact
from trading_agent.core.models import MacroEvent, NewsEvent
from trading_agent.core.time import ensure_utc, parse_timestamp
from trading_agent.data.providers.adapter_base import AdapterInfo, CredentialSpec, LiveDataAdapter
from trading_agent.net.client import HttpClient, NetError

_BASE = "https://api.stlouisfed.org/fred"
_FAR_FUTURE = "9999-12-31"


class AdapterUnavailable(RuntimeError):
    """Der Adapter kann die Anfrage nicht bedienen (fehlende Credentials / Quelle offline).
    **Kein Fallback auf erfundene Daten** — der Aufrufer muss den Zustand als NOT_AVAILABLE
    behandeln."""


@dataclasses.dataclass(frozen=True, slots=True)
class MacroSeriesSpec:
    canonical: str  # z. B. "US_CPI"
    fred_series_id: str  # z. B. "CPIAUCSL"
    event_type: str  # NewsEvent.event_type
    impact: NewsImpact
    unit: str
    release_id: int | None = None  # fred/releases/dates — falls für den Kalender relevant


# Kanonische High-Impact-Serien. `release_id`: FRED-Release, dessen `dates`-Endpunkt die
# geplanten Veröffentlichungszeitpunkte liefert (10 = CPI, 21 = Personal Income & Outlays /
# PCE, 50 = Employment Situation / NFP + Unemployment, 53 = GDP, 17 = H.15 Selected Interest
# Rates). FOMC-/EZB-Sitzungstermine sind KEIN FRED-Release → `release_id=None`, der Kalender
# fällt dort auf `fetch_macro`-Vintages zurück.
SERIES: dict[str, MacroSeriesSpec] = {
    "US_CPI": MacroSeriesSpec("US_CPI", "CPIAUCSL", "US_CPI", NewsImpact.HIGH, "index_sa", 10),
    "US_CORE_CPI": MacroSeriesSpec(
        "US_CORE_CPI", "CPILFESL", "US_CORE_CPI", NewsImpact.HIGH, "index_sa", 10
    ),
    "US_PCE": MacroSeriesSpec("US_PCE", "PCEPI", "US_PCE", NewsImpact.HIGH, "index_sa", 21),
    "US_CORE_PCE": MacroSeriesSpec(
        "US_CORE_PCE", "PCEPILFE", "US_CORE_PCE", NewsImpact.HIGH, "index_sa", 21
    ),
    "US_NFP": MacroSeriesSpec("US_NFP", "PAYEMS", "US_NFP", NewsImpact.HIGH, "thousands_sa", 50),
    "US_UNEMPLOYMENT": MacroSeriesSpec(
        "US_UNEMPLOYMENT", "UNRATE", "US_UNEMPLOYMENT", NewsImpact.MEDIUM, "percent_sa", 50
    ),
    "US_GDP": MacroSeriesSpec(
        "US_GDP", "GDPC1", "US_GDP", NewsImpact.MEDIUM, "bn_chained_2017_saar", 53
    ),
    "US_RETAIL_SALES": MacroSeriesSpec(
        "US_RETAIL_SALES", "RSAFS", "US_RETAIL_SALES", NewsImpact.MEDIUM, "mn_usd_sa", None
    ),
    "FED_FUNDS_TARGET_UPPER": MacroSeriesSpec(
        "FED_FUNDS_TARGET_UPPER", "DFEDTARU", "FOMC_RATE", NewsImpact.HIGH, "percent", 17
    ),
    "ECB_DEPOSIT_RATE": MacroSeriesSpec(
        "ECB_DEPOSIT_RATE", "ECBDFR", "ECB_RATE", NewsImpact.HIGH, "percent", None
    ),
    # Markt-Reihen für den Cross-Asset-Kontext (täglich, praktisch nicht revidiert — FRED liefert
    # sie trotzdem mit realtime_start, also bleibt der PIT-Pfad identisch).
    "DXY": MacroSeriesSpec("DXY", "DTWEXBGS", "DXY", NewsImpact.LOW, "index_broad_usd", None),
    "US10Y": MacroSeriesSpec("US10Y", "DGS10", "US10Y", NewsImpact.LOW, "percent", None),
    "VIX": MacroSeriesSpec("VIX", "VIXCLS", "VIX", NewsImpact.LOW, "index", None),
    "US02Y": MacroSeriesSpec("US02Y", "DGS2", "US02Y", NewsImpact.LOW, "percent", None),
}

#: kanonische Serien, die den Cross-Asset-Kontext speisen (keine Makro-„Releases")
CROSS_ASSET_SERIES: tuple[str, ...] = ("DXY", "US10Y", "US02Y", "VIX")


class FredAlfredProvider(LiveDataAdapter):
    """PIT-Makrodaten. Async REST über den gemeinsamen ``HttpClient`` (Rate-Limit + Retry +
    Circuit-Breaker). Credentials nur über ENV (``FRED_API_KEY``), nie im Code."""

    def __init__(
        self,
        *,
        api_key_env: str = "FRED_API_KEY",
        announce_lead_days: int = 365,
        client: HttpClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            AdapterInfo(
                name="fred_alfred",
                asset_classes=("crypto", "forex", "equity", "gold"),
                data_kinds=(DataKind.MACRO, DataKind.NEWS),
                modes=("historical",),
                credentials=CredentialSpec(
                    provider="fred_alfred",
                    env_vars=(api_key_env,),
                    read_only=True,
                    note="FRED/ALFRED API-Key (kostenlos). Nur Lesezugriff, keine Orders.",
                ),
                redistribution_allowed=False,
                note=(
                    "ALFRED-Vintages = echtes PIT (realtime_start). Release-Ankündigungszeit "
                    f"konservativ genähert: scheduled - {announce_lead_days}d."
                ),
            )
        )
        self._api_key_env = api_key_env
        self._announce_lead = timedelta(days=announce_lead_days)
        self._transport = transport
        self._client = client
        self._owns_client = client is None

    # ---- intern --------------------------------------------------------------
    def _key(self) -> str:
        key = os.environ.get(self._api_key_env)
        if not key:
            raise AdapterUnavailable(
                f"{self._api_key_env} nicht gesetzt — FRED/ALFRED ist NOT_AVAILABLE. "
                "Keine Simulation."
            )
        return key

    def _http(self) -> HttpClient:
        if self._client is None:
            self._client = HttpClient(
                _BASE, name="fred", rate_per_sec=8.0, transport=self._transport
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, object]:
        params = {**params, "api_key": self._key(), "file_type": "json"}
        try:
            payload = await self._http().get_json(path, params)
        except NetError as exc:
            self._fail(str(exc))
            raise AdapterUnavailable(f"FRED-Request fehlgeschlagen: {exc}") from exc
        if not isinstance(payload, dict):
            self._fail("unerwartete FRED-Antwort")
            raise AdapterUnavailable("unerwartete FRED-Antwort")
        return payload

    # ---- öffentlich ---------------------------------------------------------
    async def fetch_macro(
        self,
        canonical: str,
        start: datetime,
        end: datetime,
        *,
        as_of: datetime | None = None,
    ) -> list[MacroEvent]:
        """Alle Vintages einer Serie für Referenzperioden in ``[start, end)``.

        ``available_time`` = ``realtime_start`` der Vintage (echtes PIT). ``as_of`` filtert
        zusätzlich Vintages heraus, die zum Replay-Zeitpunkt noch nicht bekannt waren.
        """
        spec = SERIES.get(canonical.upper())
        if spec is None:
            raise KeyError(f"unbekannte kanonische Serie: {canonical}")
        start, end = ensure_utc(start), ensure_utc(end)
        as_of_dt = ensure_utc(as_of) if as_of is not None else None

        payload = await self._get(
            "/series/observations",
            {
                "series_id": spec.fred_series_id,
                "realtime_start": "1776-07-04",
                "realtime_end": _FAR_FUTURE,
                "observation_start": start.date().isoformat(),
                "observation_end": end.date().isoformat(),
                "sort_order": "asc",
            },
        )
        obs = payload.get("observations", [])
        if not isinstance(obs, list):
            raise AdapterUnavailable("FRED: observations fehlt")

        # je (Referenzperiode) die Vintages nach realtime_start ordnen → revision-Zähler
        by_period: dict[str, list[dict[str, str]]] = {}
        for row in obs:
            if not isinstance(row, dict):
                continue
            if row.get("value") in (None, ".", ""):
                continue
            by_period.setdefault(str(row["date"]), []).append(row)

        events: list[MacroEvent] = []
        for period, rows in by_period.items():
            ref = parse_timestamp(period)
            rows.sort(key=lambda r: str(r["realtime_start"]))
            for rev, row in enumerate(rows):
                avail = _release_datetime(str(row["realtime_start"]))
                if avail < ref:
                    # FRED gibt realtime_start als Datum; ein Erstwert kann am selben Tag oder
                    # später kommen, nie davor. Schutz gegen Datums-Rundung.
                    avail = ref
                if as_of_dt is not None and avail > as_of_dt:
                    break  # spätere Revisionen erst recht unbekannt
                events.append(
                    MacroEvent(
                        series_id=spec.canonical,
                        reference_period=ref,
                        value=float(row["value"]),
                        available_time=avail,
                        revision=rev,
                        unit=spec.unit,
                        source="fred_alfred",
                        ingested_at=datetime.now(UTC),
                    )
                )
        self._ok()
        return sorted(events, key=lambda e: (e.available_time, e.revision))

    async def release_calendar(
        self,
        canonical: str,
        start: datetime,
        end: datetime,
        *,
        as_of: datetime | None = None,
    ) -> list[NewsEvent]:
        """Geplante Veröffentlichungszeitpunkte als ``NewsEvent`` (``actual=None``) für den
        Pre-Positioning-Ban. ``available_time`` = ``scheduled - announce_lead_days``
        (**konservative Näherung**, im Adapter-``note`` markiert)."""
        spec = SERIES.get(canonical.upper())
        if spec is None:
            raise KeyError(f"unbekannte kanonische Serie: {canonical}")
        if spec.release_id is None:
            raise AdapterUnavailable(
                f"{canonical}: kein FRED-Release-Kalender (FOMC/EZB-Termine separat pflegen)"
            )
        start, end = ensure_utc(start), ensure_utc(end)
        as_of_dt = ensure_utc(as_of) if as_of is not None else None

        payload = await self._get(
            f"/release/dates?release_id={spec.release_id}",
            {
                "realtime_start": "1776-07-04",
                "realtime_end": _FAR_FUTURE,
                "include_release_dates_with_no_data": "true",
                "sort_order": "asc",
            },
        )
        dates = payload.get("release_dates", [])
        if not isinstance(dates, list):
            raise AdapterUnavailable("FRED: release_dates fehlt")

        out: list[NewsEvent] = []
        for row in dates:
            if not isinstance(row, dict):
                continue
            sched = _release_datetime(str(row["date"]))
            if not (start <= sched < end):
                continue
            avail = sched - self._announce_lead
            if as_of_dt is not None and avail > as_of_dt:
                continue
            out.append(
                NewsEvent(
                    event_id=f"{spec.event_type}:{sched.date().isoformat()}",
                    event_type=spec.event_type,
                    impact=spec.impact,
                    scheduled_time=sched,
                    available_time=avail,
                    affected_symbols=[],
                    actual=None,
                    forecast=None,
                    previous=None,
                )
            )
        self._ok()
        return sorted(out, key=lambda e: e.scheduled_time)


def _release_datetime(date_str: str) -> datetime:
    """FRED liefert nur ein Datum. Konvention: 12:30 UTC (~08:30 ET, der übliche
    US-Makro-Veröffentlichungszeitpunkt). Dokumentiert, nicht erfunden — die Uhrzeit ist eine
    bewusste konservative Konvention, kein realer Zeitstempel aus der Quelle."""
    d = parse_timestamp(date_str)
    return d.replace(hour=12, minute=30, second=0, microsecond=0)


__all__ = [
    "CROSS_ASSET_SERIES",
    "SERIES",
    "AdapterUnavailable",
    "FredAlfredProvider",
    "MacroSeriesSpec",
]
