"""Objektive Confirmation-Entry-Muster (``SPEC-ADDENDUM-0.1.1.md`` §2, ``0.1.1`` C7).

Gilt **ausschließlich** für ``setups.SMC-SWEEP-REV-01.entry.mode = confirmation_market``
(``SMC-SWEEP-REV-01.md`` §9). Timeframe = ``entry.confirmation_tf`` (M1). Nur ``confirmed`` M1-Bars.

Drei Muster — **und nur diese** (§2.5): Engulfing, Pin Bar, Minor-CHoCH auf M1. Die Confirmation ist
ein **GATE** für den Entry im ``confirmation_market``-Modus:

* **kein** eigenständiger Score-Faktor,
* **kein** Ersatz für den §7-CHoCH/BOS auf dem Struktur-TF (der bleibt Pflicht-Kettenglied),
* sie löst **nicht allein** ``BUY``/``SELL`` aus.

Volle Kette: Setup-FSM → Location-Gate → RR-Gate → **Confirmation** → Confluence → Veto → Score →
Confidence → Risk → Dynamic Signal.

Point-in-time / look-ahead-frei:
* nur M1-Bars mit ``close_time <= information_cutoff``;
* Confirmation zählt erst ab ``since`` (Zeitpunkt, ab dem der Kandidat ``ARMED`` ist — i. d. R. der
  §7-Strukturbruch); frühere M1-Bars dienen nur als Kontext für den Minor-CHoCH;
* die Muster-Erkennung nutzt ausschließlich die Confirmation-Bar und ihre Vorgeschichte;
* ``entry_ref_price`` = ``open`` der **nächsten** M1-Bar ist eine reine Ausführungsreferenz und
  nicht Teil der Erkennung;
* fehlt M1 komplett ⇒ **keine** Confirmation (kein Absturz), der Kandidat bleibt ``ARMED`` bis Expiry.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import datetime

from trading_agent.analysis.mtf import MtfContext
from trading_agent.core.enums import (
    ConfirmationPattern,
    Direction,
    Polarity,
    StructureBreakKind,
    Timeframe,
    ZoneKind,
)
from trading_agent.core.models import OHLCV
from trading_agent.core.time import ensure_utc
from trading_agent.core.version import STRATEGY_VERSION
from trading_agent.strategy.primitives.atr import atr_at_index, atr_series
from trading_agent.strategy.primitives.models import FVG, OrderBlock
from trading_agent.strategy.primitives.structure import structure_breaks
from trading_agent.strategy.primitives.swings import detect_swings
from trading_agent.strategy.setup_detection import SetupCandidate

EntryZone = FVG | OrderBlock


# --------------------------------------------------------------------------------- Parameter


@dataclasses.dataclass(frozen=True, slots=True)
class ConfirmationParams:
    """``PROPOSED DEFAULT`` aus ``SPEC-ADDENDUM-0.1.1.md`` §2 (empirisch zu validieren)."""

    confirmation_timeframe: Timeframe = Timeframe.M1
    atr_period: int = 14
    require_zone_contact: bool = True

    # §2.1 Engulfing
    engulf_tol_atr: float = 0.05
    engulf_min_body_atr: float = 0.6
    engulf_min_body_ratio: float = 1.0

    # §2.2 Pin Bar / Rejection
    pin_min_wick_ratio: float = 2.0
    pin_min_wick_range_frac: float = 0.6
    pin_max_opp_wick_frac: float = 0.2
    pin_pierce_tol_atr: float = 0.15
    pin_min_range_atr: float = 0.5

    # §2.3 Minor-CHoCH auf confirmation_tf
    choch_swing_left: int = 1
    choch_swing_right: int = 1
    choch_min_swings: int = 1
    # choch_min_leg_atr aus primitives.md §1 geerbt; auf M1 ggf. kleiner (Kalibrierung)
    choch_min_leg_atr: float = 0.5
    choch_buffer_atr: float = 0.0
    choch_zone_pad_atr: float = 0.5


# ------------------------------------------------------------------------------- Ausgabe


@dataclasses.dataclass(frozen=True, slots=True)
class EntryConfirmation:
    """Ein erkanntes Confirmation-Muster in der Entry-Zone (``SPEC-ADDENDUM-0.1.1.md`` §2.4)."""

    pattern: ConfirmationPattern
    timeframe: Timeframe
    bar_timestamp: datetime  # open_time der Confirmation-Bar; == information_cutoff (§2.4)
    direction: Polarity  # == D
    strength: float  # 0..1, muster-spezifisch
    zone_kind: ZoneKind
    zone_id: str
    entry_ref_price: float | None  # open der nächsten M1-Bar (Ausführungsreferenz, nicht Erkennung)
    strategy_version: str = STRATEGY_VERSION

    @property
    def confirmation_id(self) -> str:
        return f"CONF-{self.pattern.value}-{self.timeframe.value}-{self.bar_timestamp.isoformat()}"

    @property
    def information_cutoff(self) -> datetime:
        return self.bar_timestamp


@dataclasses.dataclass(frozen=True, slots=True)
class ConfirmationScan:
    """Ergebnis der Confirmation-Suche für **eine** Entry-Zone / Richtung."""

    confirmed: bool
    direction: Direction
    zone_id: str
    zone_kind: ZoneKind
    checked_through: datetime | None
    confirmations: tuple[EntryConfirmation, ...] = ()
    note: str = ""
    strategy_version: str = STRATEGY_VERSION

    @property
    def primary(self) -> EntryConfirmation | None:
        """Früheste Confirmation-Bar; bei Gleichstand das stärkste Muster."""
        return self.confirmations[0] if self.confirmations else None


# ------------------------------------------------------------------------------- öffentlich


def find_confirmation(
    zone: EntryZone,
    direction: Direction,
    m1_bars: Sequence[OHLCV],
    *,
    now: datetime | None = None,
    since: datetime | None = None,
    instrument: str | None = None,
    params: ConfirmationParams | None = None,
) -> ConfirmationScan:
    """Sucht Engulfing / Pin / Minor-CHoCH in ``zone`` und Richtung ``direction`` auf M1.

    ``now`` = ``information_cutoff`` (Default: letzte M1-``close_time``). ``since`` = frühester
    zulässiger Confirmation-Zeitpunkt (i. d. R. der §7-Strukturbruch). ``instrument`` filtert die
    M1-Serie (Bindung an den M5/MTF-Kontext).
    """
    p = params or ConfirmationParams()
    pol = Polarity.of(direction)
    zlow, zhigh, zmid = _zone_bounds(zone)
    zid = _zone_id(zone)
    zkind = zone.kind

    m1 = sorted(
        (b for b in m1_bars if b.timeframe is p.confirmation_timeframe),
        key=lambda b: b.open_time,
    )
    if instrument is not None:
        m1 = [b for b in m1 if b.instrument == instrument]
    if not m1:
        return ConfirmationScan(
            False,
            direction,
            zid,
            zkind,
            None,
            note="keine M1-Daten – confirmation_market nicht möglich",
        )

    cutoff = ensure_utc(now) if now is not None else m1[-1].close_time
    m1 = [b for b in m1 if b.close_time <= cutoff]  # nur abgeschlossene Bars
    if not m1:
        return ConfirmationScan(
            False,
            direction,
            zid,
            zkind,
            cutoff,
            note="keine abgeschlossene M1-Bar <= information_cutoff",
        )

    since_u = ensure_utc(since) if since is not None else None
    atr_ser = atr_series(m1, p.atr_period)

    found: list[EntryConfirmation] = []
    for i, bar in enumerate(m1):
        if since_u is not None and bar.open_time < since_u:
            continue
        a = atr_at_index(atr_ser, i) or 0.0
        if a <= 0.0 or bar.range <= 0.0:
            continue
        if p.require_zone_contact and not (bar.low <= zhigh and bar.high >= zlow):
            continue
        nxt = m1[i + 1].open if i + 1 < len(m1) else None
        prev = m1[i - 1] if i > 0 else None
        for pattern, strength in _detect_all(m1, i, prev, bar, zlow, zhigh, zmid, pol, a, p):
            found.append(
                EntryConfirmation(
                    pattern=pattern,
                    timeframe=p.confirmation_timeframe,
                    bar_timestamp=bar.open_time,
                    direction=pol,
                    strength=strength,
                    zone_kind=zkind,
                    zone_id=zid,
                    entry_ref_price=nxt,
                )
            )

    if not found:
        note = "kein Muster in der Zone"
        if since_u is not None and all(b.open_time < since_u for b in m1):
            note = "keine M1-Bar seit ARMED"
        return ConfirmationScan(False, direction, zid, zkind, cutoff, note=note)

    found.sort(key=lambda c: (c.bar_timestamp, -c.strength, c.pattern.value))
    return ConfirmationScan(True, direction, zid, zkind, cutoff, tuple(found))


def confirmation_for_candidate(
    mtf: MtfContext,
    candidate: SetupCandidate,
    m1_bars: Sequence[OHLCV],
    *,
    params: ConfirmationParams | None = None,
) -> ConfirmationScan:
    """Bindet die M1-Confirmation an den MTF-Kontext + einen ``ARMED``-Kandidaten.

    ``since`` = §7-Strukturbruch (Confirmation erst nach dem Struktur-Shift). Instrument-/Cutoff-
    Bindung an ``mtf``. Nur sinnvoll, wenn der Kandidat ``ARMED`` ist und eine Entry-Zone hat.
    """
    zone = candidate.entry_zone
    if not candidate.is_armed or zone is None:
        return ConfirmationScan(
            False,
            candidate.direction,
            candidate.setup_id,
            ZoneKind.FVG,
            mtf.information_cutoff,
            note="Kandidat nicht ARMED / keine Entry-Zone",
        )

    since = (
        candidate.structure_break.break_bar_timestamp
        if candidate.structure_break is not None
        else None
    )
    return find_confirmation(
        zone,
        candidate.direction,
        m1_bars,
        now=mtf.information_cutoff,
        since=since,
        instrument=mtf.instrument,
        params=params,
    )


# ------------------------------------------------------------------------------- Detektoren


def detect_engulfing(
    prev: OHLCV | None,
    bar: OHLCV,
    direction: Polarity,
    atr: float,
    params: ConfirmationParams | None = None,
) -> float | None:
    """§2.1 — ``strength`` (0..1), wenn ``bar`` ein Engulfing in ``direction`` ist (Vorbar ``prev``)."""
    if prev is None or atr <= 0.0 or bar.range <= 0.0:
        return None
    p = params or ConfirmationParams()
    body_b = abs(bar.close - bar.open)
    body_p = abs(prev.close - prev.open)
    tol = p.engulf_tol_atr * atr

    if direction is Polarity.BULLISH:
        if not (prev.close < prev.open):  # (1) Vorbar bearisch
            return None
        if not (bar.close > bar.open):  # (2) aktuelle Bar bullisch
            return None
        if not (bar.open <= prev.close + tol):  # (3a) open[b] <= close[p]
            return None
        if not (bar.close >= prev.open - tol):  # (3b) close[b] >= open[p]
            return None
        if not (bar.close >= prev.open):  # (5) Close über dem gesamten Vorbar-Body
            return None
    else:  # BEARISH — spiegelbildlich
        if not (prev.close > prev.open):
            return None
        if not (bar.close < bar.open):
            return None
        if not (bar.open >= prev.close - tol):
            return None
        if not (bar.close <= prev.open + tol):
            return None
        if not (bar.close <= prev.open):
            return None

    if body_b < p.engulf_min_body_atr * atr:  # (4) Mindestgröße absolut
        return None
    if body_b < p.engulf_min_body_ratio * body_p:  # (4) Mindestgröße relativ zum Vorbar
        return None

    size_term = min(1.0, body_b / (2.0 * p.engulf_min_body_atr * atr))
    ratio_term = min(1.0, (body_b / max(body_p, 1e-9)) / (2.0 * max(p.engulf_min_body_ratio, 1e-9)))
    return round(0.5 * size_term + 0.5 * ratio_term, 6)


def detect_pin(
    bar: OHLCV,
    zone_low: float,
    zone_high: float,
    direction: Polarity,
    atr: float,
    params: ConfirmationParams | None = None,
) -> float | None:
    """§2.2 — ``strength`` (0..1), wenn ``bar`` eine Pin/Rejection an der Zone in ``direction`` ist."""
    if atr <= 0.0 or bar.range <= 0.0:
        return None
    p = params or ConfirmationParams()
    rng = bar.range
    body = abs(bar.close - bar.open)
    upper_wick = bar.high - max(bar.open, bar.close)
    lower_wick = min(bar.open, bar.close) - bar.low
    zone_mid = 0.5 * (zone_low + zone_high)

    if direction is Polarity.BULLISH:
        signal_wick, opp_wick = lower_wick, upper_wick
        pierces = bar.low <= zone_low + p.pin_pierce_tol_atr * atr  # (4)
        body_held = min(bar.open, bar.close) >= zone_mid  # (5)
    else:  # BEARISH — spiegelbildlich
        signal_wick, opp_wick = upper_wick, lower_wick
        pierces = bar.high >= zone_high - p.pin_pierce_tol_atr * atr
        body_held = max(bar.open, bar.close) <= zone_mid

    if signal_wick < p.pin_min_wick_ratio * body:  # (1)
        return None
    if signal_wick < p.pin_min_wick_range_frac * rng:  # (2)
        return None
    if opp_wick > p.pin_max_opp_wick_frac * rng:  # (3)
        return None
    if not pierces:  # (4)
        return None
    if not body_held:  # (5)
        return None
    if rng < p.pin_min_range_atr * atr:  # (6)
        return None

    denom = 1.0 - p.pin_min_wick_range_frac
    frac_term = (
        min(1.0, max(0.0, (signal_wick / rng - p.pin_min_wick_range_frac) / denom))
        if denom > 0.0
        else 1.0
    )
    size_term = min(1.0, rng / (2.0 * p.pin_min_range_atr * atr))
    return round(0.6 * frac_term + 0.4 * size_term, 6)


def detect_minor_choch(
    m1_window: Sequence[OHLCV],
    index: int,
    zone_low: float,
    zone_high: float,
    direction: Polarity,
    atr: float,
    params: ConfirmationParams | None = None,
) -> float | None:
    """§2.3 — Minor-CHoCH (``primitives.md`` §3) auf M1 mit reduziertem Fraktal, Richtung ``direction``.

    ``index`` = Index der Confirmation-Bar in ``m1_window``. Der CHoCH muss **auf dieser Bar**
    schließen und einen Swing innerhalb der Zone (± ``choch_zone_pad_atr``) brechen.
    """
    if atr <= 0.0 or index <= 0 or index >= len(m1_window):
        return None
    p = params or ConfirmationParams()
    window = list(m1_window[: index + 1])
    swings = detect_swings(
        window,
        p.confirmation_timeframe,
        left=p.choch_swing_left,
        right=p.choch_swing_right,
        min_leg_atr=p.choch_min_leg_atr,
        atr_period=p.atr_period,
    )
    breaks = structure_breaks(
        window,
        swings,
        p.confirmation_timeframe,
        min_swings=max(1, p.choch_min_swings),
        choch_buffer_atr=p.choch_buffer_atr,
        atr_period=p.atr_period,
    )
    pad = p.choch_zone_pad_atr * atr
    bar_ts = window[index].open_time
    for b in breaks:
        if b.kind is not StructureBreakKind.CHOCH or b.direction is not direction:
            continue
        if b.break_bar_timestamp != bar_ts:  # CHoCH muss auf der Confirmation-Bar schließen
            continue
        if b.broken_swing is None:
            continue
        if not (zone_low - pad <= b.broken_swing.price <= zone_high + pad):  # (3)
            continue
        return round(min(1.0, 0.5 + 0.5 * b.break_distance_atr), 6)
    return None


# ------------------------------------------------------------------------------- intern


def _detect_all(
    m1: Sequence[OHLCV],
    i: int,
    prev: OHLCV | None,
    bar: OHLCV,
    zlow: float,
    zhigh: float,
    zmid: float,
    pol: Polarity,
    atr: float,
    p: ConfirmationParams,
) -> list[tuple[ConfirmationPattern, float]]:
    out: list[tuple[ConfirmationPattern, float]] = []
    e = detect_engulfing(prev, bar, pol, atr, p)
    if e is not None:
        out.append((ConfirmationPattern.ENGULFING, e))
    pin = detect_pin(bar, zlow, zhigh, pol, atr, p)
    if pin is not None:
        out.append((ConfirmationPattern.PIN, pin))
    ch = detect_minor_choch(m1, i, zlow, zhigh, pol, atr, p)
    if ch is not None:
        out.append((ConfirmationPattern.MINOR_CHOCH, ch))
    return out


def _zone_bounds(zone: EntryZone) -> tuple[float, float, float]:
    lo, hi = float(zone.zone_low), float(zone.zone_high)
    return lo, hi, 0.5 * (lo + hi)


def _zone_id(zone: EntryZone) -> str:
    if isinstance(zone, OrderBlock):
        return zone.zone_id
    return f"FVG-{zone.timeframe.value}-{zone.direction.value}-{zone.bar_index}"


__all__ = [
    "ConfirmationParams",
    "ConfirmationScan",
    "EntryConfirmation",
    "EntryZone",
    "confirmation_for_candidate",
    "detect_engulfing",
    "detect_minor_choch",
    "detect_pin",
    "find_confirmation",
]
