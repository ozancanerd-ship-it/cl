"""Der Scan-Durchgang: parallel, fehlertolerant, und ohne den Speicher zu sprengen."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from trading_agent.core.enums import AssetClass, Timeframe
from trading_agent.scanner.scan_runner import handelt_durchgehend, scanne

T0 = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass
class Bar:
    open_time: datetime
    close_time: datetime
    open: float = 100.0
    high: float = 101.0
    low: float = 99.0
    close: float = 100.5
    volume: float = 10.0
    timeframe: Timeframe = Timeframe.M5


class Provider:
    """Liefert genug Bars — ausser fuer Symbole, die absichtlich versagen."""

    def __init__(self, kaputt: set[str] | None = None, leer: set[str] | None = None) -> None:
        self.kaputt = kaputt or set()
        self.leer = leer or set()
        self.aufrufe: list[tuple[str, Timeframe]] = []
        self.gleichzeitig = 0
        self.hoechstens = 0

    async def fetch_ohlcv(
        self, instrument: str, tf: Timeframe, start: datetime, ende: datetime
    ) -> list[Bar]:
        self.gleichzeitig += 1
        self.hoechstens = max(self.hoechstens, self.gleichzeitig)
        try:
            await asyncio.sleep(0)
            self.aufrufe.append((instrument, tf))
            if instrument in self.kaputt:
                raise RuntimeError("Boerse schweigt")
            n = 5 if instrument in self.leer else 300
            schritt = timedelta(minutes=5)
            return [Bar(T0 + schritt * i, T0 + schritt * (i + 1), timeframe=tf) for i in range(n)]
        finally:
            self.gleichzeitig -= 1


@dataclass
class Chance:
    instrument: str
    score: float
    zus: dict


def _bewerter(name: str, mtf: Any, kurs: float, *, zusatz: dict | None = None, **_: Any) -> Any:
    return Chance(instrument=name, score=float(len(name)), zus=dict(zusatz or {}))


def _kein_mtf(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """``build_mtf_context`` durch etwas Triviales ersetzen — hier zaehlt der Ablauf."""
    gesehen: list[Any] = []

    class M:
        def __init__(self) -> None:
            self.per_tf: dict = {}

    def fake(m5: Any, **kw: Any) -> Any:
        gesehen.append(kw.get("instrument"))
        return M()

    monkeypatch.setattr("trading_agent.scanner.scan_runner.build_mtf_context", fake)
    return gesehen


@pytest.mark.asyncio
async def test_scannt_alle_und_sortiert_nach_score(monkeypatch: pytest.MonkeyPatch) -> None:
    _kein_mtf(monkeypatch)
    p = Provider()
    erg = await scanne(p, ["AA", "BBBB", "CCC"], asset_class=AssetClass.CRYPTO, bewerter=_bewerter)
    assert [c.instrument for c in erg.chancen] == ["BBBB", "CCC", "AA"]
    assert erg.vollstaendig


@pytest.mark.asyncio
async def test_ein_ausfall_kippt_den_scan_nicht(monkeypatch: pytest.MonkeyPatch) -> None:
    _kein_mtf(monkeypatch)
    p = Provider(kaputt={"BB"})
    erg = await scanne(p, ["AA", "BB", "CC"], asset_class=AssetClass.CRYPTO, bewerter=_bewerter)
    assert {c.instrument for c in erg.chancen} == {"AA", "CC"}
    assert "BB" in erg.ausfaelle
    assert not erg.vollstaendig


@pytest.mark.asyncio
async def test_zu_wenig_historie_wird_vermerkt(monkeypatch: pytest.MonkeyPatch) -> None:
    _kein_mtf(monkeypatch)
    p = Provider(leer={"NEU"})
    erg = await scanne(p, ["ALT", "NEU"], asset_class=AssetClass.CRYPTO, bewerter=_bewerter)
    assert "NEU" in erg.ausfaelle
    assert "M5" in erg.ausfaelle["NEU"]


@pytest.mark.asyncio
async def test_nebenlaeufigkeit_wird_eingehalten(monkeypatch: pytest.MonkeyPatch) -> None:
    _kein_mtf(monkeypatch)
    p = Provider()
    await scanne(
        p,
        [f"S{i}" for i in range(12)],
        asset_class=AssetClass.CRYPTO,
        bewerter=_bewerter,
        nebenlaeufig=3,
    )
    assert p.hoechstens <= 3


@pytest.mark.asyncio
async def test_verarbeite_bekommt_den_kontext_und_der_wird_nicht_aufgehoben(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Der Kontext ist das Speicherproblem — er darf nur im Rueckruf auftauchen."""
    _kein_mtf(monkeypatch)
    gesehen: list[str] = []
    erg = await scanne(
        Provider(),
        ["AA", "BB"],
        asset_class=AssetClass.CRYPTO,
        bewerter=_bewerter,
        verarbeite=lambda chance, mtf: gesehen.append(chance.instrument),
    )
    assert sorted(gesehen) == ["AA", "BB"]
    for c in erg.chancen:
        assert not hasattr(c, "per_tf")


@pytest.mark.asyncio
async def test_pruefe_kann_ablehnen_und_der_grund_steht_daneben(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _kein_mtf(monkeypatch)
    erg = await scanne(
        Provider(),
        ["ECHT", "FAKE"],
        asset_class=AssetClass.CRYPTO,
        bewerter=_bewerter,
        pruefe=lambda name, reihen: "kein echter Coin" if name == "FAKE" else None,
    )
    assert [c.instrument for c in erg.chancen] == ["ECHT"]
    assert erg.abgelehnt == {"FAKE": "kein echter Coin"}
    assert not erg.ausfaelle  # ablehnen ist kein Fehler


@pytest.mark.asyncio
async def test_zusatz_erreicht_den_bewerter(monkeypatch: pytest.MonkeyPatch) -> None:
    _kein_mtf(monkeypatch)
    erg = await scanne(
        Provider(),
        ["AA"],
        asset_class=AssetClass.CRYPTO,
        bewerter=_bewerter,
        zusatz={"AA": {"umsatz_24h": 42.0}},
    )
    assert erg.chancen[0].zus == {"umsatz_24h": 42.0}


# ------------------------------------------------------ 24/7-Pruefung


def _tage(n: int, nur_werktags: bool = False) -> list[Bar]:
    aus = []
    for i in range(n):
        t = T0 + timedelta(days=i)
        if nur_werktags and t.weekday() >= 5:
            continue
        aus.append(Bar(t, t + timedelta(days=1)))
    return aus


def test_echter_coin_handelt_durchgehend() -> None:
    assert handelt_durchgehend(_tage(90))


def test_boersenzeit_instrument_faellt_auf() -> None:
    assert not handelt_durchgehend(_tage(90, nur_werktags=True))


def test_zu_wenig_daten_behauptet_nichts() -> None:
    """Im Zweifel nicht ablehnen — eine Behauptung ohne Grundlage waere schlimmer."""
    assert handelt_durchgehend(_tage(10, nur_werktags=True))
