"""Phase 3 — Confirmation-Entry-Muster (``strategy/price_action.py``, ``SPEC-ADDENDUM-0.1.1`` §2).

Golden + Edge Cases für Engulfing / Pin / Minor-CHoCH, Long/Short-Symmetrie, Point-in-time /
Look-ahead, Determinismus, stabile IDs, M1↔MTF-Bindung. Confirmation ist ein GATE (kein
Score-Faktor) und löst nicht allein BUY/SELL aus.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from trading_agent.core.enums import (
    ConfirmationPattern,
    Direction,
    Polarity,
    Timeframe,
    ZoneKind,
    ZoneState,
)
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time, parse_timestamp
from trading_agent.strategy.price_action import (
    ConfirmationParams,
    ConfirmationScan,
    EntryConfirmation,
    detect_engulfing,
    detect_minor_choch,
    detect_pin,
    find_confirmation,
)
from trading_agent.strategy.primitives.atr import atr_at_index, atr_series
from trading_agent.strategy.primitives.models import FVG, OrderBlock

M1 = Timeframe.M1
T0 = parse_timestamp("2024-06-03T12:00:00Z")
_MIRROR = 200.0

# Entry-Zone: bullische FVG 100.0–101.0 (mid 100.5)
_ZONE = FVG(
    direction=Polarity.BULLISH,
    timeframe=Timeframe.M5,
    zone_low=100.0,
    zone_high=101.0,
    created_bar=T0,
    bar_index=7,
    state=ZoneState.UNMITIGATED,
)


def _bar(t: datetime, o: float, h: float, low: float, c: float) -> OHLCV:
    return OHLCV(
        instrument="BTCUSD",
        timeframe=M1,
        open_time=t,
        close_time=bar_close_time(t, M1),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1.0,
        source="t",
    )


def _rows(
    rows: list[tuple[float, float, float, float]], *, start: datetime = T0, inst: str = "BTCUSD"
) -> list[OHLCV]:
    out: list[OHLCV] = []
    t = start
    for o, h, low, c in rows:
        out.append(
            OHLCV(
                instrument=inst,
                timeframe=M1,
                open_time=t,
                close_time=bar_close_time(t, M1),
                open=o,
                high=h,
                low=low,
                close=c,
                volume=1.0,
                source="t",
            )
        )
        t += timedelta(seconds=60)
    return out


# 16 flache Warmup-Bars in der Zone (Range 0.4 → ATR ≈ 0.4, keine Swings, keine Muster)
_WARMUP = [(100.5, 100.7, 100.3, 100.5)] * 16


def _mirror_rows(
    rows: list[tuple[float, float, float, float]],
) -> list[tuple[float, float, float, float]]:
    return [(_MIRROR - o, _MIRROR - low, _MIRROR - h, _MIRROR - c) for o, h, low, c in rows]


# --------------------------------------------------------------------------- Engulfing (§2.1)

_ENGULF_PREV = (100.8, 100.85, 100.15, 100.2)  # bearische Vorbar, Body 0.6
_ENGULF_BAR = (100.15, 101.05, 100.1, 101.0)  # bullisch, umschließt den Vorbar-Body, Body 0.85


def _atr_at(bars: list[OHLCV], idx: int, period: int = 14) -> float:
    return atr_at_index(atr_series(bars, period), idx) or 0.0


def test_engulfing_golden_bullish() -> None:
    bars = _rows([*_WARMUP, _ENGULF_PREV, _ENGULF_BAR])
    atr = _atr_at(bars, len(bars) - 1)
    s = detect_engulfing(bars[-2], bars[-1], Polarity.BULLISH, atr)
    assert s is not None and 0.0 < s <= 1.0


def test_engulfing_rejects_non_engulfing_body() -> None:
    bars = _rows(
        [*_WARMUP, _ENGULF_PREV, (100.4, 100.9, 100.35, 100.75)]
    )  # Body 0.35, kein Umschluss
    atr = _atr_at(bars, len(bars) - 1)
    assert detect_engulfing(bars[-2], bars[-1], Polarity.BULLISH, atr) is None


def test_engulfing_min_body_atr_boundary() -> None:
    bars = _rows([*_WARMUP, _ENGULF_PREV, _ENGULF_BAR])
    atr = _atr_at(bars, len(bars) - 1)
    strict = ConfirmationParams(engulf_min_body_atr=10.0)  # Body kann die Schwelle nicht erreichen
    assert detect_engulfing(bars[-2], bars[-1], Polarity.BULLISH, atr, strict) is None


def test_engulfing_needs_prior_opposite_color() -> None:
    bars = _rows([*_WARMUP, (100.2, 100.9, 100.15, 100.85), _ENGULF_BAR])  # Vorbar bullisch
    atr = _atr_at(bars, len(bars) - 1)
    assert detect_engulfing(bars[-2], bars[-1], Polarity.BULLISH, atr) is None


def test_engulfing_no_prev_bar() -> None:
    assert detect_engulfing(None, _bar(T0, 1, 2, 0.5, 1.8), Polarity.BULLISH, 0.4) is None


def test_engulfing_long_short_symmetry() -> None:
    up = _rows([*_WARMUP, _ENGULF_PREV, _ENGULF_BAR])
    down = _rows(_mirror_rows([*_WARMUP, _ENGULF_PREV, _ENGULF_BAR]))
    a_up = _atr_at(up, len(up) - 1)
    a_dn = _atr_at(down, len(down) - 1)
    s_up = detect_engulfing(up[-2], up[-1], Polarity.BULLISH, a_up)
    s_dn = detect_engulfing(down[-2], down[-1], Polarity.BEARISH, a_dn)
    assert s_up is not None and s_dn is not None
    assert round(s_up, 6) == round(s_dn, 6)
    # gespiegelte Richtung greift nicht
    assert detect_engulfing(up[-2], up[-1], Polarity.BEARISH, a_up) is None


# --------------------------------------------------------------------------- Pin Bar (§2.2)

_PIN_BAR = (100.7, 100.95, 99.7, 100.85)  # langer unterer Docht, Körper über Zonenmitte


def test_pin_golden_bullish() -> None:
    bars = _rows([*_WARMUP, _PIN_BAR])
    atr = _atr_at(bars, len(bars) - 1)
    s = detect_pin(bars[-1], _ZONE.zone_low, _ZONE.zone_high, Polarity.BULLISH, atr)
    assert s is not None and 0.0 < s <= 1.0


def test_pin_rejects_large_opposite_wick() -> None:
    bars = _rows([*_WARMUP, (100.7, 101.6, 99.7, 100.85)])  # großer oberer Docht
    atr = _atr_at(bars, len(bars) - 1)
    assert detect_pin(bars[-1], _ZONE.zone_low, _ZONE.zone_high, Polarity.BULLISH, atr) is None


def test_pin_rejects_body_below_zone_mid() -> None:
    bars = _rows([*_WARMUP, (100.2, 100.35, 99.4, 100.3)])  # Körper unter der Zonenmitte
    atr = _atr_at(bars, len(bars) - 1)
    assert detect_pin(bars[-1], _ZONE.zone_low, _ZONE.zone_high, Polarity.BULLISH, atr) is None


def test_pin_requires_wick_pierce_into_zone() -> None:
    bars = _rows([*_WARMUP, (100.7, 100.95, 100.55, 100.85)])  # Docht sticht nicht bis zone_low
    atr = _atr_at(bars, len(bars) - 1)
    assert detect_pin(bars[-1], _ZONE.zone_low, _ZONE.zone_high, Polarity.BULLISH, atr) is None


def test_pin_zero_range_bar() -> None:
    flat = _bar(T0, 100.5, 100.5, 100.5, 100.5)
    assert detect_pin(flat, _ZONE.zone_low, _ZONE.zone_high, Polarity.BULLISH, 0.4) is None


def test_pin_long_short_symmetry() -> None:
    up = _rows([*_WARMUP, _PIN_BAR])
    down = _rows(_mirror_rows([*_WARMUP, _PIN_BAR]))
    a_up = _atr_at(up, len(up) - 1)
    a_dn = _atr_at(down, len(down) - 1)
    s_up = detect_pin(up[-1], _ZONE.zone_low, _ZONE.zone_high, Polarity.BULLISH, a_up)
    zlo, zhi = _MIRROR - _ZONE.zone_high, _MIRROR - _ZONE.zone_low
    s_dn = detect_pin(down[-1], zlo, zhi, Polarity.BEARISH, a_dn)
    assert s_up is not None and s_dn is not None
    assert round(s_up, 6) == round(s_dn, 6)


# --------------------------------------------------------------------- Minor-CHoCH M1 (§2.3)

# Warmup + Abwärtsstruktur (2 fallende Hochs, 2 fallende Tiefs) + Bar, die über das letzte LH schließt.
_CHOCH_ZONE = FVG(
    direction=Polarity.BULLISH,
    timeframe=Timeframe.M5,
    zone_low=102.4,
    zone_high=103.0,
    created_bar=T0,
    bar_index=9,
    state=ZoneState.UNMITIGATED,
)
_CHOCH_STRUCTURE = [
    (102.0, 103.2, 101.9, 103.0),
    (103.0, 103.3, 102.9, 103.1),  # SH ~103.3
    (103.1, 103.2, 101.6, 101.8),
    (101.8, 101.9, 101.4, 101.5),  # SL ~101.4
    (101.5, 102.5, 101.5, 102.4),
    (102.4, 102.7, 102.2, 102.5),  # LH ~102.7 (< SH)
    (102.5, 102.6, 100.8, 101.0),
    (101.0, 101.2, 100.6, 100.8),  # LL ~100.6 (< SL)
    (100.8, 101.5, 100.7, 101.3),
    (101.3, 102.95, 101.2, 102.9),  # CHoCH: schließt über dem LH (102.7)
]
_CHOCH_WARMUP = [(102.0, 102.2, 101.8, 102.0)] * 16


def test_minor_choch_golden_bullish() -> None:
    bars = _rows([*_CHOCH_WARMUP, *_CHOCH_STRUCTURE])
    idx = len(bars) - 1
    atr = _atr_at(bars, idx)
    s = detect_minor_choch(
        bars, idx, _CHOCH_ZONE.zone_low, _CHOCH_ZONE.zone_high, Polarity.BULLISH, atr
    )
    assert s is not None and 0.0 < s <= 1.0


def test_minor_choch_needs_prior_opposite_structure() -> None:
    # Aufwärtsstruktur → ein bullischer CHoCH kann hier nicht entstehen
    up = [
        (100.0, 100.6, 99.9, 100.5),
        (100.5, 101.4, 100.4, 101.3),
        (101.3, 101.5, 100.7, 100.9),
        (100.9, 102.2, 100.8, 102.1),
        (102.1, 102.4, 101.6, 101.8),
        (101.8, 103.1, 101.7, 103.0),
    ]
    bars = _rows([*_CHOCH_WARMUP, *up])
    idx = len(bars) - 1
    atr = _atr_at(bars, idx)
    assert detect_minor_choch(bars, idx, 100.0, 103.5, Polarity.BULLISH, atr) is None


def test_minor_choch_broken_swing_outside_zone() -> None:
    bars = _rows([*_CHOCH_WARMUP, *_CHOCH_STRUCTURE])
    idx = len(bars) - 1
    atr = _atr_at(bars, idx)
    # Zone weit weg vom gebrochenen LH (102.7) → Bedingung (3) verletzt
    assert detect_minor_choch(bars, idx, 90.0, 91.0, Polarity.BULLISH, atr) is None


def test_minor_choch_long_short_symmetry() -> None:
    up = _rows([*_CHOCH_WARMUP, *_CHOCH_STRUCTURE])
    down = _rows(_mirror_rows([*_CHOCH_WARMUP, *_CHOCH_STRUCTURE]))
    i = len(up) - 1
    s_up = detect_minor_choch(
        up, i, _CHOCH_ZONE.zone_low, _CHOCH_ZONE.zone_high, Polarity.BULLISH, _atr_at(up, i)
    )
    zlo, zhi = _MIRROR - _CHOCH_ZONE.zone_high, _MIRROR - _CHOCH_ZONE.zone_low
    s_dn = detect_minor_choch(down, i, zlo, zhi, Polarity.BEARISH, _atr_at(down, i))
    assert s_up is not None and s_dn is not None
    assert round(s_up, 6) == round(s_dn, 6)


# --------------------------------------------------------------------- find_confirmation


def test_find_confirmation_detects_pin_in_zone() -> None:
    bars = _rows([*_WARMUP, _PIN_BAR])
    scan = find_confirmation(_ZONE, Direction.LONG, bars)
    assert isinstance(scan, ConfirmationScan)
    assert scan.confirmed
    c = scan.primary
    assert c is not None
    assert c.pattern is ConfirmationPattern.PIN
    assert c.direction is Polarity.BULLISH
    assert c.zone_kind is ZoneKind.FVG
    assert c.zone_id == "FVG-M5-bullish-7"
    assert c.bar_timestamp == bars[-1].open_time
    assert c.information_cutoff == c.bar_timestamp
    assert c.entry_ref_price is None  # keine nächste M1-Bar vorhanden


def test_find_confirmation_entry_ref_is_next_bar_open() -> None:
    bars = _rows([*_WARMUP, _PIN_BAR, (100.85, 101.2, 100.8, 101.1)])
    scan = find_confirmation(_ZONE, Direction.LONG, bars)
    assert scan.confirmed and scan.primary is not None
    assert scan.primary.entry_ref_price == bars[-1].open


def test_find_confirmation_no_m1_data() -> None:
    scan = find_confirmation(_ZONE, Direction.LONG, [])
    assert not scan.confirmed
    assert scan.primary is None
    assert "keine M1" in scan.note


def test_find_confirmation_instrument_binding() -> None:
    bars = _rows([*_WARMUP, _PIN_BAR], inst="ETHUSD")
    scan = find_confirmation(_ZONE, Direction.LONG, bars, instrument="BTCUSD")
    assert not scan.confirmed and "keine M1" in scan.note


def test_find_confirmation_lookahead_cutoff() -> None:
    bars = _rows([*_WARMUP, (100.5, 100.7, 100.3, 100.5), _PIN_BAR])
    before = find_confirmation(_ZONE, Direction.LONG, bars, now=bars[-2].close_time)
    after = find_confirmation(_ZONE, Direction.LONG, bars, now=bars[-1].close_time)
    assert not before.confirmed  # Pin-Bar liegt nach dem cutoff
    assert after.confirmed


def test_find_confirmation_since_gates_confirmation_bar() -> None:
    bars = _rows([*_WARMUP, _PIN_BAR])
    gated = find_confirmation(
        _ZONE, Direction.LONG, bars, since=bars[-1].close_time + timedelta(minutes=5)
    )
    assert not gated.confirmed
    assert "seit ARMED" in gated.note


def test_find_confirmation_zone_contact_required() -> None:
    # Engulfing-Muster, aber komplett unterhalb der Zone
    away = [(97.8, 97.85, 97.15, 97.2), (97.15, 98.05, 97.1, 98.0)]
    bars = _rows([*[(97.5, 97.7, 97.3, 97.5)] * 16, *away])
    contact = find_confirmation(_ZONE, Direction.LONG, bars)
    assert not contact.confirmed
    no_contact = find_confirmation(
        _ZONE, Direction.LONG, bars, params=ConfirmationParams(require_zone_contact=False)
    )
    assert no_contact.confirmed and no_contact.primary is not None
    assert no_contact.primary.pattern is ConfirmationPattern.ENGULFING


def test_find_confirmation_deterministic_replay() -> None:
    bars = _rows([*_WARMUP, _PIN_BAR])
    assert find_confirmation(_ZONE, Direction.LONG, bars) == find_confirmation(
        _ZONE, Direction.LONG, list(bars)
    )


def test_find_confirmation_earliest_bar_is_primary() -> None:
    bars = _rows([*_WARMUP, _PIN_BAR, (100.7, 100.95, 99.7, 100.85)])  # zwei Pins
    scan = find_confirmation(_ZONE, Direction.LONG, bars)
    assert scan.confirmed
    assert scan.primary is not None
    assert scan.primary.bar_timestamp == bars[-2].open_time
    assert len(scan.confirmations) == 2


def test_find_confirmation_order_block_zone_id() -> None:
    ob = OrderBlock(
        direction=Polarity.BULLISH,
        timeframe=Timeframe.M5,
        zone_low=100.0,
        zone_high=101.0,
        ob_bar=T0,
        bar_index=12,
        state=ZoneState.UNMITIGATED,
    )
    scan = find_confirmation(ob, Direction.LONG, _rows([*_WARMUP, _PIN_BAR]))
    assert scan.confirmed and scan.primary is not None
    assert scan.primary.zone_kind is ZoneKind.ORDER_BLOCK
    assert scan.primary.zone_id == ob.zone_id


def test_confirmation_id_stable_and_typed() -> None:
    bars = _rows([*_WARMUP, _PIN_BAR])
    c1 = find_confirmation(_ZONE, Direction.LONG, bars).primary
    c2 = find_confirmation(_ZONE, Direction.LONG, list(bars)).primary
    assert c1 is not None and c2 is not None
    assert c1.confirmation_id == c2.confirmation_id
    assert c1.confirmation_id.startswith("CONF-pin-M1-")
    assert isinstance(c1, EntryConfirmation)


def test_find_confirmation_short_symmetry() -> None:
    up = _rows([*_WARMUP, _PIN_BAR])
    down = _rows(_mirror_rows([*_WARMUP, _PIN_BAR]))
    zlo, zhi = _MIRROR - _ZONE.zone_high, _MIRROR - _ZONE.zone_low
    short_zone = FVG(
        direction=Polarity.BEARISH,
        timeframe=Timeframe.M5,
        zone_low=zlo,
        zone_high=zhi,
        created_bar=T0,
        bar_index=7,
        state=ZoneState.UNMITIGATED,
    )
    s_up = find_confirmation(_ZONE, Direction.LONG, up)
    s_dn = find_confirmation(short_zone, Direction.SHORT, down)
    assert s_up.confirmed and s_dn.confirmed
    assert s_up.primary is not None and s_dn.primary is not None
    assert s_up.primary.pattern is s_dn.primary.pattern
    assert s_up.primary.direction is Polarity.BULLISH
    assert s_dn.primary.direction is Polarity.BEARISH
    assert round(s_up.primary.strength, 6) == round(s_dn.primary.strength, 6)


# --------------------------------------------------------------------- confirmation_for_candidate


def test_confirmation_for_candidate_binds_to_mtf() -> None:
    import tests.unit.test_setup_fsm as fsm
    from trading_agent.strategy.price_action import confirmation_for_candidate
    from trading_agent.strategy.setup_detection import detect_setups

    scan = detect_setups(fsm._full_long_mtf())
    cand = scan.primary
    assert cand is not None and cand.is_armed

    # M1-Serie um die Entry-FVG herum mit einem Pin (in der FVG-Zone)
    fvg = cand.entry_fvg
    assert fvg is not None
    zlo, zhi = fvg.zone_low, fvg.zone_high
    mid = 0.5 * (zlo + zhi)
    warm = [(mid, mid + 0.1, mid - 0.1, mid)] * 16
    pin = (mid + 0.15, mid + 0.2, zlo - 0.3, mid + 0.1)
    assert cand.structure_break is not None
    start = cand.structure_break.break_bar_timestamp
    m1_bars = _rows([*warm, pin], start=start)

    out = confirmation_for_candidate(fsm._full_long_mtf(), cand, m1_bars)
    assert out.confirmed and out.primary is not None
    assert out.primary.pattern is ConfirmationPattern.PIN
    assert out.direction is Direction.LONG


def test_confirmation_for_candidate_not_armed() -> None:
    import tests.unit.test_setup_fsm as fsm
    from trading_agent.strategy.price_action import confirmation_for_candidate
    from trading_agent.strategy.setup_detection import detect_setups

    mtf = fsm._mtf(m15_liquidity=(fsm._level(),))  # bleibt LIQUIDITY_IDENTIFIED
    cand = detect_setups(mtf).primary
    assert cand is not None and not cand.is_armed
    out = confirmation_for_candidate(mtf, cand, _rows([*_WARMUP, _PIN_BAR]))
    assert not out.confirmed
    assert "nicht ARMED" in out.note
