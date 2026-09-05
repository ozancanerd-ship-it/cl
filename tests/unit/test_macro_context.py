"""Makrolage — Kontext und Risiko, nie ein Handelssignal."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading_agent.analysis.macro_context import (
    VIX_NERVOES,
    VIX_PANIK,
    MacroLage,
    Termin,
    baue_werte,
    bewerte,
    warnungen_fuer,
)


def _reihen(
    vix: float = 14.0,
    dxy: float = 100.0,
    y10: float = 4.0,
    spx: float = 5000.0,
    vix_davor: float | None = None,
    dxy_davor: float | None = None,
    y10_davor: float | None = None,
    spx_davor: float | None = None,
) -> dict[str, list[float]]:
    def reihe(jetzt: float, davor: float | None) -> list[float]:
        d = davor if davor is not None else jetzt
        return [d] * 21 + [jetzt]

    return {
        "vix": reihe(vix, vix_davor),
        "dxy": reihe(dxy, dxy_davor),
        "us10y": reihe(y10, y10_davor),
        "spx": reihe(spx, spx_davor),
    }


def test_ruhiger_markt_ist_risk_on() -> None:
    lage = bewerte(baue_werte(_reihen(vix=13.0, spx=5200.0, spx_davor=5000.0)))
    assert lage.regime == "risk_on"
    assert lage.punkte > 0
    assert any("VIX" in g for g in lage.begruendung)


def test_panik_ist_risk_off() -> None:
    lage = bewerte(baue_werte(_reihen(vix=VIX_PANIK + 3, spx=4600.0, spx_davor=5000.0)))
    assert lage.regime == "risk_off"
    assert lage.punkte < 0


def test_schnell_steigender_vix_zaehlt_zusaetzlich() -> None:
    ruhig = bewerte(baue_werte(_reihen(vix=VIX_NERVOES + 1)))
    schnell = bewerte(baue_werte(_reihen(vix=VIX_NERVOES + 1, vix_davor=15.0)))
    assert schnell.punkte < ruhig.punkte
    assert any("fuenf Tagen" in g for g in schnell.begruendung)


def test_starker_dollar_ist_gegenwind_fuer_gold_und_krypto() -> None:
    lage = bewerte(baue_werte(_reihen(dxy=105.0, dxy_davor=100.0)))
    assert "Gegenwind" in lage.wirkung.get("gold", "")
    assert "Gegenwind" in lage.wirkung.get("krypto", "")


def test_steigende_renditen_belasten_wachstum_und_gold() -> None:
    lage = bewerte(baue_werte(_reihen(y10=4.6, y10_davor=4.0)))
    assert "Renditen" in lage.wirkung.get("aktien", "")


def test_ohne_auffaelligkeit_wird_nichts_behauptet() -> None:
    lage = bewerte(baue_werte(_reihen(vix=20.0)))
    assert lage.regime == "neutral"
    assert lage.begruendung == ("keine der Kennzahlen zeigt etwas Auffaelliges",)


def test_punkte_bleiben_im_rahmen() -> None:
    schlimm = bewerte(
        baue_werte(
            _reihen(
                vix=60.0,
                vix_davor=12.0,
                dxy=110.0,
                dxy_davor=100.0,
                y10=5.0,
                y10_davor=4.0,
                spx=4000.0,
                spx_davor=5000.0,
            )
        )
    )
    assert -1.0 <= schlimm.punkte <= 1.0
    assert schlimm.regime == "risk_off"


# ------------------------------------------------------------------ Warnungen


def _lage(regime_werte: dict) -> MacroLage:
    return bewerte(baue_werte(_reihen(**regime_werte)))


def test_risk_off_warnt_bei_long_nicht_bei_short() -> None:
    lage = _lage({"vix": VIX_PANIK + 2, "spx": 4600.0, "spx_davor": 5000.0})
    assert any("gegen den Wind" in w for w in warnungen_fuer(lage, "krypto", "long"))
    assert not any("gegen den Wind" in w for w in warnungen_fuer(lage, "krypto", "short"))


def test_risk_on_warnt_bei_short() -> None:
    lage = _lage({"vix": 12.0, "spx": 5200.0, "spx_davor": 5000.0})
    assert any("gegen den Wind" in w for w in warnungen_fuer(lage, "aktien", "short"))


def test_ohne_lage_keine_warnung() -> None:
    assert warnungen_fuer(None, "krypto", "long") == []


def test_wichtiger_termin_in_kuerze_wird_gemeldet() -> None:
    jetzt = datetime.now(UTC)
    lage = bewerte(
        baue_werte(_reihen()),
        [
            Termin("Non-Farm Employment Change", "USD", jetzt + timedelta(hours=5), "High"),
            Termin("Irgendwas Kleines", "USD", jetzt + timedelta(hours=2), "Low"),
            Termin("FOMC", "USD", jetzt + timedelta(days=9), "High"),
        ],
    )
    w = warnungen_fuer(lage, "krypto", "long")
    assert any("Non-Farm" in x for x in w)
    assert not any("Irgendwas" in x for x in w)  # Low zaehlt nicht
    assert not any("FOMC" in x for x in w)  # zu weit weg


def test_termin_betrifft_nur_die_passende_anlageklasse() -> None:
    jetzt = datetime.now(UTC)
    lage = bewerte(
        baue_werte(_reihen()), [Termin("BoE Rate", "GBP", jetzt + timedelta(hours=4), "High")]
    )
    assert any("BoE" in x for x in warnungen_fuer(lage, "aktien", "long"))
    assert not any("BoE" in x for x in warnungen_fuer(lage, "krypto", "long"))


def test_vergangene_termine_zaehlen_nicht() -> None:
    jetzt = datetime.now(UTC)
    lage = bewerte(
        baue_werte(_reihen()), [Termin("CPI", "USD", jetzt - timedelta(hours=2), "High")]
    )
    assert lage.naechste_termine() == []


# ------------------------------------------------------------------ Persistenz


def test_lage_ueberlebt_speichern_und_laden() -> None:
    jetzt = datetime.now(UTC)
    lage = bewerte(
        baue_werte(_reihen(vix=28.0)), [Termin("CPI", "USD", jetzt + timedelta(hours=3), "High")]
    )
    wieder = MacroLage.from_dict(lage.as_dict())
    assert wieder is not None
    assert wieder.regime == lage.regime
    assert round(wieder.punkte, 3) == round(lage.punkte, 3)
    assert len(wieder.termine) == 1
    assert wieder.wert("vix") is not None
    assert wieder.wert("vix").wert == 28.0


def test_from_dict_auf_nichts_gibt_nichts() -> None:
    assert MacroLage.from_dict(None) is None
