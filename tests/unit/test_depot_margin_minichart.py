"""Margin zaehlt mit eigenem Geld; jede Krypto-Position bekommt einen Mini-Chart (07.10.2026).

Ozan, 07.10.: „Ich will die Analysen auch grafisch sehen" und das Depot „sauber".
Vorher stand Zcash 5x mit dem Nominalwert (150 $) im Depotwert, eingesetzt waren 30 $.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[2] / "site" / "template.html"


def _s() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_margin_wert_ist_eigenes_geld() -> None:
    s = _s()
    assert "wert = einstand / Number(pos.hebel) + gv;" in s
    assert "nominal = markt;" in s
    # Marktgewicht (Gleichlauf) bleibt der Nominalwert, das Kapital das eigene Geld.
    assert "const nomEU = b.nominal != null ? stand.inEuro(b, b.nominal)" in s
    assert "return {markt: nomEU, kapital: wertEU," in s


def test_minichart_steht_auf_der_karte_und_holt_kerzen_gepuffert() -> None:
    s = _s()
    assert "${hebelKasten(b)}\n        ${miniChart(b)}" in s
    assert "api.kraken.com/0/public/OHLC" in s
    assert "600000" in s  # hoechstens alle 10 Minuten
    assert "if (!b || istBar(b.pos)) return '';" in s
    # Aktien und Scheine: Verlauf aus dem Scan (Gleichlauf-Block, Feld k)
    assert "S.gleichlauf.k" in s


def test_depot_grafik_steht_ueber_der_tabelle() -> None:
    s = _s()
    assert "h += depotGrafik(stand);" in s and s.index("h += depotGrafik(stand);") < s.index("h += `<h2>Auf einen Blick</h2>")
    assert "Wo dein Geld liegt" in s and "Was jede Position seit Kauf gebracht hat" in s


def test_stresstest_kappt_verlust_bei_schein_und_margin() -> None:
    s = _s()
    assert "function stresstest(stand)" in s
    assert "eff = -t.gw.kapital" in s  # Knock-out / Liquidation: hoechstens das eigene Geld
    assert "eff = Math.max(eff, -t.gw.kapital)" in s
    assert "try { h += stresstest(stand); } catch(e){}" in s


def test_karte_zeigt_btc_beta() -> None:
    assert "BTC-Beta <b>" in _s()


def test_kapital_ranking_ohne_erfundene_punktzahl() -> None:
    s = _s()
    assert "function kapitalRanking(stand)" in s
    assert "try { h += kapitalRanking(stand); } catch(e){}" in s
    assert "try { h += zweitmeinungKasten(); } catch(e){ console.error(e); }\n    h += glKasten(stand);" in s
    assert "alarm.ja === true" in s  # neue Chancen nur mit bestandenem Alarm-Tor
    assert "Reihenfolge nach Dringlichkeit, nicht nach einer Punktzahl" in s
