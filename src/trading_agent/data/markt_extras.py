"""Zusatzdaten zum Scan: Finanzierung und offene Positionen am Terminmarkt, Stimmung.

WARUM

Masterplan §5 nennt Funding, Open Interest und Liquidationen als Zutaten fuer die
Bewertung eines Coins, §9 die Stimmung des Marktes. Bis zum 27.09. stand davon nichts im
Scan. Der Chart eines Coins erzaehlt, wo der Kurs war; der Terminmarkt erzaehlt, wie die
Leute positioniert sind — und ein Markt, in dem alle schon long sind und dafuer zahlen,
ist ein anderer als einer, in dem die Leerverkaeufer zahlen.

WAS DAMIT PASSIERT — UND WAS NICHT

Die Zahlen stehen in der App neben der Analyse, als **Kontext**. In den Score gehen sie
nicht ein, bis eine vorab registrierte Studie zeigt, dass sie dort etwas verbessern
(``docs/SIGNAL-STUDIE-2026-09.md``). Ein Faktor, der gut klingt, ist noch kein Faktor.

QUELLEN (frei, ohne Schluessel, aus der CI erreichbar)

* Kraken Futures ``/derivatives/api/v3/tickers`` — alle Perpetuals in EINER Antwort:
  Finanzierungssatz, offene Positionen, Mark- und Indexpreis, Tagesumsatz.
* alternative.me ``/fng/`` — der Krypto-Angst-und-Gier-Index (0 = Panik, 100 = Gier).
"""

from __future__ import annotations

import math
from typing import Any

KRAKEN_FUTURES_TICKER = "https://futures.kraken.com/derivatives/api/v3/tickers"
ANGST_GIER = "https://api.alternative.me/fng/?limit=31"

#: Kraken fuehrt Bitcoin am Terminmarkt als XBT.
_UMBENANNT = {"XBT": "BTC", "XDG": "DOGE"}

#: Ab diesen Jahreswerten gilt die Finanzierung als ueberhitzt. Bewusst weit gesetzt —
#: es ist ein Hinweis, keine Regel (die Regel braucht erst die Studie).
FUNDING_HEISS_JAHR_PCT = 50.0
FUNDING_KALT_JAHR_PCT = -30.0


def _zahl(x: Any) -> float | None:
    if isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def basis_aus_future(symbol: str) -> str | None:
    """``PF_XBTUSD`` → ``BTC``. Nur lineare Perpetuals in Dollar."""
    s = str(symbol or "").upper()
    if not s.startswith("PF_") or not s.endswith("USD"):
        return None
    b = s[3:-3]
    return _UMBENANNT.get(b, b) or None


