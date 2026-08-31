"""Chart-Annotation-Payloads (Masterplan §58) — **Daten, kein Rendering**.

Erzeugt aus einem ``SignalReport`` (+ optional MTF-Kontext) die Objekte, die eine
Lightweight-Charts-Frontend-Komponente direkt zeichnen kann: Preislinien (Entry/SL/TP),
Marker (Setup-Punkt), Zonen (Liquidität / OB), Session-Bänder.

Alle Preise unverändert übernommen; keine Analyse hier.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PriceLine:
    price: float
    title: str
    color: str
    line_style: str = "solid"  # solid | dashed | dotted


@dataclass(frozen=True, slots=True)
class Marker:
    time: str  # ISO-8601
    position: str  # aboveBar | belowBar | inBar
    shape: str  # arrowUp | arrowDown | circle | square
    color: str
    text: str


@dataclass(frozen=True, slots=True)
class Zone:
    price_top: float
    price_bottom: float
    color: str
    title: str


@dataclass(frozen=True, slots=True)
class ChartAnnotations:
    instrument: str
    as_of: str
    price_lines: list[PriceLine] = field(default_factory=list)
    markers: list[Marker] = field(default_factory=list)
    zones: list[Zone] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "instrument": self.instrument,
            "as_of": self.as_of,
            "price_lines": [asdict(p) for p in self.price_lines],
            "markers": [asdict(m) for m in self.markers],
            "zones": [asdict(z) for z in self.zones],
        }


_C_ENTRY = "#2962FF"
_C_STOP = "#EF5350"
_C_TP = "#26A69A"
_C_LIQ = "rgba(255,193,7,0.15)"


def build_chart_annotations(
    signal_report: object,
    *,
    mtf: object = None,
    now: datetime | None = None,
) -> ChartAnnotations:
    sr = signal_report
    instrument = str(getattr(sr, "instrument", "?"))
    cutoff = getattr(sr, "information_cutoff", None)
    as_of = (
        cutoff.isoformat() if isinstance(cutoff, datetime) else (now or datetime.now()).isoformat()
    )

    lines: list[PriceLine] = []
    for attr, title, color, style in (
        ("entry", "Entry", _C_ENTRY, "solid"),
        ("stop_loss", "SL", _C_STOP, "dashed"),
        ("tp1", "TP1", _C_TP, "dotted"),
        ("tp2", "TP2", _C_TP, "dashed"),
    ):
        val = getattr(sr, attr, None)
        if isinstance(val, (int, float)):
            lines.append(PriceLine(price=float(val), title=title, color=color, line_style=style))
    tp3_ind = getattr(sr, "tp3_indicative", None)
    if isinstance(tp3_ind, (int, float)):
        lines.append(
            PriceLine(
                price=float(tp3_ind), title="TP3 (indikativ)", color=_C_TP, line_style="dotted"
            )
        )

    markers: list[Marker] = []
    direction = str(getattr(sr, "direction", "") or "")
    action = str(getattr(sr, "action", "") or "")
    if action in ("BUY", "SELL"):
        markers.append(
            Marker(
                time=as_of,
                position="belowBar" if direction == "LONG" else "aboveBar",
                shape="arrowUp" if direction == "LONG" else "arrowDown",
                color=_C_ENTRY,
                text=f"{action} {getattr(sr, 'tier', '') or ''}".strip(),
            )
        )

    zones: list[Zone] = []
    per_tf = getattr(mtf, "per_tf", {}) or {}
    entry = getattr(sr, "entry", None)
    for ctx in per_tf.values():
        for lv in getattr(ctx, "liquidity", ()) or ():
            price = getattr(lv, "price", None)
            if not isinstance(price, (int, float)):
                continue
            half = abs(price) * 0.001
            zones.append(
                Zone(
                    price_top=float(price) + half,
                    price_bottom=float(price) - half,
                    color=_C_LIQ,
                    title=str(getattr(lv, "kind", "liquidity")),
                )
            )
        if len(zones) >= 6:
            break
    _ = entry

    return ChartAnnotations(
        instrument=instrument,
        as_of=as_of,
        price_lines=lines,
        markers=markers,
        zones=zones[:6],
    )


__all__ = ["ChartAnnotations", "Marker", "PriceLine", "Zone", "build_chart_annotations"]
