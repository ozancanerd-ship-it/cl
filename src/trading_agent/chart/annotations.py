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
    # Bevorzugt den Setup-Timeframe (H4); fällt auf den ersten verfügbaren zurück.
    ctxs = list(per_tf.values())
    setup_ctx = next(
        (c for c in ctxs if str(getattr(getattr(c, "timeframe", None), "value", "")) == "H4"),
        ctxs[0] if ctxs else None,
    )

    for ctx in ctxs:
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

    if setup_ctx is not None:
        _swing_markers(setup_ctx, markers)
        _break_markers(setup_ctx, markers)
        _imbalance_zones(setup_ctx, zones)

    return ChartAnnotations(
        instrument=instrument,
        as_of=as_of,
        price_lines=lines,
        markers=markers[:24],
        zones=zones[:14],
    )


_C_BULL_ZONE = "rgba(38,166,154,0.13)"
_C_BEAR_ZONE = "rgba(239,83,80,0.13)"
_C_OB_ZONE = "rgba(41,98,255,0.13)"


def _iso(v: object) -> str:
    return v.isoformat() if isinstance(v, datetime) else str(v)


def _swing_markers(ctx: object, out: list[Marker]) -> None:
    swings = list(getattr(ctx, "swings", ()) or [])[-10:]
    for s in swings:
        is_high = str(getattr(getattr(s, "type", None), "value", "")).endswith("high")
        label = getattr(getattr(s, "label", None), "value", None) or ("H" if is_high else "L")
        out.append(
            Marker(
                time=_iso(getattr(s, "timestamp", "")),
                position="aboveBar" if is_high else "belowBar",
                shape="circle",
                color="#8b949e",
                text=str(label).upper(),
            )
        )


def _break_markers(ctx: object, out: list[Marker]) -> None:
    for b in list(getattr(ctx, "structure_breaks", ()) or [])[-6:]:
        kind = str(getattr(getattr(b, "kind", None), "value", "break")).upper()
        bull = str(getattr(getattr(b, "direction", None), "value", "")) == "bullish"
        out.append(
            Marker(
                time=_iso(getattr(b, "break_bar_timestamp", "")),
                position="belowBar" if bull else "aboveBar",
                shape="arrowUp" if bull else "arrowDown",
                color=_C_TP if bull else _C_STOP,
                text=kind,
            )
        )


def _imbalance_zones(ctx: object, out: list[Zone]) -> None:
    for fvg in list(getattr(ctx, "fvgs", ()) or []):
        if str(getattr(getattr(fvg, "state", None), "value", "")) not in ("unmitigated", "partial"):
            continue
        bull = str(getattr(getattr(fvg, "direction", None), "value", "")) == "bullish"
        out.append(
            Zone(
                price_top=float(getattr(fvg, "zone_high", 0.0)),
                price_bottom=float(getattr(fvg, "zone_low", 0.0)),
                color=_C_BULL_ZONE if bull else _C_BEAR_ZONE,
                title=f"FVG {'↑' if bull else '↓'}",
            )
        )
    for ob in list(getattr(ctx, "order_blocks", ()) or []):
        if str(getattr(getattr(ob, "state", None), "value", "")) not in ("unmitigated", "partial"):
            continue
        bull = str(getattr(getattr(ob, "direction", None), "value", "")) == "bullish"
        out.append(
            Zone(
                price_top=float(getattr(ob, "zone_high", 0.0)),
                price_bottom=float(getattr(ob, "zone_low", 0.0)),
                color=_C_OB_ZONE,
                title=f"OB {'↑' if bull else '↓'}",
            )
        )


__all__ = ["ChartAnnotations", "Marker", "PriceLine", "Zone", "build_chart_annotations"]
