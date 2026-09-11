"""data/providers/sec_edgar — Fundamentaldaten aus den Original-Einreichungen.

Warum getestet: hier wird aus Rohzahlen gerechnet, und jede der Rechnungen hat eine
Art, still falsch zu sein.

* **Look-ahead.** Der Quartalsbericht existiert nicht am Quartalsende, sondern Wochen
  später. Wer ihn vorher benutzt, baut einen Backtest, der in die Zukunft sieht — und
  merkt es nie, weil das Ergebnis besser aussieht statt schlechter.
* **Vermischte Perioden.** Ein Jahreswert im Zähler und ein Quartalswert im Nenner
  ergeben eine Marge, die formal richtig gerechnet und trotzdem Unsinn ist.
* **Hochrechnen.** Aus zwei Quartalen einen Jahresumsatz zu verdoppeln liefert eine
  Zahl mit Nachkommastelle, die nichts bedeutet.

Alle Antworten hier sind gebaut. Es geht nicht um EDGAR, sondern um unsere Auswertung.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from trading_agent.data.providers.sec_edgar import (
    ERLAUBTE_FORMULARE,
    _letzte_vier_quartale,
    _neueste,
    _quartal_vorjahr,
    _symbol_kern,
    kennzahlen_aus_facts,
)


def _fakt(
    val: float, start: str | None, end: str, filed: str, form: str = "10-Q"
) -> dict[str, Any]:
    d = {"val": val, "end": end, "filed": filed, "form": form, "accn": "x"}
    if start:
        d["start"] = start
    return d


def _facts(**etiketten: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """``_facts(Revenues={"USD": [...]})`` → die Struktur, die companyfacts liefert."""
    return {"facts": {"us-gaap": {k: {"units": v} for k, v in etiketten.items()}}}


#: Vier saubere Quartale Umsatz, jeweils rund sechs Wochen nach Quartalsende eingereicht.
_VIER_QUARTALE = [
    _fakt(100.0, "2025-01-01", "2025-03-31", "2025-05-10"),
    _fakt(110.0, "2025-04-01", "2025-06-30", "2025-08-08"),
    _fakt(120.0, "2025-07-01", "2025-09-30", "2025-11-07"),
    _fakt(140.0, "2025-10-01", "2025-12-31", "2026-02-06"),
]


# ── Punkt-in-der-Zeit ────────────────────────────────────────────────────────────
def test_noch_nicht_eingereichte_zahl_ist_unsichtbar() -> None:
    """Am 1. Januar gibt es den Bericht fuer das vierte Quartal noch nicht."""
    f = _facts(Revenues={"USD": _VIER_QUARTALE})
    # Stichtag vor der letzten Einreichung: nur drei Quartale sind sichtbar,
    # also kommt keine TTM-Summe zustande.
    assert _letzte_vier_quartale(f, ("Revenues",), "USD", datetime(2026, 1, 1).date()) is None
    # Einen Tag nach der Einreichung schon.
    summe = _letzte_vier_quartale(f, ("Revenues",), "USD", datetime(2026, 2, 7).date())
    assert summe is not None and summe[0] == 470.0


def test_neueste_nimmt_nur_sichtbare_werte() -> None:
    f = _facts(
        StockholdersEquity={
            "USD": [
                _fakt(1000.0, None, "2025-09-30", "2025-11-07"),
                _fakt(1200.0, None, "2025-12-31", "2026-02-06"),
            ]
        }
    )
    alt = _neueste(f, ("StockholdersEquity",), "USD", datetime(2025, 12, 31).date())
    assert alt is not None and alt.wert == 1000.0
    neu = _neueste(f, ("StockholdersEquity",), "USD", datetime(2026, 3, 1).date())
    assert neu is not None and neu.wert == 1200.0


def test_korrektur_gewinnt_gegen_erstfassung() -> None:
    """Dieselbe Periode, spaeter berichtigt — die Korrektur zaehlt."""
    f = _facts(
        Revenues={
            "USD": [
                _fakt(100.0, "2025-01-01", "2025-03-31", "2025-05-10"),
                _fakt(95.0, "2025-01-01", "2025-03-31", "2025-06-20", form="10-Q/A"),
            ]
        }
    )
    t = _neueste(f, ("Revenues",), "USD", datetime(2026, 1, 1).date(), art="quartal")
    assert t is not None and t.wert == 95.0


# ── Perioden nicht vermischen ───────────────────────────────────────────────────
def test_jahreswert_zaehlt_nicht_als_quartal() -> None:
    f = _facts(
        Revenues={
            "USD": [
                _fakt(470.0, "2025-01-01", "2025-12-31", "2026-02-06", form="10-K"),
                *_VIER_QUARTALE,
            ]
        }
    )
    t = _neueste(f, ("Revenues",), "USD", datetime(2026, 3, 1).date(), art="quartal")
    assert t is not None and t.wert == 140.0, "der 10-K-Jahreswert darf nicht durchrutschen"


def test_drei_quartale_werden_nicht_hochgerechnet() -> None:
    f = _facts(Revenues={"USD": _VIER_QUARTALE[:3]})
    assert _letzte_vier_quartale(f, ("Revenues",), "USD", datetime(2026, 3, 1).date()) is None


def test_luecke_zwischen_quartalen_wird_nicht_ueberbrueckt() -> None:
    """Vier Quartale, die sich ueber zwei Jahre verteilen, sind keine TTM-Summe."""
    f = _facts(
        Revenues={
            "USD": [
                _fakt(90.0, "2023-01-01", "2023-03-31", "2023-05-10"),
                _fakt(100.0, "2025-01-01", "2025-03-31", "2025-05-10"),
                _fakt(110.0, "2025-04-01", "2025-06-30", "2025-08-08"),
                _fakt(120.0, "2025-07-01", "2025-09-30", "2025-11-07"),
            ]
        }
    )
    assert _letzte_vier_quartale(f, ("Revenues",), "USD", datetime(2026, 3, 1).date()) is None


def test_nur_geprüfte_formulare() -> None:
    """8-K enthaelt oft dieselbe Zahl — aber in wechselnder Abgrenzung."""
    assert "8-K" not in ERLAUBTE_FORMULARE
    f = _facts(
        Revenues={"USD": [_fakt(999.0, "2025-10-01", "2025-12-31", "2026-01-20", form="8-K")]}
    )
    assert _neueste(f, ("Revenues",), "USD", datetime(2026, 3, 1).date(), art="quartal") is None


# ── Wachstum ────────────────────────────────────────────────────────────────────
def test_wachstum_vergleicht_mit_dem_vorjahresquartal() -> None:
    """Gegen das Vorquartal zu vergleichen misst die Jahreszeit, nicht das Unternehmen."""
    f = _facts(
        Revenues={
            "USD": [
                _fakt(100.0, "2024-10-01", "2024-12-31", "2025-02-06"),
                _fakt(80.0, "2025-07-01", "2025-09-30", "2025-11-07"),
                _fakt(125.0, "2025-10-01", "2025-12-31", "2026-02-06"),
            ]
        }
    )
    paar = _quartal_vorjahr(f, ("Revenues",), "USD", datetime(2026, 3, 1).date())
    assert paar is not None
    jung, alt = paar
    assert (jung.wert, alt.wert) == (125.0, 100.0)


def test_ohne_vorjahresquartal_kein_wachstum() -> None:
    f = _facts(Revenues={"USD": _VIER_QUARTALE[:2]})
    assert _quartal_vorjahr(f, ("Revenues",), "USD", datetime(2026, 3, 1).date()) is None


# ── Die zusammengesetzten Kennzahlen ────────────────────────────────────────────
def _vollstaendig() -> dict[str, Any]:
    def q(werte: list[float]) -> list[dict[str, Any]]:
        zeiten = [
            ("2025-01-01", "2025-03-31", "2025-05-10"),
            ("2025-04-01", "2025-06-30", "2025-08-08"),
            ("2025-07-01", "2025-09-30", "2025-11-07"),
            ("2025-10-01", "2025-12-31", "2026-02-06"),
        ]
        return [_fakt(v, s, e, f) for v, (s, e, f) in zip(werte, zeiten, strict=True)]

    return _facts(
        RevenueFromContractWithCustomerExcludingAssessedTax={"USD": q([100, 110, 120, 140])},
        GrossProfit={"USD": q([50, 56, 62, 74])},
        OperatingIncomeLoss={"USD": q([20, 23, 26, 32])},
        NetIncomeLoss={"USD": q([15, 17, 20, 25])},
        NetCashProvidedByUsedInOperatingActivities={"USD": q([25, 28, 31, 38])},
        PaymentsToAcquirePropertyPlantAndEquipment={"USD": q([5, 5, 6, 6])},
        DepreciationDepletionAndAmortization={"USD": q([4, 4, 5, 5])},
        InterestExpense={"USD": q([1, 1, 1, 1])},
        EarningsPerShareDiluted={"USD/shares": q([0.15, 0.17, 0.20, 0.25])},
        StockholdersEquity={"USD": [_fakt(500.0, None, "2025-12-31", "2026-02-06")]},
        AssetsCurrent={"USD": [_fakt(300.0, None, "2025-12-31", "2026-02-06")]},
        LiabilitiesCurrent={"USD": [_fakt(150.0, None, "2025-12-31", "2026-02-06")]},
        CashAndCashEquivalentsAtCarryingValue={
            "USD": [_fakt(80.0, None, "2025-12-31", "2026-02-06")]
        },
        LongTermDebtNoncurrent={"USD": [_fakt(200.0, None, "2025-12-31", "2026-02-06")]},
    )


def test_margen_und_bilanzkennzahlen_stimmen() -> None:
    k = kennzahlen_aus_facts("TEST", _vollstaendig(), datetime(2026, 3, 1, tzinfo=UTC))
    assert k is not None
    umsatz = 100 + 110 + 120 + 140  # 470
    assert k.gross_margin is not None and abs(k.gross_margin - 242 / umsatz) < 1e-9
    assert k.operating_margin is not None and abs(k.operating_margin - 101 / umsatz) < 1e-9
    assert k.roe is not None and abs(k.roe - 77 / 500) < 1e-9
    # Freier Cashflow = operativer Cashflow minus Investitionen.
    assert k.fcf_margin is not None and abs(k.fcf_margin - (122 - 22) / umsatz) < 1e-9
    assert k.current_ratio == 2.0
    # Nettoverschuldung (200 - 80) gegen EBITDA (101 + 18).
    assert k.net_debt_to_ebitda is not None and abs(k.net_debt_to_ebitda - 120 / 119) < 1e-9
    assert k.interest_coverage is not None and abs(k.interest_coverage - 101 / 4) < 1e-9


def test_kurs_gewinn_verhaeltnis_nur_mit_kurs() -> None:
    """EDGAR kennt keine Kurse — ohne einen bleibt das KGV leer statt geraten."""
    ohne = kennzahlen_aus_facts("TEST", _vollstaendig(), datetime(2026, 3, 1, tzinfo=UTC))
    assert ohne is not None and ohne.pe is None
    mit = kennzahlen_aus_facts("TEST", _vollstaendig(), datetime(2026, 3, 1, tzinfo=UTC), kurs=38.5)
    assert mit is not None and mit.pe is not None
    assert abs(mit.pe - 38.5 / 0.77) < 1e-6


def test_stand_der_daten_ist_das_periodenende_nicht_heute() -> None:
    k = kennzahlen_aus_facts("TEST", _vollstaendig(), datetime(2026, 6, 30, tzinfo=UTC))
    assert k is not None
    assert k.as_of_report.date().isoformat() == "2025-12-31"


def test_leere_antwort_gibt_none() -> None:
    assert kennzahlen_aus_facts("TEST", {"facts": {}}, datetime(2026, 3, 1, tzinfo=UTC)) is None
    assert kennzahlen_aus_facts("TEST", {}, datetime(2026, 3, 1, tzinfo=UTC)) is None


def test_negatives_vorjahres_eps_erzeugt_kein_wachstum() -> None:
    """Von -1 auf +1 sind es weder +200 % noch -200 %. Dann lieber keine Zahl."""
    f = _facts(
        NetIncomeLoss={"USD": _VIER_QUARTALE},
        EarningsPerShareDiluted={
            "USD/shares": [
                _fakt(-0.50, "2024-10-01", "2024-12-31", "2025-02-06"),
                _fakt(0.80, "2025-10-01", "2025-12-31", "2026-02-06"),
            ]
        },
    )
    k = kennzahlen_aus_facts("TEST", f, datetime(2026, 3, 1, tzinfo=UTC))
    assert k is not None and k.eps_growth_yoy is None


def test_alternative_etiketten_werden_der_reihe_nach_versucht() -> None:
    """Aeltere Einreichungen melden Umsatz als ``Revenues``, neuere anders."""
    f = _facts(Revenues={"USD": _VIER_QUARTALE}, NetIncomeLoss={"USD": _VIER_QUARTALE})
    k = kennzahlen_aus_facts("TEST", f, datetime(2026, 3, 1, tzinfo=UTC))
    assert k is not None and k.gross_margin is None  # kein Rohertrag gemeldet
    assert k.revenue_growth_yoy is None  # kein Vorjahresquartal vorhanden


def test_symbolkern_schneidet_die_quelle_ab() -> None:
    assert _symbol_kern("NVDA-YFD") == "NVDA"
    assert _symbol_kern("aapl") == "AAPL"
    assert _symbol_kern("BRK.B") == "BRK"
