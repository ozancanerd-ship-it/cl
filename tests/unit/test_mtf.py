"""Phase 3 — Multi-Timeframe-Assembly (``analysis/mtf.py``, ``0.1.1`` C11).

M5-Basis → M15 / H4 / D1. Getestet: Timestamp-Alignment, nur abgeschlossene Bars, fehlende Bars,
stale data, unterschiedliche Timeframes, Look-ahead-Schutz, Long/Short-Symmetrie, deterministisches
Replay, leere Zusatzdaten-Slots.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from trading_agent.analysis.mtf import (
    MTF_TF_ORDER,
    MtfContext,
    MtfError,
    MtfParams,
    build_mtf_context,
)
from trading_agent.core.enums import AssetClass, Bias, RegimeDirectional, Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time, parse_timestamp

S = parse_timestamp("2024-01-01T00:00:00Z")
_M5_PER_DAY = 86400 // 300  # 288

# Tages-Anker mit HH/HL-Zickzack (Aufwärtsstruktur mit Pullbacks).
_DAILY_UP = [
    100.0,
    104.0,
    102.0,
    108.0,
    105.0,
    112.0,
    109.0,
    116.0,
    113.0,
    121.0,
    118.0,
    126.0,
    123.0,
    131.0,
    128.0,
    136.0,
]
_MIRROR = 200.0


def _m5_series(
    daily: list[float], *, extra_bars: int = 0, wiggle: float = 0.6, w: float = 0.25
) -> list[OHLCV]:
    """Erzeugt ``len(daily) * 288 (+ extra_bars)`` M5-Bars: pro Tag linear von ``daily[d-1]`` nach
    ``daily[d]`` interpoliert, plus deterministischer Intraday-Zickzack für Swings."""
    prices: list[float] = []
    prev = daily[0]
    for tgt in daily:
        for k in range(_M5_PER_DAY):
            frac = (k + 1) / _M5_PER_DAY
            base = prev + (tgt - prev) * frac
            zig = wiggle * (1.0 if (k // 12) % 2 == 0 else -1.0)
            prices.append(round(base + zig, 4))
        prev = tgt
    for k in range(extra_bars):
        prices.append(round(prev + wiggle * (1.0 if (k // 12) % 2 == 0 else -1.0), 4))

    out: list[OHLCV] = []
    t = S
    for i, p in enumerate(prices):
        o = (prices[i - 1] + p) / 2 if i > 0 else p
        out.append(
            OHLCV(
                instrument="BTCUSD",
                timeframe=Timeframe.M5,
                open_time=t,
                close_time=bar_close_time(t, Timeframe.M5),
                open=o,
                high=max(o, p) + w,
                low=min(o, p) - w,
                close=p,
                volume=1.0,
                source="t",
            )
        )
        t += timedelta(seconds=300)
    return out


def _build(bars: list[OHLCV], **kw: object) -> MtfContext:
    return build_mtf_context(
        bars,
        instrument="BTCUSD",
        asset_class=AssetClass.CRYPTO,
        **kw,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- Grundstruktur


def test_build_populates_all_timeframes() -> None:
    ctx = _build(_m5_series(_DAILY_UP))
    assert set(ctx.per_tf) == set(MTF_TF_ORDER)
    assert ctx.base_timeframe is Timeframe.M5
    for tf in MTF_TF_ORDER:
        tfc = ctx.per_tf[tf]
        assert tfc.timeframe is tf
        assert len(tfc.bars) > 0
        assert all(b.timeframe is tf for b in tfc.bars)
    # MarketContext ist look-ahead-geprüft und trägt die rohen Serien
    assert ctx.market_context.instrument == "BTCUSD"
    assert ctx.market_context.base_timeframe is Timeframe.M5
    assert set(ctx.market_context.series) == set(MTF_TF_ORDER)


def test_additional_data_slots_are_empty_no_fake_data() -> None:
    mc = _build(_m5_series(_DAILY_UP)).market_context
    assert mc.derivatives.funding_rate is None
    assert mc.derivatives.open_interest is None
    assert mc.cross_asset.vix is None
    assert mc.cross_asset.dxy_trend is None
    assert mc.news.events == ()
    assert mc.news.feed_as_of is None
    assert mc.news.feed_available is False


def test_empty_input_raises() -> None:
    try:
        _build([])
    except MtfError:
        pass
    else:  # pragma: no cover
        raise AssertionError("MtfError erwartet")


# ----------------------------------------------------------------- Alignment / Timeframes


def test_timestamp_alignment() -> None:
    ctx = _build(_m5_series(_DAILY_UP))
    d1 = ctx.per_tf[Timeframe.D1].bars
    for b in d1:
        assert b.open_time.hour == 0 and b.open_time.minute == 0 and b.open_time.second == 0
        assert b.close_time - b.open_time == timedelta(days=1)
    for b in ctx.per_tf[Timeframe.H4].bars:
        assert b.open_time.hour % 4 == 0 and b.open_time.minute == 0


def test_different_timeframes_bar_counts() -> None:
    ctx = _build(_m5_series(_DAILY_UP))
    n_m5 = len(ctx.per_tf[Timeframe.M5].bars)
    assert len(ctx.per_tf[Timeframe.M15].bars) == n_m5 // 3
    assert len(ctx.per_tf[Timeframe.H4].bars) == n_m5 // 48
    assert len(ctx.per_tf[Timeframe.D1].bars) == n_m5 // 288 == len(_DAILY_UP)


def test_only_completed_bars() -> None:
    # 40 zusätzliche M5-Bars (< 1 H4, < 1 D1) im unvollständigen Folgetag -> keine neue HTF-Bar
    ctx = _build(_m5_series(_DAILY_UP, extra_bars=40))
    assert len(ctx.per_tf[Timeframe.D1].bars) == len(_DAILY_UP)
    assert len(ctx.per_tf[Timeframe.H4].bars) == len(_DAILY_UP) * (_M5_PER_DAY // 48)
    for tf in MTF_TF_ORDER:
        for b in ctx.per_tf[tf].bars:
            assert b.close_time <= ctx.information_cutoff


# --------------------------------------------------------------------- Look-ahead-Schutz


def test_lookahead_slicing_equivalence() -> None:
    full = _m5_series(_DAILY_UP)
    cutoff = full[10 * _M5_PER_DAY - 1].close_time  # exakt Ende von Tag 10
    from_full = _build(full, now=cutoff)
    from_sliced = _build([b for b in full if b.close_time <= cutoff], now=cutoff)
    assert from_full == from_sliced
    assert len(from_full.per_tf[Timeframe.D1].bars) == 10


def test_future_bars_do_not_leak() -> None:
    full = _m5_series(_DAILY_UP)
    cutoff = full[8 * _M5_PER_DAY - 1].close_time
    with_future = _build(full, now=cutoff)
    without_future = _build(full[: 8 * _M5_PER_DAY], now=cutoff)
    assert with_future.per_tf[Timeframe.D1] == without_future.per_tf[Timeframe.D1]
    assert with_future.htf_directional is without_future.htf_directional


# ------------------------------------------------------------------------- fehlende Bars


def test_missing_bars_flagged_and_lowers_confidence() -> None:
    bars = _m5_series(_DAILY_UP)
    gapped = bars[:1000] + bars[1150:]  # ~12h Lücke
    ctx = _build(gapped, now=bars[-1].close_time)
    assert ctx.issues  # mindestens ein Befund
    assert ctx.data_confidence < 1.0
    assert any("M5" in msg for msg in ctx.issues)


def test_stale_data_zeroes_freshness() -> None:
    bars = _m5_series(_DAILY_UP)
    stale_now = bars[-1].close_time + timedelta(hours=30)
    ctx = _build(bars, now=stale_now)
    assert ctx.per_tf[Timeframe.M5].data_confidence == 0.0
    assert ctx.data_confidence == 0.0
    assert ctx.issues


def test_short_history_lowers_confidence_without_crash() -> None:
    ctx = _build(_m5_series(_DAILY_UP[:3]))  # nur 3 Tage -> Warmup unvollständig
    assert ctx.data_confidence < 1.0
    assert any("Warmup" in msg or "Bars" in msg for msg in ctx.issues)


# --------------------------------------------------------------------- Long/Short-Symmetrie

_OPPOSITE = {
    RegimeDirectional.TREND_UP: RegimeDirectional.TREND_DOWN,
    RegimeDirectional.TREND_DOWN: RegimeDirectional.TREND_UP,
    RegimeDirectional.RANGE: RegimeDirectional.RANGE,
    RegimeDirectional.UNCLEAR: RegimeDirectional.UNCLEAR,
    RegimeDirectional.CONFLICTING: RegimeDirectional.CONFLICTING,
}
_OPP_BIAS = {Bias.LONG: Bias.SHORT, Bias.SHORT: Bias.LONG, Bias.NONE: Bias.NONE}


def test_long_short_symmetry() -> None:
    up = _build(_m5_series(_DAILY_UP))
    down = _build(_m5_series([_MIRROR - x for x in _DAILY_UP]))
    # Aggregat-Kontrakt: HTF-Konsens und -Bias spiegeln exakt
    assert down.htf_directional is _OPPOSITE[up.htf_directional]
    assert down.htf_bias is _OPP_BIAS[up.htf_bias]
    # HTF-Regime (D1/H4) speisen den Konsens -> exakte Spiegelung
    for tf in (Timeframe.D1, Timeframe.H4):
        u, d = up.per_tf[tf], down.per_tf[tf]
        assert d.regime.directional is _OPPOSITE[u.regime.directional]
        assert d.bias is _OPP_BIAS[u.bias]
    # Assembly-Invarianten: identische Bar-Zahl, Datenqualität und Befunde je Richtung
    for tf in MTF_TF_ORDER:
        u, d = up.per_tf[tf], down.per_tf[tf]
        assert len(u.bars) == len(d.bars)
        assert (
            abs(len(u.swings) - len(d.swings)) <= 1
        )  # Detektor-Tie-Break nahe Fraktal-Gleichstand
        assert u.data_confidence == d.data_confidence
    assert up.data_confidence == down.data_confidence
    assert up.issues == down.issues


# ---------------------------------------------------------------- deterministisches Replay


def test_deterministic_replay() -> None:
    bars = _m5_series(_DAILY_UP)
    assert _build(bars) == _build(list(bars))


def test_params_min_bars_configurable() -> None:
    bars = _m5_series(_DAILY_UP)
    lenient = MtfParams(min_bars={tf: 1 for tf in MTF_TF_ORDER})
    ctx = _build(bars, params=lenient)
    # Freshness/Consistency weiter 1.0, Completeness jetzt gesättigt -> hohe Confidence
    assert ctx.per_tf[Timeframe.D1].data_confidence == 1.0


# ------------------------------------------------------------------------- HTF-Regime-Gate


def test_htf_regime_gate_present_and_consistent() -> None:
    ctx = _build(_m5_series(_DAILY_UP))
    assert ctx.htf_regime_gate is not None
    assert ctx.regime_ok is ctx.htf_regime_gate.ok
    assert isinstance(ctx.information_cutoff, datetime)


def test_native_higher_overrides_resampling() -> None:
    bars = _m5_series(_DAILY_UP)
    resampled_d1 = _build(bars).per_tf[Timeframe.D1].bars
    native = {Timeframe.D1: list(resampled_d1[:5])}
    ctx = build_mtf_context(
        bars,
        instrument="BTCUSD",
        asset_class=AssetClass.CRYPTO,
        native_higher=native,
    )
    assert len(ctx.per_tf[Timeframe.D1].bars) == 5
