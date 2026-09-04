"""SETUP-TSMOM-ENSEMBLE-01 — Time-Series-Momentum mit Volatilitaets-Zielsteuerung.

Warum dieses Setup existiert: ``docs/STRATEGIE-ENTSCHEID-2026-09-04.md``. Die
SMC-/Sweep-Reversal-Familie ist auf der groessten verfuegbaren Stichprobe (958 OOS-Trades,
Kostenmodell je Symbol) mit −0.196 R bei t = −3.71 **signifikant negativ**. Kein Setup aus
798 getesteten Konfigurationen uebersteht die Multiple-Testing-Korrektur.

Time-Series-Momentum ist die Gegenhypothese — und der wesentliche Unterschied ist, dass sie
**ausserhalb dieses Projekts** Bestand hat: Moskowitz/Ooi/Pedersen (Journal of Financial
Economics, 2012) ueber 58 Futures, vier Assetklassen und 25 Jahre; Han/Kang/Ryu fuer den
Kryptomarkt. Erstbefund auf eigenen Daten (2017–2026, zwei volle Zyklen inklusive der
Baerenmaerkte 2018 und 2022): Sharpe 0.86 gegen 0.76 bei Buy & Hold, maximaler Drawdown
37 % statt 81 %.

**Das ist ein Erstbefund, kein Beleg.** Der Status bleibt ``in_validation`` und damit
SHADOW, bis die volle Pruefkette durch ist.

Zwei Dinge unterscheiden dieses Setup grundlegend von den bisherigen:

1. **Es ist eine Allokationsregel, kein Einstiegs-Setup.** Es beantwortet nicht "wo ist der
   Einstieg", sondern "wie viel von was — und wann gar nichts". Ausgestiegen wird, wenn das
   Signal dreht, nicht an einem Kursziel.
2. **Die Parameter werden nicht optimiert.** Die fuenf Rueckblickfenster sind vorab
   festgelegt und werden als Ensemble gemittelt. Genau deshalb ist es ein Ensemble: Wer die
   Fenster durchprobiert, landet wieder beim Multiple-Testing-Problem aus Befund F1.

Der Stop ist ein **Katastrophenschutz**, kein Ausstiegsmechanismus. Die Risk-Engine braucht
eine Distanz zur Positionsgroessen-Rechnung; die eigentliche Steuerung laeuft ueber das
Gewicht.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from enum import StrEnum

from trading_agent.core.version import STRATEGY_VERSION

SETUP_TSMOM_ENSEMBLE = "SETUP-TSMOM-ENSEMBLE-01"

# Die projektweite Strategie-Version aus core.version — ein Setup erfindet keine eigene,
# sonst zeigt die ValidationRegistry-Suche (setup_id, strategy_version) ins Leere und faellt
# still auf UNVALIDATED zurueck.
SETUP_PARAMS_VERSION = "tsmom-ensemble-1"
"""Version der eingefrorenen Parameter. Aenderung = neue Hypothese, neuer Registereintrag."""

# Kalendertage pro Jahr — Krypto handelt durchgehend.
_TRADING_DAYS = 365


class TsmomState(StrEnum):
    """Zustand der Allokationsregel. Bewusst kuerzer als eine Setup-FSM."""

    FLAT = "flat"  # kein Fenster positiv, oder Volatilitaet unbrauchbar
    PARTIAL = "partial"  # einige Fenster positiv
    FULL = "full"  # alle Fenster positiv
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True, slots=True)
class TsmomParams:
    """Vorab festgelegt. Aenderungen sind neue Hypothesen und gehoeren ins Register."""

    lookbacks: tuple[int, ...] = (28, 56, 90, 120, 180)
    vol_window: int = 60
    target_vol: float = 0.40
    max_weight: float = 1.0
    min_agreement: float = 0.20
    disaster_stop_atr: float = 3.0

    def warmup_bars(self) -> int:
        return max(max(self.lookbacks), self.vol_window) + 1


@dataclass(frozen=True, slots=True)
class TsmomReport:
    """Vollstaendig erklaerbar — Masterplan-Regel 'No Blind AI'."""

    state: TsmomState
    target_weight: float
    agreement: float
    per_lookback: dict[int, bool]
    lookback_returns: dict[int, float]
    realized_vol: float
    vol_scalar: float
    reasons: list[str] = field(default_factory=list)
    setup_id: str = SETUP_TSMOM_ENSEMBLE
    strategy_version: str = STRATEGY_VERSION

    @property
    def is_long(self) -> bool:
        return self.target_weight > 0.0

    @property
    def borderline(self) -> bool:
        """Knapp: die Fenster sind uneins. Sichtbar machen statt glaetten."""
        return 0.0 < self.agreement < 1.0


def _annualised_vol(closes: list[float], window: int) -> float:
    if len(closes) < window + 1:
        return 0.0
    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(len(closes) - window, len(closes))]
    if len(rets) < 2:
        return 0.0
    return statistics.pstdev(rets) * math.sqrt(_TRADING_DAYS)


def evaluate_tsmom(
    closes: list[float],
    *,
    params: TsmomParams | None = None,
    atr: float | None = None,
) -> TsmomReport:
    """Bewertet die Reihe bis EINSCHLIESSLICH ``closes[-1]``.

    Der Aufrufer ist dafuer verantwortlich, dass ``closes`` keine Bars aus der Zukunft
    enthaelt. Die Position gilt ab der Folgebar — dieselbe Konvention wie im uebrigen
    Research-Pfad (Entry auf ``bar[i+1].open``).
    """
    p = params or TsmomParams()
    if len(closes) < p.warmup_bars():
        return TsmomReport(
            state=TsmomState.INSUFFICIENT_DATA,
            target_weight=0.0,
            agreement=0.0,
            per_lookback={},
            lookback_returns={},
            realized_vol=0.0,
            vol_scalar=0.0,
            reasons=[f"zu wenige Bars: {len(closes)} < {p.warmup_bars()}"],
        )

    last = closes[-1]
    per_lb: dict[int, bool] = {}
    rets: dict[int, float] = {}
    for lb in p.lookbacks:
        past = closes[-1 - lb]
        r = (last / past - 1.0) if past > 0 else 0.0
        rets[lb] = round(r, 6)
        per_lb[lb] = r > 0.0

    agreement = sum(per_lb.values()) / len(per_lb)
    vol = _annualised_vol(closes, p.vol_window)

    reasons: list[str] = []
    pos = [lb for lb, ok in per_lb.items() if ok]
    neg = [lb for lb, ok in per_lb.items() if not ok]
    if pos:
        reasons.append(f"positiv ueber {', '.join(f'{lb}d' for lb in pos)}")
    if neg:
        reasons.append(f"negativ ueber {', '.join(f'{lb}d' for lb in neg)}")

    if vol <= 0:
        return TsmomReport(
            state=TsmomState.FLAT,
            target_weight=0.0,
            agreement=agreement,
            per_lookback=per_lb,
            lookback_returns=rets,
            realized_vol=0.0,
            vol_scalar=0.0,
            reasons=[*reasons, "realisierte Volatilitaet nicht bestimmbar — kein Gewicht"],
        )

    vol_scalar = min(p.max_weight, p.target_vol / vol)
    weight = vol_scalar * agreement

    if agreement < p.min_agreement:
        return TsmomReport(
            state=TsmomState.FLAT,
            target_weight=0.0,
            agreement=agreement,
            per_lookback=per_lb,
            lookback_returns=rets,
            realized_vol=round(vol, 6),
            vol_scalar=round(vol_scalar, 6),
            reasons=[
                *reasons,
                f"Zustimmung {agreement:.0%} unter Schwelle {p.min_agreement:.0%} — flat",
            ],
        )

    state = TsmomState.FULL if agreement >= 1.0 else TsmomState.PARTIAL
    reasons.append(
        f"Volatilitaet {vol:.1%} annualisiert -> Skalar {vol_scalar:.2f} (Ziel {p.target_vol:.0%})"
    )
    if vol_scalar < p.max_weight:
        reasons.append("Gewicht durch Volatilitaet begrenzt, nicht durch das Signal")
    return TsmomReport(
        state=state,
        target_weight=round(weight, 6),
        agreement=agreement,
        per_lookback=per_lb,
        lookback_returns=rets,
        realized_vol=round(vol, 6),
        vol_scalar=round(vol_scalar, 6),
        reasons=reasons,
    )


def disaster_stop(entry: float, atr: float, *, params: TsmomParams | None = None) -> float | None:
    """Katastrophen-Stop, KEIN Ausstiegsmechanismus.

    Die Regel steigt aus, wenn das Signal dreht. Dieser Stop existiert nur, damit die
    Risk-Engine eine Distanz zur Positionsgroessen-Rechnung hat und ein Gap-Ereignis
    begrenzt bleibt. Er liegt bewusst weit weg — ein enger Stop wuerde das Setup zu einer
    Breakout-Strategie machen, und genau die ist widerlegt.
    """
    p = params or TsmomParams()
    if entry <= 0 or atr <= 0:
        return None
    stop = entry - p.disaster_stop_atr * atr
    return stop if stop > 0 else None


__all__ = [
    "SETUP_TSMOM_ENSEMBLE",
    "STRATEGY_VERSION",
    "TsmomParams",
    "TsmomReport",
    "TsmomState",
    "disaster_stop",
    "evaluate_tsmom",
]
