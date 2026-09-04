"""Hypothesen-Register und Multiple-Testing-Korrektur.

Hintergrund: ``docs/INDEPENDENT-METHOD-AUDIT-2026-09-03.md``, Befund F1. Allein der Lauf
v11 testete 20 Setups mal 4 RR-Stufen = 80 Konfigurationen; im Repository liegen 16 solche
Laeufe. Unter der Nullhypothese "kein Setup hat einen Edge" sind bei 80 Konfigurationen
rund vier scheinbare Treffer auf dem 5-%-Niveau rein zufaellig zu erwarten. Gefunden
wurden sieben. Ohne Korrektur ist ein einzelner p-Wert damit ohne Aussage.

Zwei Korrekturen:

**Bonferroni** — konservativ und ohne Annahmen: Schwelle ``alpha / K``. Bei K = 80 und
alpha = 5 % also ``p < 0.000625``. Zu streng, wenn die Konfigurationen stark korreliert
sind (was sie hier sind: dieselben Daten, verwandte Filter), aber als harte Untergrenze
brauchbar.

**Deflated Sharpe Ratio** (Bailey & Lopez de Prado, 2014) — beruecksichtigt zusaetzlich,
wie stark die Sharpe-Werte ueber die Versuche streuen, sowie Schiefe und Woelbung der
Renditeverteilung. Das ist die passendere Kennzahl, wenn viele verwandte Varianten
getestet wurden.

Beide brauchen dieselbe Zahl: **wie viele Konfigurationen wurden insgesamt probiert.**
Genau die haelt dieses Register fest — auch die verworfenen. Eine Hypothese, die nicht
im Register steht, existiert fuer die Korrektur nicht, und das Ergebnis ist dann zu gut.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_EULER_GAMMA = 0.5772156649015329
_DEFAULT_PATH = "config/hypothesis_registry.json"


# ----------------------------------------------------------------------------- Statistik


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """Inverse Standardnormalverteilung (Acklam-Approximation, |Fehler| < 1.15e-9)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p muss echt zwischen 0 und 1 liegen")
    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )
    p_low, p_high = 0.02425, 1 - 0.02425
    if p < p_low:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p > p_high:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    q = p - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
        * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    )


def expected_max_sharpe(n_trials: int, var_sharpe: float) -> float:
    """Erwarteter MAXIMALER Sharpe unter H0 (kein Edge) bei ``n_trials`` Versuchen.

    Selbst wenn keine Strategie funktioniert, sieht die beste von vielen gut aus. Das ist
    die Groesse, gegen die der beobachtete Wert antreten muss — nicht gegen Null.
    """
    if n_trials < 2 or var_sharpe <= 0:
        return 0.0
    k = float(n_trials)
    return math.sqrt(var_sharpe) * (
        (1 - _EULER_GAMMA) * norm_ppf(1 - 1 / k) + _EULER_GAMMA * norm_ppf(1 - 1 / (k * math.e))
    )


def deflated_sharpe(
    *,
    sharpe: float,
    n_obs: int,
    n_trials: int,
    var_sharpe_across_trials: float,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Wahrscheinlichkeit, dass der beobachtete Sharpe echt ist (Bailey/Lopez de Prado).

    ``sharpe`` ist der Sharpe je Beobachtung (bei R-Multiplen: ``mean(R)/stdev(R)``),
    ``n_obs`` die Zahl der Trades. ``kurtosis`` ist die NICHT-exzess Woelbung (3.0 = normal).
    Rueckgabe unter 0.95 heisst: das Ergebnis ist mit reinem Zufall unter dieser Zahl von
    Versuchen vereinbar.
    """
    if n_obs < 3:
        return 0.0
    sr0 = expected_max_sharpe(n_trials, var_sharpe_across_trials)
    denom_sq = 1.0 - skew * sharpe + ((kurtosis - 1.0) / 4.0) * sharpe * sharpe
    if denom_sq <= 0:
        return 0.0
    z = ((sharpe - sr0) * math.sqrt(n_obs - 1)) / math.sqrt(denom_sq)
    return norm_cdf(z)


def bonferroni_threshold(n_trials: int, alpha: float = 0.05) -> float:
    return alpha / max(1, n_trials)


# ----------------------------------------------------------------------------- Register


@dataclass(frozen=True, slots=True)
class Hypothesis:
    """Eine getestete Konfiguration. Auch verworfene gehoeren hierher."""

    id: str
    setup: str
    run: str
    date: str
    n_configs: int = 1
    params: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    verdict: str = "getestet"
    note: str = ""


class HypothesisRegistry:
    def __init__(self, entries: list[Hypothesis] | None = None, note: str = "") -> None:
        self.entries: list[Hypothesis] = list(entries or [])
        self.note = note

    # -- Persistenz ------------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path = _DEFAULT_PATH) -> HypothesisRegistry:
        p = Path(path)
        if not p.exists():
            return cls()
        doc = json.loads(p.read_text(encoding="utf-8"))
        return cls(
            [Hypothesis(**e) for e in doc.get("entries", [])],
            note=doc.get("note", ""),
        )

    def save(self, path: str | Path = _DEFAULT_PATH) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {
                    "version": 1,
                    "updated": datetime.now(UTC).isoformat(),
                    "note": self.note,
                    "total_configurations": self.n_trials,
                    "entries": [asdict(e) for e in self.entries],
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    # -- Inhalt ----------------------------------------------------------------------
    def add(self, h: Hypothesis) -> bool:
        """``False``, wenn die id schon drin ist (idempotentes Nachtragen)."""
        if any(e.id == h.id for e in self.entries):
            return False
        self.entries.append(h)
        return True

    @property
    def n_trials(self) -> int:
        """Summe ALLER je probierten Konfigurationen — das ist die Zahl fuer die Korrektur."""
        return sum(max(1, e.n_configs) for e in self.entries)

    def sharpes(self) -> list[float]:
        out = []
        for e in self.entries:
            v = e.result.get("sharpe_r")
            if isinstance(v, int | float):
                out.append(float(v))
        return out

    def var_sharpe(self) -> float:
        """Streuung der Sharpe-Werte ueber die Versuche — Eingang der Deflated Sharpe Ratio."""
        vals = self.sharpes()
        if len(vals) < 2:
            return 0.0
        m = sum(vals) / len(vals)
        return sum((v - m) ** 2 for v in vals) / (len(vals) - 1)

    def bonferroni(self, alpha: float = 0.05) -> float:
        return bonferroni_threshold(self.n_trials, alpha)

    def deflated(self, *, sharpe: float, n_obs: int, skew: float = 0.0, kurt: float = 3.0) -> float:
        return deflated_sharpe(
            sharpe=sharpe,
            n_obs=n_obs,
            n_trials=self.n_trials,
            var_sharpe_across_trials=self.var_sharpe(),
            skew=skew,
            kurtosis=kurt,
        )

    def summary(self) -> str:
        return (
            f"{len(self.entries)} Eintraege, {self.n_trials} Konfigurationen · "
            f"Bonferroni p < {self.bonferroni():.6f} · "
            f"Streuung der Sharpes {self.var_sharpe():.4f}"
        )


__all__ = [
    "Hypothesis",
    "HypothesisRegistry",
    "bonferroni_threshold",
    "deflated_sharpe",
    "expected_max_sharpe",
    "norm_cdf",
    "norm_ppf",
]
