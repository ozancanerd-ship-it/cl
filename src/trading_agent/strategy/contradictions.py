"""Widerspruchs-Matrix C1–C12 + Negativfaktoren (``contradictions.md`` §4/§5).

**Grundsatz:** ein starkes positives Signal darf einen harten Negativfaktor **nie** überstimmen;
Widersprüche werden **nicht gemittelt**. Diese Engine liefert ein **explizites** Ergebnis — sie
ändert **nicht stillschweigend** den Score. ``evaluate()`` und/oder die Penalty-Logik konsumieren es.

**Drei Arten von Records:**

* ``HARD_CONFLICT`` (severity ``BLOCK``) — die **matrix-eigenen** harten Ausgänge: **C1**
  (opposing Liquidität darüber gebrochen+gehalten), **C2** (beide Seiten gesweept), **C9 ≥ 50 %**
  (Entry in gegen-D HTF-Zone), **C12** (zwei gegenläufige Setups). Jeder trägt einen
  ``NoTradeReason``.
* ``VETO_ECHO`` (severity ``INFO``) — **C3–C8, C10** sind Restatements vorhandener Vetos
  (V4/V6/V8/V9/V1/V3/V8). Sie werden **nicht re-entschieden**: der ``VetoReport`` entscheidet
  (``contradictions.md`` §6, Schritt 4 vor Schritt 5). Hier nur protokolliert mit ``covered_by_veto``.
* ``NEGATIVE_FACTOR`` (severity ``PENALTY``) — die §5-Abzüge (``messy_sweep`` −8,
  ``proximity_opposing_htf_zone`` −10, ``stale_structure`` −5, ``weak_displacement`` −6,
  ``mtf_partial_disagreement`` −7, ``wide_sl`` −5, ``late_session`` −4). Die Punktwerte sind
  **unkalibriert** (``contradictions.md`` §8); im **MVP** wendet ``evaluate()`` sie **nicht** auf
  den Score an (``ScoreParams.penalties = {}``, C2).

**Kein Double-Counting:** korrelierte Roh-Größen werden über **einen** gemeinsamen Faktor gelesen —
insbesondere C9 (≥ 50 %) und ``proximity_opposing_htf_zone`` (< 50 %) verzweigen nach dem Betrag
**desselben** Confluence-Faktors ``opposing_htf_zone_proximity``.

**Point-in-time / look-ahead-frei / deterministisch. Long/Short-symmetrisch** (Seiten über
``MarketSide.against`` / ``Polarity.of``).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from enum import StrEnum

from trading_agent.analysis.mtf import MtfContext
from trading_agent.core.enums import (
    LiquidityState,
    MarketSide,
    NoTradeReason,
    Timeframe,
    VetoId,
)
from trading_agent.core.version import STRATEGY_VERSION
from trading_agent.strategy.confluence import ConfluenceReport
from trading_agent.strategy.gates import GateReport
from trading_agent.strategy.setup_detection import SetupCandidate, SetupScan
from trading_agent.strategy.veto import VetoReport

_Evidence = Mapping[str, str | float | int | bool | None]
_Hard = Callable[[str, NoTradeReason, str, _Evidence], None]
_Penalty = Callable[[str, float, str, _Evidence, datetime], None]


class ContradictionKind(StrEnum):
    HARD_CONFLICT = "hard_conflict"
    VETO_ECHO = "veto_echo"
    NEGATIVE_FACTOR = "negative_factor"


class ContradictionSeverity(StrEnum):
    BLOCK = "block"
    PENALTY = "penalty"
    INFO = "info"


# C3–C8, C10 → welches Veto sie abbilden (contradictions.md §4)
_VETO_ECHO_ROWS: dict[str, tuple[VetoId, str]] = {
    "C3": (VetoId.V4, "Technischer Bias + HIGH-Impact-USD-Event im Blackout"),
    "C4": (VetoId.V6, "hoher Score + data_confidence < 0.50"),
    "C5": (VetoId.V8, "hoher Score + RR < min_to_tp2"),
    "C6": (VetoId.V9, "Setup-Richtung + korrelierte Exposure am Cap"),
    "C7": (VetoId.V1, "D1/H4 gegensätzliche Trends"),
    "C8": (VetoId.V3, "Ketten-Gates erfüllt + coiled COMPRESSION"),
    "C10": (VetoId.V8, "opposing LiquidityLevel < min_target_room_r × R über dem Entry"),
}


# --------------------------------------------------------------------------------- Parameter


@dataclasses.dataclass(frozen=True, slots=True)
class ContradictionParams:
    sweep_timeframe: Timeframe = Timeframe.M15
    target_timeframes: tuple[Timeframe, ...] = (Timeframe.M15, Timeframe.H4, Timeframe.D1)

    # C1 / C2 Frische-Fenster (in sweep_timeframe-Bars)
    c1_freshness_bars: int = 20
    c2_window_bars: int = 3

    # C9 (contradictions.md §4)
    opposing_zone_overlap_veto: float = 0.50

    # §5 Negativfaktoren
    messy_sweep_points: float = 8.0
    proximity_points: float = 10.0
    stale_structure_points: float = 5.0
    weak_displacement_points: float = 6.0
    mtf_partial_points: float = 7.0
    wide_sl_points: float = 5.0
    late_session_points: float = 4.0

    # §5 Schwellen
    age_saturation_bars: int = 50  # stale_structure
    displacement_min_atr: float = 1.5
    weak_displacement_factor: float = 1.2  # net_move_atr < 1.2 × min_atr
    mtf_partial_band: tuple[float, float] = (0.33, 0.66)
    wide_sl_atr_factor: float = 2.0
    late_session_min: float = 20.0  # < N min bis Session-Ende


# --------------------------------------------------------------------------------- Ausgabe


@dataclasses.dataclass(frozen=True, slots=True)
class ContradictionRecord:
    contradiction_id: str  # "C1".."C12" oder Negativfaktor-Name
    kind: ContradictionKind
    severity: ContradictionSeverity
    reason: str
    penalty_points: float  # 0.0 außer bei NEGATIVE_FACTOR
    no_trade_reason: NoTradeReason | None  # gesetzt bei HARD_CONFLICT
    covered_by_veto: VetoId | None  # gesetzt bei VETO_ECHO
    evidence: _Evidence
    timestamp: datetime
    information_cutoff: datetime


@dataclasses.dataclass(frozen=True, slots=True)
class ContradictionReport:
    instrument: str
    information_cutoff: datetime
    records: tuple[ContradictionRecord, ...]
    strategy_version: str = STRATEGY_VERSION

    @property
    def blocked(self) -> bool:
        return any(r.severity is ContradictionSeverity.BLOCK for r in self.records)

    @property
    def hard_reasons(self) -> tuple[NoTradeReason, ...]:
        return tuple(
            r.no_trade_reason
            for r in self.records
            if r.severity is ContradictionSeverity.BLOCK and r.no_trade_reason is not None
        )

    @property
    def negative_penalties(self) -> dict[str, float]:
        return {
            r.contradiction_id: r.penalty_points
            for r in self.records
            if r.kind is ContradictionKind.NEGATIVE_FACTOR
        }

    @property
    def penalties_total(self) -> float:
        return float(sum(self.negative_penalties.values()))

    @property
    def veto_echoes(self) -> tuple[VetoId, ...]:
        return tuple(r.covered_by_veto for r in self.records if r.covered_by_veto is not None)


# --------------------------------------------------------------------------------- öffentlich


def assess_contradictions(
    mtf: MtfContext,
    candidate: SetupCandidate,
    *,
    confluence: ConfluenceReport,
    gates: GateReport | None = None,
    veto: VetoReport | None = None,
    scan: SetupScan | None = None,
    minutes_to_session_end: float | None = None,
    params: ContradictionParams | None = None,
) -> ContradictionReport:
    """Wertet die C1–C12-Matrix + §5-Negativfaktoren aus. ``veto`` / ``scan`` / ``gates`` sind
    optional — fehlen sie, entfallen die davon abhängigen Zeilen (kein Fake)."""
    p = params or ContradictionParams()
    cutoff = mtf.information_cutoff
    recs: list[ContradictionRecord] = []

    def hard(cid: str, reason: NoTradeReason, msg: str, ev: _Evidence) -> None:
        recs.append(
            ContradictionRecord(
                contradiction_id=cid,
                kind=ContradictionKind.HARD_CONFLICT,
                severity=ContradictionSeverity.BLOCK,
                reason=msg,
                penalty_points=0.0,
                no_trade_reason=reason,
                covered_by_veto=None,
                evidence=ev,
                timestamp=cutoff,
                information_cutoff=cutoff,
            )
        )

    def penalty(name: str, points: float, msg: str, ev: _Evidence, ts: datetime) -> None:
        recs.append(
            ContradictionRecord(
                contradiction_id=name,
                kind=ContradictionKind.NEGATIVE_FACTOR,
                severity=ContradictionSeverity.PENALTY,
                reason=msg,
                penalty_points=points,
                no_trade_reason=None,
                covered_by_veto=None,
                evidence=ev,
                timestamp=ts,
                information_cutoff=cutoff,
            )
        )

    _c1_opposing_breakout(hard, mtf, candidate, p, cutoff)
    _c2_both_sides_swept(hard, mtf, candidate, p, cutoff)
    _c9_opposing_htf_zone(hard, penalty, confluence, p, cutoff)
    _c11_overstretched_break(hard, candidate, cutoff)
    _c12_counter_setup(hard, candidate, scan, cutoff)
    _veto_echoes(recs, veto, cutoff)
    _negative_factors(penalty, mtf, candidate, gates, minutes_to_session_end, p, cutoff)

    return ContradictionReport(
        instrument=mtf.instrument, information_cutoff=cutoff, records=tuple(recs)
    )


# --------------------------------------------------------------------------------- Matrix-Zeilen


def _c1_opposing_breakout(
    hard: _Hard, mtf: MtfContext, cand: SetupCandidate, p: ContradictionParams, cutoff: datetime
) -> None:
    """C1: opposing Liquidität in Richtung D wurde gebrochen **und gehalten** (kein Reclaim)."""
    target = MarketSide.BUY_SIDE if cand.direction.sign > 0 else MarketSide.SELL_SIDE
    price = _entry_price(cand)
    horizon = timedelta(seconds=p.sweep_timeframe.seconds * p.c1_freshness_bars)
    for tf in p.target_timeframes:
        c = mtf.tf(tf)
        if c is None:
            continue
        for lv in c.liquidity:
            if lv.side is not target or lv.state is not LiquidityState.BROKEN:
                continue
            if price is not None and cand.direction.sign * (lv.price - price) <= 0:
                continue  # nur Ziele *in* Richtung D
            if lv.swept_at is not None and (cutoff - lv.swept_at) > horizon:
                continue  # nicht mehr frisch
            hard(
                "C1",
                NoTradeReason.OPPOSING_LIQUIDITY_BREAKOUT,
                f"opposing {lv.type.value} ({tf.value}) in Richtung D bereits gebrochen und gehalten",
                {
                    "level_type": lv.type.value,
                    "level_price": lv.price,
                    "timeframe": tf.value,
                    "swept_at": lv.swept_at.isoformat() if lv.swept_at is not None else None,
                },
            )
            return


def _c2_both_sides_swept(
    hard: _Hard, mtf: MtfContext, cand: SetupCandidate, p: ContradictionParams, cutoff: datetime
) -> None:
    """C2: im Sweep-Fenster wurde **beide** Seiten genommen ⇒ kein Edge (``MESSY_LIQUIDITY``)."""
    c = mtf.tf(p.sweep_timeframe)
    if c is None or cand.sweep is None:
        return
    window = timedelta(seconds=p.sweep_timeframe.seconds * p.c2_window_bars)
    anchor = cand.sweep.reclaim_bar
    sides: set[MarketSide] = set()
    for lv in c.liquidity:
        if lv.state is not LiquidityState.SWEPT or lv.swept_at is None:
            continue
        if abs(lv.swept_at - anchor) <= window:
            sides.add(lv.side)
    if MarketSide.BUY_SIDE in sides and MarketSide.SELL_SIDE in sides:
        hard(
            "C2",
            NoTradeReason.MESSY_LIQUIDITY,
            "beide Liquiditäts-Seiten im selben Fenster gesweept — kein Edge",
            {"window_bars": p.c2_window_bars, "sides": "buy_side+sell_side"},
        )


def _c9_opposing_htf_zone(
    hard: _Hard,
    penalty: _Penalty,
    confluence: ConfluenceReport,
    p: ContradictionParams,
    cutoff: datetime,
) -> None:
    """C9: Entry-Zone innerhalb einer gegen-D HTF-Zone. **Ein** Confluence-Faktor, Verzweigung
    nach Betrag: ≥ 50 % Überlappung ⇒ hart; sonst Negativfaktor ``proximity_opposing_htf_zone``."""
    f = next((x for x in confluence.factors if x.factor == "opposing_htf_zone_proximity"), None)
    if f is None:
        return
    overlap = -f.contribution  # contribution ist negativ (= −overlap)
    if overlap <= 0.0:
        return
    if overlap >= p.opposing_zone_overlap_veto:
        hard(
            "C9",
            NoTradeReason.ENTRY_INTO_OPPOSING_HTF_ZONE,
            f"Entry-Zone überlappt eine gegen-D HTF-Zone zu {overlap * 100:.0f}% (≥ 50 %)",
            {"overlap": round(overlap, 4), "confluence_factor": "opposing_htf_zone_proximity"},
        )
    else:
        penalty(
            "proximity_opposing_htf_zone",
            p.proximity_points,
            f"Entry-Zone nahe einer gegen-D HTF-Zone ({overlap * 100:.0f}% Überlappung)",
            {"overlap": round(overlap, 4)},
            cutoff,
        )


def _c11_overstretched_break(hard: _Hard, cand: SetupCandidate, cutoff: datetime) -> None:
    """C11: der Struktur-Bruch war überdehnt ⇒ zählt nicht (``NO_STRUCTURE_SHIFT``).

    Die FSM filtert das i. d. R. bereits; hier nur als Absicherung, falls ein roher Bruch
    durchgereicht wurde."""
    brk = cand.structure_break
    if brk is None:
        return
    if cand.abort_reason is NoTradeReason.NO_STRUCTURE_SHIFT:
        hard(
            "C11",
            NoTradeReason.NO_STRUCTURE_SHIFT,
            "Struktur-Bruch überdehnt / Kette hierfür abgebrochen",
            {"break_distance_atr": round(brk.break_distance_atr, 4)},
        )


def _c12_counter_setup(
    hard: _Hard, cand: SetupCandidate, scan: SetupScan | None, cutoff: datetime
) -> None:
    """C12: zweites, gegenläufiges ARMED-Setup auf demselben Instrument ⇒ **beide** NO_TRADE."""
    if scan is None:
        return
    opp = _opposite_sign(cand)
    for other in scan.candidates:
        if other.setup_id == cand.setup_id:
            continue
        if other.is_armed and other.direction.sign == opp:
            hard(
                "C12",
                NoTradeReason.COUNTER_SETUP_CONFLICT,
                "gegenläufiges ARMED-Setup auf demselben Instrument",
                {"counter_setup_id": other.setup_id, "counter_direction": other.direction.value},
            )
            return


def _veto_echoes(
    recs: list[ContradictionRecord], veto: VetoReport | None, cutoff: datetime
) -> None:
    if veto is None:
        return
    fired = set(veto.veto_ids)
    for cid, (vid, msg) in _VETO_ECHO_ROWS.items():
        if vid in fired:
            recs.append(
                ContradictionRecord(
                    contradiction_id=cid,
                    kind=ContradictionKind.VETO_ECHO,
                    severity=ContradictionSeverity.INFO,
                    reason=f"{msg} — abgedeckt von Veto {vid.value}",
                    penalty_points=0.0,
                    no_trade_reason=None,
                    covered_by_veto=vid,
                    evidence={"veto": vid.value},
                    timestamp=cutoff,
                    information_cutoff=cutoff,
                )
            )


# --------------------------------------------------------------------------------- §5 Negativfaktoren


def _negative_factors(
    penalty: _Penalty,
    mtf: MtfContext,
    cand: SetupCandidate,
    gates: GateReport | None,
    minutes_to_session_end: float | None,
    p: ContradictionParams,
    cutoff: datetime,
) -> None:
    # messy_sweep — 2 Pools DERSELBEN Seite im Fenster (contradictions.md §5)
    n_pools = _same_side_pools(mtf, cand, p)
    if 1 < n_pools <= 2:
        penalty(
            "messy_sweep",
            p.messy_sweep_points,
            f"{n_pools} gesweepte Pools derselben Seite im Fenster",
            {"pools_in_window": n_pools},
            cutoff,
        )

    # stale_structure — beteiligte Swings zu alt
    stale = _stale_structure(cand, p, cutoff)
    if stale is not None:
        penalty(
            "stale_structure",
            p.stale_structure_points,
            f"beteiligter Swing {stale:.0f} Bars seit Bestätigung (> {p.age_saturation_bars})",
            {"max_bars_since_confirmation": round(stale, 1)},
            cutoff,
        )

    # weak_displacement — knapp über der Mindestgröße
    disp = cand.displacement
    if disp is not None and disp.net_move_atr < p.weak_displacement_factor * p.displacement_min_atr:
        penalty(
            "weak_displacement",
            p.weak_displacement_points,
            f"net_move {disp.net_move_atr:.2f} ATR < "
            f"{p.weak_displacement_factor} × {p.displacement_min_atr}",
            {"net_move_atr": round(disp.net_move_atr, 4)},
            disp.end_bar,
        )

    # mtf_partial_disagreement — Band (0.33, 0.66)
    dis = mtf.htf_regime_gate.disagreement
    lo, hi = p.mtf_partial_band
    if lo < dis < hi:
        penalty(
            "mtf_partial_disagreement",
            p.mtf_partial_points,
            f"mtf_disagreement {dis:.2f} im Teilkonflikt-Band ({lo}, {hi})",
            {"mtf_disagreement": round(dis, 4)},
            cutoff,
        )

    # wide_sl — SL-Distanz > 2 × ATR(sweep_tf), aber unter Cap (sonst wäre es V10)
    if gates is not None and gates.rr is not None and gates.rr.geometry is not None:
        r_dist = gates.rr.geometry.r_distance
        atr_s = _tf_atr(mtf, p.sweep_timeframe)
        if atr_s > 0.0 and r_dist > p.wide_sl_atr_factor * atr_s:
            penalty(
                "wide_sl",
                p.wide_sl_points,
                f"SL-Distanz {r_dist:.4f} > {p.wide_sl_atr_factor} × ATR({atr_s:.4f})",
                {"r_distance": round(r_dist, 4), "atr_sweep_tf": round(atr_s, 4)},
                cutoff,
            )

    # late_session — Entry in den letzten Minuten einer Session
    if minutes_to_session_end is not None and minutes_to_session_end < p.late_session_min:
        penalty(
            "late_session",
            p.late_session_points,
            f"{minutes_to_session_end:.0f} min bis Session-Ende < {p.late_session_min:.0f}",
            {"minutes_to_session_end": minutes_to_session_end},
            cutoff,
        )


# --------------------------------------------------------------------------------- intern


def _entry_price(cand: SetupCandidate) -> float | None:
    zone = cand.entry_zone
    if zone is None:
        return None
    return zone.zone_high if cand.direction.sign > 0 else zone.zone_low


def _opposite_sign(cand: SetupCandidate) -> int:
    return -cand.direction.sign


def _tf_atr(mtf: MtfContext, tf: Timeframe) -> float:
    c = mtf.tf(tf)
    return c.atr if c is not None else 0.0


def _same_side_pools(mtf: MtfContext, cand: SetupCandidate, p: ContradictionParams) -> int:
    c = mtf.tf(p.sweep_timeframe)
    if c is None or cand.sweep is None:
        return 1
    window = timedelta(seconds=p.sweep_timeframe.seconds * p.c2_window_bars * 2)
    anchor = cand.sweep.reclaim_bar
    own_key = (cand.liquidity.type, round(cand.liquidity.price, 6))
    own_side = cand.liquidity.side
    n = 1
    for lv in c.liquidity:
        if (lv.type, round(lv.price, 6)) == own_key or lv.side is not own_side:
            continue
        if lv.swept_at is not None and abs(lv.swept_at - anchor) <= window:
            n += 1
    return n


def _stale_structure(
    cand: SetupCandidate, p: ContradictionParams, cutoff: datetime
) -> float | None:
    swings = list(cand.liquidity.members)
    if cand.structure_break is not None and cand.structure_break.broken_swing is not None:
        swings.append(cand.structure_break.broken_swing)
    worst: float | None = None
    for s in swings:
        bars = (cutoff - s.confirmed_at).total_seconds() / s.timeframe.seconds
        if bars > p.age_saturation_bars and (worst is None or bars > worst):
            worst = bars
    return worst


__all__ = [
    "ContradictionKind",
    "ContradictionParams",
    "ContradictionRecord",
    "ContradictionReport",
    "ContradictionSeverity",
    "assess_contradictions",
]
