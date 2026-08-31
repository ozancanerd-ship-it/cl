"""(9) Makro-Regime — **Analyse-Schicht, strikt Point-in-Time, Evidence/Context (kein Gate)**.

Verdichtet PIT-Makro-Zeitreihen (``data.providers.fred_alfred`` → ``MacroEvent`` mit
``available_time``) zu einem :class:`MacroContext`:

* ``rate_cycle``       — Leitzins-Pfad der relevanten Zentralbank (TIGHTENING / EASING / HOLD)
* ``inflation_trend``  — steigt / fällt die Inflationsrate (YoY, sonst MoM-Fallback)
* ``growth_trend``     — Beschäftigungs-/Wachstumsdynamik (NFP-MoM, sonst UNRATE)
* ``risk_sentiment``   — aus Cross-Asset-Proxies (VIX / DXY / Yields), via
  ``data.providers.cross_asset.build_cross_asset_from_macro``

**Point-in-Time zwingend.** Pro Referenzperiode zählt nur die zum ``as_of`` neueste bekannte
Revision (``available_time <= as_of``). Fehlt Historie ⇒ Term = ``UNKNOWN`` — **kein Fake, keine
Extrapolation**.

**Kein Trade-Signal.** Dieses Modul blockiert nichts und erzeugt keine Richtung. Es liefert
Kontext für Confluence/Confidence und die spätere News/Macro-Agent-Fläche.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from trading_agent.core.enums import (
    AssetClass,
    MacroRateCycle,
    MacroRiskSentiment,
    MacroTrend,
)
from trading_agent.core.models import MacroEvent
from trading_agent.core.time import ensure_utc
from trading_agent.core.types import CrossAssetContext
from trading_agent.data.providers.cross_asset import build_cross_asset_from_macro

_EPS = 1e-9

# Zentralbank-Leitzinsreihe je Asset-Klasse (kanonische FRED-Serien-IDs, s. fred_alfred.SERIES).
_RATE_SERIES_BY_ASSET: dict[AssetClass, str] = {
    AssetClass.CRYPTO: "FED_FUNDS_TARGET_UPPER",
    AssetClass.ALTCOIN: "FED_FUNDS_TARGET_UPPER",
    AssetClass.GOLD: "FED_FUNDS_TARGET_UPPER",
    AssetClass.FOREX: "FED_FUNDS_TARGET_UPPER",
    AssetClass.EQUITY: "FED_FUNDS_TARGET_UPPER",
    AssetClass.ETF: "FED_FUNDS_TARGET_UPPER",
}


@dataclasses.dataclass(frozen=True, slots=True)
class MacroParams:
    """Alle Schwellen ``PROPOSED DEFAULT`` (``CALIBRATION_BACKLOG.md``)."""

    rate_series: str | None = None  # None ⇒ aus Asset-Klasse (Default FED_FUNDS_TARGET_UPPER)
    rate_lookback_days: int = 180
    rate_change_eps: float = 0.05  # Prozentpunkte — kleinere Δ = HOLD
    inflation_series: tuple[str, ...] = ("US_CORE_CPI", "US_CPI")
    growth_series: tuple[str, ...] = ("US_NFP", "US_UNEMPLOYMENT")
    trend_eps_rel: float = 0.002  # relative Δ, unter der ein Trend als STABLE gilt
    min_points_trend: int = 4
    vix_series: str = "VIX"
    dxy_series: str = "DXY"
    yield_series: str = "US10Y"


@dataclasses.dataclass(frozen=True, slots=True)
class MacroContext:
    """Verdichtetes Makro-Regime zu einem ``as_of`` — look-ahead-frei, deterministisch."""

    as_of: datetime
    rate_cycle: MacroRateCycle
    inflation_trend: MacroTrend
    growth_trend: MacroTrend
    risk_sentiment: MacroRiskSentiment
    cross_asset: CrossAssetContext
    evidence: tuple[str, ...]

    @property
    def known(self) -> bool:
        return not (
            self.rate_cycle is MacroRateCycle.UNKNOWN
            and self.inflation_trend is MacroTrend.UNKNOWN
            and self.growth_trend is MacroTrend.UNKNOWN
            and self.risk_sentiment is MacroRiskSentiment.UNKNOWN
        )


# --------------------------------------------------------------------------------- intern

_Point = tuple[datetime, float]  # (reference_period, value) — PIT-dedupliziert


def _pit_series(events: Sequence[MacroEvent], as_of: datetime) -> list[_Point]:
    """Pro ``reference_period`` die neueste Revision mit ``available_time <= as_of``,
    aufsteigend nach ``reference_period``."""
    best: dict[datetime, tuple[datetime, float]] = {}
    for e in events:
        avail = ensure_utc(e.available_time)
        if avail > as_of:
            continue
        ref = ensure_utc(e.reference_period)
        cur = best.get(ref)
        if cur is None or avail >= cur[0]:
            best[ref] = (avail, float(e.value))
    return [(ref, val) for ref, (_, val) in sorted(best.items())]


def _first_available(
    series: Mapping[str, Sequence[MacroEvent]], ids: Sequence[str], as_of: datetime
) -> tuple[str, list[_Point]] | None:
    for sid in ids:
        pts = _pit_series(series.get(sid, ()), as_of)
        if len(pts) >= 2:
            return sid, pts
    return None


def _trend(values: Sequence[float], eps_rel: float) -> MacroTrend:
    if len(values) < 2:
        return MacroTrend.UNKNOWN
    base = abs(values[0]) if abs(values[0]) > _EPS else _EPS
    chg = (values[-1] - values[0]) / base
    if chg > eps_rel:
        return MacroTrend.RISING
    if chg < -eps_rel:
        return MacroTrend.FALLING
    return MacroTrend.STABLE


def _yoy(points: Sequence[_Point]) -> list[float]:
    """YoY-Rate aus monatlichen Index-Levels (12-Monats-Abstand). Leer, wenn zu kurz."""
    if len(points) < 13:
        return []
    vals = [v for _, v in points]
    return [
        (vals[i] / vals[i - 12] - 1.0) for i in range(12, len(vals)) if abs(vals[i - 12]) > _EPS
    ]


# --------------------------------------------------------------------------------- Terme


def _rate_cycle(
    points: Sequence[_Point], as_of: datetime, p: MacroParams
) -> tuple[MacroRateCycle, str]:
    if len(points) < 2:
        return MacroRateCycle.UNKNOWN, "rate: keine PIT-Historie"
    # Anker relativ zum Serien-Ende (nicht zu as_of — die Reihe kann vor as_of enden).
    anchor = points[-1][0] - timedelta(days=p.rate_lookback_days)
    past = [v for ref, v in points if ref <= anchor]
    ref_val = past[-1] if past else points[0][1]
    cur = points[-1][1]
    delta = cur - ref_val
    if delta > p.rate_change_eps:
        return (
            MacroRateCycle.TIGHTENING,
            f"rate: +{delta:.2f}pp über ~{p.rate_lookback_days}d ({cur:.2f})",
        )
    if delta < -p.rate_change_eps:
        return (
            MacroRateCycle.EASING,
            f"rate: {delta:.2f}pp über ~{p.rate_lookback_days}d ({cur:.2f})",
        )
    return MacroRateCycle.HOLD, f"rate: Δ {delta:+.2f}pp ~flach ({cur:.2f})"


def _inflation_trend(
    series: Mapping[str, Sequence[MacroEvent]], as_of: datetime, p: MacroParams
) -> tuple[MacroTrend, str]:
    found = _first_available(series, p.inflation_series, as_of)
    if found is None:
        return MacroTrend.UNKNOWN, "inflation: keine PIT-Historie"
    sid, pts = found
    yoy = _yoy(pts)
    if len(yoy) >= p.min_points_trend:
        seg = yoy[-p.min_points_trend :]
        t = _trend(seg, p.trend_eps_rel)
        return t, f"inflation({sid}): YoY {seg[0] * 100:.1f}%→{seg[-1] * 100:.1f}% ⇒ {t.value}"
    seg = [v for _, v in pts[-max(p.min_points_trend, 3) :]]
    t = _trend(seg, p.trend_eps_rel)
    return t, f"inflation({sid}): MoM-Level-Fallback ⇒ {t.value}"


def _growth_trend(
    series: Mapping[str, Sequence[MacroEvent]], as_of: datetime, p: MacroParams
) -> tuple[MacroTrend, str]:
    for sid in p.growth_series:
        pts = _pit_series(series.get(sid, ()), as_of)
        if len(pts) < max(p.min_points_trend, 3):
            continue
        seg = [v for _, v in pts[-p.min_points_trend :]]
        t = _trend(seg, p.trend_eps_rel)
        if (
            sid == "US_UNEMPLOYMENT"
        ):  # invertieren: steigende Arbeitslosigkeit = schwächeres Wachstum
            t = {
                MacroTrend.RISING: MacroTrend.FALLING,
                MacroTrend.FALLING: MacroTrend.RISING,
            }.get(t, t)
        return t, f"growth({sid}): {seg[0]:.1f}→{seg[-1]:.1f} ⇒ {t.value}"
    return MacroTrend.UNKNOWN, "growth: keine PIT-Historie"


def _risk_sentiment(
    cross: CrossAssetContext,
) -> tuple[MacroRiskSentiment, str]:
    if cross.as_of is None:
        return MacroRiskSentiment.UNKNOWN, "risk: keine Cross-Asset-Daten"
    if cross.risk_off:
        return MacroRiskSentiment.RISK_OFF, f"risk: risk_off (VIX {cross.vix})"
    calm = cross.vix is not None and cross.vix < 16.0
    from trading_agent.core.enums import RegimeDirectional

    dxy_up = cross.dxy_trend is RegimeDirectional.TREND_UP
    if calm and not dxy_up:
        return MacroRiskSentiment.RISK_ON, f"risk: VIX {cross.vix} ruhig, DXY nicht steigend"
    return MacroRiskSentiment.NEUTRAL, f"risk: neutral (VIX {cross.vix}, DXY {cross.dxy_trend})"


# --------------------------------------------------------------------------------- öffentlich


def assess_macro(
    series: Mapping[str, Sequence[MacroEvent]],
    *,
    as_of: datetime,
    asset_class: AssetClass = AssetClass.CRYPTO,
    params: MacroParams | None = None,
) -> MacroContext:
    """``series``: kanonische Serien-ID → ``MacroEvent``-Liste (roh, mehrere Revisionen erlaubt).

    Erwartete IDs (soweit vorhanden): ``FED_FUNDS_TARGET_UPPER``, ``US_CORE_CPI`` / ``US_CPI``,
    ``US_NFP`` / ``US_UNEMPLOYMENT``, ``VIX``, ``DXY``, ``US10Y``.
    """
    p = params or MacroParams()
    as_of = ensure_utc(as_of)
    evidence: list[str] = []

    rate_id = p.rate_series or _RATE_SERIES_BY_ASSET.get(asset_class, "FED_FUNDS_TARGET_UPPER")
    rate_pts = _pit_series(series.get(rate_id, ()), as_of)
    rate_cycle, ev = _rate_cycle(rate_pts, as_of, p)
    evidence.append(ev)

    infl, ev = _inflation_trend(series, as_of, p)
    evidence.append(ev)

    growth, ev = _growth_trend(series, as_of, p)
    evidence.append(ev)

    cross = build_cross_asset_from_macro(
        as_of=as_of,
        dxy=list(series.get(p.dxy_series, ())) or None,
        us10y_yield=list(series.get(p.yield_series, ())) or None,
        vix=list(series.get(p.vix_series, ())) or None,
    )
    risk, ev = _risk_sentiment(cross)
    evidence.append(ev)

    return MacroContext(
        as_of=as_of,
        rate_cycle=rate_cycle,
        inflation_trend=infl,
        growth_trend=growth,
        risk_sentiment=risk,
        cross_asset=cross,
        evidence=tuple(evidence),
    )


def unknown_macro(as_of: datetime) -> MacroContext:
    """Expliziter „nichts bekannt"-Kontext (kein Feed) — kein Fake."""
    return MacroContext(
        as_of=ensure_utc(as_of),
        rate_cycle=MacroRateCycle.UNKNOWN,
        inflation_trend=MacroTrend.UNKNOWN,
        growth_trend=MacroTrend.UNKNOWN,
        risk_sentiment=MacroRiskSentiment.UNKNOWN,
        cross_asset=CrossAssetContext(),
        evidence=("keine PIT-Makro-Quelle",),
    )


__all__ = [
    "MacroContext",
    "MacroParams",
    "assess_macro",
    "unknown_macro",
]
