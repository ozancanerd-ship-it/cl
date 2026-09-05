"""Das Universum kommt aus der Boerse, nicht aus einer Liste — und filtert nachvollziehbar."""

from __future__ import annotations

from typing import Any

from trading_agent.scanner.universe import (
    UniversumFilter,
    bilde_universum,
    ist_hebel_token,
    ist_tokenisierte_aktie,
    nur_namen,
)


def _sym(basis: str, quote: str = "USDT") -> dict[str, Any]:
    return {"instrument": f"{basis}{quote}", "basis": basis, "quote": quote, "spot": True}


def _tick(basis: str, *, umsatz: float, kurs: float = 10.0, trades: int = 50_000) -> dict[str, Any]:
    return {
        "instrument": f"{basis}USDT",
        "last": kurs,
        "high": kurs * 1.05,
        "low": kurs * 0.95,
        "quote_volume": umsatz,
        "price_change_pct": 1.5,
        "trades": trades,
    }


def test_sortiert_nach_umsatz_und_deckelt() -> None:
    syms = [_sym(b) for b in ("BTC", "ETH", "SOL", "ADA")]
    ticker = [
        _tick("BTC", umsatz=900e6),
        _tick("ETH", umsatz=400e6),
        _tick("SOL", umsatz=90e6),
        _tick("ADA", umsatz=20e6),
    ]
    eintraege, bericht = bilde_universum(syms, ticker, UniversumFilter(max_symbole=3))
    assert nur_namen(eintraege) == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert bericht.nach_liquiditaet == 4
    assert bericht.genommen == 3


def test_stablecoins_und_verpackte_doppel_fliegen_raus() -> None:
    syms = [_sym(b) for b in ("BTC", "USDC", "FDUSD", "WBTC", "WBETH")]
    ticker = [_tick(b, umsatz=500e6) for b in ("BTC", "USDC", "FDUSD", "WBTC", "WBETH")]
    eintraege, bericht = bilde_universum(syms, ticker)
    assert nur_namen(eintraege) == ["BTCUSDT"]
    assert bericht.verworfen["stablecoin_oder_doppel"] == 4


def test_hebel_token_fliegen_raus() -> None:
    for b in ("BTCUP", "ETHDOWN", "ADABULL", "XRPBEAR", "BTC3L", "ETH3S"):
        assert ist_hebel_token(b), b
    for b in ("BTC", "ETH", "SOL", "DOGE"):
        assert not ist_hebel_token(b), b


def test_tokenisierte_aktien_und_etfs_fliegen_raus() -> None:
    """NVDAB und QQQB sehen im Universum aus wie Altcoins. Sie sind es nicht."""
    for b in ("NVDAB", "TSLAB", "AAPLB", "QQQB", "SPYB", "SOXLB", "TQQQB"):
        assert ist_tokenisierte_aktie(b), b
    # Echte Coins, die zufaellig auf B enden, bleiben drin.
    for b in ("BNB", "ARB", "SHIB", "TRB", "CKB", "DGB", "BB", "YB"):
        assert not ist_tokenisierte_aktie(b), b


def test_bericht_nennt_die_ausgeschlossenen_token_namentlich() -> None:
    """Ein faelschlich aussortierter Coin soll auffallen, nicht verschwinden."""
    syms = [_sym(b) for b in ("BTC", "NVDAB", "QQQB")]
    ticker = [_tick(b, umsatz=500e6) for b in ("BTC", "NVDAB", "QQQB")]
    _, bericht = bilde_universum(syms, ticker)
    assert bericht.tokenisiert == ("NVDAB", "QQQB")
    assert bericht.verworfen["tokenisierte_aktie"] == 2


def test_duenne_paare_fallen_durch_die_liquiditaetsschwelle() -> None:
    syms = [_sym(b) for b in ("BTC", "DUENN", "TEUER")]
    ticker = [
        _tick("BTC", umsatz=900e6),
        _tick("DUENN", umsatz=100e3),
        _tick("TEUER", umsatz=900e6, kurs=0.00001),
    ]
    eintraege, bericht = bilde_universum(syms, ticker)
    assert nur_namen(eintraege) == ["BTCUSDT"]
    assert bericht.verworfen["umsatz"] == 1
    assert bericht.verworfen["kurs"] == 1


def test_pflichtwerte_bleiben_auch_unter_der_schwelle() -> None:
    """BTC und ETH sind die Referenz fuer den ganzen Markt — sie muessen im Ranking sein."""
    syms = [_sym(b) for b in ("BTC", "ETH", "SOL")]
    ticker = [
        _tick("BTC", umsatz=1e3),
        _tick("ETH", umsatz=1e3),
        _tick("SOL", umsatz=800e6),
    ]
    eintraege, _ = bilde_universum(syms, ticker)
    assert set(nur_namen(eintraege)) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
    # Pflichtwerte stehen vorn, damit sie nicht vom Deckel abgeschnitten werden.
    assert nur_namen(eintraege)[:2] == ["BTCUSDT", "ETHUSDT"]


def test_paar_ohne_kurs_wird_gezaehlt_nicht_geraten() -> None:
    eintraege, bericht = bilde_universum([_sym("GEIST")], [], UniversumFilter())
    assert eintraege == []
    assert bericht.verworfen["kein_kurs"] == 1


def test_andere_quote_waehrung_wird_ignoriert() -> None:
    syms = [_sym("BTC"), _sym("BTC", quote="EUR")]
    ticker = [_tick("BTC", umsatz=900e6)]
    eintraege, bericht = bilde_universum(syms, ticker)
    assert nur_namen(eintraege) == ["BTCUSDT"]
    assert bericht.verworfen["quote"] == 1
