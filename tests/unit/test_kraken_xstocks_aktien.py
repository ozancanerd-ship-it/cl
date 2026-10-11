"""Kraken-xStocks: Live-Kurs fuer Aktien, aber nur wo Kraken wirklich einen hat.

WARUM ES DIESEN TEST GIBT

Ozan, 11.10. 17:18: "Kraken hat jetzt auch die Option sozusagen mit Aktien zu handeln.
Das heisst, du muesstest die eigentlich auch dort alle sehen." Kraken fuehrt seit 2026
tokenisierte Aktien (xStocks) im oeffentlichen Spotmarkt — aber eine Stichprobe vom
11.10. zeigt: von den gefuehrten Aktien stehen nur wenige als "online" (beidseitig
liquide, ein echter Kurs), die meisten anderen sind "post_only" (nur einseitig im Buch)
oder fehlen bei Kraken ganz. Eine Zeile als "live" zu kennzeichnen, obwohl der Markt
keine Gegenseite hat, waere schlimmer als gar keine Kennzeichnung — sie saehe aus wie
ein Kurs, auf den man eine Order legen kann.

Festgehalten wird:

1. Eine Aktie mit "online"-Status bei Kraken bekommt ``kraken_live`` mit dem
   WebSocket-Paarnamen (z.B. "AAPLx/USD").
2. Eine Aktie mit "post_only" bekommt KEIN ``kraken_live`` — bleibt beim Yahoo-Stand,
   ohne falsche Live-Kennzeichnung.
3. Eine Aktie, die bei Kraken gar nicht gefuehrt wird, bleibt ebenso unberuehrt.
4. Krypto- und Gold-Zeilen werden nicht angefasst — diese Funktion ist nur fuer
   ``klasse == "aktien"``.
5. Scheitert der Kraken-Abruf (Netzwerk, Fehler), bleibt JEDE Zeile unveraendert —
   kein Absturz, keine verlorene Bewertung.
6. Eine Zeile, die beim letzten Lauf noch ``kraken_live`` hatte und inzwischen nicht
   mehr "online" ist, verliert das Feld wieder — sonst zeigt die App einen Live-Kurs,
   der in Wahrheit keiner mehr ist.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest


def _modul() -> Any:
    pfad = Path(__file__).resolve().parents[2] / "scripts" / "build_scan_data.py"
    spec = importlib.util.spec_from_file_location("build_scan_data", pfad)
    assert spec and spec.loader
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


class _FakeProvider:
    def __init__(self, paare: dict[str, dict[str, Any]] | None = None, *, kaputt: bool = False):
        self._paare = paare or {}
        self._kaputt = kaputt
        self.aclose_aufgerufen = False

    async def list_xstock_pairs(self) -> dict[str, dict[str, Any]]:
        if self._kaputt:
            raise ConnectionError("kein Netz")
        return self._paare

    async def aclose(self) -> None:
        self.aclose_aufgerufen = True


@pytest.mark.asyncio
async def test_online_paar_bekommt_kraken_live(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _modul()
    fake = _FakeProvider({"AAPL": {"ws_pair": "AAPLx/USD", "status": "online"}})
    monkeypatch.setattr("trading_agent.data.providers.kraken.KrakenDataProvider", lambda: fake)
    zeilen = [{"instrument": "AAPL", "klasse": "aktien"}]
    erg = await m._kraken_xstocks_anhaengen(zeilen)
    assert zeilen[0]["kraken_live"] == "AAPLx/USD"
    assert erg["gesetzt"] == 1
    assert fake.aclose_aufgerufen


@pytest.mark.asyncio
async def test_post_only_bekommt_kein_kraken_live(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _modul()
    fake = _FakeProvider({"META": {"ws_pair": "METAx/USD", "status": "post_only"}})
    monkeypatch.setattr("trading_agent.data.providers.kraken.KrakenDataProvider", lambda: fake)
    zeilen = [{"instrument": "META", "klasse": "aktien"}]
    await m._kraken_xstocks_anhaengen(zeilen)
    assert "kraken_live" not in zeilen[0]


@pytest.mark.asyncio
async def test_bei_kraken_unbekannte_aktie_bleibt_unberuehrt(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _modul()
    fake = _FakeProvider({"AAPL": {"ws_pair": "AAPLx/USD", "status": "online"}})
    monkeypatch.setattr("trading_agent.data.providers.kraken.KrakenDataProvider", lambda: fake)
    zeilen = [{"instrument": "COST", "klasse": "aktien"}]
    await m._kraken_xstocks_anhaengen(zeilen)
    assert "kraken_live" not in zeilen[0]


@pytest.mark.asyncio
async def test_krypto_und_gold_zeilen_werden_nicht_angefasst(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _modul()
    fake = _FakeProvider({"BTC": {"ws_pair": "BTC/USD", "status": "online"}})
    monkeypatch.setattr("trading_agent.data.providers.kraken.KrakenDataProvider", lambda: fake)
    zeilen = [
        {"instrument": "BTCUSDT", "klasse": "krypto"},
        {"instrument": "PAXGUSD", "klasse": "gold"},
    ]
    await m._kraken_xstocks_anhaengen(zeilen)
    assert "kraken_live" not in zeilen[0]
    assert "kraken_live" not in zeilen[1]


@pytest.mark.asyncio
async def test_fehlschlag_laesst_alle_zeilen_unveraendert(monkeypatch: pytest.MonkeyPatch) -> None:
    m = _modul()
    fake = _FakeProvider(kaputt=True)
    monkeypatch.setattr("trading_agent.data.providers.kraken.KrakenDataProvider", lambda: fake)
    zeilen = [{"instrument": "AAPL", "klasse": "aktien", "kraken_live": "AAPLx/USD"}]
    erg = await m._kraken_xstocks_anhaengen(zeilen)
    # Absichtlich: bei einem Fehlschlag bleibt eine VORHANDENE Kennzeichnung stehen statt
    # geloescht zu werden — ein Netzwerkfehler ist kein Beweis dafuer, dass Kraken den
    # Markt inzwischen geschlossen hat.
    assert zeilen[0]["kraken_live"] == "AAPLx/USD"
    assert erg["gesetzt"] == 0


@pytest.mark.asyncio
async def test_nicht_mehr_online_verliert_das_feld_wieder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eine Zeile aus einem frueheren Lauf hatte ``kraken_live`` — Kraken hat den Markt
    inzwischen auf "post_only" zurueckgestuft. Die Zeile darf das Feld nicht einfach
    behalten, sonst zeigt die App weiter einen Live-Kurs, der keiner mehr ist."""
    m = _modul()
    fake = _FakeProvider({"AAPL": {"ws_pair": "AAPLx/USD", "status": "post_only"}})
    monkeypatch.setattr("trading_agent.data.providers.kraken.KrakenDataProvider", lambda: fake)
    zeilen = [{"instrument": "AAPL", "klasse": "aktien", "kraken_live": "AAPLx/USD"}]
    await m._kraken_xstocks_anhaengen(zeilen)
    assert "kraken_live" not in zeilen[0]
