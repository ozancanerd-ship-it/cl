"""Der Gleichlauf muss messen, was da ist — und schweigen, wo nichts ist."""

from __future__ import annotations

import base64
import math
from datetime import UTC, datetime, timedelta

import pytest

from trading_agent.scanner import gleichlauf as gl


class _Kerze:
    def __init__(self, tag: datetime, close: float) -> None:
        self.open_time = tag
        self.close = close


def _tage(n: int) -> list[datetime]:
    """n aufeinanderfolgende Wochentage — das Raster, das Aktien vorgeben."""
    raus: list[datetime] = []
    t = datetime(2026, 1, 5, tzinfo=UTC)
    while len(raus) < n:
        if t.weekday() < 5:
            raus.append(t)
        t += timedelta(days=1)
    return raus


def _reihe(kurse: list[float], tage: list[datetime]) -> dict:
    return gl.sammle([_Kerze(t, k) for t, k in zip(tage, kurse, strict=True)])


def _korrelation(block: dict, a: str, b: str) -> float:
    """Dieselbe Rechnung, die der Browser macht — aus den Base64-Bytes."""
    va = [x - block["mitte"] for x in base64.b64decode(block["z"][a])]
    vb = [x - block["mitte"] for x in base64.b64decode(block["z"][b])]
    n = len(va)
    ma, mb = sum(va) / n, sum(vb) / n
    sa = math.sqrt(sum((x - ma) ** 2 for x in va))
    sb = math.sqrt(sum((x - mb) ** 2 for x in vb))
    return sum((va[i] - ma) * (vb[i] - mb) for i in range(n)) / (sa * sb)


def test_gleichlauf_erkennt_zwei_werte_die_dasselbe_tun() -> None:
    """Zwei Reihen mit identischen Renditen muessen bei 1,0 landen — auch quantisiert."""
    tage = _tage(120)
    schritte = [1 + 0.02 * math.sin(i / 3) for i in range(len(tage))]
    a, b = [100.0], [7.0]
    for s in schritte[1:]:
        a.append(a[-1] * s)
        b.append(b[-1] * s)
    block = gl.baue({"A": _reihe(a, tage), "B": _reihe(b, tage), **_fueller(tage)})
    assert block is not None
    assert _korrelation(block, "A", "B") > 0.99


def test_gegenlaeufige_werte_landen_bei_minus_eins() -> None:
    tage = _tage(120)
    a, b = [100.0], [100.0]
    for i in range(1, len(tage)):
        s = 1 + 0.02 * math.sin(i / 3)
        a.append(a[-1] * s)
        b.append(b[-1] / s)
    block = gl.baue({"A": _reihe(a, tage), "B": _reihe(b, tage), **_fueller(tage)})
    assert block is not None
    assert _korrelation(block, "A", "B") < -0.99


def test_unabhaengige_werte_bleiben_nahe_null() -> None:
    tage = _tage(120)
    a = [100.0]
    b = [100.0]
    for i in range(1, len(tage)):
        a.append(a[-1] * (1 + 0.02 * math.sin(i / 3)))
        b.append(b[-1] * (1 + 0.02 * math.cos(i / 7.5)))
    block = gl.baue({"A": _reihe(a, tage), "B": _reihe(b, tage), **_fueller(tage)})
    assert block is not None
    assert abs(_korrelation(block, "A", "B")) < 0.5


def test_krypto_wochenende_verschiebt_die_reihen_nicht() -> None:
    """Krypto handelt sieben Tage. Wenn das Raster das nicht beruecksichtigt, wird
    Montag gegen Samstag korreliert — und aus zwei identischen Werten wird Rauschen."""
    tage = _tage(120)
    schritte = [1 + 0.02 * math.sin(i / 3) for i in range(len(tage))]
    aktie = [100.0]
    for s in schritte[1:]:
        aktie.append(aktie[-1] * s)

    # Derselbe Wert, aber zusaetzlich mit Wochenendkerzen dazwischen.
    krypto: dict = {}
    t = tage[0]
    idx = 0
    kurs = 100.0
    while t <= tage[-1]:
        if t.weekday() < 5:
            kurs = aktie[idx]
            idx += 1
        else:
            kurs = kurs * 1.001
        krypto[t.date()] = kurs
        t += timedelta(days=1)

    block = gl.baue({"AKTIE": _reihe(aktie, tage), "KRYPTO": krypto, **_fueller(tage)})
    assert block is not None
    assert block["tage"] <= gl.TAGE
    assert _korrelation(block, "AKTIE", "KRYPTO") > 0.95


