"""Decision ledger + trade ledger (SQLite, append-only).

* ``DecisionLedger`` records every step of the lifecycle
  (``DATA_SNAPSHOT -> ANALYSIS -> SETUP -> SCORE -> RISK -> SIGNAL -> APPROVAL -> ORDER -> FILL
  -> MANAGEMENT -> EXIT``), each tagged with a ``trace_id`` and the running versions.
* ``TradeLedger`` records a ``TradeRecord`` per completed trade (R multiple, MFE/MAE, costs).

Both share one SQLite file so a trade and its decision trail stay together.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from trading_agent.core.enums import Side
from trading_agent.core.models import UtcDatetime
from trading_agent.core.time import to_epoch_ms
from trading_agent.core.version import SCHEMA_VERSION


class TradeRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=SCHEMA_VERSION)
    trade_id: str
    run_id: str | None = None
    trace_id: str | None = None
    instrument: str
    direction: Side
    setup_id: str = "REFERENCE"
    strategy_version: str = "0.0.0"

    signal_ts: UtcDatetime
    information_cutoff: UtcDatetime
    entry_ts: UtcDatetime
    entry_price: float
    qty: float
    initial_sl: float | None = None
    initial_tp: float | None = None

    exit_ts: UtcDatetime
    exit_price: float
    exit_reason: str

    gross_r: float
    realized_r: float
    pnl_ccy: float
    fees_ccy: float = 0.0
    funding_ccy: float = 0.0
    slippage_ccy: float = 0.0

    mfe_r: float = 0.0
    mae_r: float = 0.0
    bars_held: int = 0
    win_loss: str = "SCRATCH"


class Ledger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts_ms INTEGER NOT NULL,
                    trace_id TEXT,
                    step TEXT NOT NULL,
                    instrument TEXT,
                    strategy_version TEXT,
                    config_hash TEXT,
                    code_sha TEXT,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_dec_trace ON decisions(trace_id);
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    trace_id TEXT,
                    entry_ms INTEGER NOT NULL,
                    exit_ms INTEGER NOT NULL,
                    instrument TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    realized_r REAL NOT NULL,
                    win_loss TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_trades_run ON trades(run_id);
                """
            )

    # ---------------------------------------------------------------- decisions

    def record_decision(
        self,
        step: str,
        *,
        trace_id: str | None = None,
        instrument: str | None = None,
        payload: dict[str, Any] | None = None,
        strategy_version: str | None = None,
        config_hash: str | None = None,
        code_sha: str | None = None,
        ts: datetime | None = None,
    ) -> None:
        moment = ts or datetime.now(UTC)
        with self._conn() as c:
            c.execute(
                "INSERT INTO decisions(ts_ms, trace_id, step, instrument, strategy_version, "
                "config_hash, code_sha, payload) VALUES (?,?,?,?,?,?,?,?)",
                (
                    to_epoch_ms(moment),
                    trace_id,
                    step,
                    instrument,
                    strategy_version,
                    config_hash,
                    code_sha,
                    json.dumps(payload or {}, default=str),
                ),
            )

    def decisions_for(self, trace_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM decisions WHERE trace_id=? ORDER BY id", (trace_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def decision_count(self) -> int:
        with self._conn() as c:
            return int(c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0])

    # ---------------------------------------------------------------- trades

    def record_trade(self, rec: TradeRecord) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO trades(trade_id, run_id, trace_id, entry_ms, exit_ms, "
                "instrument, direction, realized_r, win_loss, payload) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    rec.trade_id,
                    rec.run_id,
                    rec.trace_id,
                    to_epoch_ms(rec.entry_ts),
                    to_epoch_ms(rec.exit_ts),
                    rec.instrument,
                    rec.direction.value,
                    rec.realized_r,
                    rec.win_loss,
                    rec.model_dump_json(),
                ),
            )

    def trades(self, run_id: str | None = None) -> list[TradeRecord]:
        with self._conn() as c:
            if run_id is None:
                rows = c.execute("SELECT payload FROM trades ORDER BY entry_ms").fetchall()
            else:
                rows = c.execute(
                    "SELECT payload FROM trades WHERE run_id=? ORDER BY entry_ms", (run_id,)
                ).fetchall()
        return [TradeRecord.model_validate_json(r["payload"]) for r in rows]


__all__ = ["Ledger", "TradeRecord"]
