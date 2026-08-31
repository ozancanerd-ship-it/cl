"""Brokerunabhängige Aggregat-Typen der Strategy Engine (Phase 3, ``strategy_version 0.1.1``).

* ``MarketContext`` — Momentaufnahme aller Marktdaten + externem Kontext, die ``strategy.evaluate``
  sehen darf. **Look-ahead-Schutz:** jede Bar in jeder Serie hat ``close_time <= information_cutoff``;
  jedes ``NewsEvent`` hat ``available_time <= information_cutoff``.
  Multi-Timeframe: Basis-Serie ist M5, ``M15/H4/D1`` werden daraus abgeleitet (``0.1.1`` C11).
* ``DerivativesContext`` / ``CrossAssetContext`` / ``NewsContext`` — typisierte Slots für spätere
  Zusatzdaten (Funding/OI/Basis · DXY/Yields/VIX · Economic Calendar). **Aktuell leer** — keine
  erfundenen Live-Daten. Andockpunkte laut ``docs/CONTINUOUS_IMPROVEMENT.md`` §6a.
* ``PortfolioContext`` — optionaler Portfolio-Zustand für Veto V9 / Duplikat-Prüfungen. Fehlt er,
  ist V9 pass-through (``0.1.1`` C9). Der vollständige ``PortfolioState`` kommt in Phase 4.

Verbindliche Definitionen: ``docs/strategy/`` (``backtest-labeling.md`` §1, ``confidence.md``,
``no-trade.md``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from trading_agent.core.enums import Direction, RegimeDirectional, Timeframe
from trading_agent.core.models import OHLCV, NewsEvent
from trading_agent.core.time import ensure_utc


@dataclass(frozen=True, slots=True)
class DerivativesContext:
    """Crypto-Derivate-Kontext (Funding / Open Interest / Basis / CVD). Slots für spätere Phasen.

    Jedes Feld ``None`` ⇒ nicht verfügbar. Werte müssen point-in-time sein
    (``*_as_of <= information_cutoff``, unter Berücksichtigung des Publish-Lags der Quelle).
    """

    funding_rate: float | None = None  # realisierte 8h-Rate des letzten Settlements
    funding_rate_as_of: datetime | None = None
    open_interest: float | None = None
    open_interest_as_of: datetime | None = None
    open_interest_delta_pct: float | None = None  # OI-Änderung über ein Fenster
    basis_pct: float | None = None
    cvd_divergence: float | None = None  # -1..1 (Sweep-CVD-Divergenz), None = nicht berechnet


@dataclass(frozen=True, slots=True)
class CrossAssetContext:
    """Intermarket-Kontext (DXY / Real Yields / VIX / Risk-off). Slots für spätere Phasen."""

    dxy_trend: RegimeDirectional | None = None
    real_yield_10y: float | None = None
    vix: float | None = None
    risk_off: bool = False  # aggregierter Risk-off-Flag (später via VIX/DVOL/Shock)
    as_of: datetime | None = None


@dataclass(frozen=True, slots=True)
class NewsContext:
    """Ereignis-/Kalender-Kontext. ``feed_as_of=None`` ⇒ Feed fehlt (fail-safe ⇒ kein Entry)."""

    events: tuple[NewsEvent, ...] = ()  # nur Events mit available_time <= information_cutoff
    feed_as_of: datetime | None = None
    risk_off: bool = False
    blocking_event_id: str | None = None
    minutes_to_next_high_impact: float | None = None

    @property
    def feed_available(self) -> bool:
        return self.feed_as_of is not None


@dataclass(frozen=True, slots=True)
class MarketContext:
    """Alles, was die Strategy Engine zu einem ``information_cutoff`` sehen darf — nicht mehr.

    ``series`` bildet Timeframe → aufsteigend sortierte, **confirmed** Bars ab. ``base_timeframe``
    (M5) ist die einzige nativ geladene Serie; höhere Timeframes werden per ``data/resample.py``
    daraus abgeleitet, bevor der ``MarketContext`` gebaut wird.
    """

    instrument: str
    base_timeframe: Timeframe
    information_cutoff: datetime
    series: dict[Timeframe, tuple[OHLCV, ...]]
    spread: float | None = None
    account_equity: float | None = None
    derivatives: DerivativesContext = field(default_factory=DerivativesContext)
    cross_asset: CrossAssetContext = field(default_factory=CrossAssetContext)
    news: NewsContext = field(default_factory=NewsContext)

    def __post_init__(self) -> None:
        cutoff = ensure_utc(self.information_cutoff)
        object.__setattr__(self, "information_cutoff", cutoff)
        if self.base_timeframe not in self.series:
            raise ValueError(
                f"MarketContext.series enthält die base_timeframe {self.base_timeframe} nicht"
            )
        for tf, bars in self.series.items():
            for i in range(1, len(bars)):
                if bars[i].open_time <= bars[i - 1].open_time:
                    raise ValueError(f"Serie {tf} ist nicht streng aufsteigend sortiert")
            for b in bars:
                if b.timeframe != tf:
                    raise ValueError(f"Bar mit timeframe {b.timeframe} in Serie {tf}")
                if b.close_time > cutoff:
                    raise ValueError(
                        f"Look-ahead: Bar {tf} schließt {b.close_time.isoformat()} "
                        f"nach information_cutoff {cutoff.isoformat()}"
                    )
        for ev in self.news.events:
            if ev.available_time > cutoff:
                raise ValueError(
                    f"Look-ahead: NewsEvent {ev.event_id} verfügbar ab "
                    f"{ev.available_time.isoformat()} nach information_cutoff {cutoff.isoformat()}"
                )
        for tag, ts in (
            ("derivatives.funding_rate", self.derivatives.funding_rate_as_of),
            ("derivatives.open_interest", self.derivatives.open_interest_as_of),
            ("cross_asset", self.cross_asset.as_of),
            ("news.feed", self.news.feed_as_of),
        ):
            if ts is not None and ensure_utc(ts) > cutoff:
                raise ValueError(
                    f"Look-ahead: {tag} as_of {ts.isoformat()} nach {cutoff.isoformat()}"
                )

    # ---- Zugriff -------------------------------------------------------------------------

    @property
    def timeframes(self) -> tuple[Timeframe, ...]:
        return tuple(sorted(self.series, key=lambda tf: tf.seconds))

    def bars(self, timeframe: Timeframe) -> tuple[OHLCV, ...]:
        return self.series.get(timeframe, ())

    def last(self, timeframe: Timeframe) -> OHLCV | None:
        s = self.series.get(timeframe)
        return s[-1] if s else None

    @property
    def price(self) -> float | None:
        """Letzter bekannter Close der Basis-Serie."""
        last = self.last(self.base_timeframe)
        return last.close if last is not None else None

    def has(self, *timeframes: Timeframe) -> bool:
        return all(self.series.get(tf) for tf in timeframes)


@dataclass(frozen=True, slots=True)
class OpenPositionInfo:
    instrument: str
    direction: Direction
    open_risk_pct: float = 0.0  # offenes 1R-Risiko dieser Position, % Equity
    cluster_id: str | None = None


@dataclass(frozen=True, slots=True)
class PortfolioContext:
    """Minimaler Portfolio-Zustand für Phase-3-Vetos (``0.1.1`` C9).

    Ohne dieses Objekt (``portfolio_context=None`` bei ``evaluate``) sind V9 und die
    Duplikat-/Gegenpositions-Prüfungen **pass-through**. Phase 4 ersetzt es durch den
    vollständigen ``PortfolioState``.
    """

    open_positions: tuple[OpenPositionInfo, ...] = ()
    armed_setups: dict[str, Direction] = field(default_factory=dict)
    total_open_risk_pct: float = 0.0
    cluster_open_risk_pct: float = 0.0
    cluster_cap_pct: float = 1.0
    correlation_threshold: float = 0.70
    static_correlations: dict[tuple[str, str], float] = field(default_factory=dict)

    def open_direction(self, instrument: str) -> Direction | None:
        for p in self.open_positions:
            if p.instrument.upper() == instrument.upper():
                return p.direction
        return None

    def correlation(self, a: str, b: str) -> float:
        au, bu = a.upper(), b.upper()
        if au == bu:
            return 1.0
        lo, hi = (au, bu) if au <= bu else (bu, au)
        if (lo, hi) in self.static_correlations:
            return self.static_correlations[(lo, hi)]
        return self.static_correlations.get((hi, lo), 0.0)


__all__ = [
    "CrossAssetContext",
    "DerivativesContext",
    "MarketContext",
    "NewsContext",
    "OpenPositionInfo",
    "PortfolioContext",
]
