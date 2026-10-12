"""Day-Trading-Reiter: Chart + Analyse (12.10.2026).

Ozan, 12.10.: "ich kann bei den daytrades vor allem gold nicht den chart und die analyse
sehen". Der Reiter zeichnet jetzt einen Kerzenchart (Kraken live) mit den Marken der Regel
und erklaert darunter, was fuer einen Einstiegs-Alarm fehlt. Festgehalten wird:

1. Die Analyse im Browser rechnet GENAU wie der Alarm in Python (``daytrade_ausbruch.analyse``)
   — sonst zeigt die Seite "Ausbruch", und der Alarm kommt nicht (oder umgekehrt).
2. Gold steht als erster Wert in der Auswahl, mit Pax Gold (PAXG/USD) von Kraken.
3. Die Alarm-Schwelle im Chart ist dieselbe wie im Lohnt-sich-Tor (0,25 ATR).
4. Der Chart hat einen eigenen Container — der 5-Minuten-Neuaufbau zerreisst ihn nicht.

Ozan, 12.10. 09:30: "Gold muss alle charts haben ... wenn sekunden sogar unterschiede machen
... und dort anders analysieren". Dazu:

5. Alle Kraken-Zeiteinheiten von 1 Min. bis Woche; Websocket-Kerzen werden richtig
   eingepflegt (gleiche Zeit ersetzt, neue haengt an, alte wird ignoriert).
6. Gold-Analyse: Sitzungs-Hochs/-Tiefs, Vortag, Tagesspanne, RSI (Wilder) und Trend rechnen
   nachpruefbar richtig; US-Termine werden fuer Gold gefiltert.
"""

from __future__ import annotations

import json
import math
import random
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from trading_agent.strategy.setups import daytrade_ausbruch as m

VORLAGE = Path(__file__).resolve().parents[2] / "site" / "template.html"


def _funktion(roh: str, name: str) -> str:
    start = roh.index(f"function {name}(")
    tiefe, i = 0, roh.index("{", start)
    anfang = i
    while True:
        if roh[i] == "{":
            tiefe += 1
        elif roh[i] == "}":
            tiefe -= 1
            if tiefe == 0:
                break
        i += 1
    return roh[start:anfang] + roh[anfang : i + 1]


def _konst(roh: str, name: str) -> str:
    mm = re.search(rf"const {name}\s*=", roh)
    assert mm, name
    return roh[mm.start() : roh.index(";", mm.start()) + 1] + "\n"


def _reihe(seed: int, n: int = 400, ausbruch: float | None = None) -> list[tuple]:
    rnd = random.Random(seed)
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    k, c = [], 100.0
    for i in range(n):
        o = c
        c = o * (1 + rnd.gauss(0.0004, 0.006)) + 0.3 * math.sin(i / 7)
        h = max(o, c) * (1 + abs(rnd.gauss(0, 0.002)))
        lo = min(o, c) * (1 - abs(rnd.gauss(0, 0.002)))
        k.append((t0 + timedelta(hours=i), o, h, lo, c))
    if ausbruch is not None:
        hoch = max(x[2] for x in k[-48:])
        t, o, h, lo, _ = k[-1]
        c = hoch * (1 + ausbruch)
        k.append(
            (
                t + timedelta(hours=1),
                k[-1][4],
                max(c, k[-1][4]) * 1.0005,
                min(c, k[-1][4]) * 0.999,
                c,
            )
        )
    return k


