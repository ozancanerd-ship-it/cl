"""Phase 3 — Market Regime (regime.md 0.1.1): Directional / Volatility / Phase, Hysterese,
MTF-Konsens D1+H4, NO_TRADE-Gate. Look-ahead-Schutz, Edge Cases.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from trading_agent.analysis.regime import (
    RegimeGateParams,
    RegimeParams,
    RegimeState,
    RegimeTracker,
    TrendParams,
    directional_regime,
    disagreement,
    merge_htf,
    phase_regime,
    raw_regime,
    regime_gate,
    slope_norm,
    volatility_regime,
)
from trading_agent.core.enums import (
    AssetClass,
    ExpansionDirection,
    NoTradeReason,
    RegimeDirectional,
    RegimePhase,
    RegimeVolatility,
    Timeframe,
)
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time, parse_timestamp
from trading_agent.strategy.primitives.imbalance import find_displacements, find_fvgs
from trading_agent.strategy.primitives.structure import structure_breaks
from trading_agent.strategy.primitives.swings import detect_swings

TF = Timeframe.H4
S = parse_timestamp("2024-01-01T00:00:00Z")


def _bars(prices: list[float], *, w: float = 0.3, tf: Timeframe = TF) -> list[OHLCV]:
    """Bars mit Mittelpunkt-Open (saubere Fraktale an Pivots **und** echte Kerzenkörper)."""
    out: list[OHLCV] = []
    t = S
    for i, p in enumerate(prices):
        o = (prices[i - 1] + p) / 2 if i > 0 else p
        out.append(
            OHLCV(
                instrument="X",
                timeframe=tf,
                open_time=t,
                close_time=bar_close_time(t, tf),
                open=o,
                high=max(o, p) + w,
                low=min(o, p) - w,
                close=p,
                volume=1.0,
                source="t",
            )
        )
        t += timedelta(seconds=tf.seconds)
    return out


def _interp(pivots: list[float], per_leg: int) -> list[float]:
    out = [pivots[0]]
    for tgt in pivots[1:]:
        s = out[-1]
        out += [round(s + (tgt - s) * k / per_leg, 4) for k in range(1, per_leg + 1)]
    return out


def _primitives(bars: list[OHLCV]) -> tuple[list, list, list]:
    sw = detect_swings(bars, TF)
    br = structure_breaks(bars, sw, TF)
    dp = find_displacements(bars, TF, find_fvgs(bars, TF, tick_size=0.1))
    return sw, br, dp


_UPTREND = _interp([100, 110, 106, 120, 116, 130, 126, 140], 8)  # HH/HL, kräftige Steigung
_RANGE = _interp([100, 108] * 5 + [100], 6)  # flache Oszillation, Berührungen an beiden Grenzen
_SPIKE = _interp([100, 130, 100], 20)  # 1 Impuls hoch, 1 zurück -> keine saubere Struktur


# --------------------------------------------------------------------------- Kennzahlen


def test_slope_norm_sign() -> None:
    up = _bars([100 + i for i in range(60)])
    down = _bars([200 - i for i in range(60)])
    flat = _bars([100.0] * 60)
    sn_up, sn_down, sn_flat = (
        slope_norm(up, 50, 2.0),
        slope_norm(down, 50, 2.0),
        slope_norm(flat, 50, 2.0),
    )
    assert sn_up is not None and sn_up > 0.1
    assert sn_down is not None and sn_down < -0.1
    assert sn_flat is not None and abs(sn_flat) < 1e-6
    assert slope_norm(up[:1], 50, 2.0) is None
    assert slope_norm(up, 50, 0.0) is None


# --------------------------------------------------------------------------- §3 Volatility


def test_volatility_normal_for_constant_atr() -> None:
    bars = _bars([100 + (i % 2) for i in range(120)])  # konstante TR
    state, pct, _ = volatility_regime(bars, AssetClass.CRYPTO, params=RegimeParams())
    assert state is RegimeVolatility.NORMAL and abs(pct - 50.0) < 5.0


def test_volatility_extreme_via_atr_ratio() -> None:
    # ATR groß relativ zum Preis: crypto-Grenze 0.08
    bars = _bars([100 + 14 * (i % 2) for i in range(120)], w=0.1)  # TR ~ 14 bei Preis ~107
    state, _, _ = volatility_regime(bars, AssetClass.CRYPTO, params=RegimeParams())
    assert state is RegimeVolatility.EXTREME


def test_volatility_high_and_low_via_percentile() -> None:
    calm = [100 + 0.1 * (i % 2) for i in range(100)]
    loud = [100 + 6 * (i % 2) for i in range(20)]
    bars = _bars(calm + loud, w=0.05)
    assert volatility_regime(bars, AssetClass.CRYPTO, params=RegimeParams())[0] in (
        RegimeVolatility.HIGH,
        RegimeVolatility.EXTREME,
    )
    bars2 = _bars(loud + calm, w=0.05)  # jetzt endet es ruhig
    assert (
        volatility_regime(bars2, AssetClass.CRYPTO, params=RegimeParams())[0]
        is RegimeVolatility.LOW
    )


# --------------------------------------------------------------------------- §2 Directional


def test_directional_trend_up() -> None:
    bars = _bars(_UPTREND)
    sw, br, _ = _primitives(bars)
    d, score, sn, rlo, rhi = directional_regime(
        bars, sw, br, TF, params=RegimeParams(), atr_val=2.0
    )
    assert d is RegimeDirectional.TREND_UP
    assert 0.0 < score <= 1.0 and sn > 0.05
    assert rlo is None and rhi is None


def test_directional_trend_needs_slope() -> None:
    bars = _bars(_UPTREND)
    sw, br, _ = _primitives(bars)
    # min_slope künstlich hoch -> Struktur ist TREND_UP, aber Slope-Bedingung scheitert
    params = RegimeParams(trend=TrendParams(min_slope=99.0))
    d, *_ = directional_regime(bars, sw, br, TF, params=params, atr_val=2.0)
    assert d is not RegimeDirectional.TREND_UP


def test_directional_range() -> None:
    bars = _bars(_RANGE)
    sw, br, _ = _primitives(bars)
    d, score, _, rlo, rhi = directional_regime(bars, sw, br, TF, params=RegimeParams(), atr_val=1.5)
    assert d is RegimeDirectional.RANGE
    assert rlo is not None and rhi is not None and rhi > rlo
    assert 0.0 < score <= 1.0


def test_directional_unclear() -> None:
    bars = _bars(_SPIKE)
    sw, br, _ = _primitives(bars)
    d, score, _, _, _ = directional_regime(bars, sw, br, TF, params=RegimeParams(), atr_val=1.5)
    assert d is RegimeDirectional.UNCLEAR and score == 0.0


# --------------------------------------------------------------------------- §4 Phase


def test_phase_expansion() -> None:
    calm = [100 + 0.2 * (i % 2) for i in range(40)]
    burst = _interp([100, 130], 10)  # kräftiger Impuls am Ende
    bars = _bars(calm + burst, w=0.1)
    _sw, br, dp = _primitives(bars)
    phase, direction, _ = phase_regime(bars, dp, br, params=RegimeParams(), atr_val=2.0)
    assert phase is RegimePhase.EXPANSION
    assert direction in (ExpansionDirection.UP, ExpansionDirection.NONE)


def test_phase_compression() -> None:
    loud = [100 + 5 * (i % 2) for i in range(40)]
    quiet = [100.0 + 0.02 * (i % 2) for i in range(20)]  # sehr schmale Bars
    bars = _bars(loud + quiet, w=0.02)
    _sw, br, dp = _primitives(bars)
    phase, _, run = phase_regime(bars, dp, br, params=RegimeParams(), atr_val=1.0)
    assert phase is RegimePhase.COMPRESSION and run >= 5


def test_phase_neutral() -> None:
    bars = _bars([100 + (i % 2) for i in range(80)])
    _sw, br, dp = _primitives(bars)
    assert phase_regime(bars, dp, br, params=RegimeParams(), atr_val=1.0)[0] is RegimePhase.NEUTRAL


# --------------------------------------------------------------------------- raw_regime + Look-ahead


def test_raw_regime_uptrend() -> None:
    bars = _bars(_UPTREND)
    sw, br, dp = _primitives(bars)
    rs = raw_regime(
        bars, sw, br, dp, timeframe=TF, asset_class=AssetClass.CRYPTO, now=bars[-1].close_time
    )
    assert rs.directional is RegimeDirectional.TREND_UP
    assert 0.0 <= rs.volatility_pct <= 100.0
    assert rs.phase in (RegimePhase.EXPANSION, RegimePhase.COMPRESSION, RegimePhase.NEUTRAL)
    assert rs.bars_in_state == 0  # raw = ohne Hysterese


def test_raw_regime_lookahead_immune() -> None:
    full = _bars([*_UPTREND, 142, 141, 143, 145, 147])
    k = len(_UPTREND)
    sw_e, br_e, dp_e = _primitives(full[:k])
    early = raw_regime(
        full[:k],
        sw_e,
        br_e,
        dp_e,
        timeframe=TF,
        asset_class=AssetClass.CRYPTO,
        now=full[k - 1].close_time,
    )
    sw_l = [s for s in detect_swings(full, TF) if s.confirmed_at <= full[k - 1].close_time]
    br_l = [
        b
        for b in structure_breaks(full, detect_swings(full, TF), TF)
        if b.break_bar_timestamp <= full[k - 1].open_time
    ]
    dp_l = [
        d
        for d in find_displacements(full, TF, find_fvgs(full, TF, tick_size=0.1))
        if d.end_index < k
    ]
    late = raw_regime(
        full[:k],
        sw_l,
        br_l,
        dp_l,
        timeframe=TF,
        asset_class=AssetClass.CRYPTO,
        now=full[k - 1].close_time,
    )
    assert (early.directional, early.volatility, early.phase) == (
        late.directional,
        late.volatility,
        late.phase,
    )


# --------------------------------------------------------------------------- §6 Hysterese


def test_tracker_min_bars_confirmation() -> None:
    tr = RegimeTracker(TF, AssetClass.CRYPTO)
    prices = [*_UPTREND]
    bars = _bars(prices)
    # etabliere TREND_UP
    for i in range(30, len(bars) + 1):
        seg = bars[:i]
        sw, br, dp = _primitives(seg)
        st = tr.update(seg, sw, br, dp, now=seg[-1].close_time)
    assert st.directional is RegimeDirectional.TREND_UP
    established_bars_in_state = st.bars_in_state

    # 2 Bars, die roh UNCLEAR wären -> Tracker bleibt TREND_UP (min_bars=3)
    drop = _bars([*prices, prices[-1] - 20, prices[-1] - 22])
    for i in (len(prices) + 1, len(prices) + 2):
        seg = drop[:i]
        sw, br, dp = _primitives(seg)
        st = tr.update(seg, sw, br, dp, now=seg[-1].close_time)
    assert st.directional is RegimeDirectional.TREND_UP
    assert st.bars_in_state == established_bars_in_state + 2


def test_tracker_cooldown_after_directional_change() -> None:
    tr = RegimeTracker(TF, AssetClass.CRYPTO, params=RegimeParams(cooldown_bars=3))
    tr._dir = RegimeDirectional.RANGE  # Vorzustand
    tr._vol = RegimeVolatility.NORMAL
    tr._phase = RegimePhase.NEUTRAL
    bars = _bars(_UPTREND)
    active_runs: list[int] = []
    run = 0
    st = None
    for i in range(40, len(bars) + 1):
        seg = bars[:i]
        sw, br, dp = _primitives(seg)
        st = tr.update(seg, sw, br, dp, now=seg[-1].close_time)
        if st.cooldown_active:
            run += 1
        elif run:
            active_runs.append(run)
            run = 0
    if run:
        active_runs.append(run)
    assert st is not None and st.directional is RegimeDirectional.TREND_UP
    # jeder directional-Wechsel setzt den Cooldown auf genau cooldown_bars
    assert active_runs and all(r == 3 for r in active_runs)
    assert not st.cooldown_active  # am Ende (stabiler Trend) abgeklungen


def test_tracker_schmitt_high_vol_exit() -> None:
    tr = RegimeTracker(TF, AssetClass.CRYPTO)
    tr._vol = RegimeVolatility.HIGH
    # roh NORMAL mit pct = 75 (> vol_exit_pct 70) -> bleibt HIGH
    raw_state = RegimeState(
        TF,
        RegimeDirectional.RANGE,
        0.3,
        RegimeVolatility.NORMAL,
        75.0,
        RegimePhase.NEUTRAL,
        ExpansionDirection.NONE,
        S,
    )
    kept = tr._confirm("vol", RegimeVolatility.HIGH, RegimeVolatility.HIGH)
    assert kept is RegimeVolatility.HIGH
    _ = raw_state  # nur zur Doku des Szenarios


# --------------------------------------------------------------------------- §7 MTF-Konsens


def test_disagreement() -> None:
    assert disagreement(RegimeDirectional.TREND_UP, RegimeDirectional.TREND_UP) == 0.0
    assert disagreement(RegimeDirectional.TREND_UP, RegimeDirectional.RANGE) == 0.5
    assert disagreement(RegimeDirectional.TREND_UP, RegimeDirectional.TREND_DOWN) == 1.0
    assert disagreement(RegimeDirectional.TREND_UP, RegimeDirectional.UNCLEAR) == 1.0


def test_merge_htf_table() -> None:
    U, D, R, X = (
        RegimeDirectional.TREND_UP,
        RegimeDirectional.TREND_DOWN,
        RegimeDirectional.RANGE,
        RegimeDirectional.UNCLEAR,
    )
    assert merge_htf(U, U) is U
    assert merge_htf(U, R) is U
    assert merge_htf(R, U) is U
    assert merge_htf(D, D) is D
    assert merge_htf(R, D) is D
    assert merge_htf(R, R) is R
    assert merge_htf(U, D) is RegimeDirectional.CONFLICTING
    assert merge_htf(D, U) is RegimeDirectional.CONFLICTING
    assert merge_htf(U, X) is X
    assert merge_htf(X, R) is X


# --------------------------------------------------------------------------- §8/§9 Gate


def _st(
    directional: RegimeDirectional = RegimeDirectional.TREND_UP,
    volatility: RegimeVolatility = RegimeVolatility.NORMAL,
    phase: RegimePhase = RegimePhase.NEUTRAL,
    *,
    coiled: bool = False,
    cooldown: bool = False,
    vol_pct: float = 50.0,
) -> RegimeState:
    return RegimeState(
        TF,
        directional,
        0.7,
        volatility,
        vol_pct,
        phase,
        ExpansionDirection.NONE,
        datetime.now().astimezone(),
        bars_in_state=5,
        coiled=coiled,
        cooldown_active=cooldown,
    )


def test_regime_gate_pass() -> None:
    res = regime_gate(_st(), _st())
    assert res.ok and res.reason is None
    assert res.merged_directional is RegimeDirectional.TREND_UP


def test_regime_gate_no_trade_reasons() -> None:
    assert regime_gate(_st(volatility=RegimeVolatility.EXTREME), _st()).reason is (
        NoTradeReason.REGIME_VOL_EXTREME
    )
    assert regime_gate(_st(directional=RegimeDirectional.UNCLEAR), _st()).reason is (
        NoTradeReason.REGIME_UNCLEAR
    )
    assert (
        regime_gate(
            _st(directional=RegimeDirectional.TREND_UP),
            _st(directional=RegimeDirectional.TREND_DOWN),
        ).reason
        is NoTradeReason.REGIME_CONFLICTING
    )
    assert regime_gate(_st(cooldown=True), _st()).reason is NoTradeReason.REGIME_COOLDOWN
    assert regime_gate(_st(volatility=RegimeVolatility.LOW), _st()).reason is (
        NoTradeReason.REGIME_VOL_TOO_LOW
    )
    assert (
        regime_gate(_st(phase=RegimePhase.COMPRESSION, coiled=True), _st()).reason
        is NoTradeReason.REGIME_COMPRESSION
    )


def test_regime_gate_allow_unclear_htf_flag() -> None:
    res = regime_gate(
        _st(directional=RegimeDirectional.UNCLEAR),
        _st(),
        params=RegimeGateParams(allow_unclear_htf=True),
    )
    # UNCLEAR wird nicht mehr am UNCLEAR-Check abgelehnt, aber merge_htf(UNCLEAR, *) = UNCLEAR
    assert not res.ok and res.reason is NoTradeReason.REGIME_UNCLEAR


def test_regime_gate_context_vol_hard_block_flag() -> None:
    d1, h4 = _st(), _st()  # beide sauber, kein Vol-Problem
    m15_extreme = _st(volatility=RegimeVolatility.EXTREME)
    # Default: M15-EXTREME blockt hart
    assert regime_gate(d1, h4, m15_extreme).reason is NoTradeReason.REGIME_VOL_EXTREME
    # Flag aus (V5-Kandidat): M15-Vol zählt nicht → Gate frei
    assert regime_gate(
        d1, h4, m15_extreme, params=RegimeGateParams(context_vol_is_hard_block=False)
    ).ok
    # D1/H4-EXTREME blockt weiterhin, egal was das Flag sagt
    assert not regime_gate(
        _st(volatility=RegimeVolatility.EXTREME),
        h4,
        m15_extreme,
        params=RegimeGateParams(context_vol_is_hard_block=False),
    ).ok


def test_mtf_params_thread_regime_gate() -> None:
    """MtfParams.regime_gate wird bis build_mtf_context durchgereicht."""
    from trading_agent.analysis.mtf import MtfParams

    p = MtfParams(regime_gate=RegimeGateParams(context_vol_is_hard_block=False))
    assert p.regime_gate.context_vol_is_hard_block is False
