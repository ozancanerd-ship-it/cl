#!/usr/bin/env python3
"""Baut die Web-App aus dem Forward-Journal — dieselbe Rechnung wie der Telegram-Report.

Warum es das gibt: der Telegram-Text ist gut zum Handeln, aber schlecht zum Nachschauen.
Diese Seite ist das Nachschlagewerk — Plan, Signale, Erwartung, Grenzen — und sie wird von
``.github/workflows/daily.yml`` jeden Tag neu erzeugt und auf GitHub Pages veroeffentlicht.

Die Vorlage liegt in ``site/``, das Ergebnis in ``_site/``. Getrennt, weil GitHub Pages
den ganzen Ausgabeordner ausliefert — die Vorlage mit ihrem ``__DATA__``-Platzhalter
hat dort nichts zu suchen.

Wichtig: sie zieht sich ihre Zahlen NICHT selbst. Sie ist statisch und traegt ihr Baudatum
sichtbar im Fuss. Steht dort ein altes Datum, ist der taegliche Job nicht gelaufen — das
ist die Anzeige dafuer, dass etwas kaputt ist, und genau so soll es sein. Eine Seite, die
sich selbst live nachlaedt, wuerde einen Ausfall verbergen.

    python3 scripts/build_site.py --out _site
"""

from __future__ import annotations

import argparse
import contextlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

# Lesbare Namen. Der Instrumentenschluessel ist fuer die Maschine, nicht fuer Ozan.
NAMES: dict[str, str] = {
    "BTCUSDT": "Bitcoin",
    "ETHUSDT": "Ethereum",
    "BNBUSDT": "BNB",
    "NVDA-YFD": "NVIDIA",
    "AAPL-YFD": "Apple",
    "MSFT-YFD": "Microsoft",
    "AMD-YFD": "AMD",
    "GOOGL-YFD": "Alphabet",
    "META-YFD": "Meta",
    "EURUSD-YFD": "EUR/USD",
    "GBPUSD-YFD": "GBP/USD",
    "USDJPY-YFD": "USD/JPY",
    "XAUUSD-YFD": "Gold",
}
KLASSE: dict[str, str] = {
    "BTCUSDT": "Krypto",
    "ETHUSDT": "Krypto",
    "BNBUSDT": "Krypto",
    "NVDA-YFD": "Aktien",
    "AAPL-YFD": "Aktien",
    "MSFT-YFD": "Aktien",
    "AMD-YFD": "Aktien",
    "GOOGL-YFD": "Aktien",
    "META-YFD": "Aktien",
    "EURUSD-YFD": "W\u00e4hrungen",
    "GBPUSD-YFD": "W\u00e4hrungen",
    "USDJPY-YFD": "W\u00e4hrungen",
    "XAUUSD-YFD": "Rohstoffe",
}


# Aus scripts/tsmom_trade_stats.py auf dem Multi-Asset-Panel, Stand 2026-09-04, 398
# Positionen. Bewusst eingefroren: das beschreibt die Vergangenheit und aendert sich nicht
# taeglich. Neu berechnen heisst, das Skript mit dem vollen Parquet-Repo laufen zu lassen —
# in der CI liegen diese 271 MB nicht, und sie sollen dort auch nicht liegen.
def _alarm_verlauf(repo: Path, grenze: int = 25) -> list[dict]:
    """Die zuletzt tatsaechlich verschickten Alarme.

    Nicht "was koennte man melden", sondern was rausgegangen ist. Damit steht auf der
    Seite dasselbe wie im Telegram-Verlauf und man sieht, ob das System still war, weil
    nichts los war — oder weil es kaputt ist.
    """
    f = repo / "data" / "repository_real" / "live" / "alerts.jsonl"
    if not f.exists():
        return []
    zeilen: list[dict] = []
    for zeile in f.read_text(encoding="utf-8").splitlines():
        zeile = zeile.strip()
        if not zeile:
            continue
        try:
            eintrag = json.loads(zeile)
        except json.JSONDecodeError:
            continue
        if isinstance(eintrag, dict):
            zeilen.append(eintrag)
    return zeilen[-grenze:][::-1]


def _kopiere_scan(repo: Path, out: Path) -> dict[str, int]:
    """``web/scan.json`` und ``web/asset/*.json`` in die Ausgabe legen.

    Fehlt der Scan, wird bewusst NICHTS kopiert und auch nichts Altes stehengelassen:
    die App zeigt dann „kein Scan vorhanden" statt einer Rangliste von gestern. Ein
    veralteter Scan ist gefaehrlicher als gar keiner — er sieht richtig aus.
    """
    import shutil

    quelle = repo / "web"
    stat = {"scan": 0, "wachliste": 0, "assets": 0}
    for name, schluessel in (("scan.json", "scan"), ("watchlist.json", "wachliste")):
        f = quelle / name
        if f.exists():
            shutil.copy2(f, out / name)
            stat[schluessel] = 1
    ordner = quelle / "asset"
    if ordner.is_dir():
        ziel = out / "asset"
        ziel.mkdir(parents=True, exist_ok=True)
        for datei in sorted(ordner.glob("*.json")):
            shutil.copy2(datei, ziel / datei.name)
            stat["assets"] += 1
    return stat


