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
    assert "if (!b || istBar(b.pos) || istSchein(b.pos)) return '';" in s
