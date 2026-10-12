"""``DAYTRADE-AUSBRUCH-48H`` — Tageshandel-Setup fuer Coins (und experimentell Gold).

Herkunft: ``scripts/daytrade_studie.py`` Setup M2 (vorab festgelegt, 11.10.2026).
1-Std.-Kerzen. LONG, wenn der Stundenschluss ueber dem Hoch der letzten 48 Kerzen liegt,
ueber der EMA 200 (1 Std.) und — bei Coins — Bitcoin ueber seiner EMA 200 steht.
Stop 2 ATR(24) unter dem Einstieg. Ausstieg, sobald ein Stundenschluss unter dem Tief der
letzten 24 Kerzen liegt (zur naechsten Eroeffnung), spaetestens nach 72 Kerzen.

Ergebnis der Studie (24 Coins, 01.2022–09.2026, Kosten 0,25 % je Runde):
IS +0,069 R (n 3.977), OOS +0,076 R (n 2.455), 14/24 Coins im OOS im Plus, robust gegen
Lookback 36/72 und Trail 12/48 — aber Bootstrap-Untergrenze knapp unter 0 ("KANDIDAT", nicht
"BELEGT") und **nur mit niedrigen Kosten**: bei 0,5 % je Runde negativ. Also nur ueber
Bybit-Spot (0,1 % je Seite) oder Limit-Orders, nicht mit Kraken-Marktorders (0,4 % je Seite).

Gold (PAXG) mit denselben Regeln: IS −0,43 R, OOS +0,45 R — lief nur in der Gold-Hausse
2025/26. Deshalb dort ausdruecklich experimentell.

Reine Funktionen, keine Netzwerkzugriffe. Kerzen als ``(t, o, h, l, c)``-Tupel, damit Studie
und Live-Lauf dieselbe Rechnung benutzen (Backtest ≡ Paper).
"""

from __future__ import annotations

from datetime import datetime

SETUP_ID = "DAYTRADE-AUSBRUCH-48H"
LOOKBACK = 48
TRAIL = 24
MAX_BARS = 72
STOP_ATR = 2.0
MIN_RISIKO = 0.003
#: "Lohnt sich"-Tor (Nachtrag 12.10.2026, ``docs/DAYTRADE-STUDIE-2026-10.md`` Abschnitt 6):
#: Welle-Ausbrueche, die nur knapp (< 0,25 ATR(24)) ueber das 48-Std.-Hoch schliessen, waren
#: in IS (+0,02 R) und OOS (−0,30 R) die schwaechste Gruppe. Mit Tor: IS +0,27 R, OOS +0,28 R,
#: OOS-Bootstrap-5 % +0,11 R, 18/24 Coins im Plus. Schwellen 0,15–0,5 liefern dasselbe Bild.
MIN_UEBERHANG_ATR = 0.25

Kerze = tuple  # (t, o, h, l, c)


def ema(werte: list[float], n: int) -> list[float | None]:
    a, out, e = 2 / (n + 1), [], None
    for i, v in enumerate(werte):
        e = v if e is None else a * v + (1 - a) * e
        out.append(e if i >= n - 1 else None)
    return out


def atr(k: list[Kerze], n: int) -> list[float | None]:
    out, tr_s = [], []
    for i, (_, _, h, lo, c) in enumerate(k):
        pc = k[i - 1][4] if i else c
        tr_s.append(max(h - lo, abs(h - pc), abs(lo - pc)))
        out.append(sum(tr_s[-n:]) / n if i >= n else None)
    return out


def ist_signal(
    k: list[Kerze],
    i: int,
    e200: list[float | None],
    *,
    markt_ok: bool | None = None,
    lookback: int = LOOKBACK,
) -> bool:
    """LONG-Signal am Schluss der Kerze ``i``? ``markt_ok`` = Bitcoin ueber EMA 200
    (``None`` = kein Marktfilter, z. B. Gold)."""
    if i < max(lookback, 200) or e200[i] is None:
        return False
    c = k[i][4]
    if c <= max(x[2] for x in k[i - lookback : i]) or c <= e200[i]:
        return False
    return markt_ok is not False


def ueberhang(
    k: list[Kerze], i: int, a: list[float | None], lookback: int = LOOKBACK
) -> float | None:
    """Wie weit der Schluss der Kerze ``i`` ueber dem Hoch der ``lookback`` Kerzen davor
    liegt — in ATR(24). Negativ = noch unter der Marke."""
    if i < lookback or not a[i]:
        return None
    return (k[i][4] - max(x[2] for x in k[i - lookback : i])) / a[i]


