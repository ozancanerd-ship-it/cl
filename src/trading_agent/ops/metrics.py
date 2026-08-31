"""In-process metrics registry (counters, gauges, histograms).

No external metrics server. ``snapshot()`` returns a plain dict for the dashboard / logs.
Labels are encoded into the metric key as ``name{k=v,k2=v2}``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


def _key(name: str, labels: dict[str, str] | None) -> str:
    if not labels:
        return name
    inner = ",".join(f"{k}={labels[k]}" for k in sorted(labels))
    return f"{name}{{{inner}}}"


@dataclass
class _Hist:
    count: int = 0
    total: float = 0.0
    min: float = field(default=float("inf"))
    max: float = field(default=float("-inf"))

    def observe(self, v: float) -> None:
        self.count += 1
        self.total += v
        self.min = min(self.min, v)
        self.max = max(self.max, v)

    def as_dict(self) -> dict[str, float | int]:
        return {
            "count": self.count,
            "avg": self.total / self.count if self.count else 0.0,
            "min": self.min if self.count else 0.0,
            "max": self.max if self.count else 0.0,
        }


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._hists: dict[str, _Hist] = {}

    def incr(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        k = _key(name, labels)
        with self._lock:
            self._counters[k] = self._counters.get(k, 0.0) + value

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        with self._lock:
            self._gauges[_key(name, labels)] = value

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        k = _key(name, labels)
        with self._lock:
            self._hists.setdefault(k, _Hist()).observe(value)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {k: h.as_dict() for k, h in self._hists.items()},
            }

    def counter_value(self, name: str, labels: dict[str, str] | None = None) -> float:
        return self._counters.get(_key(name, labels), 0.0)


__all__ = ["MetricsRegistry"]
