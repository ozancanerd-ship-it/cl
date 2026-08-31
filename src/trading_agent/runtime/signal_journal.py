"""``SignalJournal`` — persistiert **jedes Signal und jede Änderung** als JSONL (Masterplan §24/§30).

Ein EventBus-Subscriber. Schreibt sofort (append + flush) eine Zeile je:

* **signal** — eine tradebare Decision (BUY/SELL), inkl. vollem ``SignalReport`` + Freigabe-Status
  (LIVE / SHADOW / BLOCKED).
* **revision** — ``SignalRevised``: Entry/SL/TP/Score/Confidence neu bewertet, STRENGTHENED /
  WEAKENED / *_CHANGED / INVALIDATED / EXPIRED.
* **trade** — ``PaperPositionChanged``: OPENED → FILLED → TP1 → SL-Move → PARTIAL → EXIT/CLOSED.
* **alert** — ``AlertRaised``.

Kein Löschen, kein Überschreiben. Rein lesbar für Report/UI/`edge_health_check`.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trading_agent.runtime.bus import EventBus
from trading_agent.runtime.events import (
    AlertRaised,
    DecisionMade,
    PaperPositionChanged,
    SignalRevised,
)


class SignalJournal:
    def __init__(self, path: str | Path, *, build_report: Any = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._build_report = build_report  # signal_report.build_signal_report | None
        self._opportunity_for: Any = None  # optionaler Callable(instrument) -> OpportunityScore
        self._risk_pct: float | None = None
        self._gate: Any = None  # (result, registry) -> gated result  (governance.apply_live_gate)
        self._registry: Any = None
        self.counts: dict[str, int] = {"signal": 0, "revision": 0, "trade": 0, "alert": 0}

    def configure(
        self,
        *,
        opportunity_for: Any = None,
        risk_pct: float | None = None,
        apply_live_gate: Any = None,
        registry: Any = None,
    ) -> None:
        self._opportunity_for = opportunity_for
        self._risk_pct = risk_pct
        self._gate = apply_live_gate
        self._registry = registry

    # ------------------------------------------------------------------ EventBus

    def attach(self, bus: EventBus) -> None:
        bus.subscribe(DecisionMade, self._on_decision)
        bus.subscribe(SignalRevised, self._on_revision)
        bus.subscribe(PaperPositionChanged, self._on_trade)
        bus.subscribe(AlertRaised, self._on_alert)

    async def _on_decision(self, ev: DecisionMade) -> None:
        if ev.decision_type not in ("buy", "sell"):
            return
        result = ev.result
        if self._gate is not None and self._registry is not None:
            result = self._gate(result, registry=self._registry)
        row: dict[str, Any] = {
            "kind": "signal",
            "instrument": ev.instrument,
            "decision": ev.decision_type.upper(),
            "setup_state": ev.setup_state,
            "score": ev.score,
            "confidence": ev.confidence,
        }
        if self._build_report is not None:
            opp = self._opportunity_for(ev.instrument) if self._opportunity_for else None
            rep = self._build_report(result, opportunity=opp, risk_pct=self._risk_pct)
            if rep is not None:
                row["report"] = rep.as_dict()
                row["eligibility"] = rep.live_eligibility
        lg = getattr(result, "live_gate", None)
        if lg is not None:
            row["live_gate"] = lg.as_dict()
        self._write(ev.ts, row)
        self.counts["signal"] += 1

    async def _on_revision(self, ev: SignalRevised) -> None:
        upd = ev.signal
        rev = getattr(upd, "revision", None)
        row: dict[str, Any] = {
            "kind": "revision",
            "instrument": ev.instrument,
            "signal_id": ev.signal_id,
            "state": ev.state,
            "change": ev.change,
            "is_new": bool(getattr(upd, "is_new", False)),
        }
        if rev is not None:
            snap = getattr(rev, "snapshot", None)
            row["revision"] = snap if isinstance(snap, dict) else getattr(rev, "revision", None)
            row["change_kind"] = str(getattr(getattr(rev, "change_kind", None), "value", ""))
            row["changes"] = list(getattr(rev, "changes", ()) or ())
        self._write(ev.ts, row)
        self.counts["revision"] += 1

    async def _on_trade(self, ev: PaperPositionChanged) -> None:
        pos = ev.position
        row: dict[str, Any] = {
            "kind": "trade",
            "instrument": ev.instrument,
            "change": ev.change,  # OPENED / FILLED / TP1 / SL / PARTIAL / EXIT_REQUIRED / CLOSED
            "realized_r": ev.realized_r,
        }
        for attr in ("position_id", "direction", "state", "entry_price", "sl", "avg_entry"):
            v = getattr(pos, attr, None)
            if v is not None:
                row[attr] = getattr(v, "value", v)
        # aktuelle SL/TP-Levels + Teil-Fortschritt, falls das Positions-Objekt sie trägt
        for attr in ("current_sl", "tp1", "tp2", "tp3_ref", "tp_level_reached", "unrealized_r"):
            v = getattr(pos, attr, None)
            if v is not None:
                row[attr] = v() if callable(v) else v
        self._write(ev.ts, row)
        self.counts["trade"] += 1

    async def _on_alert(self, ev: AlertRaised) -> None:
        self._write(
            ev.ts,
            {
                "kind": "alert",
                "instrument": ev.instrument,
                "alert_type": ev.alert_type,
                "message": ev.message,
                "delivered": ev.delivered,
            },
        )
        self.counts["alert"] += 1

    # ------------------------------------------------------------------ intern

    def _write(self, ts: datetime | None, row: dict[str, Any]) -> None:
        row["ts"] = (ts or datetime.now(UTC)).isoformat()
        row["logged_at"] = datetime.now(UTC).isoformat()
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out


__all__ = ["SignalJournal"]
