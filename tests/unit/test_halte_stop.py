"""Der Halte-Stop: Chandelier aus Tageskerzen — fuer jeden Wert, nicht nur mit Setup.

Die Parameter (k = 5, ATR 14, Hoch 22) stammen aus der vorab registrierten Studie
``docs/HALTE-STOP-STUDIE-2026-09.md``. Dieser Test haelt fest, dass die Rechnung genau das
tut, was dort steht — und dass sie bei zu wenig Kerzen lieber nichts sagt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trading_agent.scanner.halte_stop import K_ATR, N_ATR, N_HOCH, atr_wilder, halte_werte


@dataclass
class _Kerze:
    open_time: datetime
    high: float
    low: float
    close: float


def _kerzen(n: int, *, spanne: float = 2.0, start: float = 100.0, schritt: float = 0.5):
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        _Kerze(
            t0 + timedelta(days=i),
            start + i * schritt + spanne / 2,
            start + i * schritt - spanne / 2,
            start + i * schritt,
        )
        for i in range(n)
    ]


def test_atr_einer_gleichmaessigen_reihe() -> None:
    k = _kerzen(60, spanne=2.0, schritt=0.5)
    atr = atr_wilder([b.high for b in k], [b.low for b in k], [b.close for b in k])
    # Jede Kerze: Hoch-Tief 2,0; Luecke zum Vortag 0,5 → wahre Spanne 2,0 (1,0 + 0,5 < 2)
    assert atr is not None and abs(atr - 2.0) < 1e-9


def test_chandelier_liegt_k_atr_unter_dem_22_tage_hoch() -> None:
    k = _kerzen(60, spanne=2.0, schritt=0.5)
    hw = halte_werte(k)
    assert hw is not None
    hoch = max(b.high for b in k[-N_HOCH:])
    assert abs(hw["stop_long"] - (hoch - K_ATR * hw["atr"])) < 1e-9
    assert abs(hw["stop_short"] - (min(b.low for b in k[-N_HOCH:]) + K_ATR * hw["atr"])) < 1e-9
    assert hw["stand"] == k[-1].open_time.date().isoformat()
    assert hw["stop_long"] < k[-1].close


def test_zu_wenig_kerzen_ergibt_keine_marke() -> None:
    assert halte_werte(_kerzen(max(N_ATR + 2, N_HOCH) - 1)) is None
    assert halte_werte([]) is None


def test_die_parameter_stehen_so_in_der_studie() -> None:
    """Wer k aendert, muss die Studie neu rechnen — nicht nur die Zahl im Code."""
    studie = (
        Path(__file__).resolve().parents[2] / "docs" / "HALTE-STOP-STUDIE-2026-09.md"
    ).read_text(encoding="utf-8")
    assert "k × ATR(14" in studie and "22 Tageskerzen" in studie
    assert K_ATR == 5.0 and N_ATR == 14 and N_HOCH == 22
