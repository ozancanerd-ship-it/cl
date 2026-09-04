"""scripts/daily_report — Uebersetzung Journalzeile -> handelbarer Euro-Plan.

Warum getestet: dieser Report ist das, was Ozan tatsaechlich liest und danach handelt.
Ein Rechenfehler hier ist kein Schoenheitsfehler, sondern eine falsche Order. Geprueft
werden deshalb genau die Stellen, an denen Geld entsteht: Deckelung durch risk.yaml,
Umrechnung in Euro, und der Vortagsvergleich, aus dem KAUFEN/VERKAUFEN entsteht.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "daily_report.py"
_spec = importlib.util.spec_from_file_location("daily_report", _PATH)
assert _spec and _spec.loader
dr = importlib.util.module_from_spec(_spec)
sys.modules["daily_report"] = dr
_spec.loader.exec_module(dr)

RISK = {
    "equity": 400.0,
    "max_positions": 8,
    "max_exposure_pct": 60.0,
    "min_cash_pct": 40.0,
    "daily_loss_pct": 2.0,
    "max_dd_pct": 10.0,
}


def _entry(weights: dict[str, float], *, portfolio_weight: float = 0.5) -> dict:
    return {
        "date": "2026-09-04",
        "eligibility": "shadow",
        "portfolio_weight": portfolio_weight,
        "n_long": sum(1 for w in weights.values() if w > 0),
        "n_instruments": len(weights),
        "instruments": [
            {
                "instrument": k,
                "close": 100.0,
                "target_weight": w,
                "agreement": 1.0,
                "state": "full" if w > 0 else "flat",
                "realized_vol": 0.4,
            }
            for k, w in weights.items()
        ],
    }


CRYPTO = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
AKTIEN = ["NVDA-YFD", "AAPL-YFD", "MSFT-YFD", "AMD-YFD", "GOOGL-YFD", "META-YFD"]


def test_exposure_never_exceeds_risk_config() -> None:
    """Auch wenn die Regel lauter starke Longs will: mehr als 60 % darf nie investiert sein."""
    weights = dict.fromkeys(CRYPTO + AKTIEN, 1.0)
    plan = dr.build_plan(_entry(weights, portfolio_weight=1.0), RISK)
    assert plan["invested_eur"] <= 0.60 * RISK["equity"] + 1e-6
    assert plan["cash_eur"] >= 0.40 * RISK["equity"] - 1e-6


def test_max_positions_truncates() -> None:
    weights = {s: 1.0 - i * 0.01 for i, s in enumerate(CRYPTO + AKTIEN + ["XAUUSD-YFD"])}
    risk = {**RISK, "max_positions": 3}
    plan = dr.build_plan(_entry(weights, portfolio_weight=0.6), risk)
    assert len(plan["positions"]) <= 3


def test_selection_spreads_across_asset_classes() -> None:
    """Der eigentliche Punkt: sechs Aktien duerfen Krypto und Gold nicht verdraengen.

    Krypto allein war OOS Sharpe -0.21, gemischt 1.08. Ein Auswahlverfahren, das bei
    knappem Geld in einer Klasse landet, baut die schlechtere Variante nach.
    """
    # Aktien haben die hoechsten Gewichte — nach reiner Gewichtssortierung waeren die
    # ersten vier Plaetze alle Aktien.
    weights = {s: 0.9 - i * 0.01 for i, s in enumerate(AKTIEN)}
    weights |= {"BTCUSDT": 0.5, "XAUUSD-YFD": 0.45}
    plan = dr.build_plan(_entry(weights, portfolio_weight=0.6), RISK)
    klassen = {dr.ASSET_CLASS[x["instrument"]] for x in plan["positions"]}
    assert len(klassen) >= 3, f"nur {klassen} — die Mischung ist verloren"


def test_round_robin_alternates_classes() -> None:
    kand = [
        {"instrument": "NVDA-YFD", "target_weight": 0.9},
        {"instrument": "AAPL-YFD", "target_weight": 0.85},
        {"instrument": "MSFT-YFD", "target_weight": 0.8},
        {"instrument": "BTCUSDT", "target_weight": 0.7},
        {"instrument": "XAUUSD-YFD", "target_weight": 0.6},
    ]
    order = [x["instrument"] for x in dr._round_robin_klassen(kand)]
    assert order[:3] == ["NVDA-YFD", "BTCUSDT", "XAUUSD-YFD"]
    assert set(order) == {x["instrument"] for x in kand}  # nichts geht verloren


def test_budget_follows_conviction_not_just_the_cap() -> None:
    """Wenige/schwache Longs -> kleineres Budget. Sonst waere der Deckel das einzige Signal."""
    weights = {"BTCUSDT": 0.2, "ETHUSDT": 0.1}
    schwach = dr.build_plan(_entry(weights, portfolio_weight=0.10), RISK)
    stark = dr.build_plan(_entry(weights, portfolio_weight=0.55), RISK)
    assert schwach["invested_eur"] < stark["invested_eur"]
    assert schwach["invested_eur"] == 0.10 * RISK["equity"]


def test_flat_instruments_are_not_bought() -> None:
    plan = dr.build_plan(_entry({"BTCUSDT": 0.5, "ETHUSDT": 0.0, "BNBUSDT": 0.0}), RISK)
    assert [x["instrument"] for x in plan["positions"]] == ["BTCUSDT"]


def test_instruments_without_account_get_no_budget() -> None:
    """FX ohne Broker darf kein Geld binden - aber es muss sichtbar bleiben."""
    plan = dr.build_plan(_entry({"BTCUSDT": 0.5, "EURUSD-YFD": 0.5}), RISK)
    assert [x["instrument"] for x in plan["positions"]] == ["BTCUSDT"]
    assert [x["instrument"] for x in plan["ohne_konto"]] == ["EURUSD-YFD"]
    assert plan["ohne_konto"][0]["blocked_by"] == "FX-Broker"


def test_fee_floor_limits_the_number_of_positions() -> None:
    """Eine 20-EUR-Aktienposition kostet 10 % Gebuehr rein und raus. Lieber vier grosse."""
    weights = dict.fromkeys(AKTIEN, 1.0)  # 6 Aktien, 240 EUR Budget -> je 40 EUR waere zu wenig
    plan = dr.build_plan(_entry(weights, portfolio_weight=0.6), RISK)
    assert plan["zu_klein"], "was nicht reinpasst, muss benannt werden"
    for x in plan["positions"]:
        assert x["eur"] >= 60.0 - 1e-6
    assert len(plan["positions"]) == 4  # 240 / 60
    # das Budget geht nicht verloren, es verteilt sich auf die Ueberlebenden
    assert plan["invested_eur"] == 0.6 * RISK["equity"]


def test_tiny_account_proposes_nothing_rather_than_a_fee_trap() -> None:
    """Bei 50 EUR Equity ist keine Aktienposition sinnvoll — dann eben keine."""
    risk = {**RISK, "equity": 50.0}
    plan = dr.build_plan(_entry(dict.fromkeys(AKTIEN, 1.0), portfolio_weight=0.6), risk)
    assert plan["positions"] == []
    assert plan["cash_eur"] == 50.0


def test_crypto_has_no_minimum_problem() -> None:
    weights = dict.fromkeys(CRYPTO, 1.0)
    plan = dr.build_plan(_entry(weights, portfolio_weight=0.6), RISK)
    assert plan["zu_klein"] == []
    assert len(plan["positions"]) == 3


def test_cost_pct_reflects_the_venue() -> None:
    plan = dr.build_plan(_entry({"BTCUSDT": 1.0, "NVDA-YFD": 1.0}, portfolio_weight=0.6), RISK)
    cost = {x["instrument"]: x["cost_pct"] for x in plan["positions"]}
    assert len(cost) == 2
    assert cost["BTCUSDT"] < 1.0  # 0.2 % rein + raus
    assert cost["NVDA-YFD"] > cost["BTCUSDT"]  # 1 EUR pauschal wiegt bei 120 EUR schwerer


def test_diff_first_run_is_all_buy() -> None:
    plan = dr.build_plan(_entry({"BTCUSDT": 0.5}), RISK)
    d = dr.diff_plans(plan, None)
    assert d["first_run"] is True
    assert [x["instrument"] for x in d["buy"]] == ["BTCUSDT"]
    assert d["sell"] == []


def test_diff_detects_exit() -> None:
    gestern = dr.build_plan(_entry({"BTCUSDT": 0.5, "ETHUSDT": 0.5}), RISK)
    heute = dr.build_plan(_entry({"BTCUSDT": 0.5, "ETHUSDT": 0.0}), RISK)
    d = dr.diff_plans(heute, gestern)
    assert [x["instrument"] for x in d["sell"]] == ["ETHUSDT"]
    assert d["buy"] == []


def test_diff_ignores_noise_but_sees_real_shifts() -> None:
    """Ein Gewicht, das um 2 % wackelt, ist keine Nachricht wert. Eine Verdopplung schon."""
    gestern = dr.build_plan(_entry({"BTCUSDT": 0.50, "ETHUSDT": 0.50}), RISK)
    fast_gleich = dr.build_plan(_entry({"BTCUSDT": 0.51, "ETHUSDT": 0.49}), RISK)
    assert dr.diff_plans(fast_gleich, gestern)["adjust"] == []

    verschoben = dr.build_plan(_entry({"BTCUSDT": 0.90, "ETHUSDT": 0.10}), RISK)
    assert {x["instrument"] for x in dr.diff_plans(verschoben, gestern)["adjust"]} == {
        "BTCUSDT",
        "ETHUSDT",
    }


def test_render_is_plain_text_and_mentions_the_decision() -> None:
    plan = dr.build_plan(_entry({"BTCUSDT": 0.5, "EURUSD-YFD": 0.5}), RISK)
    text = dr.render(plan, dr.diff_plans(plan, None))
    assert "TAGESPLAN" in text
    assert "KAUFEN" in text
    assert "FX-Broker" in text  # die gesperrte Zeile wird benannt, nicht verschwiegen
    assert "verteilt auf" in text
    assert "Kill-Switch" in text
    assert len(text) < 4096  # Telegram-Limit fuer eine Nachricht


def test_render_survives_a_completely_flat_day() -> None:
    """Kein Long, kein Budget: der Report darf nicht durch Division durch Null sterben."""
    plan = dr.build_plan(_entry({"BTCUSDT": 0.0, "ETHUSDT": 0.0}, portfolio_weight=0.0), RISK)
    text = dr.render(plan, dr.diff_plans(plan, None))
    assert plan["positions"] == []
    assert plan["cash_eur"] == RISK["equity"]
    assert "TAGESPLAN" in text


def test_risk_config_parser_reads_the_real_file() -> None:
    """Gegen die echte Datei pruefen, nicht gegen eingetippte Zahlen.

    Die Equity aendert sich, wenn Ozan Geld bewegt — ein fester Wert im Test wuerde nur
    bedeuten, dass jemand ihn nachtraegt. Geprueft wird, dass der Parser die Datei
    wirklich liest und die Grenzen zueinander passen.
    """
    pfad = Path(__file__).resolve().parents[2] / "config" / "risk.yaml"
    cfg = dr._risk_config(str(pfad))
    roh = pfad.read_text(encoding="utf-8")

    assert f"starting_equity: {cfg['equity']:.0f}" in roh
    assert cfg["equity"] > 0
    assert cfg["max_positions"] == 8
    assert cfg["max_exposure_pct"] == 60.0
    assert cfg["min_cash_pct"] == 40.0
    # Zusammen duerfen investiert + Mindest-Cash nie ueber 100 % liegen.
    assert cfg["max_exposure_pct"] + cfg["min_cash_pct"] <= 100.0


# ── Abgleich mit dem echten Depot ────────────────────────────────────────────────
# Warum getestet: ohne diesen Abgleich empfahl der Bericht am 2026-09-04, NVIDIA zu
# kaufen — waehrend NVIDIA bereits ein Viertel von Ozans Vermoegen war. Der Fehler war
# nicht die Rechnung, sondern dass der Plan den Bestand gar nicht kannte.


def _snapshot(equity: float, gewichte: dict[str, float]) -> dict:
    return {
        "as_of": "2026-09-04T20:00:00+00:00",
        "equity": equity,
        "ranking": [{"instrument": k, "weight_pct": v * 100.0} for k, v in gewichte.items()],
    }


def test_abgleich_erkennt_uebergewicht() -> None:
    plan = dr.build_plan(_entry({"NVDA-YFD": 1.0, "BTCUSDT": 1.0}, portfolio_weight=0.6), RISK)
    a = dr.abgleich(plan, _snapshot(400.0, {"NVDA": 0.60}))
    nvda = next(z for z in a["zeilen"] if z["instrument"] == "NVDA-YFD")
    assert nvda["ist_eur"] == pytest.approx(240.0)
    assert nvda["delta_eur"] < 0, "zu viel NVIDIA muss als Reduzieren erscheinen"


def test_abgleich_zaehlt_dasselbe_asset_ueber_konten_zusammen() -> None:
    """Bitcoin liegt je nach Konto als BTCUSDT oder BTC-EUR — das ist eine Position."""
    plan = dr.build_plan(_entry({"BTCUSDT": 1.0}, portfolio_weight=0.6), RISK)
    a = dr.abgleich(plan, _snapshot(400.0, {"BTCUSDT": 0.10, "BTC-EUR": 0.15}))
    btc = next(z for z in a["zeilen"] if z["instrument"] == "BTCUSDT")
    assert btc["ist_eur"] == pytest.approx(100.0)


def test_abgleich_weist_alles_ausserhalb_der_regel_aus() -> None:
    plan = dr.build_plan(_entry({"BTCUSDT": 1.0}, portfolio_weight=0.6), RISK)
    a = dr.abgleich(plan, _snapshot(1000.0, {"BTCUSDT": 0.10, "SEIUSDT": 0.30, "PLTR": 0.05}))
    namen = [k for k, _ in a["draussen"]]
    assert namen == ["SEIUSDT", "PLTR"]  # nach Groesse sortiert
    assert a["draussen_eur"] == pytest.approx(350.0)
    assert "BTCUSDT" not in namen  # gedeckte Positionen gehoeren nicht dorthin


def test_jedes_plan_instrument_hat_eine_deckungsregel() -> None:
    """Ein fehlender Eintrag hiesse: Bestand wird stillschweigend als 0 gelesen."""
    for inst in dr.INSTRUMENT_VENUE:
        assert inst in dr.DECKUNG, f"{inst} fehlt in DECKUNG"


def test_render_abgleich_nennt_beide_richtungen() -> None:
    plan = dr.build_plan(_entry({"NVDA-YFD": 1.0, "BTCUSDT": 1.0}, portfolio_weight=0.6), RISK)
    txt = dr.render_abgleich(dr.abgleich(plan, _snapshot(1000.0, {"NVDA": 0.50, "SEIUSDT": 0.20})))
    assert "REDUZIEREN" in txt
    assert "KAUFEN" in txt
    assert "AUSSERHALB DER REGEL" in txt
