"""Daten-Frische je Instrument und Timeframe.

Hintergrund: ``docs/INDEPENDENT-METHOD-AUDIT-2026-09-03.md``, Befund F12. Die sechs
Kern-Krypto-Reihen endeten am 2025-06-30, waehrend die Forschung mit ``--end 2026-08-29``
lief. Das OOS-Fenster war fuer genau diese Symbole sechs Monate statt zwanzig — und
niemand hat es bemerkt, weil nichts danach gesehen hat.

Nach dem Nachladen der fehlenden 13 Monate wurden 14 von 15 Setups schlechter. Der Fehler
war also nicht kosmetisch: er hat die zentrale Aussage des Projekts getragen.

Dieses Modul ist die Gegenmassnahme. Es kennt die erwartete Aktualitaet je Quelle —
Binance-Vision veroeffentlicht Monatsdateien mit Verzoegerung, Yahoo ist tagesaktuell —
und meldet alles, was darueber hinaus zurueckliegt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# Erwartete Verzoegerung je Quelle, in Tagen. Ueberschritten = STALE.
#
# Es gibt zwei Profile, weil "veraltet" davon abhaengt, wofuer die Reihe benutzt wird.
#
# research: Binance Vision liefert Monatsarchive mit bis zu ~5 Wochen Versatz. Fuer einen
#   Backtest ueber sieben Jahre ist das egal — der letzte Monat aendert nichts an der Aussage.
#
# live: fuer eine taegliche Allokationsregel ist es NICHT egal. Am 2026-09-04 waren die
#   Krypto-Reihen 34 Tage alt und blieben unter der 45-Tage-Schwelle unauffaellig. Der
#   Forward-Lauf haengte einen einzigen aktuellen Kurs an — 34 Tage Bewegung wurden zu
#   einer Tageskerze von +29 %, die realisierte Volatilitaet sprang von ~50 % auf 79.6 %,
#   das Zielgewicht halbierte sich. Eine Toleranz, die diesen Fall durchlaesst, ist fuer
#   den Live-Pfad keine Pruefung.
_TOLERANCE_DAYS: dict[str, int] = {
    "binance_vision": 45,
    "yahoo": 10,  # Wochenende + Feiertag + Ingest-Versatz; faengt echte Luecken trotzdem
    "dukascopy": 14,
    "default": 45,
}

_TOLERANCE_DAYS_LIVE: dict[str, int] = {
    "binance_vision": 3,  # Krypto handelt jeden Tag; mehr als zwei Tage Lueck ist ein Defekt
    "yahoo": 5,  # Wochenende + ein Feiertag
    "dukascopy": 5,
    "default": 5,
}

PROFILES: dict[str, dict[str, int]] = {
    "research": _TOLERANCE_DAYS,
    "live": _TOLERANCE_DAYS_LIVE,
}


@dataclass(frozen=True, slots=True)
class SeriesAge:
    instrument: str
    timeframe: str
    bars: int
    first: datetime | None
    last: datetime | None
    age_days: int
    tolerance_days: int

    @property
    def stale(self) -> bool:
        return self.last is None or self.age_days > self.tolerance_days

    @property
    def label(self) -> str:
        if self.last is None:
            return "LEER"
        return "VERALTET" if self.stale else "ok"


def _as_utc(value: object) -> datetime | None:
    """Parquet-Zeitstempel auf tz-bewusstes UTC bringen; naive Werte gelten als UTC."""
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _tolerance_for(instrument: str, profile: str = "research") -> int:
    """Wie alt die Reihe hoechstens sein darf — abhaengig davon, wofuer sie benutzt wird."""
    table = PROFILES.get(profile, _TOLERANCE_DAYS)
    if instrument.endswith("-YF"):
        return table["yahoo"]
    if instrument in ("XAUUSD", "EURUSD", "GBPUSD", "USDJPY"):
        return table["dukascopy"]
    if instrument.endswith("USDT"):
        return table["binance_vision"]
    return table["default"]


def scan_repository(
    repo: str | Path,
    *,
    timeframe: str = "H4",
    now: datetime | None = None,
    profile: str = "research",
) -> list[SeriesAge]:
    """Alle Instrumente eines Repositories auf Aktualitaet pruefen.

    Liest die Parquet-Dateien direkt ueber pyarrow — ohne die volle Repository-Maschinerie,
    damit die Pruefung auch dann noch laeuft, wenn eine Reihe defekt ist. pyarrow statt
    pandas, weil ``src`` bewusst schlanke Laufzeit-Abhaengigkeiten hat.
    """
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    now = now or datetime.now(UTC)
    root = Path(repo) / "ohlcv"
    out: list[SeriesAge] = []
    if not root.exists():
        return out

    for inst_dir in sorted(root.glob("instrument=*")):
        instrument = inst_dir.name.split("instrument=", 1)[1]
        files = sorted((inst_dir / f"timeframe={timeframe}").rglob("*.parquet"))
        tol = _tolerance_for(instrument, profile)
        if not files:
            out.append(SeriesAge(instrument, timeframe, 0, None, None, 10**6, tol))
            continue
        bars = 0
        lo: datetime | None = None
        hi: datetime | None = None
        try:
            for f in files:
                col = pq.read_table(f, columns=["open_time"]).column("open_time")
                if len(col) == 0:
                    continue
                bars += len(col)
                mm = pc.min_max(col)
                f_lo, f_hi = _as_utc(mm["min"].as_py()), _as_utc(mm["max"].as_py())
                lo = f_lo if lo is None or (f_lo is not None and f_lo < lo) else lo
                hi = f_hi if hi is None or (f_hi is not None and f_hi > hi) else hi
        except Exception:  # defekte Parquet — als leer melden, nie stillschweigend ueberspringen
            out.append(SeriesAge(instrument, timeframe, 0, None, None, 10**6, tol))
            continue
        if hi is None:
            out.append(SeriesAge(instrument, timeframe, 0, None, None, 10**6, tol))
            continue
        out.append(
            SeriesAge(
                instrument=instrument,
                timeframe=timeframe,
                bars=bars,
                first=lo,
                last=hi,
                age_days=(now - hi).days,
                tolerance_days=tol,
            )
        )
    return out


def stale_series(ages: list[SeriesAge], *, only: list[str] | None = None) -> list[SeriesAge]:
    sel = [a for a in ages if only is None or a.instrument in only]
    return [a for a in sel if a.stale]


def warn_if_stale(
    repo: str | Path,
    symbols: list[str],
    *,
    timeframe: str = "H4",
    now: datetime | None = None,
    profile: str = "research",
) -> list[SeriesAge]:
    """Fuer Research-Skripte: prueft die verwendeten Symbole und gibt die veralteten zurueck.

    Bewusst kein ``raise`` — ein Lauf auf teilweise veralteten Daten kann legitim sein.
    Aber er darf nicht mehr *unbemerkt* passieren.
    """
    ages = scan_repository(repo, timeframe=timeframe, now=now)
    return stale_series(ages, only=symbols)


def format_report(ages: list[SeriesAge]) -> str:
    lines = [
        f"{'Instrument':<14}{'TF':<5}{'Bars':>8}  {'von':<12}{'bis':<12}{'Alter':>7}  Status",
        "-" * 74,
    ]
    for a in sorted(ages, key=lambda x: -x.age_days):
        first = a.first.date().isoformat() if a.first else "—"
        last = a.last.date().isoformat() if a.last else "—"
        age = "—" if a.last is None else f"{a.age_days}d"
        mark = "  ← " + a.label if a.stale else ""
        lines.append(
            f"{a.instrument:<14}{a.timeframe:<5}{a.bars:>8}  {first:<12}{last:<12}{age:>7}{mark}"
        )
    n_stale = sum(1 for a in ages if a.stale)
    lines.append("")
    lines.append(f"{n_stale} von {len(ages)} Reihen veraltet oder leer.")
    return "\n".join(lines)


__all__ = [
    "SeriesAge",
    "format_report",
    "scan_repository",
    "stale_series",
    "warn_if_stale",
]
