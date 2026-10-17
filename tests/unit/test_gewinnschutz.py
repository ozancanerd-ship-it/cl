"""Gewinnschutz: HOLD / PROFIT PROTECTION / PARTIAL / EXIT — mehrere Signale, nie eines allein."""

from __future__ import annotations

import math

from trading_agent.portfolio_intel.gewinnschutz import (
    EXIT,
    HOLD,
    PARTIAL,
    PROFIT_PROTECTION,
    UNBEWERTET,
    bewerte,
    spiegeln,
    swings,
)


def _kerzen(pfad: list[float], vol: float = 100.0, docht: float = 0.4) -> list[tuple]:
    out, p = [], pfad[0]
    for c in pfad:
        o = p
        out.append((o, max(o, c) + docht, min(o, c) - docht, c, vol))
        p = c
    return out


def _aufwaerts(n: int = 80) -> list[float]:
    return [100 + 0.8 * i + 2.0 * math.sin(i * 0.9) for i in range(n)]


def _umkehr() -> list[float]:
    """Aufwaertstrend, dann tieferes Hoch und Bruch unter das letzte hoehere Tief."""
    pfad = _aufwaerts(70)
    spitze = pfad[-1]
    pfad += [spitze - 1, spitze - 3, spitze - 2.0, spitze - 4.5, spitze - 3.5, spitze - 6.5, spitze - 8.0, spitze - 10.0]
    return pfad


def _lauf(pfad: list[float], *, lang: bool = True, hoch: float | None = None, trend=None, h1=None, einstieg: float = 100.0, **kw):
    k = _kerzen(pfad) if lang else spiegeln(_kerzen(pfad))
    if not lang:  # Short: Kurse negiert wieder positiv machen — Gewinn faellt mit steigendem Kurs nicht
        k = _kerzen([200 - (p - 100) * 1 for p in pfad])
    kurs = k[-1][3]
    return bewerte(
        lang=lang,
        kurs=kurs,
        einstieg=einstieg,
        hoch_gv_pct=hoch,
        menge=kw.pop("menge", 12.0),
        stop=kw.pop("stop", 95.0),
        ziel_next=kw.pop("ziel_next", kurs * 1.15),
        kerzen={"h4": k, "h1": h1, "d1": k},
        trend=trend if trend is not None else {"d1": "auf", "h4": "auf"},
        **kw,
    )


def test_intakte_struktur_bleibt_hold_auch_bei_hohem_gewinn() -> None:
    r = _lauf(_aufwaerts(), hoch=None)
    assert r.gewinn_pct and r.gewinn_pct > 20
    assert r.urteil == HOLD
    assert "Kein Verkauf nur wegen der Höhe des Gewinns" in r.aktion
    assert any("Aufwärtsstruktur intakt" in s for s in r.staerken)


def test_swing_punkte_brauchen_bestaetigung() -> None:
    k = _kerzen(_aufwaerts())
    sw = swings(k)
    assert sw and all(i < len(k) - 2 for i, _a, _p in sw)  # der Rand ist nie ein Swing


def test_mehrere_signale_geben_schutz_oder_teilverkauf_mit_menge() -> None:
    pfad = _umkehr()
    # Ein Strukturbruch allein (CHoCH) reicht nicht — das Urteil bleibt HOLD ...
    r0 = _lauf(pfad, hoch=pfad[69] - 100, trend={"d1": "auf", "h4": "gemischt"})
    assert [s.name for s in r0.signale] == ["choch"] and r0.urteil == HOLD
    # ... mit einem zweiten, unabhaengigen Signal (H4-Trend dreht) wird es PROFIT PROTECTION
    r = _lauf(pfad, hoch=pfad[69] - 100, trend={"d1": "auf", "h4": "ab"})
    assert r.urteil == PROFIT_PROTECTION and r.trail_stop is not None and "Stops auf" in r.aktion
    # mit drei Signalen (+ D1 dagegen + Gewinn-Abgabe) Teilverkauf mit echter Menge
    r3 = _lauf(pfad, hoch=80.0, trend={"d1": "ab", "h4": "ab"}, ziel_next=pfad[-1] * 1.5)
    assert r3.urteil == PARTIAL
    assert r3.teil_menge and r3.rest_menge and abs(r3.teil_menge + r3.rest_menge - 12.0) < 1e-9
    assert "danach bleiben" in r3.aktion


