"""Stock Fundamentals (Masterplan §19) — **nur Einzelaktien, nie ETFs**.

Verdichtet Provider-Rohdaten (Bewertung, Wachstum, Profitabilität, Bilanz) zu einem
``FundamentalContext`` mit vier 0–1-Sub-Scores + einem Gesamturteil. Fehlt eine Kennzahl,
fließt sie **nicht** ein (kein Default-Wert) — ist gar nichts da, ist der Kontext ``UNKNOWN``.

Kein Look-ahead: der Aufrufer übergibt nur Kennzahlen, deren Berichtszeitpunkt ``<= as_of`` ist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class FundamentalVerdict(StrEnum):
    STRONG = "strong"
    SOLID = "solid"
    MIXED = "mixed"
    WEAK = "weak"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class StockFundamentals:
    """Provider-neutrale Kennzahlen zu einem Ticker, PIT (Werte mit ``as_of_report <= as_of``)."""

    symbol: str
    as_of_report: datetime
    # Bewertung
    pe: float | None = None
    forward_pe: float | None = None
    peg: float | None = None
    ev_ebitda: float | None = None
    price_to_sales: float | None = None
    # Wachstum (YoY, als Bruchteil: 0.15 = +15 %)
    revenue_growth_yoy: float | None = None
    eps_growth_yoy: float | None = None
    # Profitabilität / Qualität
    gross_margin: float | None = None
    operating_margin: float | None = None
    roe: float | None = None
    fcf_margin: float | None = None
    # Bilanz / Gesundheit
    net_debt_to_ebitda: float | None = None
    current_ratio: float | None = None
    interest_coverage: float | None = None


@dataclass(frozen=True, slots=True)
class FundamentalContext:
    symbol: str
    as_of: datetime
    verdict: FundamentalVerdict
    valuation: float | None  # 0..1 (1 = günstig)
    growth: float | None  # 0..1
    quality: float | None  # 0..1
    health: float | None  # 0..1
    composite: float | None  # 0..1 (gewichtetes Mittel der vorhandenen Sub-Scores)
    evidence: tuple[str, ...] = field(default_factory=tuple)

    @property
    def known(self) -> bool:
        return self.verdict is not FundamentalVerdict.UNKNOWN

    def as_dict(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "as_of": self.as_of.isoformat(),
            "verdict": self.verdict.value,
            "valuation": self.valuation,
            "growth": self.growth,
            "quality": self.quality,
            "health": self.health,
            "composite": self.composite,
            "evidence": list(self.evidence),
        }


def _clip01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _lo_is_good(value: float | None, *, good: float, bad: float) -> float | None:
    """Kleinerer Wert = besser (z. B. KGV). Linear zwischen ``good`` (→1) und ``bad`` (→0)."""
    if value is None:
        return None
    if value <= 0:
        return 0.0  # negatives KGV / EV-EBITDA = kein Qualitätssignal
    return _clip01((bad - value) / (bad - good))


def _hi_is_good(value: float | None, *, good: float, bad: float) -> float | None:
    if value is None:
        return None
    return _clip01((value - bad) / (good - bad))


def _avg(scores: list[float | None]) -> float | None:
    present = [s for s in scores if s is not None]
    return sum(present) / len(present) if present else None


def assess_fundamentals(f: StockFundamentals, *, as_of: datetime) -> FundamentalContext:
    ev: list[str] = []

    valuation = _avg(
        [
            _lo_is_good(f.forward_pe or f.pe, good=12.0, bad=40.0),
            _lo_is_good(f.ev_ebitda, good=8.0, bad=25.0),
            _lo_is_good(f.peg, good=1.0, bad=3.0),
            _lo_is_good(f.price_to_sales, good=2.0, bad=12.0),
        ]
    )
    growth = _avg(
        [
            _hi_is_good(f.revenue_growth_yoy, good=0.25, bad=-0.05),
            _hi_is_good(f.eps_growth_yoy, good=0.25, bad=-0.10),
        ]
    )
    quality = _avg(
        [
            _hi_is_good(f.gross_margin, good=0.60, bad=0.20),
            _hi_is_good(f.operating_margin, good=0.25, bad=0.0),
            _hi_is_good(f.roe, good=0.20, bad=0.0),
            _hi_is_good(f.fcf_margin, good=0.20, bad=0.0),
        ]
    )
    health = _avg(
        [
            _lo_is_good(f.net_debt_to_ebitda, good=1.0, bad=5.0),
            _hi_is_good(f.current_ratio, good=2.0, bad=1.0),
            _hi_is_good(f.interest_coverage, good=8.0, bad=1.5),
        ]
    )

    sub = {"valuation": valuation, "growth": growth, "quality": quality, "health": health}
    for name, score in sub.items():
        if score is not None:
            ev.append(f"{name}={score:.2f}")

    weights = {"valuation": 0.25, "growth": 0.30, "quality": 0.30, "health": 0.15}
    present = {k: v for k, v in sub.items() if v is not None}
    if not present:
        return FundamentalContext(
            symbol=f.symbol,
            as_of=as_of,
            verdict=FundamentalVerdict.UNKNOWN,
            valuation=None,
            growth=None,
            quality=None,
            health=None,
            composite=None,
            evidence=("keine Fundamentaldaten verfügbar",),
        )
    wsum = sum(weights[k] for k in present)
    composite = _clip01(sum(present[k] * weights[k] for k in present) / wsum)

    if composite >= 0.70:
        verdict = FundamentalVerdict.STRONG
    elif composite >= 0.55:
        verdict = FundamentalVerdict.SOLID
    elif composite >= 0.40:
        verdict = FundamentalVerdict.MIXED
    else:
        verdict = FundamentalVerdict.WEAK

    return FundamentalContext(
        symbol=f.symbol,
        as_of=as_of,
        verdict=verdict,
        valuation=valuation,
        growth=growth,
        quality=quality,
        health=health,
        composite=round(composite, 4),
        evidence=tuple(ev),
    )


def unknown_fundamentals(symbol: str, as_of: datetime) -> FundamentalContext:
    return FundamentalContext(
        symbol=symbol,
        as_of=as_of,
        verdict=FundamentalVerdict.UNKNOWN,
        valuation=None,
        growth=None,
        quality=None,
        health=None,
        composite=None,
        evidence=("kein Fundamental-Feed",),
    )


__all__ = [
    "FundamentalContext",
    "FundamentalVerdict",
    "StockFundamentals",
    "assess_fundamentals",
    "unknown_fundamentals",
]