def _plan_json(repo: Path) -> dict:
    """daily_report.py als Unterprozess — eine Quelle fuer die Zahlen, nicht zwei."""
    out = subprocess.run(
        [sys.executable, str(repo / "scripts" / "daily_report.py"), "--json"],
        capture_output=True,
        text=True,
        cwd=repo,
        check=True,
    )
    return json.loads(out.stdout)


def _rules(repo: Path) -> dict[str, float]:
    txt = (repo / "config" / "risk.yaml").read_text(encoding="utf-8")
    want = {
        "daily_loss_pct": 2.0,
        "weekly_loss_pct": 5.0,
        "max_drawdown_pct": 10.0,
        "max_total_exposure_pct": 60.0,
        "min_cash_pct": 40.0,
        "max_positions": 8.0,
    }
    out = dict(want)
    for line in txt.splitlines():
        s = line.split("#", 1)[0].strip()
        if ":" not in s:
            continue
        k, v = (x.strip() for x in s.split(":", 1))
        if k in want and v:
            with contextlib.suppress(ValueError):
                out[k] = float(v)
    return {
        "daily_loss_pct": out["daily_loss_pct"],
        "weekly_loss_pct": out["weekly_loss_pct"],
        "max_dd_pct": out["max_drawdown_pct"],
        "max_exposure_pct": out["max_total_exposure_pct"],
        "min_cash_pct": out["min_cash_pct"],
        "max_positions": int(out["max_positions"]),
    }


def _icon_png() -> bytes:
    """Ein 512er-Quadrat in Akzentgruen — reicht als Homescreen-Symbol, keine Abhaengigkeit.

    Handgeschriebenes PNG statt Pillow: die CI soll fuer ein Icon kein Bildpaket ziehen.
    """
    import struct
    import zlib

    size, rgb = 512, (29, 92, 87)
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="_site")
    args = ap.parse_args()

    repo = Path(__file__).resolve().parents[1]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    d = _plan_json(repo)
    plan = d["plan"]
    # Der Gesamtmarkt-Scan wird NICHT mehr eingebettet, sondern zur Laufzeit geholt.
    # Mit dynamischem Universum sind das schnell mehrere hundert Kilobyte plus je Wert
    # eine Detaildatei mit Kerzen — eingebettet muesste die App das alles laden, bevor
    # sie die erste Zeile zeigt. Getrennt laedt sie die Rangliste sofort und den Rest
    # erst beim Antippen. Nebenwirkung, die wir wollen: die Seite zieht sich den neuen
    # Stundenscan selbst, ohne dass die Seite neu gebaut werden muss.
    scan_kopiert = _kopiere_scan(repo, out)
    payload = {
        "plan": plan,
        "diff": d["diff"],
        "first_run": d["first_run"],
        "alarme": _alarm_verlauf(repo),
        "rules": _rules(repo),
        "names": NAMES,
        "klasse": KLASSE,
        "journal_days": d["journal_days"],
        "scan_dateien": scan_kopiert,
        "setup": "TSMOM-Ensemble",
        "params_version": (
            d["signals"][0].get("params_version", "tsmom-ensemble-1")
            if d.get("signals")
            else "tsmom-ensemble-1"
        ),
        "subtitle": (
            f"Time-Series-Momentum \u00fcber {plan['n_total']} Instrumente in vier "
            "Anlageklassen. Nichts wird automatisch ausgef\u00fchrt."
        ),
        "built_at": datetime.now(UTC).strftime("%d.%m.%Y %H:%M UTC"),
    }

    tpl = (repo / "site" / "template.html").read_text(encoding="utf-8")
    # In ein <script type="application/json"> darf kein "</script>" geraten.
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    (out / "index.html").write_text(tpl.replace("__DATA__", blob), encoding="utf-8")

    (out / "manifest.webmanifest").write_text(
        json.dumps(
            {
                "name": "Trading-Signale",
                "short_name": "Signale",
                "start_url": ".",
                "display": "standalone",
                "background_color": "#F3F5F4",
                "theme_color": "#1D5C57",
                "icons": [{"src": "icon.png", "sizes": "512x512", "type": "image/png"}],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (out / "icon.png").write_bytes(_icon_png())
    (out / ".nojekyll").write_text("", encoding="utf-8")

    kb = (out / "index.html").stat().st_size / 1024
    print(f"{out / 'index.html'}  ({kb:.0f} KB, Stand {plan['date']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