def derivate_tabelle(antwort: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Aus der Kraken-Futures-Antwort je Coin: Finanzierung, offene Positionen, Aufschlag.

    Kraken rechnet die Finanzierung stuendlich. ``fundingRate`` ist ein absoluter Betrag
    je Kontrakt; geteilt durch den Markpreis ergibt sich der Satz je Stunde.
    """
    aus: dict[str, dict[str, Any]] = {}
    for t in (antwort or {}).get("tickers") or []:
        if not isinstance(t, dict) or t.get("tag") != "perpetual" or t.get("suspended"):
            continue
        basis = basis_aus_future(str(t.get("symbol") or ""))
        mark = _zahl(t.get("markPrice"))
        if not basis or not mark or mark <= 0:
            continue
        fr = _zahl(t.get("fundingRate"))
        fr_neu = _zahl(t.get("fundingRatePrediction"))
        oi = _zahl(t.get("openInterest"))
        idx = _zahl(t.get("indexPrice"))
        umsatz = _zahl(t.get("volumeQuote"))
        stunde = fr / mark if fr is not None else None
        eintrag: dict[str, Any] = {
            "funding_8h_pct": round(stunde * 8 * 100, 5) if stunde is not None else None,
            "funding_jahr_pct": round(stunde * 8760 * 100, 2) if stunde is not None else None,
            "funding_naechste_jahr_pct": (
                round(fr_neu / mark * 8760 * 100, 2) if fr_neu is not None else None
            ),
            "oi_usd": round(oi * mark, 0) if oi is not None else None,
            "umsatz_24h_usd": round(umsatz, 0) if umsatz is not None else None,
            "aufschlag_pct": round((mark / idx - 1) * 100, 4) if idx else None,
        }
        if eintrag["oi_usd"] and umsatz:
            eintrag["oi_zu_umsatz"] = round(eintrag["oi_usd"] / umsatz, 3)
        # Bei zwei Kontrakten auf denselben Coin gewinnt der mit mehr offenen Positionen.
        alt = aus.get(basis)
        if alt is None or (eintrag["oi_usd"] or 0) > (alt.get("oi_usd") or 0):
            aus[basis] = eintrag
    return aus


def muenze(instrument: str) -> str:
    s = str(instrument or "").upper()
    for q in ("USDT", "USDC", "USD", "EUR"):
        if s.endswith(q) and len(s) > len(q):
            return s[: -len(q)]
    return s


def derivate_satz(d: dict[str, Any] | None) -> str | None:
    """Ein beschreibender Satz, wenn die Finanzierung aus dem Rahmen faellt.

    Bewusst NICHT fuer die Warnliste: die Funding-Studie (docs/FUNDING-STUDIE-2026-09.md)
    fand keinen Vorhersagewert. Der Satz beschreibt die Lage, er sagt nichts voraus."""
    if not d:
        return None
    j = _zahl(d.get("funding_jahr_pct"))
    if j is None:
        return None
    if j >= FUNDING_HEISS_JAHR_PCT:
        return (
            f"Terminmarkt: Longs zahlen {j:.0f} % aufs Jahr gerechnet — die Long-Seite "
            "ist voll"
        )
    if j <= FUNDING_KALT_JAHR_PCT:
        return (
            f"Terminmarkt: Leerverkäufer zahlen {abs(j):.0f} % aufs Jahr — viele wetten "
            "dagegen"
        )
    return None


def stimmung_zusammenfassen(
    derivate: dict[str, dict[str, Any]], coins: list[str]
) -> dict[str, Any] | None:
    """Eine Zeile fuer die Marktuebersicht: Finanzierung von Bitcoin und im Median."""
    werte = [_zahl((derivate.get(c) or {}).get("funding_jahr_pct")) for c in coins if c in derivate]
    werte_ok = sorted(w for w in werte if w is not None)
    if not werte_ok:
        return None
    mitte = werte_ok[len(werte_ok) // 2]
    btc = derivate.get("BTC") or {}
    return {
        "n": len(werte_ok),
        "median_funding_jahr_pct": round(mitte, 2),
        "anteil_positiv": round(sum(1 for w in werte_ok if w > 0) / len(werte_ok) * 100, 1),
        "heiss": sum(1 for w in werte_ok if w >= FUNDING_HEISS_JAHR_PCT),
        "kalt": sum(1 for w in werte_ok if w <= FUNDING_KALT_JAHR_PCT),
        "btc_funding_jahr_pct": btc.get("funding_jahr_pct"),
        "btc_oi_usd": btc.get("oi_usd"),
    }


def angst_gier_aus(antwort: dict[str, Any]) -> dict[str, Any] | None:
    """alternative.me: aktueller Wert, dazu vor einer Woche und vor einem Monat."""
    daten = (antwort or {}).get("data") or []
    reihe = []
    for d in daten:
        w = _zahl(d.get("value"))
        if w is not None:
            reihe.append((w, str(d.get("value_classification") or "")))
    if not reihe:
        return None
    text_de = {
        "Extreme Fear": "extreme Angst",
        "Fear": "Angst",
        "Neutral": "neutral",
        "Greed": "Gier",
        "Extreme Greed": "extreme Gier",
    }
    return {
        "wert": reihe[0][0],
        "text": text_de.get(reihe[0][1], reihe[0][1]),
        "vorwoche": reihe[7][0] if len(reihe) > 7 else None,
        "vormonat": reihe[30][0] if len(reihe) > 30 else None,
    }


async def hole_json(url: str, *, timeout: float = 20.0) -> dict[str, Any] | None:
    """Ein GET mit JSON-Antwort; ``None`` bei jedem Fehler (der Scan haengt nicht daran)."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(url, headers={"User-Agent": "AI-Trading-Agent/1.0"})
            r.raise_for_status()
            d = r.json()
            return d if isinstance(d, dict) else None
    except Exception:
        return None


__all__ = [
    "ANGST_GIER",
    "FUNDING_HEISS_JAHR_PCT",
    "FUNDING_KALT_JAHR_PCT",
    "KRAKEN_FUTURES_TICKER",
    "angst_gier_aus",
    "basis_aus_future",
    "derivate_satz",
    "derivate_tabelle",
    "hole_json",
    "muenze",
    "stimmung_zusammenfassen",
]