def test_strukturbruch_plus_trend_dagegen_plus_schlechtes_chance_risiko_ist_exit() -> None:
    pfad = _umkehr()
    kurs = _kerzen(pfad)[-1][3]
    r = _lauf(pfad, hoch=pfad[69] - 100, trend={"d1": "ab", "h4": "ab"}, ziel_next=kurs * 1.01)
    assert r.urteil == EXIT
    assert any(s.name in ("choch", "tief_bruch") for s in r.signale)


def test_einzelne_rote_kerze_oder_rsi_erzwingt_nichts() -> None:
    pfad = _aufwaerts()
    pfad[-1] = pfad[-2] - 2.2  # eine einzelne rote Kerze in intakter Struktur
    r = _lauf(pfad)
    assert r.urteil == HOLD
    # ein einzelnes Signal (hier: Quartalszahlen) bleibt HOLD
    r2 = _lauf(_aufwaerts(), zahlen_tage=1)
    assert r2.urteil == HOLD and [s.name for s in r2.signale] == ["ereignis"]


def test_gewinn_abgabe_allein_ist_kein_urteil() -> None:
    r = _lauf(_aufwaerts(), hoch=100.0)  # Hoechstgewinn 100 %, jetzt weniger ⇒ nur ein Signal
    assert [s.name for s in r.signale] == ["gewinn_abgabe"]
    assert r.urteil == HOLD


def test_zu_wenig_oder_fehlende_daten_sind_unbewertet_und_stehen_im_fehlt() -> None:
    r = bewerte(lang=True, kurs=120.0, einstieg=100.0, hoch_gv_pct=None, menge=1.0, stop=95.0, ziel_next=140.0, kerzen={"h4": _kerzen(_aufwaerts(10))}, trend={})
    assert r.urteil == UNBEWERTET
    assert any("fehlen" in f for f in r.fehlt)
    r2 = bewerte(lang=True, kurs=120.0, einstieg=None, hoch_gv_pct=None, menge=1.0, stop=95.0, ziel_next=None, kerzen=None, trend=None)
    assert r2.urteil == UNBEWERTET and any("Einstiegskurs" in f for f in r2.fehlt)


def test_fehlendes_h4_und_d1_wird_ausgewiesen() -> None:
    k = _kerzen(_aufwaerts())
    r = bewerte(lang=True, kurs=k[-1][3], einstieg=100.0, hoch_gv_pct=None, menge=1.0, stop=95.0, ziel_next=None, kerzen={"h1": k, "h4": None, "d1": None}, trend={"d1": None, "h4": None})
    texte = " | ".join(r.fehlt)
    assert "H4-Kerzen fehlen" in texte and "D1-Kerzen fehlen" in texte and "Kursziel" in texte and "News/Makro" in texte
    assert r.zeitebene == "H1"


def test_kein_gewinn_kein_urteil() -> None:
    r = bewerte(lang=True, kurs=100.2, einstieg=100.0, hoch_gv_pct=None, menge=1.0, stop=95.0, ziel_next=120.0, kerzen={"h4": _kerzen(_aufwaerts())}, trend={})
    assert r.urteil == HOLD and "Kein Gewinn zu schützen" in r.aktion


