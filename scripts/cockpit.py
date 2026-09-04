#!/usr/bin/env python3
"""Das taegliche Cockpit — ein Befehl, eine klare Lage.

Verbindet, was bisher in fuenf Skripten verstreut lag: die eingefrorene TSMOM-Regel, die
harten Risikogrenzen aus ``config/risk.yaml``, den Kill-Switch und die echten Kontostaende.

Es HANDELT NICHT. Es sagt, was die Regel will, was die Grenzen davon uebrig lassen, und was
sich seit gestern geaendert hat. Ausgefuehrt wird von Hand — solange
``execution.mode: paper`` gilt, ohnehin gar nicht.

    python3 scripts/cockpit.py
    python3 scripts/cockpit.py --no-accounts    # ohne Kontoabfrage, nur Signale
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

from trading_agent.core.version import STRATEGY_VERSION
from trading_agent.governance.live_gate import evaluate_live_gate
from trading_agent.governance.validation import ValidationRegistry
from trading_agent.strategy.setups.tsmom import (
    SETUP_TSMOM_ENSEMBLE,
    TsmomParams,
    evaluate_tsmom,
)

sys.path.insert(0, str(Path(__file__).parent))
from tsmom_forward import UNIVERSE, _merged_history

CLASS_OF = {
    "BTCUSDT": "Krypto",
    "ETHUSDT": "Krypto",
    "BNBUSDT": "Krypto",
    "NVDA-YFD": "Aktien",
    "AAPL-YFD": "Aktien",
    "MSFT-YFD": "Aktien",
    "AMD-YFD": "Aktien",
    "GOOGL-YFD": "Aktien",
    "META-YFD": "Aktien",
    "EURUSD-YFD": "Waehrungen",
    "GBPUSD-YFD": "Waehrungen",
    "USDJPY-YFD": "Waehrungen",
    "XAUUSD-YFD": "Rohstoffe",
}
_BAR = "─" * 66


def _load_risk(path: str) -> dict:
    import yaml

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"{path} fehlt. Ohne Risikogrenzen laeuft dieses Cockpit nicht — "
            "das ist Absicht (siehe docs/TSMOM-MULTIASSET-ERGEBNIS-2026-09-04.md)."
        )
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def _load_env() -> None:
    p = Path(".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _accounts() -> dict[str, object]:
    """Depotstand ueber ``scripts/portfolio_hub.py``.

    Bewusst KEINE eigene Kontologik: die Aggregation ueber drei Boersen hatte bereits einen
    Fehler (Bybit-Spot-Bestaende liefen als Cash, 2026-09-03). Ein zweiter Nachbau haette
    denselben Fehler in einer zweiten Datei — deshalb wird der eine korrigierte Pfad benutzt.
    """
    import subprocess

    out: dict[str, object] = {"equity": 0.0, "positions": [], "cash_pct": None, "error": None}
    try:
        r = subprocess.run(
            [sys.executable, "scripts/portfolio_hub.py", "--json", "--alerts-journal", ""],
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "PYTHONPATH": "src"},
            check=False,
        )
        txt = r.stdout
        i = txt.find("{")
        if i < 0:
            out["error"] = (r.stderr or "keine Ausgabe").strip().splitlines()[-1][:80]
            return out
        d = json.loads(txt[i:])
        out["equity"] = float(d.get("equity") or 0.0)
        out["cash_pct"] = d.get("cash_pct")
        out["positions"] = d.get("ranking") or []
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="data/repository_real")
    ap.add_argument("--risk", default="config/risk.yaml")
    ap.add_argument("--journal", default="data/repository_real/live/tsmom_forward.jsonl")
    ap.add_argument("--no-accounts", action="store_true")
    args = ap.parse_args()

    _load_env()
    risk = _load_risk(args.risk)
    params = TsmomParams()
    reg = ValidationRegistry.from_file("config/setup_validation.json")
    gate = evaluate_live_gate(SETUP_TSMOM_ENSEMBLE, STRATEGY_VERSION, registry=reg)

    ex = risk.get("execution", {})
    pf = risk.get("portfolio", {})
    ll = risk.get("loss_limits", {})
    mode = str(ex.get("mode", "paper")).upper()

    print(_BAR)
    print(f"  COCKPIT  ·  {datetime.now(UTC).strftime('%d.%m.%Y %H:%M')} UTC")
    print(_BAR)
    print(f"  Modus:   {mode}" + ("   (es wird nichts ausgefuehrt)" if mode == "PAPER" else ""))
    print(f"  Regel:   {SETUP_TSMOM_ENSEMBLE} · {gate.eligibility.value.upper()}")
    print(
        f"  Grenzen: max {pf.get('max_total_exposure_pct')} % investiert · "
        f"max {pf.get('max_positions')} Positionen · min {pf.get('min_cash_pct')} % Cash"
    )
    print(
        f"  Notaus:  Tag −{ll.get('daily_loss_pct')} % · Woche −{ll.get('weekly_loss_pct')} % · "
        f"Drawdown −{ll.get('max_drawdown_pct')} %"
    )

    # ── Signale ──────────────────────────────────────────────────────────────
    signals = []
    for canon, (source, sym) in UNIVERSE.items():
        # Dieselbe Zusammenfuehrung wie im Forward-Lauf: sonst rechnet das Cockpit mit
        # veralteten Reihen und zeigt andere Gewichte als der Report, den Ozan bekommt.
        hist, _quelle = _merged_history(args.repo, canon, source, sym, mode="auto")
        if len(hist) < params.warmup_bars():
            continue
        closes = [c for _, c in hist]
        rep = evaluate_tsmom(closes, params=params)
        signals.append(
            {
                "instrument": canon,
                "cls": CLASS_OF.get(canon, "?"),
                "weight": rep.target_weight,
                "agreement": rep.agreement,
                "state": rep.state.value,
                "vol": rep.realized_vol,
            }
        )
    if not signals:
        print("\n  keine auswertbaren Instrumente — Daten pruefen")
        return 1

    # Vortag zum Vergleich
    prev: dict[str, float] = {}
    jp = Path(args.journal)
    if jp.exists():
        lines = [x for x in jp.read_text(encoding="utf-8").splitlines() if x.strip()]
        if len(lines) >= 2:
            with contextlib.suppress(Exception):
                for i in json.loads(lines[-2])["instruments"]:
                    prev[i["instrument"]] = i["target_weight"]

    print("\n  SIGNALE")
    print(f"  {'Instrument':<12}{'Klasse':<12}{'Zustand':<10}{'Gewicht':>9}{'gestern':>10}{'':>6}")
    print("  " + "─" * 62)
    for s in sorted(signals, key=lambda x: -x["weight"]):
        p = prev.get(s["instrument"])
        delta = ""
        if p is not None:
            d = s["weight"] - p
            delta = "  →" if abs(d) < 0.02 else ("  ▲" if d > 0 else "  ▼")
        prev_s = f"{p:.2f}" if p is not None else "—"
        print(
            f"  {s['instrument']:<12}{s['cls']:<12}{s['state']:<10}"
            f"{s['weight']:>9.2f}{prev_s:>10}{delta:>6}"
        )

    # ── Risikogrenzen anwenden ───────────────────────────────────────────────
    raw = statistics.fmean(s["weight"] for s in signals)
    cap_total = float(pf.get("max_total_exposure_pct", 60)) / 100.0
    scale = min(1.0, cap_total / raw) if raw > 0 else 0.0
    invested = raw * scale

    top = sorted(signals, key=lambda x: -x["weight"])
    max_pos = int(pf.get("max_positions", 8))
    chosen = [s for s in top if s["weight"] > 0][:max_pos]

    by_class: dict[str, float] = {}
    for s in chosen:
        by_class[s["cls"]] = by_class.get(s["cls"], 0.0) + s["weight"] * scale / len(signals)

    print("\n  NACH RISIKOGRENZEN")
    print(f"  Rohsignal:        {raw * 100:5.1f} % investiert")
    print(
        f"  Grenze:           {cap_total * 100:5.1f} %"
        + ("   (Signal wird gekappt)" if scale < 1 else "   (Signal passt)")
    )
    print(
        f"  Tatsaechlich:     {invested * 100:5.1f} % investiert · "
        f"{(1 - invested) * 100:.1f} % Cash"
    )
    print(f"  Positionen:       {len(chosen)} von max {max_pos}")
    cap_cls = float(pf.get("max_per_asset_class_pct", 35)) / 100.0
    for c, v in sorted(by_class.items(), key=lambda kv: -kv[1]):
        flag = "  ! ueber Klassengrenze" if v > cap_cls else ""
        print(f"    {c:<14}{v * 100:5.1f} %{flag}")

    # ── Abgleich mit dem echten Depot ────────────────────────────────────────
    if not args.no_accounts:
        acc = _accounts()
        if acc["error"]:
            print(f"\n  ! Depot nicht lesbar: {acc['error']}")
        else:
            eq = float(acc["equity"])  # type: ignore[arg-type]
            positions: list[dict] = acc["positions"]  # type: ignore[assignment]
            cash = acc["cash_pct"]
            print(
                f"\n  DEIN DEPOT  ·  {eq:.2f}"
                + (f"  ·  Cash {float(cash) * 100:.0f} %" if cash is not None else "")
            )
            in_universe = {c.replace("USDT", "").replace("-YFD", "") for c in UNIVERSE}
            fremd = [
                p
                for p in positions
                if str(p.get("instrument", "")).replace("USDT", "") not in in_universe
            ]
            if fremd:
                share = sum(float(p.get("weight_pct") or 0) for p in fremd)
                print(f"  {len(fremd)} von {len(positions)} Positionen liegen ausserhalb des")
                print(f"  Universums der Regel ({share:.0f} % des Depots) — dazu sagt sie nichts:")
                for p in sorted(fremd, key=lambda x: -float(x.get("weight_pct") or 0))[:6]:
                    print(
                        f"    {p.get('instrument', '')!s:<12}"
                        f"{float(p.get('weight_pct') or 0):>6.1f} %"
                    )
            if cash is not None and float(cash) * 100 < float(pf.get("min_cash_pct", 40)):
                print(
                    f"  ! Cash {float(cash) * 100:.0f} % unter der Grenze "
                    f"{pf.get('min_cash_pct')} % — Puffer fehlt"
                )

    # ── Was von einer Position zu erwarten ist ───────────────────────────────
    stats_path = Path("data/repository_real/research/tsmom_trade_stats.json")
    if stats_path.exists():
        with contextlib.suppress(Exception):
            st = json.loads(stats_path.read_text(encoding="utf-8"))
            ov = st.get("overall", {})
            bl = st.get("by_duration_bucket", {})
            lang = bl.get("ueber 3 Monate", {})
            print("\n  WAS VON EINER POSITION ZU ERWARTEN IST")
            print(
                f"  (aus {ov.get('n', 0):.0f} historischen Positionen — Statistik, keine Prognose)"
            )
            print(
                f"    Trefferquote:        {ov.get('win_rate', 0):.0f} %  "
                f"— rechne mit {100 - ov.get('win_rate', 0):.0f} % Verlustpositionen"
            )
            print(f"    typischer Verlust:   {ov.get('ret_mean_loss', 0):+.1f} %")
            print(f"    typischer Gewinn:    {ov.get('ret_mean_win', 0):+.1f} %")
            print(f"    tiefster Durchhaenger einer Position: {ov.get('worst_dip', 0):+.1f} %")
            if lang:
                print(
                    f"    Positionen ueber 3 Monate: {lang.get('n', 0):.0f} Stueck, "
                    f"{lang.get('win_rate', 0):.0f} % Trefferquote — sie tragen fast den"
                )
                print("    gesamten Gewinn. Frueh schliessen ist der teuerste Fehler.")
            print("    Ausstieg: wenn das Signal dreht, nicht bei einem Kursziel.")

    print("\n" + _BAR)
    if mode == "PAPER":
        print("  PAPER — nichts wird ausgefuehrt. Das Journal zeichnet auf, was die Regel wollte.")
    else:
        print("  LIVE — jede Order wird einzeln bestaetigt.")
    print("  Aenderungen an der Regel setzen die Forward-Zaehlung zurueck.")
    print(_BAR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
