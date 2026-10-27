import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import verlustquellen as vq  # noqa: E402


def _z(r, dauer=5.0, zustand="stop"):
    return {"r": r, "dauer_h": dauer, "zustand": zustand}


def test_sofort_erkennung():
    assert vq.ist_sofort(_z(-1.0, 0.0))
    assert not vq.ist_sofort(_z(-1.0, 0.5))
    assert not vq.ist_sofort(_z(1.0, 0.0, "ziel_erreicht"))


def test_kz_und_aussage():
    k = vq.kz([_z(-1.0)] * 40)
    assert k["n"] == 40 and k["summe"] == -40.0 and k["aussage"] == "Mittel signifikant <0"
    assert vq.kz([_z(-1.0)] * 5)["aussage"] == "n<30"


def test_echte_daten_summen_stimmen():
    res = vq.analyse()
    assert res["alle"]["n"] == res["sofort_artefakt"]["n"] + res["ohne_sofort"]["n"]
    assert abs(res["alle"]["summe"] - (res["sofort_artefakt"]["summe"] + res["ohne_sofort"]["summe"])) < 0.2
    assert res["sofort_artefakt"]["gemeldet_aufs_handy"] == 0