def test_short_wird_gespiegelt_bewertet() -> None:
    # Short, Kurs faellt (Gewinn), Struktur intakt ⇒ HOLD
    fallend = [200 - 0.8 * i - 2.0 * math.sin(i * 0.9) for i in range(80)]
    k = _kerzen(fallend)
    r = bewerte(lang=False, kurs=k[-1][3], einstieg=200.0, hoch_gv_pct=None, menge=5.0, stop=210.0, ziel_next=k[-1][3] * 0.9, kerzen={"h4": k, "d1": k}, trend={"d1": "ab", "h4": "ab"})
    assert r.gewinn_pct and r.gewinn_pct > 10
    assert r.urteil == HOLD


def test_neubewertung_nennt_wechsel_level() -> None:
    r = _lauf(_aufwaerts())
    assert "Stundenschluss" in r.neubewertung and r.wechsel_unten is not None


def test_als_dict_ist_serialisierbar() -> None:
    import json

    r = _lauf(_umkehr(), hoch=40.0)
    json.dumps(r.als_dict())


# --------------------------------------------------------------------------- Waechter-Integration


def _wache(pfad: list[float], h1_ts, trend, stand, *, preis=None, h4=None):
    """Ein Wächterlauf; Stand wird (wie bei einem Neustart) als JSON neu geladen."""
    import json

    from tests.unit.test_depot_wache import _scan, _zeile
    from trading_agent.portfolio_intel.depot_stops import pruefe_depot

    k = _kerzen(pfad)
    kurs = preis if preis is not None else k[-1][3]
    pos = [{"sym": "SOLUSD", "menge": 12, "einstieg": 100.0, "plan": {"stop": 95.0, "quelle": "selbst"}}]
    ktx = {"SOLUSD": {**trend, "rsi_h1": 50.0, "k": {"h1": None, "h4": h4 or k, "d1": k}, "h1_ts": h1_ts}}
    st, ev, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", kurs)), json.loads(json.dumps(stand)), {"SOLUSD": kurs}, ktx)
    return st, [e for e in ev if e.art.startswith("gewinnschutz")]


def test_wache_meldet_erst_nach_weiterem_stundenschluss_und_nur_einmal() -> None:
    pfad = _umkehr()
    tr = {"d1": "auf", "h4": "ab"}
    st, ev = _wache(pfad, 1_000_000.0, tr, {})
    assert ev == []  # erster Befund: nur vorläufig
    assert st["positionen"]["SOLUSD|"]["gs"]["kand"] == PROFIT_PROTECTION
    st, ev = _wache(pfad, 1_000_000.0, tr, st)  # nächster 5-Minuten-Lauf, gleiche Stundenkerze
    assert ev == []
    st, ev = _wache(pfad, 1_003_600.0, tr, st)  # eine weitere Stundenkerze, Lage unverändert
    assert [e.art for e in ev] == ["gewinnschutz"]
    assert "Höchstgewinn" in ev[0].text and "Neubewertung" in ev[0].text and "kein Auftrag" in ev[0].text
    st, ev = _wache(pfad, 1_007_200.0, tr, st)  # danach keine Wiederholung
    assert ev == []
    assert st["positionen"]["SOLUSD|"]["gs"]["stufe"] == PROFIT_PROTECTION


def test_wache_ohne_frische_stundenkerze_wird_nie_bestaetigt() -> None:
    pfad = _umkehr()
    tr = {"d1": "auf", "h4": "ab"}
    st: dict = {}
    for _ in range(4):
        st, ev = _wache(pfad, None, tr, st)
        assert ev == []
    assert st["positionen"]["SOLUSD|"]["gs"].get("stufe", HOLD) == HOLD


def test_wache_intakte_struktur_bleibt_still() -> None:
    st: dict = {}
    for ts in (1e6, 1e6 + 3600, 1e6 + 7200):
        st, ev = _wache(_aufwaerts(), ts, {"d1": "auf", "h4": "auf"}, st)
        assert ev == []
    assert st["positionen"]["SOLUSD|"]["gs"]["anzeige"]["urteil"] == HOLD


