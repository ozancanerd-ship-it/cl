"""Market Breadth (Masterplan §21).

Aggregiert die Bewegung **vieler** Instrumente zu einer Marktbreite-Kennzahl:
Advancers/Decliners, Anteil über der 20-/50-SMA, neue Hochs/Tiefs über ein Lookback.
Läuft auf den Daten, die ohnehin vorliegen (Multi-Asset-OHLCV) — **kein externer Feed nötig**.

Point-in-Time: nur Bars mit ``close_time <= as_of``. Zu wenige Instrumente / zu kurze Historie
→ ``regime = UNKNOWN`` (kein geratenes Signal).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from trading_agent.core.models import OHLCV
from trading_agent.core.time import ensure_utc

_MIN_INSTRUMENTS = 5


class BreadthRegime(StrEnum):
    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk_off"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class MarketBreadth:
    as_of: datetime
    universe_size: int
    evaluated: int  # Instrumente mit ausreichender Historie
    advancers: int
    decliners: int
    unchanged: int
    pct_above_sma20: float | None
    pct_above_sma50: float | None
    new_highs: int
    new_lows: int
    breadth_score: float  # -1..1
    regime: BreadthRegime
    detail: str = ""

    @property
    def advance_decline_ratio(self) -> float | None:
        return self.advancers / self.decliners if self.decliners else None

    def as_dict(self) -> dict[str, object]:
        return {
            "as_of": self.as_of.isoformat(),
            "universe_size": self.universe_size,
            "evaluated": self.evaluated,
            "advancers": self.advancers,
            "decliners": self.decliners,
            "unchanged": self.unchanged,
            "pct_above_sma20": self.pct_above_sma20,
            "pct_above_sma50": self.pct_above_sma50,
            "new_highs": self.new_highs,
            "new_lows": self.new_lows,
            "breadth_score": round(self.breadth_score, 4),
            "regime": self.regime.value,
            "detail": self.detail,
        }


def _sma(values: Sequence[float], n: int) -> float | None:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def compute_market_breadth(
    series: dict[str, Sequence[OHLCV]],
    *,
    as_of: datetime,
    lookback_highlow: int = 20,
    min_instruments: int = _MIN_INSTRUMENTS,
) -> MarketBreadth:
    cutoff = ensure_utc(as_of)
    universe = len(series)
    adv = dec = unch = 0
    above20 = above50 = 0
    have20 = have50 = 0
    nh = nl = 0
    evaluated = 0

    for bars in series.values():
        closes = [b.close for b in bars if ensure_utc(b.close_time) <= cutoff]
        highs = [b.high for b in bars if ensure_utc(b.close_time) <= cutoff]
        lows = [b.low for b in bars if ensure_utc(b.close_time) <= cutoff]
        if len(closes) < 2:
            continue
        evaluated += 1

        if closes[-1] > closes[-2]:
            adv += 1
        elif closes[-1] < closes[-2]:
            dec += 1
        else:
            unch += 1

        s20 = _sma(closes, 20)
        if s20 is not None:
            have20 += 1
            above20 += int(closes[-1] > s20)
        s50 = _sma(closes, 50)
        if s50 is not None:
            have50 += 1
            above50 += int(closes[-1] > s50)

        if len(closes) > lookback_highlow:
            window_hi = max(highs[-(lookback_highlow + 1) : -1])
            window_lo = min(lows[-(lookback_highlow + 1) : -1])
            if highs[-1] >= window_hi:
                nh += 1
            if lows[-1] <= window_lo:
                nl += 1

    pct20 = (above20 / have20) if have20 else None
    pct50 = (above50 / have50) if have50 else None

    if evaluated < min_instruments:
        return MarketBreadth(
            as_of=cutoff,
            universe_size=universe,
            evaluated=evaluated,
            advancers=adv,
            decliners=dec,
            unchanged=unch,
            pct_above_sma20=pct20,
            pct_above_sma50=pct50,
            new_highs=nh,
            new_lows=nl,
            breadth_score=0.0,
            regime=BreadthRegime.UNKNOWN,
            detail=f"nur {evaluated} Instrumente mit Historie (< {min_instruments})",
        )

    ad_component = (adv - dec) / evaluated
    sma_component = ((pct20 or 0.5) - 0.5) * 2.0 if pct20 is not None else 0.0
    hl_component = (nh - nl) / evaluated
    score = 0.5 * ad_component + 0.3 * sma_component + 0.2 * hl_component
    score = max(-1.0, min(1.0, score))

    if score >= 0.25:
        regime = BreadthRegime.RISK_ON
    elif score <= -0.25:
        regime = BreadthRegime.RISK_OFF
    else:
        regime = BreadthRegime.NEUTRAL

    return MarketBreadth(
        as_of=cutoff,
        universe_size=universe,
        evaluated=evaluated,
        advancers=adv,
        decliners=dec,
        unchanged=unch,
        pct_above_sma20=pct20,
        pct_above_sma50=pct50,
        new_highs=nh,
        new_lows=nl,
        breadth_score=score,
        regime=regime,
        detail=f"A/D {adv}/{dec} · >SMA20 {pct20:.0%} · NH/NL {nh}/{nl}"
        if pct20 is not None
        else f"A/D {adv}/{dec} · NH/NL {nh}/{nl}",
    )


__all__ = ["BreadthRegime", "MarketBreadth", "compute_market_breadth"]
