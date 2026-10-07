#!/usr/bin/env python3
"""Notenbank-Sitzungen holen: Fed (Beschlusstext), EZB und Bank of England (Mitteilungen).

    python3 scripts/fetch_notenbanken.py --out web/notenbanken.json

Oeffentliche Feeds, kein Schluessel. Faellt eine Quelle aus, wird sie vermerkt und der Rest
geschrieben — lieber weniger als erfunden."""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from trading_agent.analysis import notenbanken as nb

FEEDS = {
    "fed": "https://www.federalreserve.gov/feeds/press_monetary.xml",
    "ezb": "https://www.ecb.europa.eu/rss/press.html",
    "boe": "https://www.bankofengland.co.uk/rss/news",
}
UA = {"User-Agent": "Mozilla/5.0 (trading-desk notenbanken)"}


def holen(url: str) -> str:
    import httpx

    r = httpx.get(url, timeout=25.0, headers=UA, follow_redirects=True)
    r.raise_for_status()
    return r.text


def feed(text: str) -> list[dict]:
    wurzel = ET.fromstring(text.lstrip("﻿"))
    out = []
    for it in wurzel.iter("item"):
        titel = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        roh = (it.findtext("pubDate") or "").strip()
        try:
            ts = parsedate_to_datetime(roh).astimezone(UTC).isoformat()
        except (TypeError, ValueError):
            ts = None
        if titel and ts:
            out.append({"titel": re.sub(r"\s+", " ", titel), "link": link, "zeit": ts})
    out.sort(key=lambda x: x["zeit"], reverse=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="web/notenbanken.json")
    a = ap.parse_args()
    res: dict = {"erzeugt": datetime.now(UTC).isoformat(), "fehler": []}
    items: dict[str, list[dict]] = {}
    for k, u in FEEDS.items():
        try:
            items[k] = feed(holen(u))
        except Exception as exc:  # eine Quelle darf ausfallen
            items[k] = []
            res["fehler"].append(f"{k}: {type(exc).__name__}")
    # Fed: die letzten Beschlusstexte lesen (Statement = Zinsentscheid)
    entscheide = []
    for it in items["fed"]:
        if "FOMC statement" in it["titel"] and len(entscheide) < 4:
            try:
                txt = nb.fed_statement(holen(it["link"]))
            except Exception as exc:
                res["fehler"].append(f"fed-text: {type(exc).__name__}")
                continue
            e = nb.fed_entscheid(txt)
            e.update(zeit=it["zeit"], link=it["link"], satz=nb.fed_satz(e), text=txt[:900])
            if e["aktion"] != "unbekannt":
                entscheide.append(e)
    res["fed"] = {
        "entscheide": entscheide,
        "ton": nb.ton_von(entscheide[0]["aktion"]) if entscheide else "neutral",
        "mitteilungen": nb.relevante(items["fed"], 6),
    }
    res["einordnung"] = nb.einordnung(res["fed"]["ton"])
    res["ezb"] = {"mitteilungen": nb.relevante(items["ezb"], 6)}
    res["boe"] = {
        "mitteilungen": [
            i for i in items["boe"] if re.search(r"bank rate|monetary policy|\bMPC\b", i["titel"], re.I)
        ][:4]
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Fed-Entscheide: {len(entscheide)} · EZB {len(res['ezb']['mitteilungen'])} · BoE {len(res['boe']['mitteilungen'])} · Fehler {res['fehler']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