def test_wache_kurzer_ausreisser_wird_nicht_bestaetigt() -> None:
    """Schwäche nur in EINER Stunde, dann wieder intakt ⇒ kein Alarm."""
    tr = {"d1": "auf", "h4": "ab"}
    st, ev = _wache(_umkehr(), 1e6, tr, {})
    assert ev == []
    st, ev = _wache(_aufwaerts(), 1e6 + 3600, {"d1": "auf", "h4": "auf"}, st)
    assert ev == []
    st, ev = _wache(_umkehr(), 1e6 + 7200, tr, st)  # wieder schwach: Zähler beginnt neu
    assert ev == []


def test_stop_alarm_bleibt_direkt_trotz_gewinnschutz() -> None:
    _st, ev = _wache(_aufwaerts(), 1e6, {"d1": "auf", "h4": "auf"}, {}, preis=94.0)
    from tests.unit.test_depot_wache import _scan, _zeile
    from trading_agent.portfolio_intel.depot_stops import pruefe_depot

    pos = [{"sym": "SOLUSD", "menge": 12, "einstieg": 100.0, "plan": {"stop": 95.0, "quelle": "selbst"}}]
    _, e2, _ = pruefe_depot(pos, _scan(_zeile("SOLUSD", 94.0)), {}, None, None)
    assert e2[0].art == "stop"
    assert ev == []  # im Minus: kein Gewinnschutz-Urteil


def test_urteil_wird_versiegelt_geschrieben_und_nicht_bei_jedem_lauf_neu(tmp_path) -> None:
    import json
    from datetime import UTC, datetime, timedelta

    from scripts.depot_wache import ART_TITEL, DRINGEND, HANDLUNG, urteil_schreiben

    from trading_agent.security.siegel import oeffnen, schluessel_aus

    for art in ("gewinnschutz", "gewinnschutz_teil", "gewinnschutz_exit"):
        assert art in ART_TITEL and art in HANDLUNG
    assert "gewinnschutz_teil" in DRINGEND and "gewinnschutz" not in DRINGEND

    st, _ = _wache(_umkehr(), 1e6, {"d1": "auf", "h4": "ab"}, {})
    key = schluessel_aus("geheim", "depot-sync-v1")
    f = tmp_path / "u.siegel"
    jetzt = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    assert urteil_schreiben(str(f), st, key, jetzt) is True
    assert "SOLUSD" not in f.read_text()  # versiegelt: kein Klartext im Repository
    klar = json.loads(oeffnen(f.read_text(), key).decode())
    assert klar["p"][0]["sym"] == "SOLUSD" and klar["p"][0]["urteil"] in (HOLD, PROFIT_PROTECTION)
    assert urteil_schreiben(str(f), st, key, jetzt + timedelta(minutes=5)) is False  # unverändert
    assert urteil_schreiben(str(f), st, key, jetzt + timedelta(hours=2)) is True  # spätestens stündlich


