import json
from datetime import UTC, datetime

from trading_agent.scanner.watchlist import Wache, Wachliste, kontext_aus_zeile


def test_kontext_aus_echter_zeilenform():
    z = {"confidence": 0.47, "bewegung_atr": 2.75, "setup": {"qualitaet": "sauber"},
         "halte": {"atr_pct": 2.15}, "zusatz": {"renditen": {"r21": 5.3, "r63": 34.8}},
         "info": {"sektor_rang": 7, "sektor_von": 9, "zahlen": {"tage": 1}},
         "termin_sperre": "Quartalszahlen", "warnungen": ["a", "b"]}
    k = kontext_aus_zeile(z, {"makro_regime": "neutral", "angst_gier": 48})
    assert k["zahlen_in_tagen"] == 1 and k["termin_sperre"] is True and k["n_warnungen"] == 2
    assert k["makro_regime"] == "neutral" and k["r63"] == 34.8
    json.dumps(k)


def test_kontext_fehlende_felder_brechen_nichts():
    k = kontext_aus_zeile({}, None)
    assert k["confidence"] is None and k["n_warnungen"] == 0


def test_kontext_wird_gespeichert_und_roundtrip():
    jetzt = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    w = Wache(instrument="X", klasse="krypto", richtung="long", note="A", einstieg=100.0,
              einstieg_art="sofort", stop=95.0, tp1=105.0, tp2=None, tp3=None, score=60, rr=2,
              erwartet_pct=5, aufgenommen=jetzt.isoformat(), zuletzt=jetzt.isoformat(),
              kontext={"confidence": 0.5})
    w2 = Wache.from_dict(w.as_dict() if hasattr(w, "as_dict") else w.to_dict())
    assert w2.kontext == {"confidence": 0.5}
    assert Wachliste is not None
