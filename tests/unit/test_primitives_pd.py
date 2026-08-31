"""Phase 3 — Golden-Tests: Premium / Discount (§13) + Reference/Dealing Range (§0.5).

pd_position, DISCOUNT/EQUILIBRIUM/PREMIUM, konfigurierte Schwellen, Long/Short-Symmetrie
(Spiegelung ⇒ pd_position → 1 − pd_position), Look-ahead-Schutz, Edge Cases.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from trading_agent.core.enums import (
    Direction,
    MarketSide,
    PDReference,
    PDZone,
    Polarity,
    SwingType,
    Timeframe,
)
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time, parse_timestamp
from trading_agent.strategy.primitives.models import Displacement, LiquiditySweep, SwingPoint
from trading_agent.strategy.primitives.pd import (
    PdParams,
    classify_zone,
    dealing_range,
    last_impulse_leg_range,
    pd_position,
    premium_discount,
    premium_discount_for,
    swept_leg_range,
)
from trading_agent.strategy.primitives.swings import detect_swings

TF = Timeframe.M5
START = parse_timestamp("2024-06-09T00:00:00Z")


def _bars(rows: list[tuple[float, float, float, float]]) -> list[OHLCV]:
    out: list[OHLCV] = []
    t = START
    for o, h, low, c in rows:
        out.append(
            OHLCV(
                instrument="BTCUSDT",
                timeframe=TF,
                open_time=t,
                close_time=bar_close_time(t, TF),
                open=o,
                high=h,
                low=low,
                close=c,
                volume=1.0,
                source="test",
            )
        )
        t += timedelta(seconds=TF.seconds)
    return out


def _flat(prices: list[float]) -> list[OHLCV]:
    return _bars([(p, p, p, p) for p in prices])


def _swing(kind: SwingType, idx: int, price: float) -> SwingPoint:
    return SwingPoint(
        type=kind,
        timeframe=TF,
        bar_index=idx,
        timestamp=START + timedelta(seconds=TF.seconds * idx),
        price=price,
        confirmed_at=START + timedelta(seconds=TF.seconds * (idx + 2)),
    )


# ------------------------------------------------------------------------- pd_position


def test_pd_position_formula() -> None:
    assert pd_position(105.0, 100.0, 110.0) == 0.5
    assert pd_position(100.0, 100.0, 110.0) == 0.0
    assert pd_position(110.0, 100.0, 110.0) == 1.0
    assert pd_position(98.0, 100.0, 110.0) == pytest.approx(-0.2)  # unter der Range
    assert pd_position(114.0, 100.0, 110.0) == pytest.approx(1.4)  # über der Range
    with pytest.raises(ValueError, match="entartete Range"):
        pd_position(100.0, 105.0, 105.0)


def test_classify_zone_thresholds() -> None:
    assert classify_zone(0.30) is PDZone.DISCOUNT
    assert classify_zone(0.70) is PDZone.PREMIUM
    assert classify_zone(0.50) is PDZone.EQUILIBRIUM
    assert classify_zone(0.45) is PDZone.DISCOUNT  # <= discount_max
    assert classify_zone(0.55) is PDZone.PREMIUM  # >= premium_min
    assert classify_zone(-0.1) is PDZone.DISCOUNT
    assert classify_zone(1.2) is PDZone.PREMIUM
    # engere Schwellen
    assert classify_zone(0.40, discount_max=0.30, premium_min=0.70) is PDZone.EQUILIBRIUM


def test_premium_discount_object() -> None:
    pd = premium_discount(
        102.0, 100.0, 110.0, reference=PDReference.SWEPT_LEG, reference_tf=Timeframe.H4
    )
    assert pd is not None
    assert pd.reference is PDReference.SWEPT_LEG and pd.reference_tf is Timeframe.H4
    assert (pd.range_low, pd.range_high, pd.price) == (100.0, 110.0, 102.0)
    assert pd.pd_position == 0.2
    assert pd.zone is PDZone.DISCOUNT
    assert pd.equilibrium == 105.0
    assert pd.favored_direction is Direction.LONG
    assert pd.strategy_version == "0.1.1"


def test_premium_discount_equilibrium_has_no_favored_direction() -> None:
    pd = premium_discount(
        105.0, 100.0, 110.0, reference=PDReference.DEALING_RANGE, reference_tf=Timeframe.H4
    )
    assert pd is not None and pd.zone is PDZone.EQUILIBRIUM and pd.favored_direction is None


def test_premium_discount_degenerate_range_returns_none() -> None:
    assert (
        premium_discount(100.0, 105.0, 105.0, reference=PDReference.SWEPT_LEG, reference_tf=TF)
        is None
    )
    assert (
        premium_discount(100.0, 110.0, 100.0, reference=PDReference.SWEPT_LEG, reference_tf=TF)
        is None
    )


def test_custom_thresholds_via_params() -> None:
    params = PdParams(discount_max=0.25, premium_min=0.75)
    pd = premium_discount(
        103.0, 100.0, 110.0, reference=PDReference.SWEPT_LEG, reference_tf=TF, params=params
    )
    assert pd is not None and pd.pd_position == 0.3 and pd.zone is PDZone.EQUILIBRIUM


# ------------------------------------------------------------------------- Reference Ranges


def test_dealing_range_from_swings() -> None:
    swings = [
        _swing(SwingType.SWING_HIGH, 5, 112.0),
        _swing(SwingType.SWING_LOW, 10, 104.0),
        _swing(SwingType.SWING_HIGH, 16, 110.0),
        _swing(SwingType.SWING_LOW, 22, 105.0),  # letzter SL
    ]
    assert dealing_range(swings) == (105.0, 110.0)  # (letzter SL, letzter SH)
    assert dealing_range([]) is None
    assert dealing_range([_swing(SwingType.SWING_HIGH, 5, 110.0)]) is None  # kein SL
    # letzter SH unter letztem SL -> entartet
    weird = [_swing(SwingType.SWING_LOW, 5, 110.0), _swing(SwingType.SWING_HIGH, 10, 108.0)]
    assert dealing_range(weird) is None


def _sweep(extreme: float) -> LiquiditySweep:
    return LiquiditySweep(
        level=None,  # type: ignore[arg-type]
        side=MarketSide.SELL_SIDE,
        timeframe=TF,
        penetration_bar=START,
        penetration_extreme=extreme,
        penetration_depth_atr=0.5,
        reclaim_bar=START,
        reclaim_close=extreme + 3,
        bars_to_reclaim=0,
    )


def _disp(s: int, e: int, direction: Polarity) -> Displacement:
    return Displacement(
        direction=direction,
        timeframe=TF,
        start_bar=START + timedelta(seconds=TF.seconds * s),
        end_bar=START + timedelta(seconds=TF.seconds * e),
        bars=e - s + 1,
        net_move_atr=2.0,
        body_ratio=0.8,
        start_index=s,
        end_index=e,
    )


def test_swept_leg_range() -> None:
    bars = _bars(
        [(100, 100.5, 99.5, 100.0)] * 5
        + [(101, 105, 100.5, 104), (104, 109, 103.5, 108), (108, 112, 107.5, 111)]  # bull. Impuls
    )
    rng = swept_leg_range(_sweep(98.0), _disp(5, 7, Polarity.BULLISH), bars)
    assert rng == (98.0, 112.0)  # Sweep-Tief .. Displacement-Hoch


def test_swept_leg_range_bearish_uses_low() -> None:
    bars = _bars(
        [(100, 100.5, 99.5, 100.0)] * 5
        + [(99, 99.5, 95, 96), (96, 96.5, 91, 92), (92, 92.5, 88, 89)]  # bear. Impuls
    )
    rng = swept_leg_range(_sweep(102.0), _disp(5, 7, Polarity.BEARISH), bars)
    assert rng == (88.0, 102.0)  # Displacement-Tief .. Sweep-Hoch


def test_last_impulse_leg_range() -> None:
    bars = _bars([(100, 101, 99, 100)] * 4 + [(100, 106, 99.5, 105), (105, 110, 104, 109)])
    assert last_impulse_leg_range(_disp(4, 5, Polarity.BULLISH), bars) == (99.5, 110.0)


# ------------------------------------------------------------------------- Dispatch


def test_premium_discount_for_dispatch() -> None:
    swings = [_swing(SwingType.SWING_HIGH, 5, 110.0), _swing(SwingType.SWING_LOW, 10, 100.0)]
    dr = premium_discount_for(
        102.0, Timeframe.H4, params=PdParams(reference=PDReference.DEALING_RANGE), swings=swings
    )
    assert dr is not None and dr.zone is PDZone.DISCOUNT and dr.range_low == 100.0

    bars = _bars([(100, 100.5, 99.5, 100.0)] * 5 + [(101, 112, 100.5, 111)])
    sl = premium_discount_for(
        100.0,
        Timeframe.H4,
        params=PdParams(reference=PDReference.SWEPT_LEG),
        sweep=_sweep(98.0),
        displacement=_disp(5, 5, Polarity.BULLISH),
        bars=bars,
    )
    assert sl is not None and sl.pd_position == pytest.approx((100 - 98) / (112 - 98))

    # SESSION_RANGE noch nicht implementiert -> None
    assert (
        premium_discount_for(100.0, TF, params=PdParams(reference=PDReference.SESSION_RANGE))
        is None
    )
    # fehlende Eingaben -> None
    assert premium_discount_for(100.0, TF, params=PdParams(reference=PDReference.SWEPT_LEG)) is None


# ------------------------------------------------------------------------- Symmetrie


def test_long_short_symmetry() -> None:
    lo, hi, price = 100.0, 110.0, 103.0  # pd_position 0.3 -> DISCOUNT
    up = premium_discount(price, lo, hi, reference=PDReference.SWEPT_LEG, reference_tf=TF)
    # Spiegelung um 100
    m = 100.0
    down = premium_discount(
        2 * m - price, 2 * m - hi, 2 * m - lo, reference=PDReference.SWEPT_LEG, reference_tf=TF
    )
    assert up is not None and down is not None
    assert down.pd_position == pytest.approx(1.0 - up.pd_position)
    assert up.zone is PDZone.DISCOUNT and down.zone is PDZone.PREMIUM
    assert up.favored_direction is Direction.LONG and down.favored_direction is Direction.SHORT


# ------------------------------------------------------------------------- Look-ahead


def test_dealing_range_lookahead_immune() -> None:
    # Zigzag: SH@5(105) SL@9(101) SH@14(107) SL@17(103) SH@21(110) SL@25(105)
    prices = [
        100.0,
        101.2,
        102.4,
        103.6,
        104.8,
        105.0,
        104.0,
        103.0,
        102.0,
        101.0,
        102.2,
        103.4,
        104.6,
        105.8,
        107.0,
        105.5,
        104.0,
        103.0,
        104.5,
        106.0,
        108.0,
        110.0,
        109.0,
        108.0,
        106.5,
        105.0,
        106.0,
        107.5,
    ]
    bars = _flat(prices)
    full_sw = detect_swings(bars, TF)
    # bis Bar 24 bestätigte Swings
    early = detect_swings(bars[:24], TF)
    cutoff = bars[23].close_time
    filtered = [s for s in full_sw if s.confirmed_at <= cutoff]
    assert dealing_range(early) == dealing_range(filtered)  # look-ahead-frei
    # volle Serie: jüngstes Paar SH@21(110) / SL@25(105)
    assert dealing_range(full_sw) == (105.0, 110.0)


def test_swept_leg_range_lookahead_immune() -> None:
    base = [(100, 100.5, 99.5, 100.0)] * 5 + [(101, 112, 100.5, 111)]
    disp = _disp(5, 5, Polarity.BULLISH)
    short = swept_leg_range(_sweep(98.0), disp, _bars(base))
    long = swept_leg_range(
        _sweep(98.0), disp, _bars([*base, (111, 120, 110, 119), (119, 125, 118, 124)])
    )
    assert short == long == (98.0, 112.0)  # spätere Bars ändern die Leg-Range nicht


# ------------------------------------------------------------------------- Golden


def test_golden_dealing_range_classification() -> None:
    prices = [
        100.0,
        101.2,
        102.4,
        103.6,
        104.8,
        105.0,
        104.0,
        103.0,
        102.0,
        101.0,
        102.2,
        103.4,
        104.6,
        105.8,
        107.0,
        105.5,
        104.0,
        103.0,
        104.5,
        106.0,
        108.0,
        110.0,
        109.0,
        108.0,
        106.5,
        105.0,
        106.0,
        107.5,
    ]
    bars = _flat(prices)
    swings = detect_swings(bars, TF)
    rng = dealing_range(swings)
    assert rng == (105.0, 110.0)

    params = PdParams(reference=PDReference.DEALING_RANGE)
    disc = premium_discount_for(106.0, Timeframe.H4, params=params, swings=swings)
    equi = premium_discount_for(107.5, Timeframe.H4, params=params, swings=swings)
    prem = premium_discount_for(109.0, Timeframe.H4, params=params, swings=swings)
    assert (
        disc is not None and disc.zone is PDZone.DISCOUNT and disc.pd_position == pytest.approx(0.2)
    )
    assert equi is not None and equi.zone is PDZone.EQUILIBRIUM
    assert (
        prem is not None and prem.zone is PDZone.PREMIUM and prem.pd_position == pytest.approx(0.8)
    )