def test_app_oeffnet_das_versiegelte_urteil_wie_python_und_zeigt_alle_felder(tmp_path) -> None:
    """Die JS-Funktion ``oeffnenJS`` muss das von Python versiegelte Urteil mit demselben Schluessel
    lesen; ein falscher Schluessel liefert nichts. Der Kasten nennt die geforderten Angaben."""
    import json
    import subprocess
    from pathlib import Path

    from trading_agent.security.siegel import schluessel_aus, versiegeln

    s = (Path(__file__).resolve().parents[2] / "site" / "template.html").read_text(encoding="utf-8")
    a = s.index("async function oeffnenJS")
    b = s.index("async function ladeUrteil")
    text = versiegeln(json.dumps({"t": "x", "p": [{"sym": "SOL", "urteil": "HOLD"}]}).encode(), schluessel_aus("geheim", "depot-sync-v1"))
    js = (
        "const _te=new TextEncoder();"
        "async function _hmac(key,d){const k=await crypto.subtle.importKey('raw',key,{name:'HMAC',hash:'SHA-256'},false,['sign']);return new Uint8Array(await crypto.subtle.sign('HMAC',k,d));}"
        "function _cat(...a){const n=a.reduce((s,x)=>s+x.length,0);const o=new Uint8Array(n);let i=0;for(const x of a){o.set(x,i);i+=x.length;}return o;}"
        + s[a:b]
        + f"(async()=>{{console.log(JSON.stringify([await oeffnenJS({text!r},'geheim'), await oeffnenJS({text!r},'falsch')]));}})();"
    )
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout
    ok, falsch = json.loads(out)
    assert json.loads(ok)["p"][0]["sym"] == "SOL" and falsch is None
    for feld in ("gewinn_pct", "hoch_pct", "rueckgang_pct", "upside_pct", "bias", "aktion", "neubewertung", "trail_stop", "signale", "fehlt", "teil_menge", "rest_menge"):
        assert f"r.{feld}" in s[s.index("function gewinnschutzKasten"):s.index("function stopKasten")], feld
    assert "sicherText(gewinnschutzKasten)" in s


# --------------------------------------------------------------------------- Hebelscheine (17.10.)


def _schein(**extra) -> dict:
    """Open-End-Long auf Amazon: Knock-out 194,36 $, Bezugsverhältnis 10, Einstieg 4,80 € je Schein."""
    return {
        "sym": "AMZN-TURBO-LONG",
        "basis": "AMZN",
        "basis_richtung": "long",
        "hebel_richtung": "long",
        "ko": 194.36,
        "ratio": 10.0,
        "einstieg": 4.80,
        "menge": 15,
        "waehrung": "EUR",
        "konto": "Trade Republic",
        **extra,
    }


def _scan_amzn(kurs: float) -> dict:
    from tests.unit.test_depot_wache import _scan, _zeile

    return _scan(_zeile("AMZN", kurs, klasse="aktien", name="Amazon"), eurusd=1.10)


def test_schein_gewinn_wird_am_scheinpreis_gemessen_nicht_am_basiskurs() -> None:
    from trading_agent.portfolio_intel.depot_stops import ScanIndex, lage_fuer, schein_produkt

    lg = lage_fuer(_schein(), ScanIndex(_scan_amzn(250.0)))
    sp = schein_produkt(_schein(), lg)
    # (250 − 194,36) / 10 = 5,564 $ = 5,058 € je Schein
    assert abs(sp["kurs"] - 5.564 / 1.10) < 1e-6
    # Basis-Einstand: Knock-out + 4,80 € × 1,10 × 10 = 247,16 $
    assert abs(sp["basis_einstand"] - (194.36 + 4.80 * 1.10 * 10)) < 1e-6
    from trading_agent.portfolio_intel.depot_stops import pruefe_depot

    stand, ev, _ = pruefe_depot([_schein()], _scan_amzn(250.0), {})
    st = stand["positionen"]["AMZN-TURBO-LONG|trade republic"]
    assert 5.0 < st["hoch_gv_pct"] < 5.8  # ≈ +5,4 % — nicht „+5.100 %" aus 250 $ gegen 4,80 €
    assert not any(e.art in ("ueberdehnt", "gewinn_rueckgang") for e in ev)


def test_alter_hoechstgewinn_in_gemischten_einheiten_loest_keinen_fehlalarm_aus() -> None:
    from trading_agent.portfolio_intel.depot_stops import pruefe_depot

    alt = {"positionen": {"AMZN-TURBO-LONG|trade republic": {"hoch_gv_pct": 5100.0, "regel": 2}}, "gemeldet": {}}
    stand, ev, _ = pruefe_depot([_schein()], _scan_amzn(250.0), alt)
    assert not any(e.art == "gewinn_rueckgang" for e in ev)
    assert stand["positionen"]["AMZN-TURBO-LONG|trade republic"]["hoch_gv_pct"] < 10


