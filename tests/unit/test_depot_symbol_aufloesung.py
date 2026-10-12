"""Ein reines Coin-Kürzel im Depot-Text bekommt sein Krypto-Paar (12.10.2026).

Ozan, 12.10.: TAO eingestiegen (130 USD), Live-Chart und Einfügen per Text gewünscht.
Ohne Auflösung landete „TAO" ohne Kraken-Paar: kein Kurs, kein Verlauf im Depot.
"""

from __future__ import annotations

from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[2] / "site" / "template.html"


def _s() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_leseDepot_loest_coin_auf() -> None:
    s = _s()
    assert "const sym = depotSymAufloesen(teile[0]" in s


def test_aufloesung_nimmt_nur_krypto_aus_dem_scan() -> None:
    s = _s()
    start = s.index("function depotSymAufloesen(sym){")
    funk = s[start : s.index("let _pfZuletzt = 0;")]
    assert "o.klasse === 'krypto'" in funk
    assert "s + q" in funk
    assert "'USD', 'USDT'" in funk
    # Schon vollständige Paare und Aktien-Kürzel werden nicht angefasst
    assert "/(USDT|USDC|USD|EUR)$/" in funk
