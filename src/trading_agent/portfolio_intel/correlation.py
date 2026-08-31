"""``CorrelationEngine`` — echte rollierende Korrelation aus OHLCV (Masterplan §41).

Ersetzt die statische ``ClusterMap``: Pearson-Korrelation der **überlappenden** Log-Returns
je Instrument-Paar über ein Fenster, Cluster per Schwellwert (Union-Find).

Point-in-Time: es werden nur die übergebenen abgeschlossenen Bars verwendet — kein Resampling,
kein Forward-Fill über Lücken hinweg (nicht-überlappende Timestamps fallen aus dem Paar-Vergleich).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

_MIN_OVERLAP = 20  # weniger überlappende Returns → Korrelation = 0.0 (nicht bewertbar, kein Fake)


@dataclass(frozen=True, slots=True)
class CorrelationMatrix:
    instruments: tuple[str, ...]
    as_of: datetime
    window: int
    _values: dict[tuple[str, str], float]
    _samples: dict[tuple[str, str], int]

    @staticmethod
    def _key(a: str, b: str) -> tuple[str, str]:
        a, b = a.upper(), b.upper()
        return (a, b) if a <= b else (b, a)

    def correlation(self, a: str, b: str) -> float:
        if a.upper() == b.upper():
            return 1.0
        return self._values.get(self._key(a, b), 0.0)

    def samples(self, a: str, b: str) -> int:
        return self._samples.get(self._key(a, b), 0)

    def static_correlations(self) -> dict[tuple[str, str], float]:
        """Format für ``PortfolioContext.static_correlations``."""
        return dict(self._values)

    def clusters(self, threshold: float = 0.7) -> tuple[frozenset[str], ...]:
        parent = {i: i for i in self.instruments}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for (a, b), rho in self._values.items():
            if abs(rho) >= threshold:
                parent[find(a)] = find(b)

        groups: dict[str, set[str]] = {}
        for i in self.instruments:
            groups.setdefault(find(i), set()).add(i)
        return tuple(sorted((frozenset(g) for g in groups.values()), key=lambda g: sorted(g)[0]))

    def cluster_of(self, instrument: str, threshold: float = 0.7) -> frozenset[str]:
        for c in self.clusters(threshold):
            if instrument.upper() in c:
                return c
        return frozenset({instrument.upper()})

    def most_correlated(self, instrument: str, *, limit: int = 3) -> list[tuple[str, float]]:
        inst = instrument.upper()
        out = [
            (other, self.correlation(inst, other)) for other in self.instruments if other != inst
        ]
        out.sort(key=lambda kv: abs(kv[1]), reverse=True)
        return out[:limit]


def _log_returns(closes: Sequence[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(closes)):
        p0, p1 = closes[i - 1], closes[i]
        if p0 > 0 and p1 > 0:
            out.append(math.log(p1 / p0))
        else:
            out.append(0.0)
    return out


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return 0.0
    return max(-1.0, min(1.0, cov / math.sqrt(vx * vy)))


class CorrelationEngine:
    """``compute(series, window)`` → ``CorrelationMatrix``.

    ``series`` = ``{instrument: [(open_time, close), ...]}`` **oder** ``{instrument: [OHLCV, ...]}``
    (Objekte mit ``open_time`` + ``close``). Nur gemeinsame Timestamps gehen in ein Paar ein.
    """

    def __init__(self, *, window: int = 120, min_overlap: int = _MIN_OVERLAP) -> None:
        if window < 3:
            raise ValueError("window muss >= 3 sein")
        self.window = window
        self.min_overlap = min_overlap

    @staticmethod
    def _extract(bars: object) -> list[tuple[datetime, float]]:
        rows: list[tuple[datetime, float]] = []
        for b in bars:  # type: ignore[attr-defined]
            if isinstance(b, tuple):
                rows.append((b[0], float(b[1])))
            else:
                rows.append((b.open_time, float(b.close)))
        rows.sort(key=lambda r: r[0])
        return rows

    def compute(
        self,
        series: dict[str, object],
        *,
        as_of: datetime | None = None,
    ) -> CorrelationMatrix:
        by_inst: dict[str, dict[datetime, float]] = {}
        latest: datetime | None = None
        for inst, bars in series.items():
            rows = self._extract(bars)[-(self.window + 1) :]
            by_inst[inst.upper()] = dict(rows)
            if rows and (latest is None or rows[-1][0] > latest):
                latest = rows[-1][0]

        instruments = tuple(sorted(by_inst))
        values: dict[tuple[str, str], float] = {}
        samples: dict[tuple[str, str], int] = {}

        for i, a in enumerate(instruments):
            for b in instruments[i + 1 :]:
                common = sorted(set(by_inst[a]) & set(by_inst[b]))
                ca = [by_inst[a][t] for t in common]
                cb = [by_inst[b][t] for t in common]
                ra, rb = _log_returns(ca), _log_returns(cb)
                key = (a, b)
                samples[key] = len(ra)
                if len(ra) < self.min_overlap:
                    values[key] = 0.0
                else:
                    values[key] = round(_pearson(ra, rb), 4)

        return CorrelationMatrix(
            instruments=instruments,
            as_of=as_of or latest or datetime.now().astimezone(),
            window=self.window,
            _values=values,
            _samples=samples,
        )


__all__ = ["CorrelationEngine", "CorrelationMatrix"]