def test_ohne_bezugsverhaeltnis_kein_stilles_verhaeltnis_eins() -> None:
    from trading_agent.portfolio_intel.depot_stops import pruefe_depot

    pos = _schein()
    pos.pop("ratio")
    stand, _ev, _ = pruefe_depot([pos], _scan_amzn(250.0), {})
    st = stand["positionen"]["AMZN-TURBO-LONG|trade republic"]
    assert st.get("hoch_gv_pct") is None
    anz = st["gs"]["anzeige"]
    assert anz["urteil"] == UNBEWERTET and "Bezugsverhältnis" in " ".join(anz["fehlt"])
    # Mit dem aktuellen Wert aus Trade Republic (wert_manuell) wird es abgeleitet
    pos["wert_manuell"] = 15 * 5.564 / 1.10
    stand, _, _ = pruefe_depot([pos], _scan_amzn(250.0), {})
    st = stand["positionen"]["AMZN-TURBO-LONG|trade republic"]
    assert 5.0 < st["hoch_gv_pct"] < 5.8
    assert any("abgeleitet" in f for f in st["gs"]["anzeige"]["fehlt"])


def test_gewinnschutz_fuer_schein_strukturiert_am_basiswert_mit_ko_signal() -> None:
    """Struktur kommt aus den Basiswert-Kerzen, Gewinn aus dem Schein, Knock-out-Nähe zählt mit."""
    pfad = [150 + 0.8 * i + 2.0 * math.sin(i * 0.9) for i in range(70)]
    spitze = pfad[-1]
    pfad += [spitze - 1, spitze - 3, spitze - 2.0, spitze - 4.5, spitze - 3.5, spitze - 6.5, spitze - 8.0, spitze - 10.0]
    k = _kerzen(pfad)
    kurs = k[-1][3]  # ≈ 195
    r = bewerte(lang=True, kurs=kurs, einstieg=None, hoch_gv_pct=80.0, menge=15, stop=None, ziel_next=kurs * 1.3,
                kerzen={"h4": k, "d1": k}, trend={"d1": "auf", "h4": "ab"}, gewinn_pct=40.0, ko=kurs * 0.9)
    namen = [s.name for s in r.signale]
    assert "ko_puffer" in namen and "choch" in namen
    assert r.gewinn_pct == 40.0 and r.urteil in (PROFIT_PROTECTION, PARTIAL, EXIT)
    if r.trail_stop is not None:
        assert r.trail_stop > kurs * 0.9  # nie ein Stop jenseits des Knock-outs


def test_bezugsverhaeltnis_wie_bei_trade_republic_als_faktor() -> None:
    """„0,1" (Trade Republic) und „10" (Teiler) ergeben denselben Scheinpreis — in Python und in der App."""
    import subprocess
    from pathlib import Path

    from trading_agent.portfolio_intel.depot_stops import ScanIndex, lage_fuer, schein_produkt

    idx = ScanIndex(_scan_amzn(250.0))
    a = schein_produkt(_schein(ratio=0.1), lage_fuer(_schein(ratio=0.1), idx))["kurs"]
    b = schein_produkt(_schein(ratio=10.0), lage_fuer(_schein(ratio=10.0), idx))["kurs"]
    assert abs(a - b) < 1e-12
    s = (Path(__file__).resolve().parents[2] / "site" / "template.html").read_text(encoding="utf-8")
    js = s[s.index("function scheinTeiler"):s.index("function hebelLage")] + (
        "console.log(JSON.stringify([scheinTeiler({ratio:0.1}), scheinTeiler({ratio:10}), scheinTeiler({}), scheinTeiler({ratio:0})]));"
    )
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout
    assert [round(x, 9) if x else x for x in __import__("json").loads(out)] == [10, 10, None, None]