def analyse(
    k: list[Kerze], *, markt_ok: bool | None = None, lookback: int = LOOKBACK
) -> dict | None:
    """Jetzt-Zustand der letzten abgeschlossenen Kerze — fuer Seite und Chart.

    Liefert die Marken, gegen die die Regel prueft: 48-Std.-Hoch (Ausbruchsmarke), EMA 200,
    24-Std.-Tief (Ausstiegsmarke), ATR und den Stop, der bei einem Signal jetzt gaelte.
    """
    if len(k) < 201:
        return None
    i = len(k) - 1
    e = ema([x[4] for x in k], 200)
    a = atr(k, 24)
    if e[i] is None or not a[i]:
        return None
    c = k[i][4]
    marke = max(x[2] for x in k[i - lookback : i])
    ue = ueberhang(k, i, a, lookback)
    sig = ist_signal(k, i, e, markt_ok=markt_ok, lookback=lookback)
    if sig:
        status = "ausbruch"
    elif c <= e[i]:
        status = "unter_ema"
    elif markt_ok is False:
        status = "markt_zu"
    elif (marke - c) <= a[i]:
        status = "nah"
    else:
        status = "ruhig"
    return {
        "zeit": k[i][0].isoformat() if hasattr(k[i][0], "isoformat") else k[i][0],
        "kurs": c,
        "ema200": e[i],
        "marke48": marke,
        "tief24": min(x[3] for x in k[i - TRAIL + 1 : i + 1]),
        "atr24": a[i],
        "stop_bei_signal": c - STOP_ATR * a[i],
        "abstand_pct": (marke - c) / c * 100,
        "ueberhang_atr": ue,
        "signal": sig,
        "status": status,
    }


def verfolge(
    k: list[Kerze],
    i_ein: int,
    stop: float,
    *,
    kosten: float = 0.0025,
    max_bars: int = MAX_BARS,
    trail_n: int = TRAIL,
) -> dict:
    """Long-Trade ab Eroeffnung der Kerze ``i_ein``, Kerze fuer Kerze.

    Rueckgabe: ``status`` ("offen"/"geschlossen"/"ungueltig"), ``r`` (nach Kosten, nur
    geschlossen), ``r_offen``, ``grund`` (STOP/AUSSTIEG/ZEIT), ``i_aus``, ``preis_aus``,
    ``ausstieg_marke`` (aktuelles 24-Std.-Tief, unter dem verkauft wird).
    """
    ein = k[i_ein][1]
    risiko = ein - stop
    if risiko <= 0 or risiko / ein < MIN_RISIKO:
        return {"status": "ungueltig"}
    kost_r = kosten * ein / risiko
    for j in range(i_ein, len(k)):
        _, o, _h, lo, c = k[j]
        if lo <= stop:
            aus = min(o, stop)
            return {
                "status": "geschlossen",
                "r": (aus - ein) / risiko - kost_r,
                "grund": "STOP",
                "i_aus": j,
                "preis_aus": aus,
            }
        if j - i_ein + 1 >= max_bars:
            return {
                "status": "geschlossen",
                "r": (c - ein) / risiko - kost_r,
                "grund": "ZEIT",
                "i_aus": j,
                "preis_aus": c,
            }
        if j >= trail_n and c < min(x[3] for x in k[j - trail_n : j]):
            if j + 1 < len(k):
                aus = k[j + 1][1]
                return {
                    "status": "geschlossen",
                    "r": (aus - ein) / risiko - kost_r,
                    "grund": "AUSSTIEG",
                    "i_aus": j + 1,
                    "preis_aus": aus,
                }
            # Ausstiegssignal an der letzten bekannten Kerze: verkaufen zur naechsten
            # Eroeffnung — fuer den Alarm zaehlt der Schluss als Preis.
            return {
                "status": "geschlossen",
                "r": (c - ein) / risiko - kost_r,
                "grund": "AUSSTIEG",
                "i_aus": j,
                "preis_aus": c,
            }
    letzte = k[-1]
    marke = min(x[3] for x in k[-trail_n:]) if len(k) >= trail_n else None
    return {
        "status": "offen",
        "r_offen": (letzte[4] - ein) / risiko - kost_r,
        "ausstieg_marke": marke,
        "bars": len(k) - i_ein,
    }


def index_von(k: list[Kerze], t: datetime) -> int | None:
    for i in range(len(k) - 1, -1, -1):
        if k[i][0] == t:
            return i
        if k[i][0] < t:
            return None
    return None


__all__ = [
    "MIN_UEBERHANG_ATR",
    "SETUP_ID",
    "analyse",
    "atr",
    "ema",
    "index_von",
    "ist_signal",
    "ueberhang",
    "verfolge",
]