def test_stablecoin_ohne_bewegung_faellt_raus() -> None:
    tage = _tage(120)
    block = gl.baue({"USDC": _reihe([1.0] * len(tage), tage), **_fueller(tage)})
    assert block is not None
    assert "USDC" not in block["z"]


def test_zu_kurze_historie_ergibt_keinen_block() -> None:
    tage = _tage(30)
    assert gl.baue(_fueller(tage)) is None


def test_luecken_ueber_zehn_prozent_fliegen_raus() -> None:
    tage = _tage(120)
    kurse = [100.0 * (1.01**i) for i in range(len(tage))]
    reihe = _reihe(kurse, tage)
    for t in tage[20:50]:  # 30 von 120 Tagen fehlen
        reihe.pop(t.date(), None)
    block = gl.baue({"LOECHRIG": reihe, **_fueller(tage)})
    assert block is not None
    assert "LOECHRIG" not in block["z"]


def test_tagesschwankung_wird_in_prozent_geliefert() -> None:
    """Ohne die Schwankungsbreite laesst sich aus Gewichten kein Eurobetrag rechnen."""
    tage = _tage(120)
    kurse = [100.0]
    for i in range(1, len(tage)):
        kurse.append(kurse[-1] * (1 + (0.03 if i % 2 else -0.03)))
    block = gl.baue({"WILD": _reihe(kurse, tage), **_fueller(tage)})
    assert block is not None
    assert block["sd"]["WILD"] == pytest.approx(3.0, abs=0.3)


def _fueller(tage: list[datetime]) -> dict:
    """Sechs weitere Reihen — der Block verlangt mindestens fuenf, sonst ist er wertlos."""
    raus = {}
    for j in range(6):
        kurse = [100.0]
        for i in range(1, len(tage)):
            kurse.append(kurse[-1] * (1 + 0.01 * math.sin(i / (2 + j))))
        raus[f"F{j}"] = _reihe(kurse, tage)
    return raus


def test_aktien_ueberleben_ein_feld_mit_kryptomehrheit() -> None:
    """Der Fehler, den der erste echte Scan gezeigt hat.

    Im Universum sind gut die Haelfte der Werte Krypto. Setzt man die Rasterschwelle bei
    "der Haelfte", kommen Samstag und Sonntag ins Raster — und jeder Aktienwert faellt
    danach wegen Luecken raus. Der Gleichlauf-Block enthielt am Ende nur Krypto.
    """
    tage = _tage(140)
    alle: dict = {}
    # 12 Kryptoreihen mit Wochenenden.
    for j in range(12):
        t = tage[0]
        kurs = 100.0
        reihe: dict = {}
        i = 0
        while t <= tage[-1]:
            kurs *= 1 + 0.02 * math.sin(i / (3 + j * 0.3))
            reihe[t.date()] = kurs
            t += timedelta(days=1)
            i += 1
        alle[f"K{j}USD"] = reihe
    # 10 Aktienreihen, nur Wochentage.
    for j in range(10):
        kurse = [100.0]
        for i in range(1, len(tage)):
            kurse.append(kurse[-1] * (1 + 0.012 * math.cos(i / (4 + j * 0.4))))
        alle[f"AKT{j}"] = _reihe(kurse, tage)

    block = gl.baue(alle)
    assert block is not None
    for j in range(10):
        assert f"AKT{j}" in block["z"], "Aktien duerfen nicht aus dem Raster fallen"
    for j in range(12):
        assert f"K{j}USD" in block["z"]
