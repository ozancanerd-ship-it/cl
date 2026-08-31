#!/usr/bin/env python
"""READ-ONLY Diagnose-Ansicht der implementierten Strategy-Primitives (Phase 3).

Erzeugt aus einem **festen synthetischen** M5-Datensatz eine einzelne, in sich geschlossene
HTML-Datei, die zeigt, was die *bestehenden* Detektoren produzieren:

* Candlesticks
* Swing Highs / Lows + HH / HL / LH / LL  (``strategy.primitives.swings``)
* BOS / CHoCH                              (``strategy.primitives.structure``)
* Liquidity Levels, Equal Highs / Lows     (``strategy.primitives.liquidity``)
* Liquidity Sweeps                          (``strategy.primitives.liquidity``)

Es gibt **keine eigene Analyse-Logik** in dieser Datei — nur Aufrufe der Primitive-Funktionen
und Serialisierung. Keine Broker, keine Keys, keine Live-Daten, keine Orders.

    python scripts/diag_primitives.py
    open diagnostics/primitives_view.html
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from trading_agent.core.enums import Timeframe
from trading_agent.core.models import OHLCV
from trading_agent.core.time import bar_close_time, parse_timestamp
from trading_agent.core.version import STRATEGY_VERSION
from trading_agent.strategy.primitives.atr import atr
from trading_agent.strategy.primitives.liquidity import (
    classify_level_state,
    equal_level_clusters,
    swing_levels,
)
from trading_agent.strategy.primitives.structure import derive_structure_state, structure_breaks
from trading_agent.strategy.primitives.swings import detect_swings

START = parse_timestamp("2024-06-01T00:00:00Z")
TF = Timeframe.M5
OUT = Path(__file__).resolve().parents[1] / "diagnostics" / "primitives_view.html"

# Feste Preis-Pivots (Ziel, Anzahl Bars). Bewusst so gewählt, dass jede Primitive feuert:
# Warmup -> Aufwärtsstruktur (HH/HL -> TREND_UP, BOS) -> CHoCH abwärts -> Equal Highs (Doppel-LH)
# -> Doppel-Boden (Equal Lows) -> Spike-Bar sweept den ursprünglichen Swing-Low SL1.
_PIVOTS: list[tuple[float, int]] = [
    (100.0, 0),
    (101.5, 6),
    (99.0, 6),
    (100.5, 6),  # Warmup (füllt ATR-Historie)
    (114.0, 10),
    (108.0, 7),  # SH1 / SL1  (SL1 wird ganz am Ende gesweept)
    (126.0, 10),
    (119.0, 7),  # SH2 (HH) / SL2 (HL)
    (139.0, 10),  # SH3 (HH)  -> TREND_UP + bullischer BOS
    (116.0, 11),  # geradliniger Drop -> bearischer CHoCH (Close < letztes HL)
    (133.0, 9),  # SL_x (116) bildet sich, Bounce zu LH_a
    (125.0, 6),  # Zwischen-Low
    (133.0, 7),  # LH_b (133) == LH_a  -> EQUAL HIGHS
    (112.0, 9),  # SL5 (112)
    (120.0, 6),  # LH
    (112.0, 8),  # SL6 (112) == SL5  -> EQUAL LOWS
    (122.0, 7),  # Bounce zu LH (trennt SL6 von der Spike-Bar)
    (115.0, 6),  # Pullback — hier wird die Sweep-Spike-Bar injiziert
]
_SWEEP_LEG = 17  # Index in _PIVOTS: die "(115.0, 6)"-Pullback-Phase


def _leg_prices() -> list[float]:
    prices = [_PIVOTS[0][0]]
    for target, n in _PIVOTS[1:]:
        start = prices[-1]
        for k in range(1, n + 1):
            prices.append(round(start + (target - start) * k / n, 4))
    return prices


def build_dataset() -> list[OHLCV]:
    prices = _leg_prices()
    w = 0.5
    rows: list[tuple[float, float, float, float]] = []
    for i, p in enumerate(prices):
        o = (prices[i - 1] + p) / 2 if i > 0 else p  # Mittelpunkt -> saubere Fraktale an den Pivots
        c = p
        rows.append((o, max(o, c) + w, min(o, c) - w, c))

    # --- eine Sweep-Spike-Bar injizieren: Docht unter SL1 (~108), Close zurück darüber ---
    sweep_idx = sum(n for _, n in _PIVOTS[:_SWEEP_LEG]) + 2
    o = rows[sweep_idx][0]
    rows[sweep_idx] = (o, o + 0.4, 107.0, o - 0.2)  # tiefer Docht, Reclaim-Close deutlich über 108

    bars: list[OHLCV] = []
    t = START
    for o, h, low, c in rows:
        bars.append(
            OHLCV(
                instrument="DIAG",
                timeframe=TF,
                open_time=t,
                close_time=bar_close_time(t, TF),
                open=o,
                high=h,
                low=low,
                close=c,
                volume=1.0,
                source="synthetic-diagnostic",
            )
        )
        t += timedelta(seconds=TF.seconds)
    return bars


# ------------------------------------------------------------------------------- Serialisierung


def _enc(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _enc(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list | tuple):
        return [_enc(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _enc(v) for k, v in obj.items()}
    if hasattr(obj, "value"):  # StrEnum
        return obj.value
    return obj


def analyze(bars: list[OHLCV]) -> dict[str, Any]:
    swings = detect_swings(bars, TF)
    breaks = structure_breaks(bars, swings, TF)
    state = derive_structure_state(swings, TF)
    a = atr(bars, 14) or 1.0

    levels = [*swing_levels(swings, TF), *equal_level_clusters(swings, TF, atr=a, tick_size=0.1)]
    level_rows: list[dict[str, Any]] = []
    sweeps: list[dict[str, Any]] = []
    for lvl in levels:
        st, swept_at, sweep = classify_level_state(lvl, bars)
        row = _enc(lvl)
        row["state"] = st.value
        row["swept_at"] = swept_at.isoformat() if swept_at else None
        level_rows.append(row)
        if sweep is not None:
            sweeps.append(_enc(sweep))

    index_by_ts = {b.open_time.isoformat(): i for i, b in enumerate(bars)}
    return {
        "meta": {
            "strategy_version": STRATEGY_VERSION,
            "timeframe": TF.value,
            "n_bars": len(bars),
            "atr14": round(a, 4),
            "structure_state": state.directional.value,
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "bars": [
            {
                "i": i,
                "t": b.open_time.isoformat(),
                "o": b.open,
                "h": b.high,
                "l": b.low,
                "c": b.close,
            }
            for i, b in enumerate(bars)
        ],
        "index_by_ts": index_by_ts,
        "swings": [_enc(s) for s in swings],
        "breaks": [_enc(b) for b in breaks],
        "levels": level_rows,
        "sweeps": sweeps,
        "counts": {
            "swings": len(swings),
            "hh": sum(1 for s in swings if s.label and s.label.value == "hh"),
            "hl": sum(1 for s in swings if s.label and s.label.value == "hl"),
            "lh": sum(1 for s in swings if s.label and s.label.value == "lh"),
            "ll": sum(1 for s in swings if s.label and s.label.value == "ll"),
            "bos": sum(1 for b in breaks if b.kind.value == "bos"),
            "choch": sum(1 for b in breaks if b.kind.value == "choch"),
            "levels": len(level_rows),
            "equal_highs": sum(1 for r in level_rows if r["type"] == "equal_highs"),
            "equal_lows": sum(1 for r in level_rows if r["type"] == "equal_lows"),
            "sweeps": len(sweeps),
        },
    }


# ------------------------------------------------------------------------------------- HTML

_HTML = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Strategy Primitives — Diagnose</title>
<style>
  :root { color-scheme: light dark; --bg:#0f1115; --fg:#e7e9ee; --muted:#9aa3b2;
          --grid:#232833; --up:#26a269; --down:#e01b24; --card:#161a22; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
  header { padding:12px 16px; border-bottom:1px solid var(--grid); }
  header h1 { margin:0; font-size:15px; }
  header .sub { color:var(--muted); margin-top:4px; }
  .wrap { display:flex; gap:0; align-items:flex-start; }
  .chartbox { overflow-x:auto; overflow-y:hidden; flex:1; padding:8px 0 16px; }
  aside { width:360px; min-width:360px; border-left:1px solid var(--grid);
          height:calc(100vh - 62px); overflow-y:auto; padding:12px 14px; }
  aside h2 { font-size:12px; text-transform:uppercase; letter-spacing:.06em;
             color:var(--muted); margin:16px 0 6px; }
  aside h2:first-child { margin-top:0; }
  .row { padding:4px 0; border-bottom:1px solid var(--grid); white-space:pre-wrap; word-break:break-word; }
  .pill { display:inline-block; padding:1px 6px; border-radius:10px; font-size:11px; margin-right:6px; }
  .legend { display:flex; flex-wrap:wrap; gap:10px 16px; padding:8px 16px; border-bottom:1px solid var(--grid); }
  .legend span { display:flex; align-items:center; gap:6px; color:var(--muted); }
  .sw-dot { width:10px; height:10px; display:inline-block; }
  .counts { padding:8px 16px; color:var(--muted); border-bottom:1px solid var(--grid); }
  .counts b { color:var(--fg); }
  text { font:10px ui-monospace,monospace; fill:var(--fg); }
  .disclaimer { padding:10px 16px; color:var(--muted); font-size:12px; }
</style>
</head>
<body>
<header>
  <h1>Strategy Primitives — Diagnose-Ansicht (READ-ONLY)</h1>
  <div class="sub" id="meta"></div>
</header>
<div class="counts" id="counts"></div>
<div class="legend">
  <span><i class="sw-dot" style="background:#26a269"></i>Bull-Kerze</span>
  <span><i class="sw-dot" style="background:#e01b24"></i>Bear-Kerze</span>
  <span><i class="sw-dot" style="background:#4db8ff"></i>Swing High / Low</span>
  <span><i class="sw-dot" style="background:#ffd166"></i>BOS</span>
  <span><i class="sw-dot" style="background:#c77dff"></i>CHoCH</span>
  <span><i class="sw-dot" style="background:#7ee787"></i>Liquidity Level</span>
  <span><i class="sw-dot" style="background:#f2a5c0"></i>Equal High / Low</span>
  <span><i class="sw-dot" style="background:#ff7b00"></i>Liquidity Sweep</span>
</div>
<div class="wrap">
  <div class="chartbox"><svg id="chart"></svg></div>
  <aside id="panel"></aside>
</div>
<div class="disclaimer">
  Fester synthetischer Datensatz (Instrument „DIAG", <span id="tf"></span>). Ein-Timeframe-Diagnose;
  die echte Engine kombiniert D1/H4/M15/M5. Diese Seite ruft ausschließlich die vorhandenen
  Detektoren <code>strategy.primitives.{swings,structure,liquidity}</code> auf — keine eigene
  Analyse-Logik, keine Live-Daten, keine Orders.
</div>
<script>
const DATA = /*DATA*/;
const bars = DATA.bars, idxByTs = DATA.index_by_ts;
const CW = 11, PAD = 46, RIGHT = 150, H = 660;
const W = bars.length * CW + RIGHT;
let pmin = Math.min(...bars.map(b => b.l)), pmax = Math.max(...bars.map(b => b.h));
const span = pmax - pmin || 1; pmin -= span * 0.08; pmax += span * 0.08;
const X = i => PAD + i * CW + CW / 2;
const Y = p => 20 + (pmax - p) / (pmax - pmin) * (H - 40);
const svg = document.getElementById('chart');
svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
svg.setAttribute('width', W); svg.setAttribute('height', H);
const NS = 'http://www.w3.org/2000/svg';
const el = (n, a, txt) => { const e = document.createElementNS(NS, n);
  for (const k in a) e.setAttribute(k, a[k]); if (txt != null) e.textContent = txt; svg.appendChild(e); return e; };

// grid
for (let g = 0; g <= 5; g++) { const p = pmin + (pmax - pmin) * g / 5;
  el('line', {x1: PAD, y1: Y(p), x2: W - RIGHT + 40, y2: Y(p), stroke: '#232833'});
  el('text', {x: 4, y: Y(p) + 3, fill: '#9aa3b2'}, p.toFixed(1)); }

// candles
for (const b of bars) {
  const up = b.c >= b.o, col = up ? '#26a269' : '#e01b24';
  el('line', {x1: X(b.i), y1: Y(b.h), x2: X(b.i), y2: Y(b.l), stroke: col});
  const y1 = Y(Math.max(b.o, b.c)), y2 = Y(Math.min(b.o, b.c));
  el('rect', {x: X(b.i) - 3.2, y: y1, width: 6.4, height: Math.max(1, y2 - y1),
              fill: col, opacity: .92});
}

const at = ts => idxByTs[ts] ?? null;

// liquidity levels
for (const L of DATA.levels) {
  const i0 = at(L.formed_at); if (i0 == null) continue;
  const eq = L.type === 'equal_highs' || L.type === 'equal_lows';
  const broken = L.state === 'BROKEN', swept = L.state === 'SWEPT';
  const col = eq ? '#f2a5c0' : '#7ee787';
  el('line', {x1: X(i0), y1: Y(L.price), x2: W - RIGHT + 30, y2: Y(L.price),
    stroke: col, 'stroke-width': eq ? 1.8 : 1,
    'stroke-dasharray': broken ? '2 4' : (swept ? '6 3' : '1 3'),
    opacity: broken ? .4 : .85});
  el('text', {x: W - RIGHT + 34, y: Y(L.price) + 3, fill: col},
     `${L.type} @${L.price.toFixed(1)} ${L.state}`);
  for (const m of (L.members || [])) { const mi = at(m.timestamp);
    if (mi != null) el('circle', {cx: X(mi), cy: Y(m.price), r: 3, fill: col}); }
}

// structure breaks
for (const B of DATA.breaks) {
  const i = at(B.break_bar_timestamp); if (i == null) continue;
  const bos = B.kind === 'bos', col = bos ? '#ffd166' : '#c77dff';
  const dir = B.direction === 'bullish' ? '▲' : '▼';
  el('line', {x1: X(i), y1: 18, x2: X(i), y2: H - 18, stroke: col,
              'stroke-dasharray': '3 3', opacity: .7});
  el('line', {x1: X(i) - CW * 3, y1: Y(B.broken_level_price), x2: X(i) + CW * 2,
              y2: Y(B.broken_level_price), stroke: col, 'stroke-width': 1.4});
  el('text', {x: X(i) + 3, y: 30, fill: col}, `${B.kind.toUpperCase()} ${dir}`);
}

// swings + HH/HL/LH/LL
for (const S of DATA.swings) {
  const i = at(S.timestamp); if (i == null) continue;
  const high = S.type === 'swing_high';
  const y = high ? Y(S.price) - 9 : Y(S.price) + 9;
  const tri = high ? `${X(i)},${y - 6} ${X(i) - 5},${y + 3} ${X(i) + 5},${y + 3}`
                   : `${X(i)},${y + 6} ${X(i) - 5},${y - 3} ${X(i) + 5},${y - 3}`;
  el('polygon', {points: tri, fill: '#4db8ff'});
  if (S.label && S.label !== 'equal')
    el('text', {x: X(i), y: high ? y - 9 : y + 15, fill: '#4db8ff',
                'text-anchor': 'middle'}, S.label.toUpperCase());
  else if (S.label === 'equal')
    el('text', {x: X(i), y: high ? y - 9 : y + 15, fill: '#f2a5c0',
                'text-anchor': 'middle'}, 'EQ');
}

// sweeps
for (const W_ of DATA.sweeps) {
  const pi = at(W_.penetration_bar), ri = at(W_.reclaim_bar);
  if (pi == null) continue;
  el('circle', {cx: X(pi), cy: Y(W_.penetration_extreme), r: 5, fill: 'none',
                stroke: '#ff7b00', 'stroke-width': 2});
  el('line', {x1: X(pi), y1: Y(W_.penetration_extreme), x2: X(ri ?? pi),
              y2: Y(W_.reclaim_close), stroke: '#ff7b00', 'stroke-width': 2,
              'marker-end': 'url(#arr)'});
  el('text', {x: X(pi), y: Y(W_.penetration_extreme) + 18, fill: '#ff7b00',
              'text-anchor': 'middle'}, `SWEEP ${W_.side}`);
}
const defs = document.createElementNS(NS, 'defs');
defs.innerHTML = '<marker id="arr" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto">' +
                 '<path d="M0,0 L6,3 L0,6 Z" fill="#ff7b00"/></marker>';
svg.insertBefore(defs, svg.firstChild);

// meta + counts + panel
const m = DATA.meta;
document.getElementById('meta').textContent =
  `strategy_version ${m.strategy_version} · ${m.n_bars} Bars · ATR14 ${m.atr14} · ` +
  `Struktur: ${m.structure_state} · erzeugt ${m.generated_at}`;
document.getElementById('tf').textContent = m.timeframe;
const c = DATA.counts;
document.getElementById('counts').innerHTML =
  `<b>${c.swings}</b> Swings (HH ${c.hh} / HL ${c.hl} / LH ${c.lh} / LL ${c.ll}) &nbsp;·&nbsp; ` +
  `<b>${c.bos}</b> BOS &nbsp;·&nbsp; <b>${c.choch}</b> CHoCH &nbsp;·&nbsp; ` +
  `<b>${c.levels}</b> Liquidity Levels (Equal Highs ${c.equal_highs} / Equal Lows ${c.equal_lows}) ` +
  `&nbsp;·&nbsp; <b>${c.sweeps}</b> Sweeps`;

const panel = document.getElementById('panel');
const sec = (title, rows) => {
  panel.insertAdjacentHTML('beforeend', `<h2>${title} (${rows.length})</h2>`);
  for (const r of rows) panel.insertAdjacentHTML('beforeend', `<div class="row">${r}</div>`);
};
sec('Swings', DATA.swings.map(s =>
  `${(s.label || 'swing').toUpperCase()}  ${s.type}  @ ${s.price.toFixed(2)}  ` +
  `bar#${idxByTs[s.timestamp]}  conf ${s.confirmed_at.slice(11, 16)}  leg ${s.leg_size_atr.toFixed(2)}ATR`));
sec('Structure Breaks', DATA.breaks.map(b =>
  `${b.kind.toUpperCase()} ${b.direction}  broke ${b.broken_level_price.toFixed(2)}  ` +
  `@bar#${idxByTs[b.break_bar_timestamp]}  dist ${b.break_distance_atr.toFixed(2)}ATR` +
  (b.prior_state ? `  prior ${b.prior_state}` : '')));
sec('Liquidity Levels', DATA.levels.map(L =>
  `${L.type}  ${L.side}  @ ${L.price.toFixed(2)}  ${L.state}  ` +
  `strength ${L.strength.toFixed(2)}  touches ${L.touch_count}` +
  (L.members && L.members.length ? `  (${L.members.length} members)` : '')));
sec('Liquidity Sweeps', DATA.sweeps.map(s =>
  `${s.side}  level @ ${s.level.price.toFixed(2)}  pen ${s.penetration_extreme.toFixed(2)} ` +
  `(${s.penetration_depth_atr.toFixed(2)}ATR)  reclaim ${s.reclaim_close.toFixed(2)}  ` +
  `${s.bars_to_reclaim} bars  wick ${s.wick_ratio.toFixed(1)}`));
</script>
</body>
</html>
"""


def render_html(payload: dict[str, Any]) -> str:
    return _HTML.replace("/*DATA*/", json.dumps(payload, separators=(",", ":")))


def main() -> int:
    bars = build_dataset()
    payload = analyze(bars)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render_html(payload), encoding="utf-8")
    c = payload["counts"]
    print(f"wrote {OUT}")
    print(
        f"  swings={c['swings']} (HH{c['hh']}/HL{c['hl']}/LH{c['lh']}/LL{c['ll']})  "
        f"BOS={c['bos']}  CHoCH={c['choch']}  levels={c['levels']}  "
        f"equal_highs={c['equal_highs']}  equal_lows={c['equal_lows']}  sweeps={c['sweeps']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
