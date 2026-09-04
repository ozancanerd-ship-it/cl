#!/usr/bin/env python3
"""Taeglicher Klartext-Report aus dem Forward-Journal — Konsole und Telegram.

Warum das existiert: ``scripts/tsmom_forward.py`` schreibt jeden Tag auf, was die
eingefrorene Regel gesagt haette. Das ist die saubere Datenspur, aber es ist keine
Antwort auf die Frage „was mache ich jetzt?". Dieses Skript uebersetzt die Journalzeile
in genau diese Antwort — und zwar unter den harten Grenzen aus ``config/risk.yaml``:

* die Regel liefert je Instrument ein volatilitaetsskaliertes Zielgewicht
* ``max_positions`` schneidet auf die staerksten Kandidaten zu (bei 400 EUR sind mehr
  Positionen nur Gebuehren)
* ``max_total_exposure_pct`` / ``min_cash_pct`` deckeln, was insgesamt investiert sein darf
* das Ergebnis wird in Euro umgerechnet, weil Prozentzahlen niemand handeln kann

Der Vergleich mit der VORTAGSZEILE erzeugt die eigentlichen Signale:
KAUFEN (neu dabei), VERKAUFEN (rausgefallen), ANPASSEN (Gewicht deutlich verschoben).

Ohne Aenderung wird bewusst NICHTS verschickt — ausser mit ``--force``. Kein Alert-Spam.

    python3 scripts/daily_report.py                 # anzeigen
    python3 scripts/daily_report.py --send          # anzeigen + Telegram, nur bei Aenderung
    python3 scripts/daily_report.py --send --force  # immer senden (Testlauf)
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

JOURNAL = "data/repository_real/live/tsmom_forward.jsonl"
RISK = "config/risk.yaml"

# Wo Ozan das jeweilige Instrument HEUTE tatsaechlich kaufen kann, und was das kostet.
# Das ist der Unterschied zwischen einem Plan und einem Zettel: die Regel darf NVDA wollen,
# aber ohne Depot ist das keine Anweisung, sondern nur eine Beobachtung.
#
# min_order_eur ist keine Willkuer, sondern Gebuehrenarithmetik: bei Trade Republic kostet
# jede Order 1 EUR, rein und raus also 2 EUR. Bei 60 EUR Positionsgroesse sind das 3.3 %
# — bei 20 EUR waeren es 10 %, und dann frisst die Gebuehr den halben Erwartungswert.
# Positionen unter dieser Grenze werden deshalb gar nicht erst vorgeschlagen.
VENUES: dict[str, dict] = {
    "crypto": {
        "name": "Binance/Kraken/Bybit",
        "fee_pct": 0.20,  # Taker, konservativ ueber die drei Boersen
        "fee_eur": 0.0,
        "min_order_eur": 10.0,
        "api": True,  # Bestand wird automatisch gelesen
    },
    "binance_gold": {
        "name": "Binance als PAXGUSDT",  # 1:1 physisch hinterlegtes Gold
        "fee_pct": 0.20,
        "fee_eur": 0.0,
        "min_order_eur": 10.0,
        "api": True,
    },
    "tr": {
        "name": "Trade Republic (App, von Hand)",
        "fee_pct": 0.0,
        "fee_eur": 1.0,
        "min_order_eur": 60.0,
        "api": False,  # keine offizielle Schnittstelle — Bestand muss von Hand gepflegt werden
    },
}

# Signal kommt aus der praeregistrierten Reihe; das Venue sagt nur, WO man dieselbe
# Wette eingeht. XAUUSD-YFD ist GC=F (Gold-Future) — handelbar als PAXGUSDT (PAX Gold),
# 1:1 physisch hinterlegt, Binance-Spot, 28 Mio USDT Tagesumsatz, Spread praktisch null.
#
# Binance listet auch XAUUSDT, aber NUR als USD-M-Perpetual (TRADIFI_PERPETUAL, seit
# 2025-12-11 — siehe src/trading_agent/data/providers/binance.py). Das ist ein Future mit
# Hebel, Funding und Liquidationsrisiko. Unter config/risk.yaml (paper, kein Hebel,
# -10 % Kill-Switch) hat das nichts zu suchen. Spot-PAXG ist dieselbe Wette ohne die
# Sprengfalle. Wenn spaeter doch Perp, dann als eigene Entscheidung, nicht als Fussnote.
INSTRUMENT_VENUE: dict[str, str | None] = {
    "BTCUSDT": "crypto",
    "ETHUSDT": "crypto",
    "BNBUSDT": "crypto",
    "XAUUSD-YFD": "binance_gold",
    "NVDA-YFD": "tr",
    "AAPL-YFD": "tr",
    "MSFT-YFD": "tr",
    "AMD-YFD": "tr",
    "GOOGL-YFD": "tr",
    "META-YFD": "tr",
    "EURUSD-YFD": None,  # braucht einen FX-Broker — noch keiner angebunden
    "GBPUSD-YFD": None,
    "USDJPY-YFD": None,
}
BLOCKED_HINT = {
    "EURUSD-YFD": "FX-Broker",
    "GBPUSD-YFD": "FX-Broker",
    "USDJPY-YFD": "FX-Broker",
}


def venue_of(instrument: str) -> dict | None:
    key = INSTRUMENT_VENUE.get(instrument)
    return VENUES.get(key) if key else None


def roundtrip_cost_pct(v: dict, eur: float) -> float:
    """Was ein kompletter Ein- und Ausstieg in Prozent der Position kostet."""
    if eur <= 0:
        return 0.0
    return 2.0 * (v["fee_eur"] + eur * v["fee_pct"] / 100.0) / eur * 100.0


# Erwartungswerte aus scripts/tsmom_trade_stats.py auf dem Multi-Asset-Panel.
# Sie sind eine Beschreibung der Vergangenheit, keine Zusage. Sie stehen im Report,
# damit die Zahlen im Kopf stimmen, bevor die erste rote Zahl kommt.
EXPECT = {
    "win_rate": 0.30,
    "typ_win_pct": 18.3,
    "typ_loss_pct": -1.2,
    "worst_dip_pct": -20.0,
    "median_winner_days": 317,
    "profit_from_long_holds": 0.99,
}


def _load_env_file(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _risk_config(path: str) -> dict:
    """Minimal-Parser fuer die Felder, die hier gebraucht werden (kein yaml-Import noetig)."""
    txt = Path(path).read_text(encoding="utf-8")
    out: dict[str, float] = {}
    wanted = {
        "starting_equity": "equity",
        "max_positions": "max_positions",
        "max_total_exposure_pct": "max_exposure_pct",
        "min_cash_pct": "min_cash_pct",
        "daily_loss_pct": "daily_loss_pct",
        "max_drawdown_pct": "max_dd_pct",
    }
    for line in txt.splitlines():
        s = line.split("#", 1)[0].strip()
        if ":" not in s:
            continue
        k, v = s.split(":", 1)
        k, v = k.strip(), v.strip()
        if k in wanted and v:
            with contextlib.suppress(ValueError):
                out[wanted[k]] = float(v)
    out.setdefault("equity", 400.0)
    out.setdefault("max_positions", 8)
    out.setdefault("max_exposure_pct", 60.0)
    out.setdefault("min_cash_pct", 40.0)
    return out


def _journal(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def build_plan(entry: dict, risk: dict) -> dict:
    """Journalzeile -> handelbarer Euro-Plan, unter den Grenzen aus risk.yaml.

    Drei Filter, in dieser Reihenfolge:

    1. **Konto** — was nirgends gekauft werden kann, bekommt kein Budget. Es wird
       trotzdem ausgewiesen, damit sichtbar bleibt, was die Regel eigentlich wollte.
    2. **max_positions** — bei 400 EUR sind mehr Positionen nur Gebuehren.
    3. **min_order_eur** — eine Position, deren Gebuehr sie auffrisst, ist keine Position.
       Faellt eine raus, wird ihr Budget auf die verbleibenden verteilt und erneut geprueft.
    """
    equity = risk["equity"]
    max_pos = int(risk["max_positions"])
    cap = min(risk["max_exposure_pct"], 100.0 - risk["min_cash_pct"]) / 100.0

    longs = [i for i in entry.get("instruments", []) if i.get("target_weight", 0) > 0]
    longs.sort(key=lambda x: -x["target_weight"])

    # Das Budget folgt der Ueberzeugung der Regel: portfolio_weight ist das mittlere
    # Zielgewicht ueber ALLE Instrumente. Wenige/schwache Longs -> kleineres Budget.
    budget = min(cap, float(entry.get("portfolio_weight", 0.0)))

    handelbar = [i for i in longs if venue_of(i["instrument"])]
    ohne_konto = [i for i in longs if not venue_of(i["instrument"])]

    kandidaten = handelbar[:max_pos]
    zu_klein: list[dict] = []

    # Iterativ: raus, was unter der Gebuehrengrenze liegt, Rest neu skalieren, nochmal pruefen.
    while kandidaten:
        raw = sum(i["target_weight"] for i in kandidaten)
        eur = {
            i["instrument"]: (i["target_weight"] / raw * budget * equity) if raw > 0 else 0.0
            for i in kandidaten
        }
        schwaechster = min(
            kandidaten,
            key=lambda i: eur[i["instrument"]] - venue_of(i["instrument"])["min_order_eur"],  # type: ignore[index]
        )
        v = venue_of(schwaechster["instrument"])
        assert v is not None
        if eur[schwaechster["instrument"]] >= v["min_order_eur"]:
            break
        zu_klein.append(
            {
                "instrument": schwaechster["instrument"],
                "eur": eur[schwaechster["instrument"]],
                "min_order_eur": v["min_order_eur"],
                "venue": v["name"],
            }
        )
        kandidaten = [i for i in kandidaten if i is not schwaechster]

    raw = sum(i["target_weight"] for i in kandidaten)
    positions = []
    for i in kandidaten:
        v = venue_of(i["instrument"])
        assert v is not None
        w = (i["target_weight"] / raw * budget) if raw > 0 else 0.0
        eur_i = w * equity
        positions.append(
            {
                "instrument": i["instrument"],
                "weight": w,
                "eur": eur_i,
                "close": i.get("close"),
                "state": i.get("state"),
                "agreement": i.get("agreement", 0.0),
                "vol": i.get("realized_vol", 0.0),
                "venue": v["name"],
                "api": v["api"],
                "cost_pct": roundtrip_cost_pct(v, eur_i),
            }
        )

    invested = sum(x["eur"] for x in positions)
    return {
        "date": entry.get("date"),
        "eligibility": entry.get("eligibility"),
        "budget": budget,
        "invested_eur": invested,
        "cash_eur": equity - invested,
        "equity": equity,
        "n_long": entry.get("n_long"),
        "n_total": entry.get("n_instruments"),
        "positions": positions,
        "zu_klein": zu_klein,
        "ohne_konto": [
            {
                "instrument": i["instrument"],
                "blocked_by": BLOCKED_HINT.get(i["instrument"], "Konto"),
            }
            for i in ohne_konto
        ],
        "dropped": [i["instrument"] for i in handelbar[max_pos:]],
        "daily_loss_pct": risk.get("daily_loss_pct", 2.0),
        "max_dd_pct": risk.get("max_dd_pct", 10.0),
    }


def diff_plans(new: dict, old: dict | None) -> dict:
    """KAUFEN / VERKAUFEN / ANPASSEN gegenueber dem Vortag."""
    cur = {p["instrument"]: p for p in new["positions"]}
    if old is None:
        return {"buy": list(cur.values()), "sell": [], "adjust": [], "first_run": True}
    prev = {p["instrument"]: p for p in old["positions"]}
    buy = [p for k, p in cur.items() if k not in prev]
    sell = [p for k, p in prev.items() if k not in cur]
    adjust = []
    for k, p in cur.items():
        if k not in prev:
            continue
        before = prev[k]["eur"]
        after = p["eur"]
        if before > 0 and abs(after - before) / before >= 0.25 and abs(after - before) >= 5.0:
            adjust.append({**p, "eur_before": before})
    return {"buy": buy, "sell": sell, "adjust": adjust, "first_run": False}


def _line(x: dict) -> str:
    px = f"{x['close']:,.2f}" if x.get("close") else "?"
    return f"  {x['instrument']:<12} {x['eur']:>5.0f} EUR   Kurs {px}"


def render(plan: dict, d: dict) -> str:
    L: list[str] = []
    mode = "PAPER" if plan["eligibility"] != "live" else "LIVE"
    L.append(f"TAGESPLAN {plan['date']}  |  {mode}")
    L.append("")

    if d["first_run"]:
        L.append("Erster Lauf - das ist der Startbestand, keine Aenderung.")
        L.append("")

    if d["sell"]:
        L.append("== VERKAUFEN ==")
        for x in d["sell"]:
            L.append(f"  {x['instrument']:<12} {x['eur']:>5.0f} EUR raus   [{x['venue']}]")
            L.append("     Grund: Regel ist nicht mehr long bzw. aus den Top-Plaetzen gefallen")
        L.append("")

    if d["buy"]:
        L.append("== KAUFEN ==")
        for x in d["buy"]:
            L.append(_line(x) + f"   [{x['venue']}]")
            L.append(
                f"     Zustimmung {x['agreement']:.0%} · Vola {x['vol']:.0%} p.a."
                f" · Gebuehr rein+raus {x['cost_pct']:.1f} %"
            )
        L.append("")

    if d["adjust"]:
        L.append("== ANPASSEN ==")
        for x in d["adjust"]:
            richtung = "aufstocken" if x["eur"] > x["eur_before"] else "reduzieren"
            L.append(
                f"  {x['instrument']:<12} {x['eur_before']:>5.0f} -> {x['eur']:>5.0f} EUR"
                f"  ({richtung})"
            )
        L.append("")

    if not (d["buy"] or d["sell"] or d["adjust"]):
        L.append("== KEINE AENDERUNG - Bestand halten, nichts tun. ==")
        L.append("")

    L.append(f"BESTAND LAUT PLAN  ({plan['n_long']}/{plan['n_total']} long)")
    for x in sorted(plan["positions"], key=lambda y: -y["eur"]):
        hand = "" if x["api"] else "  (von Hand)"
        L.append(f"  {x['instrument']:<12} {x['eur']:>5.0f} EUR  {x['weight']:>5.1%}{hand}")
    L.append(
        f"  {'investiert':<12} {plan['invested_eur']:>5.0f} EUR"
        f"  {plan['invested_eur'] / plan['equity']:>5.1%}"
    )
    L.append(
        f"  {'Cash':<12} {plan['cash_eur']:>5.0f} EUR  {plan['cash_eur'] / plan['equity']:>5.1%}"
    )

    if plan["zu_klein"]:
        L.append("")
        L.append("NICHT VORGESCHLAGEN - Position waere zu klein fuer die Gebuehr:")
        for x in plan["zu_klein"]:
            L.append(
                f"  {x['instrument']:<12} {x['eur']:>5.0f} EUR"
                f"  (mind. {x['min_order_eur']:.0f} EUR bei {x['venue']})"
            )

    if plan["ohne_konto"]:
        L.append("")
        L.append("WILL DIE REGEL, ABER KEIN KONTO:")
        for x in plan["ohne_konto"]:
            L.append(f"  {x['instrument']:<12} fehlt: {x['blocked_by']}")

    L.append("")
    L.append("WAS DU ERWARTEN DARFST")
    L.append(
        f"  ~{EXPECT['win_rate']:.0%} der Positionen gewinnen."
        f" Die meisten enden bei {EXPECT['typ_loss_pct']:+.1f} % - klein."
    )
    L.append(
        f"  Der Gewinn kommt aus wenigen langen Laeufern:"
        f" {EXPECT['profit_from_long_holds']:.0%} des Profits kamen historisch aus"
        f" Positionen ueber 3 Monate (Median {EXPECT['median_winner_days']} Tage)."
    )
    L.append(
        f"  Ein typischer Gewinner machte {EXPECT['typ_win_pct']:+.1f} %,"
        f" lag zwischendurch aber bis zu {EXPECT['worst_dip_pct']:.0f} % im Minus."
    )
    L.append("  Deshalb nicht bei rot verkaufen. Verkauft wird, wenn hier VERKAUFEN steht.")
    L.append("")
    L.append("GRENZEN")
    L.append(f"  -{plan['daily_loss_pct']:.0f} % an einem Tag  -> keine neuen Kaeufe bis morgen")
    L.append(f"  -{plan['max_dd_pct']:.0f} % vom Hoechststand -> Kill-Switch, alles raus")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--journal", default=JOURNAL)
    ap.add_argument("--risk", default=RISK)
    ap.add_argument("--send", action="store_true", help="zusaetzlich per Telegram schicken")
    ap.add_argument("--force", action="store_true", help="auch ohne Aenderung senden")
    args = ap.parse_args()

    _load_env_file()
    rows = _journal(args.journal)
    if not rows:
        print("Journal leer — erst scripts/tsmom_forward.py laufen lassen.")
        return 1

    risk = _risk_config(args.risk)
    latest = rows[-1]
    prev_row = None
    for r in reversed(rows[:-1]):
        if r.get("date") != latest.get("date"):
            prev_row = r
            break

    plan = build_plan(latest, risk)
    prev_plan = build_plan(prev_row, risk) if prev_row else None
    d = diff_plans(plan, prev_plan)

    text = render(plan, d)
    print(text)

    if not args.send:
        return 0

    changed = bool(d["buy"] or d["sell"] or d["adjust"]) or d["first_run"]
    if not changed and not args.force:
        print("\n(nichts geaendert — kein Telegram, kein Spam)")
        return 0

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from trading_agent.ops.notify import (
        FileSink,
        Notification,
        Notifier,
        Severity,
        TelegramSink,
    )

    tg = TelegramSink(min_severity=Severity.INFO)
    if not tg.available():
        print("\n! Telegram nicht konfiguriert (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID fehlt).")
        return 2

    n = Notifier([tg, FileSink("data/repository_real/live/alerts.jsonl")], max_per_window=20)
    sev = Severity.WARNING if (d["buy"] or d["sell"]) else Severity.INFO
    ok = n.notify(
        Notification(
            severity=sev,
            title=f"Tagesplan {plan['date']}",
            body=render(plan, d),
            dedup_key=f"daily-{plan['date']}",
            ts=datetime.now(UTC),
        )
    )
    print(f"\nTelegram: {'gesendet' if ok else 'unterdrueckt (dedup/rate-limit)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
