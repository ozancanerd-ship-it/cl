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
    assert set(plan["dropped"])  # der Rest wird als abgeschnitten ausgewiesen


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


def test_position_below_fee_threshold_is_dropped_and_budget_redistributed() -> None:
    """Eine 20-EUR-Aktienposition kostet 10 % Gebuehr. Die darf nicht im Plan stehen."""
    weights = dict.fromkeys(AKTIEN, 1.0)  # 6 Aktien, 240 EUR Budget -> je 40 EUR
    plan = dr.build_plan(_entry(weights, portfolio_weight=0.6), RISK)
    assert plan["zu_klein"], "zu kleine Aktienpositionen muessen ausgewiesen werden"
    for x in plan["positions"]:
        assert x["eur"] >= 60.0 - 1e-6  # was uebrig bleibt, ist gross genug
    # das Budget geht nicht verloren, es wird auf die Ueberlebenden verteilt
    assert plan["invested_eur"] == 0.6 * RISK["equity"]


def test_crypto_has_no_minimum_problem() -> None:
    weights = dict.fromkeys(CRYPTO, 1.0)
    plan = dr.build_plan(_entry(weights, portfolio_weight=0.6), RISK)
    assert plan["zu_klein"] == []
    assert len(plan["positions"]) == 3


def test_cost_pct_reflects_the_venue() -> None:
    plan = dr.build_plan(_entry({"BTCUSDT": 1.0, "NVDA-YFD": 1.0}, portfolio_weight=0.6), RISK)
    cost = {x["instrument"]: x["cost_pct"] for x in plan["positions"]}
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
    cfg = dr._risk_config(str(Path(__file__).resolve().parents[2] / "config" / "risk.yaml"))
    assert cfg["equity"] == 400.0
    assert cfg["max_positions"] == 8
    assert cfg["max_exposure_pct"] == 60.0
    assert cfg["min_cash_pct"] == 40.0
