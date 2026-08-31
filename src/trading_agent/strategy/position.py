"""Exit / Position Management — **Paper / Simulation only** (kein Echtgeld, keine Broker-Order).

Die Entry-Analyse endet nicht am Fill. Eine offene Position wird **weiter analysiert**: Teilexits,
SL-Nachzug (Break-Even, Trail), Runner, Struktur-Invalidierung, ``EXIT_REQUIRED``, Close, Expiry.
Exit-Qualität zählt so viel wie Entry-Qualität.

Deterministisch & look-ahead-frei: ``on_bar`` verarbeitet **eine abgeschlossene** Bar; treffen SL
und TP in derselben Bar, gilt konservativ der **SL zuerst** (``worst_case_fill``). Alle R-Größen
beziehen sich auf ``r_unit = |entry - initial_sl|`` (das 1R der Position).

Der ``PaperPosition``-Record ist frozen; jede Zustandsänderung erzeugt eine neue Instanz. Die
Anbindung an den Signal-Lifecycle läuft über :func:`signal_state_for` → ``strategy.signal``.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from enum import StrEnum

from trading_agent.core.enums import Direction
from trading_agent.core.version import STRATEGY_VERSION
from trading_agent.strategy.costs import CostConfig, funding_cost_r, leg_cost_r
from trading_agent.strategy.decision import Decision
from trading_agent.strategy.signal import SignalState

# --------------------------------------------------------------------------------- Preis-Bar


@dataclasses.dataclass(frozen=True, slots=True)
class PriceBar:
    """Minimale, brokerunabhängige abgeschlossene Bar für die Simulation."""

    timestamp: datetime  # close_time der Bar
    high: float
    low: float
    close: float

    def hits_at_or_above(self, price: float) -> bool:
        return self.high >= price

    def hits_at_or_below(self, price: float) -> bool:
        return self.low <= price


# --------------------------------------------------------------------------------- Enums


class PositionState(StrEnum):
    PENDING = "pending"  # Limit am proximalen Zonenrand, noch nicht getriggert
    OPEN = "open"  # gefüllt, volle Größe
    PARTIAL = "partial"  # nach TP1/TP2 teilgeschlossen, Runner aktiv
    EXIT_REQUIRED = "exit_required"  # Re-Analyse verlangt Ausstieg, noch nicht ausgeführt
    CLOSED = "closed"
    EXPIRED = "expired"  # Pending nie gefüllt

    @property
    def is_live(self) -> bool:
        return self in (PositionState.OPEN, PositionState.PARTIAL, PositionState.EXIT_REQUIRED)

    @property
    def is_terminal(self) -> bool:
        return self in (PositionState.CLOSED, PositionState.EXPIRED)


class ExitReason(StrEnum):
    TP1 = "tp1"
    TP2 = "tp2"
    TP3 = "tp3"
    STOP_LOSS = "stop_loss"
    BREAKEVEN_STOP = "breakeven_stop"
    TRAIL_STOP = "trail_stop"
    STRUCTURE_INVALIDATION = "structure_invalidation"
    MANUAL_EXIT_REQUEST = "manual_exit_request"
    EXPIRY = "expiry"
    # Replay-Fenster zu Ende: Position wird zum letzten bekannten Close geschlossen.
    END_OF_DATA = "end_of_data"


class PositionEvent(StrEnum):
    NO_CHANGE = "no_change"
    OPENED = "opened"
    FILLED = "filled"
    TP1_REACHED = "tp1_reached"
    TP2_REACHED = "tp2_reached"
    TP3_REACHED = "tp3_reached"
    PARTIAL_EXIT = "partial_exit"
    STOP_MOVED_BE = "stop_moved_be"
    TRAIL_UPDATED = "trail_updated"
    SL_HIT = "sl_hit"
    EXIT_REQUESTED = "exit_requested"
    CLOSED = "closed"
    EXPIRED = "expired"


# --------------------------------------------------------------------------------- Records


@dataclasses.dataclass(frozen=True, slots=True)
class PositionLeg:
    """Ein einzelner (Teil-)Exit."""

    fraction: float  # Anteil der Ursprungsgröße
    price: float
    r_multiple: float  # realisiertes R dieses Legs (fraction bereits eingerechnet)
    reason: ExitReason
    at: datetime


@dataclasses.dataclass(frozen=True, slots=True)
class PaperPosition:
    position_id: str
    signal_id: str
    instrument: str
    direction: Direction
    opened_at: datetime
    information_cutoff: datetime

    # Plan (unveränderlich)
    entry: float
    initial_sl: float
    tp1: float
    tp2: float
    tp3_ref: str | None

    # Laufender Zustand
    state: PositionState
    effective_sl: float
    open_fraction: float  # noch offener Anteil der Ursprungsgröße (1.0 → 0.0)
    realized_r: float
    legs: tuple[PositionLeg, ...]

    # Tracking
    bars_pending: int
    bars_held: int
    mfe_r: float  # maximum favourable excursion in R
    mae_r: float  # maximum adverse excursion in R
    last_price: float
    tp1_done: bool
    tp2_done: bool
    tp3_done: bool
    sl_at_be: bool

    entry_ts: datetime | None = None  # Zeitpunkt des Fills (None solange PENDING)
    closed_at: datetime | None = None
    close_reason: ExitReason | None = None

    # ---- Kosten (alle in R; 0.0 wenn kein Kostenmodell konfiguriert) ----------
    gross_realized_r: float = 0.0  # Σ Leg-R **vor** Kosten
    entry_cost_r: float = 0.0
    exit_cost_r: float = 0.0
    funding_r: float = 0.0
    fees_r: float = 0.0
    slippage_r: float = 0.0

    strategy_version: str = STRATEGY_VERSION

    # ---- abgeleitete Größen ----------------------------------------------------
    @property
    def total_cost_r(self) -> float:
        return self.entry_cost_r + self.exit_cost_r + self.funding_r

    @property
    def tp_level_reached(self) -> int:
        """Höchstes erreichtes TP (0 = keins)."""
        return 3 if self.tp3_done else 2 if self.tp2_done else 1 if self.tp1_done else 0

    @property
    def r_unit(self) -> float:
        return abs(self.entry - self.initial_sl)

    @property
    def is_open(self) -> bool:
        return self.state.is_live

    def r_at(self, price: float) -> float:
        if self.r_unit == 0.0:
            return 0.0
        return (price - self.entry) * self.direction.sign / self.r_unit

    @property
    def unrealized_r(self) -> float:
        return self.open_fraction * self.r_at(self.last_price)

    @property
    def total_r(self) -> float:
        return self.realized_r + (0.0 if self.state.is_terminal else self.unrealized_r)


@dataclasses.dataclass(frozen=True, slots=True)
class PositionUpdate:
    position: PaperPosition
    event: PositionEvent
    exits: tuple[PositionLeg, ...]
    changes: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return self.event is not PositionEvent.NO_CHANGE

    @property
    def signal_state(self) -> SignalState:
        return signal_state_for(self.position)


# --------------------------------------------------------------------------------- Parameter


@dataclasses.dataclass(frozen=True, slots=True)
class PositionParams:
    tp1_close_fraction: float = 0.5
    tp2_close_fraction: float = 0.3  # Rest (0.2) = Runner
    move_sl_to_be_after_tp1: bool = True
    be_offset_r: float = 0.0  # >0 sperrt einen kleinen Gewinn ein
    trail_after_tp2: bool = True  # Runner-SL auf TP1-Preis nachziehen
    pending_expiry_bars: int = 12
    worst_case_fill: bool = True  # SL vor TP, wenn beide in einer Bar liegen
    bar_seconds: int = 300  # Länge einer Fill-Bar (M5) — für die Funding-Akkumulation


# --------------------------------------------------------------------------------- Mapping


def signal_state_for(pos: PaperPosition) -> SignalState:
    """``PaperPosition`` → Lebenszyklus-State fürs ``SignalTracker``-Ingest."""
    if pos.state is PositionState.PENDING:
        return SignalState.ARMED
    if pos.state is PositionState.EXPIRED:
        return SignalState.EXPIRED
    if pos.state is PositionState.EXIT_REQUIRED:
        return SignalState.EXIT_REQUIRED
    if pos.state is PositionState.CLOSED:
        return SignalState.CLOSED
    if pos.tp3_done:
        return SignalState.TP3_REACHED
    if pos.tp2_done:
        return SignalState.TP2_REACHED
    if pos.tp1_done:
        return SignalState.TP1_REACHED
    if pos.sl_at_be or pos.bars_held > 0:
        return SignalState.MANAGED
    return SignalState.TRIGGERED


# --------------------------------------------------------------------------------- Manager


class PositionManager:
    """Zustandslos — jede Methode bekommt die Position und gibt eine neue zurück."""

    def __init__(
        self,
        *,
        params: PositionParams | None = None,
        cost: CostConfig | None = None,
    ) -> None:
        self._p = params or PositionParams()
        self._cost = cost or CostConfig()  # Default: alle Sätze 0.0 (kein Kostenmodell)

    # ---- Kosten (alle in R) ---------------------------------------------------
    def _entry_cost_r(self, pos: PaperPosition) -> tuple[float, float, float]:
        c = leg_cost_r(
            self._cost, price=pos.entry, r_unit=pos.r_unit, is_maker=self._cost.entry_is_maker
        )
        return c.total_r, c.fee_r, c.spread_r + c.slippage_r + c.impact_r

    def _on_fill(self, pos: PaperPosition, *, at: datetime) -> PaperPosition:
        """Fill: entry_ts setzen + einmalige Entry-Kosten buchen."""
        total, _fee, _other = self._entry_cost_r(pos)
        return dataclasses.replace(pos, entry_ts=at, entry_cost_r=total)

    def _settle(self, pos: PaperPosition) -> PaperPosition:
        """Rechnet Kosten aus den Legs + Haltedauer und setzt ``realized_r`` = **netto**."""
        if self._cost.is_zero:
            gross = sum(leg.r_multiple for leg in pos.legs)
            return dataclasses.replace(pos, realized_r=gross, gross_realized_r=gross)
        gross = sum(leg.r_multiple for leg in pos.legs)
        exit_fee_r = 0.0
        exit_other_r = 0.0
        for leg in pos.legs:
            lc = leg_cost_r(
                self._cost,
                price=leg.price,
                r_unit=pos.r_unit,
                is_maker=self._cost.exit_is_maker,
            )
            exit_fee_r += leg.fraction * lc.fee_r
            exit_other_r += leg.fraction * (lc.spread_r + lc.slippage_r + lc.impact_r)
        funding = (
            funding_cost_r(
                self._cost,
                price=pos.entry,
                r_unit=pos.r_unit,
                direction=pos.direction,
                bars_held=pos.bars_held,
                bar_seconds=self._p.bar_seconds,
            )
            if pos.entry_ts is not None
            else 0.0
        )
        exit_cost = exit_fee_r + exit_other_r
        _, entry_fee_r, entry_other_r = self._entry_cost_r(pos)
        net = gross - pos.entry_cost_r - exit_cost - funding
        return dataclasses.replace(
            pos,
            gross_realized_r=gross,
            realized_r=net,
            exit_cost_r=exit_cost,
            funding_r=funding,
            fees_r=entry_fee_r + exit_fee_r,
            slippage_r=entry_other_r + exit_other_r,
        )

    # ---- Eröffnung -----------------------------------------------------------------
    def open(self, decision: Decision, *, at: datetime, pending: bool = True) -> PaperPosition:
        if not decision.is_actionable:
            raise ValueError(f"open() braucht eine BUY/SELL-Decision, nicht {decision.decision}")
        assert decision.direction is not None
        assert decision.entry is not None and decision.sl is not None
        assert decision.tp1 is not None and decision.tp2 is not None
        pos = PaperPosition(
            position_id=decision.setup_id,
            signal_id=decision.setup_id,
            instrument=decision.instrument,
            direction=decision.direction,
            opened_at=at,
            information_cutoff=decision.information_cutoff,
            entry=decision.entry,
            initial_sl=decision.sl,
            tp1=decision.tp1,
            tp2=decision.tp2,
            tp3_ref=decision.tp3_ref,
            state=PositionState.PENDING if pending else PositionState.OPEN,
            entry_ts=None if pending else at,
            effective_sl=decision.sl,
            open_fraction=1.0,
            realized_r=0.0,
            legs=(),
            bars_pending=0,
            bars_held=0,
            mfe_r=0.0,
            mae_r=0.0,
            last_price=decision.entry,
            tp1_done=False,
            tp2_done=False,
            tp3_done=False,
            sl_at_be=False,
        )
        return pos if pending else self._on_fill(pos, at=at)

    # ---- Bar-für-Bar-Simulation --------------------------------------------------
    def on_bar(self, pos: PaperPosition, bar: PriceBar) -> PositionUpdate:
        if pos.state.is_terminal:
            return PositionUpdate(pos, PositionEvent.NO_CHANGE, (), ())
        if pos.state is PositionState.PENDING:
            return self._pending_bar(pos, bar)
        return self._live_bar(pos, bar)

    def _pending_bar(self, pos: PaperPosition, bar: PriceBar) -> PositionUpdate:
        long = pos.direction is Direction.LONG
        triggered = bar.hits_at_or_below(pos.entry) if long else bar.hits_at_or_above(pos.entry)
        if triggered:
            filled = self._on_fill(
                dataclasses.replace(
                    pos,
                    state=PositionState.OPEN,
                    last_price=bar.close,
                    bars_pending=pos.bars_pending + 1,
                ),
                at=bar.timestamp,
            )
            return PositionUpdate(filled, PositionEvent.FILLED, (), ("limit filled @ entry",))
        bars_pending = pos.bars_pending + 1
        if bars_pending >= self._p.pending_expiry_bars:
            expired = dataclasses.replace(
                pos,
                state=PositionState.EXPIRED,
                bars_pending=bars_pending,
                closed_at=bar.timestamp,
                close_reason=ExitReason.EXPIRY,
            )
            return PositionUpdate(
                expired, PositionEvent.EXPIRED, (), ("pending expired — never triggered",)
            )
        return PositionUpdate(
            dataclasses.replace(pos, bars_pending=bars_pending), PositionEvent.NO_CHANGE, (), ()
        )

    def _live_bar(self, pos: PaperPosition, bar: PriceBar) -> PositionUpdate:
        long = pos.direction is Direction.LONG
        p = self._p
        changes: list[str] = []
        exits: list[PositionLeg] = []

        state = pos
        r_hi = state.r_at(bar.high if long else bar.low)  # günstigstes R der Bar
        r_lo = state.r_at(bar.low if long else bar.high)  # ungünstigstes R der Bar
        mfe_r = max(state.mfe_r, r_hi)
        mae_r = min(state.mae_r, r_lo)

        sl_hit = (
            bar.hits_at_or_below(state.effective_sl)
            if long
            else bar.hits_at_or_above(state.effective_sl)
        )

        # 1) worst-case: SL zuerst
        if sl_hit and p.worst_case_fill:
            return self._close_on_sl(state, bar, mfe_r, mae_r)

        # 2) Take-Profits in Reihenfolge
        def tp_hit(level: float) -> bool:
            return bar.hits_at_or_above(level) if long else bar.hits_at_or_below(level)

        if not state.tp1_done and tp_hit(state.tp1):
            frac = min(p.tp1_close_fraction, state.open_fraction)
            leg = PositionLeg(
                frac, state.tp1, frac * state.r_at(state.tp1), ExitReason.TP1, bar.timestamp
            )
            exits.append(leg)
            new_sl = state.effective_sl
            sl_at_be = state.sl_at_be
            if p.move_sl_to_be_after_tp1:
                new_sl = state.entry + p.be_offset_r * state.r_unit * state.direction.sign
                sl_at_be = True
                changes.append("SL → break-even nach TP1")
            state = dataclasses.replace(
                state,
                state=PositionState.PARTIAL,
                open_fraction=round(state.open_fraction - frac, 10),
                realized_r=state.realized_r + leg.r_multiple,
                legs=(*state.legs, leg),
                tp1_done=True,
                effective_sl=new_sl,
                sl_at_be=sl_at_be,
            )
            changes.append(f"TP1 hit — {frac:.0%} raus @ {state.tp1}")

        if not state.tp2_done and tp_hit(state.tp2):
            frac = min(p.tp2_close_fraction, state.open_fraction)
            leg = PositionLeg(
                frac, state.tp2, frac * state.r_at(state.tp2), ExitReason.TP2, bar.timestamp
            )
            exits.append(leg)
            new_sl = state.effective_sl
            if p.trail_after_tp2:
                new_sl = state.tp1
                changes.append("Runner-SL → TP1 nach TP2")
            state = dataclasses.replace(
                state,
                state=PositionState.PARTIAL,
                open_fraction=round(state.open_fraction - frac, 10),
                realized_r=state.realized_r + leg.r_multiple,
                legs=(*state.legs, leg),
                tp2_done=True,
                effective_sl=new_sl,
            )
            changes.append(f"TP2 hit — {frac:.0%} raus @ {state.tp2}")

        # 3) SL nach den TPs (nicht-worst-case Pfad, oder BE/Trail-Stop auf Runner)
        if not p.worst_case_fill and sl_hit:
            return self._close_on_sl(state, bar, mfe_r, mae_r)
        sl_hit_after = (
            bar.hits_at_or_below(state.effective_sl)
            if long
            else bar.hits_at_or_above(state.effective_sl)
        )
        if state is not pos and sl_hit_after and state.effective_sl != pos.effective_sl:
            # SL wurde in dieser Bar nachgezogen und der neue Level liegt schon im Bar-Range
            closed = self._close_on_sl(state, bar, mfe_r, mae_r)
            return PositionUpdate(
                closed.position,
                closed.event,
                tuple(exits) + closed.exits,
                tuple(changes) + closed.changes,
            )

        # 4) Runner voll bei TP3-Referenz? (kein numerischer Level → nur markieren)
        state = dataclasses.replace(
            state, bars_held=state.bars_held + 1, mfe_r=mfe_r, mae_r=mae_r, last_price=bar.close
        )
        if state.open_fraction <= 1e-9 and not state.state.is_terminal:
            state = dataclasses.replace(
                state,
                state=PositionState.CLOSED,
                closed_at=bar.timestamp,
                close_reason=exits[-1].reason if exits else ExitReason.TP2,
            )
            changes.append("vollständig über TPs geschlossen")

        state = self._settle(state)
        event = _event_from(state, pos, exits)
        return PositionUpdate(state, event, tuple(exits), tuple(changes))

    def _close_on_sl(
        self, pos: PaperPosition, bar: PriceBar, mfe_r: float, mae_r: float
    ) -> PositionUpdate:
        frac = pos.open_fraction
        reason = (
            ExitReason.BREAKEVEN_STOP
            if pos.sl_at_be and not pos.tp2_done
            else ExitReason.TRAIL_STOP
            if pos.tp2_done
            else ExitReason.STOP_LOSS
        )
        leg = PositionLeg(
            frac, pos.effective_sl, frac * pos.r_at(pos.effective_sl), reason, bar.timestamp
        )
        closed = self._settle(
            dataclasses.replace(
                pos,
                state=PositionState.CLOSED,
                open_fraction=0.0,
                legs=(*pos.legs, leg),
                bars_held=pos.bars_held + 1,
                mfe_r=mfe_r,
                mae_r=mae_r,
                last_price=bar.close,
                closed_at=bar.timestamp,
                close_reason=reason,
            )
        )
        return PositionUpdate(
            closed, PositionEvent.SL_HIT, (leg,), (f"{reason.value} @ {pos.effective_sl}",)
        )

    # ---- Re-Analyse: Ausstieg verlangen -----------------------------------------
    def on_reevaluation(self, pos: PaperPosition, decision: Decision) -> PositionUpdate:
        """Eine frische Decision zum selben Setup. Kippt die Analyse (Invalidierung oder
        Gegenrichtung), wird ``EXIT_REQUIRED`` gesetzt — der eigentliche Close bleibt ein
        bewusster, getrennter Schritt (:meth:`close`)."""
        if not pos.state.is_live or pos.state is PositionState.EXIT_REQUIRED:
            return PositionUpdate(pos, PositionEvent.NO_CHANGE, (), ())
        flipped = decision.direction is not None and decision.direction is pos.direction.opposite
        invalidated = bool(decision.reason_codes) or bool(decision.vetoes)
        if flipped or invalidated:
            why = "Gegen-Setup" if flipped else "Setup invalidiert / hartes Veto"
            new = dataclasses.replace(pos, state=PositionState.EXIT_REQUIRED)
            return PositionUpdate(
                new, PositionEvent.EXIT_REQUESTED, (), (f"EXIT_REQUIRED — {why}",)
            )
        return PositionUpdate(pos, PositionEvent.NO_CHANGE, (), ())

    def request_exit(
        self, pos: PaperPosition, *, reason: ExitReason = ExitReason.MANUAL_EXIT_REQUEST
    ) -> PositionUpdate:
        if not pos.state.is_live:
            return PositionUpdate(pos, PositionEvent.NO_CHANGE, (), ())
        new = dataclasses.replace(pos, state=PositionState.EXIT_REQUIRED)
        return PositionUpdate(
            new, PositionEvent.EXIT_REQUESTED, (), (f"exit requested — {reason.value}",)
        )

    def close(
        self, pos: PaperPosition, *, price: float, at: datetime, reason: ExitReason
    ) -> PositionUpdate:
        if pos.state.is_terminal:
            return PositionUpdate(pos, PositionEvent.NO_CHANGE, (), ())
        frac = pos.open_fraction
        leg = PositionLeg(frac, price, frac * pos.r_at(price), reason, at)
        closed = self._settle(
            dataclasses.replace(
                pos,
                state=PositionState.CLOSED,
                open_fraction=0.0,
                legs=(*pos.legs, leg),
                last_price=price,
                closed_at=at,
                close_reason=reason,
            )
        )
        return PositionUpdate(
            closed, PositionEvent.CLOSED, (leg,), (f"closed @ {price} — {reason.value}",)
        )


def _event_from(
    state: PaperPosition, prev: PaperPosition, exits: list[PositionLeg]
) -> PositionEvent:
    if state.state is PositionState.CLOSED:
        return PositionEvent.CLOSED
    if state.tp3_done and not prev.tp3_done:
        return PositionEvent.TP3_REACHED
    if state.tp2_done and not prev.tp2_done:
        return PositionEvent.TP2_REACHED
    if state.tp1_done and not prev.tp1_done:
        return PositionEvent.TP1_REACHED
    if exits:
        return PositionEvent.PARTIAL_EXIT
    if state.sl_at_be and not prev.sl_at_be:
        return PositionEvent.STOP_MOVED_BE
    return PositionEvent.NO_CHANGE


__all__ = [
    "ExitReason",
    "PaperPosition",
    "PositionEvent",
    "PositionLeg",
    "PositionManager",
    "PositionParams",
    "PositionState",
    "PositionUpdate",
    "PriceBar",
    "signal_state_for",
]