def _js(reihen: list[tuple[list[tuple], bool | None]]) -> list[dict | None]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node nicht vorhanden")
    roh = VORLAGE.read_text(encoding="utf-8")
    eingabe = [
        [[[x[0].timestamp() * 1000, x[1], x[2], x[3], x[4]] for x in k], markt]
        for k, markt in reihen
    ]
    skript = (
        _konst(roh, "DT_LOOKBACK")
        + _funktion(roh, "dtEma")
        + _funktion(roh, "dtAtr")
        + _funktion(roh, "dtAnalyse")
        + f"\nconst E = {json.dumps(eingabe)};\n"
        + "console.log(JSON.stringify(E.map(([k, mo]) => { const a = dtAnalyse(k, mo);"
        " if (a) delete a.emaReihe; return a; })));"
    )
    out = subprocess.run([node, "-e", skript], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.mark.parametrize("ausbruch", [None, 0.0002, 0.01])
@pytest.mark.parametrize("markt", [True, False, None])
def test_browser_rechnet_wie_der_alarm(ausbruch, markt) -> None:
    reihen = [(_reihe(s, ausbruch=ausbruch), markt) for s in (1, 2, 3)]
    js = _js(reihen)
    for (k, mo), b in zip(reihen, js, strict=True):
        p = m.analyse(k, markt_ok=mo)
        assert b is not None and p is not None
        for f in (
            "kurs",
            "ema200",
            "marke48",
            "tief24",
            "atr24",
            "stop_bei_signal",
            "abstand_pct",
            "ueberhang_atr",
        ):
            assert b[f] == pytest.approx(p[f], rel=1e-9, abs=1e-9), f
        assert b["signal"] == p["signal"]
        assert b["status"] == p["status"]


def test_zu_kurze_reihe_gibt_keine_analyse() -> None:
    assert _js([(_reihe(1, n=150), True)]) == [None]
    assert m.analyse(_reihe(1, n=150)) is None


def test_gold_zuerst_und_von_kraken() -> None:
    roh = VORLAGE.read_text(encoding="utf-8")
    assert "PAXG: {paar: 'PAXGUSD'" in roh
    assert "const out = ['PAXG'];" in _funktion(roh, "dtWahlListe")
    assert "if (!DTC.sym){ DTC.sym = 'PAXG';" in roh
    assert "api.kraken.com/0/public/OHLC?pair=" in _funktion(roh, "dtPumpe")
    # Warteschlange: nie mehr als eine Kraken-Anfrage gleichzeitig
    assert "if (DTC.laedt.size || !DTC.warte.length) return;" in _funktion(roh, "dtPumpe")


def test_alarm_schwelle_wie_im_tor() -> None:
    roh = VORLAGE.read_text(encoding="utf-8")
    assert f"DT_MIN_UEBERHANG = {m.MIN_UEBERHANG_ATR}" in roh
    assert "an.marke48 + DT_MIN_UEBERHANG * an.atr24" in _funktion(roh, "dtLinien")


def test_chart_hat_eigenen_container_und_wird_beim_tabwechsel_gemessen() -> None:
    roh = VORLAGE.read_text(encoding="utf-8")
    assert '<div id="dtchart"></div>\n    <div id="daytrading"></div>' in roh
    assert "try { dtChartAufbau(); } catch(e){ console.error(e); }" in roh
    assert "if (i===11) requestAnimationFrame(" in roh


def _node(skript: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node nicht vorhanden")
    out = subprocess.run([node, "-e", skript], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _gold_js(*namen: str) -> str:
    roh = VORLAGE.read_text(encoding="utf-8")
    return "".join(_funktion(roh, n) for n in namen)


def test_alle_zeiteinheiten_von_minute_bis_woche() -> None:
    roh = VORLAGE.read_text(encoding="utf-8")
    zeile = _konst(roh, "DT_TFS")
    for tf in (1, 5, 15, 30, 60, 240, 1440, 10080):
        assert f"[{tf}, " in zeile
    assert "wss://ws.kraken.com/v2" in _funktion(roh, "dtWsPruefen")
    assert "channel: 'ohlc'" in _funktion(roh, "dtWsSenden")
    assert "channel: 'ticker'" in _funktion(roh, "dtWsSenden")


def test_websocket_kerze_einpflegen() -> None:
    js = (
        _gold_js("dtKerzeEinpflegen")
        + """
    const a = [[0,1,2,0.5,1.5,10],[60000,1.5,2,1,1.8,5]];
    const k = (t,c) => ({interval_begin: new Date(t).toISOString(), open:1.5, high:3, low:1, close:c, volume:7});
    console.log(JSON.stringify([
      dtKerzeEinpflegen(a, k(60000, 2.5)),
      dtKerzeEinpflegen(a, k(120000, 2.7)),
      dtKerzeEinpflegen(a, k(0, 9)),
    ]));"""
    )
    ersetzt, neu, alt = _node(js)
    assert len(ersetzt) == 2 and ersetzt[-1][4] == 2.5
    assert len(neu) == 3 and neu[-1][0] == 120000
    assert alt[0][4] == 1.5  # aeltere Kerze aendert nichts


def test_sitzungen_und_tagesspanne() -> None:
    # 15-Min.-Kerzen eines UTC-Tages: Asien 00-07 Spanne 100-110, London 07-13 Spanne 105-120
    tag0 = 1_790_000_000_000 // 86_400_000 * 86_400_000
    k = []
    for i in range(0, 13 * 4):
        t = tag0 + i * 15 * 60_000
        stunde = i // 4
        h, lo = (110, 100) if stunde < 7 else (120, 105)
        k.append([t, lo + 1, h, lo, h - 1, 1])
    tage = []
    for d in range(20, 0, -1):
        t = tag0 - d * 86_400_000
        tage.append([t, 100, 100 + d, 100 - d, 100, 1])
    tage.append([tag0, 101, 130, 95, 120, 1])
    jetzt = tag0 + 12 * 3_600_000 + 5 * 60_000
    js = (
        _gold_js("dtSitzungVon", "dtSitzungen", "dtTagesSpanne")
        + f"""
    console.log(JSON.stringify([dtSitzungen({json.dumps(k)}, {jetzt}), dtTagesSpanne({json.dumps(tage)}, {jetzt})]));"""
    )
    sz, ts = _node(js)
    assert sz["jetzt"] == "london"
    assert sz["asien"] == {"h": 110, "l": 100}
    assert sz["london"] == {"h": 120, "l": 105}
    assert ts["vortagH"] == 101 and ts["vortagL"] == 99
    assert ts["adr"] == pytest.approx(sum(2 * d for d in range(1, 15)) / 14)
    assert ts["heute"] == 35 and ts["tagesOpen"] == 101


def _rsi_wilder(werte: list[float], n: int = 14) -> float:
    auf = sum(max(werte[i] - werte[i - 1], 0) for i in range(1, n + 1)) / n
    ab = sum(max(werte[i - 1] - werte[i], 0) for i in range(1, n + 1)) / n
    for i in range(n + 1, len(werte)):
        d = werte[i] - werte[i - 1]
        auf = (auf * (n - 1) + max(d, 0)) / n
        ab = (ab * (n - 1) + max(-d, 0)) / n
    return 100.0 if ab == 0 else 100 - 100 / (1 + auf / ab)


def test_trend_und_rsi() -> None:
    k = [[i, 0, 0, 0, 0] for i in range(80)]
    k = [[x[0], c, c + 1, c - 1, c] for x, c in zip(k, _reihe_werte(), strict=False)]
    js = (
        _gold_js("dtEma", "dtRsi", "dtTrend")
        + f"""
    const k = {json.dumps(k)};
    console.log(JSON.stringify([dtTrend(k), dtRsi(k.map(x => x[4]), 14),
      dtTrend(k.map(x => [x[0], -x[1], -x[3], -x[2], -x[4]])), dtTrend(k.slice(0, 40))]));"""
    )
    t, rsi, t_ab, zu_kurz = _node(js)
    assert rsi == pytest.approx(_rsi_wilder([x[4] for x in k]))
    assert t["richtung"] == "auf" and t_ab["richtung"] == "ab"
    assert 0 <= t["lage"] <= 100
    assert zu_kurz is None


def _reihe_werte() -> list[float]:
    rnd = random.Random(7)
    c, out = 100.0, []
    for _ in range(80):
        c += 0.4 + rnd.gauss(0, 0.3)
        out.append(round(c, 4))
    return out


def test_gold_termine_nur_wichtige_us_daten() -> None:
    jetzt = 1_790_000_000_000
    iso = lambda m: datetime.fromtimestamp((jetzt + m * 60_000) / 1000, UTC).isoformat()  # noqa: E731
    termine = [
        {"titel": "CPI m/m", "land": "USD", "zeitpunkt": iso(90), "wirkung": "High"},
        {"titel": "Retail Sales m/m", "land": "USD", "zeitpunkt": iso(30), "wirkung": "Medium"},
        {"titel": "Bank Holiday", "land": "USD", "zeitpunkt": iso(10), "wirkung": "Holiday"},
        {"titel": "GDP q/q", "land": "GBP", "zeitpunkt": iso(20), "wirkung": "High"},
        {"titel": "FOMC Member Speaks", "land": "USD", "zeitpunkt": iso(40), "wirkung": "Low"},
        {"titel": "NFP", "land": "USD", "zeitpunkt": iso(60 * 60), "wirkung": "High"},  # > 48 Std.
    ]
    roh = VORLAGE.read_text(encoding="utf-8")
    js = (
        _konst(roh, "DT_GOLD_TERMIN")
        + _gold_js("dtGoldTermine")
        + f"""
    console.log(JSON.stringify(dtGoldTermine({json.dumps(termine)}, {jetzt}).map(t => t.titel)));"""
    )
    assert _node(js) == ["Retail Sales m/m", "CPI m/m"]


def test_gold_hat_eigene_analyse_und_marken() -> None:
    roh = VORLAGE.read_text(encoding="utf-8")
    assert "if (info.klasse === 'gold') h += dtGoldPanel(live);" in roh
    lin = _funktion(roh, "dtLinien")
    for marke in ("Vortag-Hoch", "Asien-Hoch", "London-Hoch"):
        assert marke in lin
    panel = _funktion(roh, "dtGoldPanel")
    for teil in (
        "Zeiteinheiten-Ampel",
        "Sitzungen & Tagesmarken",
        "Was Gold gerade bewegt",
        "kein Kaufsignal",
    ):
        assert teil in panel
    # Websocket nur, solange der Reiter offen und die Seite sichtbar ist
    assert "!document.hidden && $('p11') && !$('p11').hidden" in roh


def test_kosten_je_zeiteinheit() -> None:
    k1 = [[i * 60000, 100, 101, 100, 100.5, 1] for i in range(40)] + [
        [i * 60000, 100, 100, 100, 100, 0] for i in range(40, 60)
    ]
    k60 = [[i * 3_600_000, 100, 110, 100, 105, 1] for i in range(60)]
    js = (
        _gold_js("dtKosten")
        + f"""
    console.log(JSON.stringify(dtKosten([[1, {json.dumps(k1)}], [60, {json.dumps(k60)}], [5, null]], 100, 0.01, 0.004)));"""
    )
    r = _node(js)
    assert r["runde"] == pytest.approx(0.01 + 0.8)
    eins, std = r["je"]
    assert eins["tf"] == 1 and eins["spanne"] == pytest.approx(40 / 60)
    assert eins["leer"] == pytest.approx(20 / 60 * 100)
    assert eins["anteil"] == pytest.approx(0.81 / (40 / 60) * 100)
    assert std["anteil"] == pytest.approx(8.1)
    assert r["ab"] == 60  # erste Zeiteinheit mit Kosten <= 25 % der Bewegung


# ------------------------------------------------------------- Spannen-Prognose (12.10.)
def _profil_studie() -> dict:
    return json.loads(
        (VORLAGE.parents[1] / "docs" / "stundenprofil.json").read_text(encoding="utf-8")
    )


def test_studie_liegt_vor_und_ist_ehrlich_beschriftet() -> None:
    d = _profil_studie()
    for g in ("gold", "coins"):
        assert len(d[g]["profil"]) == 168
        assert sum(d[g]["profil"]) / 168 == pytest.approx(1.0, abs=1e-3)
        for h in ("1", "4", "24"):
            x = d[g]["horizonte"][h]
            assert x["faktor68"] < x["faktor95"]
            assert x["gueltig"] == (
                0.63 <= x["abdeckung68_oos"] <= 0.73 and 0.92 <= x["abdeckung95_oos"] <= 0.98
            )
    # Gold 24 Std. hielt im OOS nicht — das muss so in der Datei stehen, nicht schoengerechnet
    assert d["gold"]["horizonte"]["24"]["gueltig"] is False


def test_browser_prognose_wie_python() -> None:
    sys.path.insert(0, str(VORLAGE.parents[1] / "scripts"))
    import stundenprofil_studie as sp

    d = _profil_studie()
    rnd = random.Random(3)
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    k, c = [], 4000.0
    for i in range(720):
        if i == 400:
            continue  # eine Luecke — beide Seiten muessen sie gleich behandeln
        o = c
        c = o * math.exp(rnd.gauss(0, 0.003))
        k.append((t0 + timedelta(hours=i), o, max(o, c) * 1.001, min(o, c) * 0.999, c))
    for g in ("gold", "coins"):
        P = d[g]
        js = (
            _gold_js("dtWochenstunde", "dtSpannePrognose")
            + f"\nconst P = {json.dumps({'profil': P['profil'], 'lambda': P['lambda'], 'horizonte': P['horizonte']})};"
            + f"\nconst k = {json.dumps([[x[0].timestamp() * 1000, x[1], x[2], x[3], x[4]] for x in k])};"
            + "\nconst r = dtSpannePrognose(k, P); console.log(JSON.stringify([r.je[1].sig, r.je[4].sig, r.je[24].sig, r.je[4].hi68, r.kurs]));"
        )
        s1, s4, s24, hi68, kurs = _node(js)
        for h, s_js in ((1, s1), (4, s4), (24, s24)):
            assert s_js == pytest.approx(
                sp.prognose_jetzt(k, P["profil"], P["lambda"], h), rel=1e-9
            )
        assert hi68 == pytest.approx(
            kurs * math.exp(P["horizonte"]["4"]["faktor68"] * s4), rel=1e-12
        )


def test_sitzungsrichtung_nur_stabil_wenn_is_und_oos_einig() -> None:
    sitz = {
        "asien": {"is": {"mittel_bp": 3, "t": 2.5}, "oos": {"mittel_bp": 2, "t": 2.1}},
        "london": {"is": {"mittel_bp": 3, "t": 2.5}, "oos": {"mittel_bp": -2, "t": -2.1}},
        "newyork": {"is": {"mittel_bp": 3, "t": 1.0}, "oos": {"mittel_bp": 2, "t": 2.1}},
    }
    js = (
        _gold_js("dtSitzungsRichtung")
        + f"\nconsole.log(JSON.stringify(dtSitzungsRichtung({json.dumps(sitz)}).map(x => x.stabil)));"
    )
    assert _node(js) == [True, False, False]


def test_alarm_lauf_liefert_profil_an_die_app() -> None:
    sys.path.insert(0, str(VORLAGE.parents[1] / "scripts"))
    import daytrade_alarme as da

    p = da.stundenprofil()
    assert p and len(p["gold"]["profil"]) == 168 and p["coins"]["lambda"]


# ------------------------------------------------------------- Lernen & Playbook (12.10. nachm.)
def test_jedes_fragezeichen_hat_eine_erklaerung() -> None:
    roh = VORLAGE.read_text(encoding="utf-8")
    begriffe = set(
        re.findall(
            r"^  (\w+): \['",
            roh[roh.index("const DT_BEGRIFFE") : roh.index("function dtFrage(")],
            re.M,
        )
    )
    benutzt = set(re.findall(r"dtFrage\('(\w+)'\)", roh))
    assert benutzt and benutzt <= begriffe, benutzt - begriffe
    assert {"swingday", "band", "gebuehr", "perp", "oos", "bootstrap"} <= begriffe


def test_playbook_zeigt_die_ehrlichen_zahlen() -> None:
    sys.path.insert(0, str(VORLAGE.parents[1] / "scripts"))
    import daytrade_alarme as da

    g = da.gold_literatur()
    assert g["nm"]["bestanden"] is False and g["orb"]["bestanden"] is False
    assert g["nm"]["oos"]["markt_perp"] < 0
    roh = VORLAGE.read_text(encoding="utf-8")
    pb = _funktion(roh, "dtPlaybook")
    for teil in (
        "Rauschzonen-Momentum",
        "Opening-Range-Breakout",
        "Asien-Range",
        "kein Vorteil",
        "Swing",
    ):
        assert teil in pb
    # Lernkarten nur aus der Live-Lage, immer mit "Merke"
    assert "<strong>Merke:</strong>" in _funktion(roh, "dtLernen")


def test_startseite_zeigt_tageshandel_und_gold() -> None:
    roh = VORLAGE.read_text(encoding="utf-8")
    assert "${depotZeile()}\n    ${sicherText(tageshandelZeile)}" in roh
    z = _funktion(roh, "tageshandelZeile")
    assert "Welle" in z and "Gold" in z and "zeigeTab(11)" in roh


def test_uebungs_journal_rechnet_richtig() -> None:
    js = (
        _gold_js("dtjPruefe", "dtjR", "dtZahl")
        + """
    const t = {status: 'offen', richtung: 'long', einstieg: 100, stop: 99, ziel: 102, t0: 1000};
    const k = [[500, 100, 98, 97, 98], [1000, 100, 101, 99.5, 100.5], [2000, 100.5, 102.5, 98.5, 101]];
    const beides = dtjPruefe(t, k);             // Stop und Ziel in derselben Kerze -> Stop
    const ziel = dtjPruefe(t, [[1000, 100, 102.2, 99.5, 102]]);
    const shortT = dtjPruefe({...t, richtung: 'short', stop: 101, ziel: 98}, [[1000, 100, 100.5, 97.9, 98]]);
    console.log(JSON.stringify([beides.status, ziel.status, shortT.status,
      dtjR({...ziel}, 0), dtjR({...ziel}, 0.004), dtjR(beides, 0),
      dtZahl('4.160,12'), dtZahl('4160.12'), dtZahl('0,25')]));"""
    )
    st1, st2, st3, r_ziel, r_ziel_geb, r_stop, z1, z2, z3 = _node(js)
    assert (st1, st2, st3) == ("stop", "ziel", "ziel")  # alte Kerze vor dem Einstieg zaehlt nicht
    assert r_ziel == pytest.approx(2.0) and r_stop == pytest.approx(-1.0)
    assert r_ziel_geb == pytest.approx(2.0 - 2 * 0.004 * 100 / 1)
    assert (z1, z2, z3) == (4160.12, 4160.12, 0.25)


def test_bilanz_gruppen_trennt_setups_und_alarme() -> None:
    trades = [
        {
            "gezaehlt": True,
            "r_drittel": 1.0,
            "setup": "Ausbruch aus der Basis",
            "klasse": "krypto",
            "gemeldet": True,
            "begonnen": "2026-09-14",
        },
        {
            "gezaehlt": True,
            "r_drittel": -1.0,
            "setup": "Ausbruch aus der Basis",
            "klasse": "krypto",
            "gemeldet": False,
            "begonnen": "2026-09-15",
        },
        {
            "gezaehlt": True,
            "r_drittel": 0.5,
            "setup": "Ausbruch aus der Basis",
            "klasse": "krypto",
            "gemeldet": False,
            "begonnen": "2026-09-16",
        },
        {
            "gezaehlt": True,
            "r_drittel": -1.0,
            "setup": "",
            "klasse": "krypto",
            "gemeldet": False,
            "begonnen": "2026-09-10",
        },
        {
            "gezaehlt": True,
            "r_drittel": -1.0,
            "setup": "Ruecksetzer im Trend",
            "klasse": "aktien",
            "gemeldet": False,
            "begonnen": "2026-09-20",
        },
        {
            "gezaehlt": False,
            "r_drittel": 9.0,
            "setup": "x",
            "klasse": "krypto",
            "gemeldet": True,
            "begonnen": "2026-09-20",
        },
    ]
    js = (
        _gold_js("bilanzGruppen")
        + f"\nconsole.log(JSON.stringify(bilanzGruppen({json.dumps(trades)})));"
    )
    g = {x["k"]: x for x in _node(js)}
    assert g["coins_setup"]["n"] == 3 and g["coins_setup"]["summe"] == pytest.approx(0.5)
    assert g["coins_setup"]["h1"] == pytest.approx(1.0) and g["coins_setup"]["h2"] == pytest.approx(
        -0.25
    )
    assert g["ohne_setup"]["n"] == 1 and g["aktien_setup"]["schnitt"] == pytest.approx(-1.0)
    assert g["alarme"]["n"] == 1  # nicht gezaehlte Trades fallen raus
    assert "gold_setup" not in g
