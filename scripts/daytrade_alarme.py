#!/usr/bin/env python3
"""Tageshandel-Alarme Krypto + Gold — die "Ausbruchswelle".

Ozan, 11.10. 22:33: "ich moechte auch Alarme ... fuer die Daytrades normal und auch fuer
Krypto und fuer Gold". Grundlage ist ``scripts/daytrade_studie.py`` (Ergebnis in
``docs/DAYTRADE-STUDIE-2026-10.md``):

* **Krypto — Ausbruchswelle.** Ein Coin schliesst eine Stunde ueber seinem 48-Std.-Hoch,
  ueber der EMA 200, Bitcoin ueber seiner EMA 200 — UND mindestens 12 der 24 beobachteten
  Coins sind in den letzten 24 Std. ebenfalls ausgebrochen. Studie: IS +0,21 R,
  OOS +0,14 R je Trade nach 0,25 % Kosten, 16/24 Coins im Plus, jedes Jahr 2022–2026 >= 0.
  Ohne die Welle (einzelne Ausbrueche) war es negativ — deshalb gibt es fuer die keinen Alarm.
* **Gold (PAXG) — experimentell.** Dieselbe Ausbruchsregel ohne Welle: OOS +0,45 R, aber
  2022–24 deutlich negativ. Lief nur in der Gold-Hausse. Seit 12.10. KEIN Push mehr, nur
  Paper und Anzeige — Ozan: "Benachrichtigung fuer Entry ... aber nur wenn es sich lohnt".
  Gold-Alarme kommen weiter aus S9 (``scripts/gold_daytrading_status.py``).

"LOHNT SICH"-TOR (12.10.2026): ein Einstiegs-Push nur, wenn der Coin in der Welle KLAR
ausbricht — Schluss mindestens 0,25 ATR(24) ueber dem 48-Std.-Hoch
(``daytrade_ausbruch.MIN_UEBERHANG_ATR``). Knappe Ausbrueche waren IS und OOS die schwaechste
Gruppe; sie laufen als Paper-Gegenprobe mit, ohne Push.

Ein Alarm je Lauf und Art (Einstiege gebuendelt, Ausstiege gebuendelt) — eine Welle mit
zehn Coins ist EINE Meldung, nicht zehn. Jeder Trade wird als Paper-Trade vorwaerts
mitgeschrieben (``data/repository_real/live/daytrade_stand.json``) — Backtest und Live
rechnen mit demselben Modul ``trading_agent.strategy.setups.daytrade_ausbruch``.

Kosten sind der Knackpunkt: bei 0,5 % je Runde ist der Vorteil weg. Also Bybit-Spot
(0,1 % je Seite) oder Limit-Orders — keine Kraken-Marktorder (0,4 % je Seite).

    python3 scripts/daytrade_alarme.py --out web/daytrade.json --send
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_agent.strategy.setups import daytrade_ausbruch as m

COINS = {
    "BTC": "XBTUSD",
    "ETH": "ETHUSD",
    "SOL": "SOLUSD",
    "XRP": "XRPUSD",
    "ADA": "ADAUSD",
    "DOGE": "XDGUSD",
    "AVAX": "AVAXUSD",
    "LINK": "LINKUSD",
    "DOT": "DOTUSD",
    "LTC": "LTCUSD",
    "BCH": "BCHUSD",
    "ATOM": "ATOMUSD",
    "NEAR": "NEARUSD",
    "UNI": "UNIUSD",
    "AAVE": "AAVEUSD",
    "FIL": "FILUSD",
    "ETC": "ETCUSD",
    "ARB": "ARBUSD",
    "OP": "OPUSD",
    "INJ": "INJUSD",
    "SUI": "SUIUSD",
    "APT": "APTUSD",
    "TRX": "TRXUSD",
    "XLM": "XLMUSD",
}
GOLD = {"PAXG": "PAXGUSD"}
#: Nur Anzeige (Chart + Analyse), zaehlt NICHT zur Welle und bekommt keine Alarme —
#: Ozan haelt TAO (12.10.). Nicht Teil der Studie.
EXTRA = {"TAO": "TAOUSD"}
NAMEN = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "SOL": "Solana",
    "XRP": "XRP",
    "ADA": "Cardano",
    "DOGE": "Dogecoin",
    "AVAX": "Avalanche",
    "LINK": "Chainlink",
    "DOT": "Polkadot",
    "LTC": "Litecoin",
    "BCH": "Bitcoin Cash",
    "ATOM": "Cosmos",
    "NEAR": "NEAR Protocol",
    "UNI": "Uniswap",
    "AAVE": "Aave",
    "FIL": "Filecoin",
    "ETC": "Ethereum Classic",
    "ARB": "Arbitrum",
    "OP": "Optimism",
    "INJ": "Injective",
    "SUI": "Sui",
    "APT": "Aptos",
    "TRX": "Tron",
    "XLM": "Stellar",
    "PAXG": "Gold (Pax Gold)",
    "TAO": "Bittensor",
}
MIN_WELLE = 12
FENSTER_BARS = 3  # Laeufe kommen alle ~10 Min.; drei Stunden decken Ausfaelle ab
KOSTEN = {"coin": 0.0025, "gold": 0.0015}
STAND = "data/repository_real/live/daytrade_stand.json"
PROTOKOLL = "data/repository_real/live/alerts.jsonl"
FORWARD_ZIEL = 100

STUDIE = {
    "krypto": {
        "regel": "1-Std.-Schluss über dem 48-Std.-Hoch, über EMA 200, Bitcoin über EMA 200 "
        "und ≥ 12 von 24 Coins in 24 Std. ebenfalls ausgebrochen (Welle)",
        "is": "+0,21 R je Trade (2022–2024)",
        "oos": "+0,14 R je Trade (01.2025–09.2026), 16/24 Coins im Plus",
        "trefferquote": "rund 25 % — viele kleine Verluste, wenige große Gewinne",
        "geld": "mit 0,25 % Risiko je Coin, max. 10 % je Coin, max. 100 % gesamt: "
        "IS +53 % Depot (2022–24), OOS +9,7 % in 21 Monaten (2025 −3,3 %, 2026 +13 %)",
        "groesse": "0,25 % Depot-Risiko je Coin, höchstens 10 % vom Depot je Coin, alle "
        "Einstiege einer Welle nehmen",
        "ohne_welle": "einzelne Ausbrüche ohne Welle: negativ — dafür kommt kein Alarm",
        "kosten": "nur mit ≤ 0,25 % Kosten je Runde (Bybit-Spot oder Limit-Order); "
        "mit Kraken-Marktorder (0,4 % je Seite) ist der Vorteil weg",
        "status": "KANDIDAT — OOS bestätigt, aber die Wellen-Schwelle wurde nach Sicht auf "
        "die IS-Daten gewählt; die Forward-Bilanz entscheidet",
        "tor": "Push nur bei klarem Ausbruch (Schluss ≥ 0,25 ATR über dem 48-Std.-Hoch): "
        "knappe Ausbrüche IS +0,02 R / OOS −0,30 R, klare IS +0,27 R / OOS +0,28 R, "
        "OOS-Untergrenze (Bootstrap 5 %) +0,11 R, 18/24 Coins im Plus. Nachtrag mit Sicht "
        "auf IS und OOS — darum weiter Kandidat",
        "doc": "docs/DAYTRADE-STUDIE-2026-10.md",
    },
    "gold": {
        "regel": "dieselbe Ausbruchsregel auf Pax Gold, ohne Welle",
        "is": "−0,43 R je Trade (2022–2024)",
        "oos": "+0,45 R je Trade (2025–09.2026)",
        "status": "EXPERIMENTELL — lief nur in der Gold-Hausse, vorher klar negativ. "
        "Lohnt sich nicht belegt: KEIN Push, nur Paper und Chart",
        "doc": "docs/DAYTRADE-STUDIE-2026-10.md",
    },
}


# ----------------------------------------------------------------------------- Daten
def kraken_kerzen(paar: str) -> list[tuple]:
    """Abgeschlossene 1-Std.-Kerzen (die laufende letzte Kerze wird verworfen)."""
    url = f"https://api.kraken.com/0/public/OHLC?pair={paar}&interval=60"
    with urllib.request.urlopen(url, timeout=30) as r:
        d = json.loads(r.read())
    if d.get("error"):
        raise RuntimeError(f"Kraken {paar}: {d['error']}")
    reihe = next(v for k, v in d["result"].items() if k != "last")
    kerzen = [
        (datetime.fromtimestamp(int(z[0]), UTC), float(z[1]), float(z[2]), float(z[3]), float(z[4]))
        for z in reihe
    ]
    jetzt = datetime.now(UTC)
    return [x for x in kerzen if x[0] + timedelta(hours=1) <= jetzt]


def alle_kerzen(
    pause: float = 0.35, paare: dict[str, str] | None = None
) -> tuple[dict[str, list], list[str]]:
    daten, fehler = {}, []
    for sym, paar in (paare or {**COINS, **GOLD}).items():
        try:
            daten[sym] = kraken_kerzen(paar)
        except Exception as exc:
            fehler.append(f"{sym}: {exc}")
        time.sleep(pause)
    return daten, fehler


# ----------------------------------------------------------------------------- Logik
def signal_zeiten(daten: dict[str, list], btc_ok: dict, ab: int = 30) -> dict[str, list]:
    """Je Coin die Zeiten der Signalkerzen in den letzten ``ab`` Kerzen."""
    out: dict[str, list] = {}
    for sym, k in daten.items():
        if sym in GOLD or len(k) < 260:
            continue
        e = m.ema([x[4] for x in k], 200)
        out[sym] = [
            k[i][0]
            for i in range(max(200, len(k) - ab), len(k))
            if m.ist_signal(k, i, e, markt_ok=btc_ok.get(k[i][0], False))
        ]
    return out


def breite_bei(sig: dict[str, list], t: datetime) -> int:
    return sum(
        1 for zeiten in sig.values() if any(t - timedelta(hours=24) < z <= t for z in zeiten)
    )


def btc_filter(daten: dict[str, list]) -> dict:
    k = daten.get("BTC") or []
    e = m.ema([x[4] for x in k], 200)
    return {x[0]: (e[i] is not None and x[4] > e[i]) for i, x in enumerate(k)}


def _klasse(sym: str) -> str:
    return "gold" if sym in GOLD else "coin"


def neue_signale(daten: dict[str, list], seit: datetime | None) -> tuple[list[dict], int]:
    """ARMED-Einstiege seit dem letzten Lauf. Gibt (Signale, aktuelle Wellenbreite)."""
    btc_ok = btc_filter(daten)
    sig = signal_zeiten(daten, btc_ok)
    jetzt_t = max((k[-1][0] for k in daten.values() if k), default=None)
    welle_jetzt = breite_bei(sig, jetzt_t) if jetzt_t else 0
    raus: list[dict] = []
    for sym, k in daten.items():
        if len(k) < 260:
            continue
        e = m.ema([x[4] for x in k], 200)
        a = m.atr(k, 24)
        erster = len(k) - 1 if seit is None else max(0, len(k) - FENSTER_BARS)
        for i in range(erster, len(k)):
            t = k[i][0]
            if seit is not None and t <= seit:
                continue
            gold = sym in GOLD
            markt = None if gold else btc_ok.get(t, False)
            if not m.ist_signal(k, i, e, markt_ok=markt) or not a[i]:
                continue
            breite = None if gold else breite_bei(sig, t)
            if not gold and breite < MIN_WELLE:
                continue
            c = k[i][4]
            stop = c - m.STOP_ATR * a[i]
            if (c - stop) / c < m.MIN_RISIKO:
                continue
            ue = m.ueberhang(k, i, a)
            lohnt = (not gold) and ue is not None and ue >= m.MIN_UEBERHANG_ATR
            raus.append(
                {
                    "sym": sym,
                    "klasse": _klasse(sym),
                    "signal_ts": t.isoformat(),
                    "signal_kurs": c,
                    "stop": round(stop, 8),
                    "ausstieg_marke": min(x[3] for x in k[i - m.TRAIL + 1 : i + 1]),
                    "breite": breite,
                    "ueberhang_atr": round(ue, 3) if ue is not None else None,
                    "lohnt": lohnt,
                    # Push nur, wenn es sich lohnt; alles andere laeuft still als Paper mit.
                    "alarm": lohnt,
                    "gemeldet": [],
                }
            )
    return raus, welle_jetzt


def verfolge_trade(t: dict, k: list) -> dict:
    """Paper-Trade aus den Kerzen nachrechnen. Einstieg = Eroeffnung der Kerze nach dem
    Signal (wie in der Studie). Solange es die noch nicht gibt: wartet."""
    ts = datetime.fromisoformat(t["signal_ts"])
    i = m.index_von(k, ts)
    if i is None:
        return {"status": "unbekannt"}
    if i + 1 >= len(k):
        return {"status": "wartet"}
    res = m.verfolge(k, i + 1, t["stop"], kosten=KOSTEN[t["klasse"]])
    res["einstieg"] = k[i + 1][1]
    if res["status"] == "geschlossen":
        res["ts_aus"] = k[res["i_aus"]][0].isoformat()
    return res


def fortschreiben(stand: dict | None, daten: dict[str, list]) -> tuple[dict, dict[str, list], int]:
    """Neuer Stand + Alarme ({"neu": [...], "aus": [...]}) + aktuelle Wellenbreite."""
    stand = dict(stand or {})
    offen: dict[str, dict] = dict(stand.get("offen") or {})
    fertig: list[dict] = list(stand.get("abgeschlossen") or [])
    seit = datetime.fromisoformat(stand["letzte_bar"]) if stand.get("letzte_bar") else None
    alarme: dict[str, list] = {"neu": [], "aus": []}

    for sym in list(offen):
        t = offen[sym]
        k = daten.get(sym)
        if not k:
            continue  # Kursausfall: Trade bleibt offen, naechster Lauf rechnet nach
        res = verfolge_trade(t, k)
        if res["status"] == "geschlossen":
            t.update(
                {
                    "einstieg": res["einstieg"],
                    "r": round(res["r"], 3),
                    "grund": res["grund"],
                    "preis_aus": res["preis_aus"],
                    "ts_aus": res["ts_aus"],
                }
            )
            fertig.append(t)
            del offen[sym]
            if t.get("alarm", True):  # alte Trades (vor 12.10.) wurden alle gemeldet
                alarme["aus"].append(t)
        elif res["status"] == "offen":
            t.update(
                {
                    "einstieg": res["einstieg"],
                    "r_offen": round(res["r_offen"], 3),
                    "ausstieg_marke": res["ausstieg_marke"],
                    "kurs": k[-1][4],
                }
            )

    signale, welle = neue_signale(daten, seit)
    for s in signale:
        if s["sym"] in offen:
            continue
        if any(f["sym"] == s["sym"] and f["signal_ts"] >= s["signal_ts"] for f in fertig):
            continue
        offen[s["sym"]] = s
        if s["alarm"]:
            alarme["neu"].append(s)
        res = verfolge_trade(s, daten[s["sym"]])
        if res["status"] == "geschlossen":  # nach einem Ausfall schon wieder vorbei
            s.update(
                {
                    "einstieg": res["einstieg"],
                    "r": round(res["r"], 3),
                    "grund": res["grund"],
                    "preis_aus": res["preis_aus"],
                    "ts_aus": res["ts_aus"],
                }
            )
            fertig.append(s)
            del offen[s["sym"]]
            if s["alarm"]:
                alarme["aus"].append(s)

    letzte = max((k[-1][0] for k in daten.values() if k), default=None)
    neu = {
        "letzte_bar": letzte.isoformat() if letzte else stand.get("letzte_bar"),
        "offen": offen,
        "abgeschlossen": fertig[-500:],
    }
    return neu, alarme, welle


# ----------------------------------------------------------------------------- Texte
def _fmt(x: float) -> str:
    d = 2 if x >= 10 else (4 if x >= 0.1 else 6)
    return f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


_GRUND = {
    "STOP": "Stop",
    "AUSSTIEG": "Ausstieg (Schluss unter 24-Std.-Tief)",
    "ZEIT": "Zeitausstieg nach 72 Std.",
}


def _abstand(t: dict) -> float:
    return (t["signal_kurs"] - t["stop"]) / t["signal_kurs"] * 100


def _groesse(t: dict) -> float:
    """Depot-Anteil: 0,25 % Risiko / Stop-Abstand, gedeckelt bei 10 %."""
    a = _abstand(t)
    return min(0.25 / a * 100, 10.0) if a > 0 else 0.0


def _zahl(x: float, d: int = 1) -> str:
    return f"{x:.{d}f}".replace(".", ",")


def alarm_texte(alarme: dict[str, list], welle: int) -> list[dict]:
    """Push-Texte. In ``alarme`` stehen nur noch Trades, die es sich zu melden lohnt
    (``alarm`` = True) — Gold-Ausbrueche und knappe Coin-Ausbrueche laufen still mit."""
    out = []
    neu_k = [t for t in alarme["neu"] if t.get("alarm", True)]
    if neu_k:
        zeilen = []
        for t in neu_k:
            ue = t.get("ueberhang_atr")
            zeilen.append(
                f"• {NAMEN.get(t['sym'], t['sym'])} ({t['sym']}/USDT): Kauf ≈ {_fmt(t['signal_kurs'])} $ · "
                f"Stop {_fmt(t['stop'])} $ (−{_zahl(_abstand(t))} %) · Größe ≈ {_groesse(t):.0f} % vom Depot"
                + (f" · Ausbruch {_zahl(ue, 2)} ATR über der Marke" if ue is not None else "")
            )
        out.append(
            {
                "titel": f"🚀 Daytrade-Einstieg — lohnt sich: {len(neu_k)} Coin"
                + ("s" if len(neu_k) > 1 else "")
                + f" (Welle {welle}/24)",
                "text": "\n".join(zeilen)
                + "\nWarum es sich lohnt: Welle (≥ 12 von 24 Coins brechen aus) UND klarer "
                "Ausbruch (≥ 0,25 ATR über dem 48-Std.-Hoch). Genau diese Fälle lagen in der "
                "Studie bei +0,27 R (2022–24) und +0,28 R (2025–26) je Trade nach Kosten.\n"
                "Ausstieg: sobald eine Stunde unter dem 24-Std.-Tief schliesst (kommt als Alarm), "
                "spätestens nach 72 Std. Kein festes Kursziel — Gewinne laufen lassen.\n"
                "Größe: 0,25 % Depot-Risiko je Coin, höchstens 10 % vom Depot je Coin. Rund 3 "
                "von 4 Trades verlieren klein, wenige gewinnen groß — einzelne Verluste sind normal.\n"
                "Bybit-Spot (USDT) oder Limit-Order — Kraken-Marktorder kostet den Vorteil.\n"
                "Kandidat, nicht bewiesen — Paper, kein Echtgeld-Rat.",
                "dedup_key": "daytrade:neu:"
                + ",".join(f"{t['sym']}@{t['signal_ts']}" for t in neu_k),
            }
        )
    aus = [t for t in alarme["aus"] if t.get("alarm", True)]
    if aus:
        zeilen = [
            f"• {NAMEN.get(t['sym'], t['sym'])}: {_GRUND.get(t['grund'], t['grund'])} bei "
            f"{_fmt(t['preis_aus'])} $ → {t['r']:+.2f} R"
            for t in aus
        ]
        out.append(
            {
                "titel": f"🔻 Daytrade verkaufen: {len(aus)} Ausstieg"
                + ("e" if len(aus) > 1 else ""),
                "text": "\n".join(zeilen) + "\nPaper-Trades — Ergebnis nach 0,25 % Kosten.",
                "dedup_key": "daytrade:aus:" + ",".join(f"{t['sym']}@{t['ts_aus']}" for t in aus),
            }
        )
    return out


def bilanz(stand: dict, klasse: str, alarm: bool | None = None) -> dict:
    """Forward-Bilanz. ``alarm`` True = nur gemeldete Trades, False = stille Gegenprobe."""
    f = [
        t
        for t in (stand.get("abgeschlossen") or [])
        if t.get("klasse") == klasse
        and "r" in t
        and (alarm is None or bool(t.get("alarm", True)) == alarm)
    ]
    n = len(f)
    s = round(sum(t["r"] for t in f), 3)
    return {
        "anzahl": n,
        "ziel": FORWARD_ZIEL,
        "summe_r": s,
        "schnitt_r": round(s / n, 3) if n else None,
        "trefferquote": round(sum(1 for t in f if t["r"] > 0) / n, 3) if n else None,
        "letzte": f[-6:][::-1],
    }


def analysen(daten: dict[str, list], btc_ok: dict) -> dict[str, dict]:
    """Jetzt-Zustand je Wert fuer Seite und Chart (gleiche Rechnung wie der Alarm)."""
    out: dict[str, dict] = {}
    for sym, k in daten.items():
        if not k:
            continue
        markt = None if (sym in GOLD or sym in EXTRA) else btc_ok.get(k[-1][0], False)
        a = m.analyse(k, markt_ok=markt)
        if not a:
            continue
        a["name"] = NAMEN.get(sym, sym)
        a["klasse"] = "gold" if sym in GOLD else ("extra" if sym in EXTRA else "coin")
        a["paar"] = {**COINS, **GOLD, **EXTRA}[sym]
        ue = a.get("ueberhang_atr")
        a["lohnt"] = bool(
            a["klasse"] == "coin" and a["signal"] and ue is not None and ue >= m.MIN_UEBERHANG_ATR
        )
        out[sym] = {x: (round(v, 8) if isinstance(v, float) else v) for x, v in a.items()}
    return out


def _senden(texte: list[dict]) -> None:
    from trading_agent.ops.notify import (
        FileSink,
        GitHubIssueSink,
        Notification,
        Notifier,
        Severity,
        TelegramSink,
        WebPushSink,
    )

    tg = TelegramSink(min_severity=Severity.INFO)
    push = WebPushSink(min_severity=Severity.INFO)
    gh = GitHubIssueSink(erwaehnen="ozancanerd-ship-it")
    sinks: list = [FileSink(PROTOKOLL)]
    for s in (push, tg):
        if s.available():
            sinks.insert(0, s)
    if gh.available():
        sinks.append(gh)
    n = Notifier(sinks, max_per_window=10, dedup_window_s=0.0)
    jetzt = datetime.now(UTC)
    raus = sum(
        1
        for a in texte
        if n.notify(
            Notification(
                severity=Severity.CRITICAL,
                title=a["titel"],
                body=a["text"],
                dedup_key=a["dedup_key"][:200],
                ts=jetzt,
            )
        )
    )
    print(f"{raus} von {len(texte)} Daytrade-Alarm(en) verschickt ({n.active_sinks})")


def gold_literatur(pfad: str = "docs/gold_intraday_literatur.json") -> dict | None:
    """Kernzahlen der Gold-Tageshandels-Studie nach der Literatur (Noise-Area-Momentum,
    5-Min.-ORB) fuer das Gold-Playbook in der App."""
    f = Path(__file__).resolve().parents[1] / pfad
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    def kurz(x: dict) -> dict:
        return {
            teil: {
                k: (x[teil].get(k) or {}).get("schnitt")
                for k in ("brutto", "limit_perp", "markt_perp", "kraken")
            }
            for teil in ("is", "oos")
        } | {"bestanden": x.get("bestanden"), "jahre": x.get("jahre_haupt")}

    return {
        "nm": kurz(d["nm"]),
        "orb": kurz(d["orb"]),
        "orb_risiko_median_pct": d.get("orb_risiko_median_pct"),
        "daten": d.get("daten"),
        "doc": d.get("doc"),
    }


def stundenprofil(pfad: str = "docs/stundenprofil.json") -> dict | None:
    """Ergebnis der Stundenprofil-Studie (scripts/stundenprofil_studie.py) fuer die App:
    Profil je Wochenstunde, EWMA-lambda, Bandfaktoren und OOS-Treffer je Horizont."""
    f = Path(__file__).resolve().parents[1] / pfad
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    aus = {"doc": d.get("doc"), "daten": d.get("daten")}
    for g in ("gold", "coins"):
        x = d.get(g) or {}
        aus[g] = {
            "lambda": x.get("lambda"),
            "profil": x.get("profil"),
            "horizonte": x.get("horizonte"),
            "sitzungen": x.get("sitzungen"),
        }
    return aus


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="web/daytrade.json")
    ap.add_argument("--stand", default=STAND)
    ap.add_argument("--send", action="store_true")
    args = ap.parse_args()

    stand_pfad = Path(args.stand)
    stand = None
    if stand_pfad.exists():
        try:
            stand = json.loads(stand_pfad.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print(f"::warning::{stand_pfad} unlesbar — starte ohne Stand")

    jetzt = datetime.now(UTC)
    daten, fehler = alle_kerzen()
    extra, fehler_extra = alle_kerzen(paare=EXTRA)
    seite: dict = {
        "aktiv": False,
        "geprueft": jetzt.isoformat(),
        "studie": STUDIE,
        "min_welle": MIN_WELLE,
        "min_ueberhang_atr": m.MIN_UEBERHANG_ATR,
        "stundenprofil": stundenprofil(),
        "gold_literatur": gold_literatur(),
        "fehler": fehler + fehler_extra,
    }
    if "BTC" not in daten or len(daten) < len(COINS) // 2:
        seite["fehler"] = [*fehler, "zu wenige Kursreihen von Kraken — kein Lauf"]
        texte: list[dict] = []
        neuer = None
    else:
        neuer, alarme, welle = fortschreiben(stand, daten)
        texte = alarm_texte(alarme, welle)
        btc_ok = btc_filter(daten)
        letzte_btc = daten["BTC"][-1][0]
        seite.update(
            {
                "aktiv": True,
                "welle": welle,
                "btc_ueber_ema200": bool(btc_ok.get(letzte_btc)),
                "offen": list(neuer["offen"].values()),
                "bilanz_krypto": bilanz(neuer, "coin", alarm=True),
                "bilanz_krypto_still": bilanz(neuer, "coin", alarm=False),
                "bilanz_gold": bilanz(neuer, "gold"),
                "analyse": analysen({**daten, **extra}, btc_ok),
                "letzte_kerze": letzte_btc.isoformat(),
            }
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(seite, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    if neuer is not None:
        stand_pfad.parent.mkdir(parents=True, exist_ok=True)
        stand_pfad.write_text(
            json.dumps(neuer, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
        )
    for a in texte:
        print(f"ALARM {a['titel']}\n{a['text']}\n")
    if args.send and texte:
        _senden(texte)
    print(
        json.dumps(
            {k: v for k, v in seite.items() if k not in ("studie", "offen", "analyse")},
            indent=2,
            default=str,
        )[:1500]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
