"""Recovery — Snapshot laden, Lücke berechnen, Laufzeit-Zustand rekonstruieren (M-01).

* :func:`gap_bars` / :func:`backfillable` — wie groß ist die Lücke seit dem letzten
  verarbeiteten Bar, und deckt der REST-Verlauf sie ab?
* :func:`paper_position_to_dict` / :func:`paper_position_from_dict` — verlustfreier Round-Trip
  einer :class:`~trading_agent.strategy.position.PaperPosition` (frozen dataclass) durch JSON.

**Fail-safe:** ein unlesbarer / unvollständiger Positions-Eintrag wird **übersprungen** und
protokolliert — lieber eine Position „vergessen" (sie wird nicht doppelt geöffnet, weil der
Engine sie schlicht nicht kennt) als korrupten State laden.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from trading_agent.core.enums import Direction, Timeframe
from trading_agent.core.time import ensure_utc, parse_timestamp
from trading_agent.strategy.position import (
    ExitReason,
    PaperPosition,
    PositionLeg,
    PositionState,
)

_log = logging.getLogger("trading_agent.state.recovery")

# konservative Obergrenze, wie weit die public-REST-Verläufe zurückreichen (M5-Bars):
# Kraken ~720, Bybit ~1000 — wir nehmen das Minimum.
REST_M5_HISTORY_BARS = 700


def gap_bars(last_processed: datetime, now: datetime, timeframe: Timeframe) -> int:
    """Anzahl vollständiger ``timeframe``-Bars zwischen ``last_processed`` und ``now`` (>= 0)."""
    delta = (ensure_utc(now) - ensure_utc(last_processed)).total_seconds()
    return max(0, int(delta // timeframe.seconds) - 1)


def backfillable(gap: int, *, limit: int = REST_M5_HISTORY_BARS) -> bool:
    return 0 < gap <= limit


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return ensure_utc(parse_timestamp(value))


def paper_position_to_dict(pos: PaperPosition) -> dict[str, Any]:
    return {
        "position_id": pos.position_id,
        "signal_id": pos.signal_id,
        "instrument": pos.instrument,
        "direction": pos.direction.value,
        "opened_at": ensure_utc(pos.opened_at).isoformat(),
        "information_cutoff": ensure_utc(pos.information_cutoff).isoformat(),
        "entry": pos.entry,
        "initial_sl": pos.initial_sl,
        "tp1": pos.tp1,
        "tp2": pos.tp2,
        "tp3_ref": pos.tp3_ref,
        "state": pos.state.value,
        "effective_sl": pos.effective_sl,
        "open_fraction": pos.open_fraction,
        "realized_r": pos.realized_r,
        "legs": [
            {
                "fraction": leg.fraction,
                "price": leg.price,
                "r_multiple": leg.r_multiple,
                "reason": leg.reason.value,
                "at": ensure_utc(leg.at).isoformat(),
            }
            for leg in pos.legs
        ],
        "bars_pending": pos.bars_pending,
        "bars_held": pos.bars_held,
        "mfe_r": pos.mfe_r,
        "mae_r": pos.mae_r,
        "last_price": pos.last_price,
        "tp1_done": pos.tp1_done,
        "tp2_done": pos.tp2_done,
        "tp3_done": pos.tp3_done,
        "sl_at_be": pos.sl_at_be,
        "entry_ts": ensure_utc(pos.entry_ts).isoformat() if pos.entry_ts else None,
        "closed_at": ensure_utc(pos.closed_at).isoformat() if pos.closed_at else None,
        "close_reason": pos.close_reason.value if pos.close_reason else None,
        "gross_realized_r": pos.gross_realized_r,
        "entry_cost_r": pos.entry_cost_r,
        "exit_cost_r": pos.exit_cost_r,
        "funding_r": pos.funding_r,
        "fees_r": pos.fees_r,
        "slippage_r": pos.slippage_r,
        "strategy_version": pos.strategy_version,
    }


def paper_position_from_dict(d: dict[str, Any]) -> PaperPosition | None:
    try:
        legs = tuple(
            PositionLeg(
                fraction=float(leg["fraction"]),
                price=float(leg["price"]),
                r_multiple=float(leg["r_multiple"]),
                reason=ExitReason(leg["reason"]),
                at=ensure_utc(parse_timestamp(leg["at"])),
            )
            for leg in d.get("legs", [])
        )
        return PaperPosition(
            position_id=d["position_id"],
            signal_id=d["signal_id"],
            instrument=d["instrument"],
            direction=Direction(d["direction"]),
            opened_at=ensure_utc(parse_timestamp(d["opened_at"])),
            information_cutoff=ensure_utc(parse_timestamp(d["information_cutoff"])),
            entry=float(d["entry"]),
            initial_sl=float(d["initial_sl"]),
            tp1=float(d["tp1"]),
            tp2=float(d["tp2"]),
            tp3_ref=d.get("tp3_ref"),
            state=PositionState(d["state"]),
            effective_sl=float(d["effective_sl"]),
            open_fraction=float(d["open_fraction"]),
            realized_r=float(d["realized_r"]),
            legs=legs,
            bars_pending=int(d["bars_pending"]),
            bars_held=int(d["bars_held"]),
            mfe_r=float(d["mfe_r"]),
            mae_r=float(d["mae_r"]),
            last_price=float(d["last_price"]),
            tp1_done=bool(d["tp1_done"]),
            tp2_done=bool(d["tp2_done"]),
            tp3_done=bool(d["tp3_done"]),
            sl_at_be=bool(d["sl_at_be"]),
            entry_ts=_dt(d.get("entry_ts")),
            closed_at=_dt(d.get("closed_at")),
            close_reason=ExitReason(d["close_reason"]) if d.get("close_reason") else None,
            gross_realized_r=float(d.get("gross_realized_r", 0.0)),
            entry_cost_r=float(d.get("entry_cost_r", 0.0)),
            exit_cost_r=float(d.get("exit_cost_r", 0.0)),
            funding_r=float(d.get("funding_r", 0.0)),
            fees_r=float(d.get("fees_r", 0.0)),
            slippage_r=float(d.get("slippage_r", 0.0)),
            strategy_version=str(
                d.get(
                    "strategy_version",
                    PaperPosition.__dataclass_fields__["strategy_version"].default,
                )
            ),
        )
    except (KeyError, ValueError, TypeError) as exc:
        _log.warning("paper position im snapshot unlesbar — übersprungen", extra={"err": str(exc)})
        return None


def clamp_backfill_start(last_processed: datetime, now: datetime, timeframe: Timeframe) -> datetime:
    """Startzeitpunkt für den REST-Backfill: nie weiter zurück als der REST-Verlauf reicht."""
    earliest = ensure_utc(now) - timedelta(seconds=timeframe.seconds * REST_M5_HISTORY_BARS)
    return max(ensure_utc(last_processed), earliest)


__all__ = [
    "REST_M5_HISTORY_BARS",
    "backfillable",
    "clamp_backfill_start",
    "gap_bars",
    "paper_position_from_dict",
    "paper_position_to_dict",
]
