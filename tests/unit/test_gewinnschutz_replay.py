"""Replay-Skript: keine Blicke in die Zukunft, Kosten wirken, Varianten laufen durch."""

from __future__ import annotations

import math

from scripts.gewinnschutz_replay import simuliere


def _daten(n: int = 400) -> dict[str, list[list[float]]]:
    h4, p = [], 100.0
    for i in range(n):
        c = 100 + 0.15 * i + 4 * math.sin(i / 7)
        h4.append([1_700_000_000 + i * 14400, p, max(p, c) + 0.5, min(p, c) - 0.5, c, c, 100.0])
        p = c
    d1 = [[1_700_000_000 + i * 86400, 100 + i, 101 + i, 99 + i, 100.5 + i, 100, 1000.0] for i in range(300)]
    return {"240": h4, "1440": d1}


def test_alle_varianten_laufen_und_einstieg_folgt_dem_signal() -> None:
    d = _daten()
    for v in ("V0", "V1", "V2"):
        trades = simuliere("TEST", d, v)
        assert trades, v
        for t in trades:
            assert t.rest == 0.0 and not t.offen
            assert t.stop0 < t.ein and t.risiko > 0
            # Einstieg = Eroeffnung einer Kerze, nie eine rueckwirkend gewaehlte Zeit
            assert any(int(r[0]) == t.t_ein and r[1] == t.ein for r in d["240"])


def test_varianten_teilen_sich_die_ersten_einstiege() -> None:
    d = _daten()
    erste = {v: simuliere("TEST", d, v)[0].t_ein for v in ("V0", "V1", "V2")}
    assert len(set(erste.values())) == 1
