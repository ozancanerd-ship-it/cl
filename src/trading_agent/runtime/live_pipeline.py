"""Live-Data-Pipeline — **read-only public market data → Paper-Position**. Kein Broker, keine Keys.

```
Kraken / Bybit  (REST warmup + WebSocket M5)
        │  BarAggregator (Trades → confirmed M5), REST-Poller als Fallback
        ▼
  confirmed M5 bar  ──► Data-Quality  ──► rolling M5-Store (+ backfilled M15/H4/D1)
        ▼
  MarketContext (information_cutoff = bar.close_time, keine Zukunftsdaten)
        ▼
  PaperLiveRunner.feed()  ──►  MTF-Context → evaluate() → Decision
        ▼                        → Dynamic Signal (Revision) → Alert → Paper Position
  EventBus:  BarClosed · DecisionMade · SignalRevised · AlertRaised · PaperPositionChanged
```

**Garantien:** nur ``fetch_*`` / public WS; ``orders_sent`` bleibt hart 0; jeder ``MarketContext``
trägt ``information_cutoff = close_time`` der auslösenden Bar (der Konstruktor wirft bei jeder
Bar/News aus der Zukunft); nicht unterstützte Feeds ⇒ das Feld bleibt leer / ``DEGRADED``,
**nichts wird simuliert**.
"""

from __future__ import annotations

import asyncio
import bisect
import contextlib
import dataclasses
import logging
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from trading_agent.core.enums import AssetClass, Timeframe
from trading_agent.core.models import OHLCV, Quote
from trading_agent.core.time import ensure_utc
from trading_agent.core.types import DerivativesContext, MarketContext, NewsContext
from trading_agent.data.providers.exchange_ws import (
    BinanceWSSource,
    BybitWSSource,
    KrakenWSSource,
)
from trading_agent.data.quality import check_ohlcv_series, sort_ohlcv
from trading_agent.data.resample import resample_ohlcv
from trading_agent.refdata.models import SessionSpec
from trading_agent.runtime.bus import EventBus
from trading_agent.runtime.events import (
    AlertRaised,
    BarClosed,
    DataQualityAlert,
    DecisionMade,
    PaperPositionChanged,
    QuoteUpdate,
    SignalRevised,
)
from trading_agent.strategy.engine import EngineParams
from trading_agent.strategy.evaluate import EvaluateParams
from trading_agent.strategy.paper_live import PaperLiveRunner, PaperLiveStep

_log = logging.getLogger("trading_agent.runtime.live")

_HIGHER: tuple[Timeframe, ...] = (Timeframe.M15, Timeframe.H1, Timeframe.H4, Timeframe.D1)


class RestMarketData(Protocol):
    """Was die Pipeline vom public REST-Adapter braucht (Kraken / Bybit erfüllen es)."""

    async def fetch_ohlcv(
        self, instrument: str, timeframe: Timeframe, start: datetime, end: datetime
    ) -> list[OHLCV]: ...

    async def fetch_quote(self, instrument: str) -> Quote: ...

    async def aclose(self) -> None: ...


def _default_higher_warmup() -> dict[Timeframe, int]:
    return {Timeframe.M15: 450, Timeframe.H1: 400, Timeframe.H4: 300, Timeframe.D1: 220}


def _default_higher_rest_every() -> dict[Timeframe, int]:
    # REST-Refresh-Kadenz je höherer TF in M5-Bars. M15 wird per Default aus dem M5-Strom
    # **rollierend resampelt** (kein REST); H4/D1 ändern sich langsam ⇒ selten nachladen.
    return {Timeframe.M15: 3, Timeframe.H1: 12, Timeframe.H4: 48, Timeframe.D1: 288}


@dataclasses.dataclass(frozen=True, slots=True)
class LivePipelineConfig:
    exchange: str  # "kraken" | "bybit"
    instruments: tuple[str, ...]
    asset_class: AssetClass = AssetClass.CRYPTO
    base_timeframe: Timeframe = Timeframe.M5
    m5_warmup: int = 400
    higher_warmup: dict[Timeframe, int] = dataclasses.field(default_factory=_default_higher_warmup)
    m5_window: int = 400  # so viele M5-Bars gehen in den MarketContext
    m5_store_bars: int = 1600  # rollierender M5-Puffer (für M15-Resample; ≈ 5.5 Tage)
    resample_m15_from_m5: bool = True  # M15 aus dem M5-Strom ableiten statt REST
    news_gate: bool = False  # False = Research-Modus (V4-Fail-safe aus); True = live-fail-safe
    higher_rest_every: dict[Timeframe, int] = dataclasses.field(
        default_factory=_default_higher_rest_every
    )
    rest_poll_seconds: float = 45.0  # REST-Fallback-Intervall
    stale_after_seconds: float = 420.0  # keine neue M5-Bar seit … ⇒ DEGRADED
    derivatives: bool = False  # Bybit Funding/OI in den DerivativesContext (nur wenn REST valide)
    derivatives_refresh_every: int = 12  # Funding/OI alle N M5-Bars aktualisieren
    ws_max_reconnects: int = 240  # WS-interne Reconnects vor Aufgabe (Supervisor startet dann neu)
    # Nicht-24/7-Assets (Gold/FX/Aktien): Liquiditätsfenster, die die Engine als
    # Session-Filter nutzt. Leer = keine Session-Restriktion (Crypto). Für XAUUSD/FX aus
    # ``refdata.seed.seed_sessions()`` befüllen.
    session_specs: tuple[SessionSpec, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class InstrumentState:
    instrument: str
    last_bar_close: datetime | None
    m5_bars: int
    last_close_price: float | None
    last_quote: Quote | None
    decisions: int
    signals: int
    alerts: int
    paper_events: int
    open_positions: int
    stale: bool
    quality_blocks: int


@dataclasses.dataclass(frozen=True, slots=True)
class InstrumentHealth:
    instrument: str
    stale: bool
    last_bar_close: datetime | None
    m5_bars: int
    higher_tf: dict[str, int]
    open_positions: int
    feed_errors: int
    dup_bars: int
    qblocks: int
    rest_backfills: int


@dataclasses.dataclass(frozen=True, slots=True)
class PipelineHealth:
    ws_restarts: int
    any_stale: bool
    orders_sent: int
    feed_errors_total: int
    instruments: dict[str, InstrumentHealth]


class LivePipeline:
    """Ein Prozess, mehrere Instrumente. ``run(max_bars=, max_seconds=)`` fährt bis Limit/Stop."""

    def __init__(
        self,
        cfg: LivePipelineConfig,
        *,
        rest_provider: RestMarketData,
        bus: EventBus | None = None,
        engine_params: EngineParams | None = None,
        ws_factory: Callable[[], KrakenWSSource | BybitWSSource | BinanceWSSource] | None = None,
    ) -> None:
        self.cfg = cfg
        self._rest = rest_provider
        self._ws_factory = ws_factory
        self.bus = bus or EventBus(raise_on_handler_error=False)
        ep = engine_params or EngineParams(evaluate=EvaluateParams(asset_class=cfg.asset_class))
        if not cfg.news_gate:
            ev = dataclasses.replace(
                ep.evaluate,
                no_trade=dataclasses.replace(ep.evaluate.no_trade, require_news_feed=False),
                veto=dataclasses.replace(ep.evaluate.veto, require_news_feed=False),
            )
            ep = dataclasses.replace(ep, evaluate=ev)
        self._runners = {s: PaperLiveRunner(engine_params=ep) for s in cfg.instruments}

        store_cap = max(cfg.m5_store_bars, cfg.m5_window, cfg.m5_warmup) + 8
        self._m5: dict[str, deque[OHLCV]] = {s: deque(maxlen=store_cap) for s in cfg.instruments}
        self._higher: dict[str, dict[Timeframe, tuple[OHLCV, ...]]] = {
            s: {} for s in cfg.instruments
        }
        self._last_open: dict[str, datetime | None] = {s: None for s in cfg.instruments}
        self._fed_opens: dict[str, set[datetime]] = {s: set() for s in cfg.instruments}
        self._quote: dict[str, Quote | None] = {s: None for s in cfg.instruments}
        self._derivatives: dict[str, DerivativesContext] = {
            s: DerivativesContext() for s in cfg.instruments
        }
        self._bars_since_refresh: dict[str, int] = {s: 0 for s in cfg.instruments}

        self._counts = {
            s: {
                "decisions": 0,
                "signals": 0,
                "alerts": 0,
                "paper": 0,
                "qblocks": 0,
                "feed_errors": 0,
                "rest_backfills": 0,
                "dup_bars": 0,
            }
            for s in cfg.instruments
        }
        self._last_fed_cutoff: dict[str, datetime] = {}
        self.steps: list[PaperLiveStep] = []
        self.orders_sent = 0  # invariant: bleibt 0
        self._stopped = False
        self._ws: KrakenWSSource | BybitWSSource | BinanceWSSource | None = None
        self._warm = False
        self._started_at: datetime | None = None
        self._recovered = False
        self.ws_restarts = 0  # Supervisor-Ebene: wie oft der WS-Stream neu gestartet wurde

    # ------------------------------------------------------------------ warmup
    async def warmup(self, *, preserve_last_open: bool = False) -> dict[str, str]:
        """``preserve_last_open=True`` (nach Recovery): den M5-Puffer + höhere TFs füllen, aber
        ``_last_open`` NICHT über den aus dem Snapshot wiederhergestellten Stand hinaus
        vorschieben — sonst würde ``_backfill_gap`` die Lücken-Bars als „veraltet" verwerfen,
        statt sie einzeln durch die Pipeline zu fahren."""
        report: dict[str, str] = {}
        end = datetime.now(UTC)
        base = self.cfg.base_timeframe
        for s in self.cfg.instruments:
            m5_from = end - timedelta(seconds=base.seconds * (self.cfg.m5_warmup + 2))
            m5 = await self._rest.fetch_ohlcv(s, base, m5_from, end)
            m5 = [b for b in sort_ohlcv(list(m5)) if b.close_time <= end]
            if not m5:
                report[s] = "BLOCKED: keine M5-Warmup-Bars vom REST"
                continue
            prev_open = self._last_open[s]
            if preserve_last_open and prev_open is not None:
                # nur Historie BIS zum Recovery-Stand laden; die Lücke danach füllt der
                # Supervisor-Backfill Bar für Bar durch die volle Pipeline.
                m5 = [b for b in m5 if b.open_time <= prev_open]
                if not m5:
                    report[s] = "OK (recovery): kein neuer Warmup nötig"
                    continue
            cap = self._m5[s].maxlen or len(m5)
            self._m5[s].clear()
            self._m5[s].extend(m5[-cap:])
            if not preserve_last_open and (prev_open is None or m5[-1].open_time > prev_open):
                self._last_open[s] = m5[-1].open_time
            await self._refresh_higher(s, end, force_rest=True)
            with contextlib.suppress(Exception):
                self._quote[s] = await self._rest.fetch_quote(s)
                q = self._quote[s]
                if q is not None:
                    await self.bus.publish(QuoteUpdate(ts=q.ts, instrument=s, quote=q))
            await self._maybe_refresh_derivatives(s, end)
            report[s] = f"OK: M5={len(self._m5[s])} " + " ".join(
                f"{tf.value}={len(self._higher[s].get(tf, ()))}" for tf in _HIGHER
            )
        self._warm = True
        self._started_at = self._started_at or datetime.now(UTC)
        return report

    async def _refresh_higher(
        self,
        instrument: str,
        end: datetime,
        *,
        only: tuple[Timeframe, ...] = _HIGHER,
        force_rest: bool = False,
    ) -> None:
        """M15 wird (Default) aus dem rollierenden M5-Puffer **resampelt** — kein REST. H4/D1
        kommen per REST (langsam veränderlich); reicht der REST-Verlauf nicht, wird aus M5
        abgeleitet. Kein Fake: leere Antwort ⇒ Feld bleibt, was es war.

        ``force_rest=True`` (Warmup): jede höhere TF einmalig per REST füllen. Danach wird M15
        nur noch aus dem M5-Strom **resampelt** und mit der bestehenden (per REST geholten)
        M15-Historie **verschmolzen** — kein weiterer REST-Call für M15."""
        m5_store = list(self._m5[instrument])
        for tf in only:
            need = self.cfg.higher_warmup.get(tf, 200)
            existing = self._higher[instrument].get(tf, ())
            rolling_m15 = (
                tf is Timeframe.M15
                and self.cfg.resample_m15_from_m5
                and not force_rest
                and len(existing) >= 3
            )
            if rolling_m15:
                recent = resample_ohlcv(
                    m5_store, self.cfg.base_timeframe, tf, require_complete=True, horizon=end
                )
                if recent:
                    ex_opens = [b.open_time for b in existing]
                    i = bisect.bisect_left(ex_opens, recent[0].open_time)
                    head = list(existing[:i])
                    step = tf.seconds
                    if head and (recent[0].open_time - head[-1].open_time).total_seconds() == step:
                        merged = head + list(recent)  # lückenlos verschmolzen
                    elif len(recent) >= need // 2:
                        merged = list(recent)  # Resample tief genug → allein nutzen
                    else:
                        continue  # noch nicht sauber mergebar → REST-M15 behalten
                    self._higher[instrument][tf] = tuple(merged[-(need + 20) :])
                continue

            frm = end - timedelta(seconds=tf.seconds * (need + 2))
            clean: list[OHLCV] = []
            try:
                bars = await self._rest.fetch_ohlcv(instrument, tf, frm, end)
                clean = [b for b in sort_ohlcv(list(bars)) if b.close_time <= end]
            except Exception as exc:
                _log.warning("higher tf fetch failed", extra={"tf": tf.value, "err": str(exc)})
            if len(clean) < 3:  # REST fehlt/kennt die TF nicht ⇒ aus M5 ableiten (kein Fake)
                clean = resample_ohlcv(
                    m5_store, self.cfg.base_timeframe, tf, require_complete=True, horizon=end
                )
            if clean and len(clean) >= max(3, len(existing) // 4):
                self._higher[instrument][tf] = tuple(clean)

    async def _maybe_refresh_derivatives(self, instrument: str, cutoff: datetime) -> None:
        """Bybit-only: Funding + Open Interest in den ``DerivativesContext`` — **nur** wenn die
        REST-Endpunkte valide, aktuelle Daten liefern. Sonst bleibt der Kontext leer (kein Fake)."""
        if not self.cfg.derivatives or self.cfg.exchange not in (
            "bybit",
            "binance",
            "binance_futures",
        ):
            return
        fund = getattr(self._rest, "fetch_funding", None)
        oi = getattr(self._rest, "fetch_open_interest", None)
        if fund is None or oi is None:
            return
        rate: float | None = None
        rate_ts: datetime | None = None
        oi_val: float | None = None
        oi_ts: datetime | None = None
        with contextlib.suppress(Exception):
            rows = await fund(instrument, cutoff - timedelta(days=3), cutoff)
            rows = [r for r in rows if ensure_utc(r.ts) <= cutoff]
            if rows:
                rate, rate_ts = float(rows[-1].rate), ensure_utc(rows[-1].ts)
        with contextlib.suppress(Exception):
            rows2 = await oi(instrument, cutoff - timedelta(days=1), cutoff)
            rows2 = [r for r in rows2 if ensure_utc(r.ts) <= cutoff]
            if rows2:
                oi_val, oi_ts = float(rows2[-1].oi), ensure_utc(rows2[-1].ts)
                if len(rows2) >= 2 and rows2[0].oi:
                    prev = float(rows2[0].oi)
                    oi_val_delta = (oi_val - prev) / prev * 100.0
                else:
                    oi_val_delta = None
            else:
                oi_val_delta = None
        if rate is None and oi_val is None:
            return  # nichts Valides ⇒ Kontext unverändert
        self._derivatives[instrument] = DerivativesContext(
            funding_rate=rate,
            funding_rate_as_of=rate_ts,
            open_interest=oi_val,
            open_interest_as_of=oi_ts,
            open_interest_delta_pct=oi_val_delta,
        )

    # ------------------------------------------------------------------ context + feed
    def _build_context(self, instrument: str, cutoff: datetime) -> MarketContext:
        cutoff = ensure_utc(cutoff)
        m5 = [b for b in self._m5[instrument] if b.close_time <= cutoff][-self.cfg.m5_window :]
        series: dict[Timeframe, tuple[OHLCV, ...]] = {self.cfg.base_timeframe: tuple(m5)}
        for tf, bars in self._higher[instrument].items():
            keep = tuple(b for b in bars if b.close_time <= cutoff)
            if keep:
                series[tf] = keep
        q = self._quote.get(instrument)
        # Spread nur nutzen, wenn die Quote nicht aus der Zukunft und nicht zu alt ist.
        spread = (
            q.spread
            if q is not None and abs((ensure_utc(q.ts) - cutoff).total_seconds()) <= 900
            else None
        )
        deriv = self._derivatives.get(instrument, DerivativesContext())
        if deriv.funding_rate_as_of is not None and ensure_utc(deriv.funding_rate_as_of) > cutoff:
            deriv = DerivativesContext()  # PIT: nichts aus der Zukunft
        return MarketContext(
            instrument=instrument,
            base_timeframe=self.cfg.base_timeframe,
            information_cutoff=cutoff,
            series=series,
            spread=spread,
            derivatives=deriv,
            news=NewsContext(feed_as_of=cutoff) if self.cfg.news_gate else NewsContext(),
        )

    async def feed_confirmed_bar(self, bar: OHLCV, *, source: str = "external") -> None:
        """Öffentlicher Einstieg für eine bereits **confirmed** M5-Bar (Tests / alternative
        Quellen). Läuft durch dieselbe Data-Quality + Dedupe + Pipeline wie der WS-Pfad."""
        await self._on_confirmed_bar(bar, source=source)

    async def _on_confirmed_bar(self, bar: OHLCV, *, source: str) -> None:
        s = bar.instrument.upper()
        if s not in self._m5:
            return
        # Schutz gegen eine noch formende Bar: eine echte confirmed M5-Bar ist beim Empfang
        # höchstens Sekunden alt — eine, deren close_time > 1 volles Intervall in der Zukunft
        # liegt, ist noch offen. (Der WS-Aggregator emittiert eigentlich nur confirmed Bars;
        # das hier ist Defense-in-Depth.)
        if bar.close_time > datetime.now(UTC) + timedelta(seconds=self.cfg.base_timeframe.seconds):
            return
        prev = self._last_open[s]
        if bar.open_time in self._fed_opens[s] or (prev is not None and bar.open_time <= prev):
            self._counts[s]["dup_bars"] += 1
            _log.debug(
                "duplicate/stale live bar ignored",
                extra={
                    "instrument": s,
                    "open": bar.open_time.isoformat(),
                    "prev": prev.isoformat() if prev else None,
                    "source": source,
                },
            )
            return  # Duplikat / veraltet
        # Data-Quality auf dem rollenden Fenster
        window = [*list(self._m5[s])[-59:], bar]
        q = check_ohlcv_series(window, instrument=s, timeframe=bar.timeframe, now=bar.close_time)
        if q.blocks_trading:
            self._counts[s]["qblocks"] += 1
            await self.bus.publish(
                DataQualityAlert(ts=bar.close_time, instrument=s, timeframe=bar.timeframe, status=q)
            )
            _log.warning("live bar blocked by data quality", extra={"instrument": s})
            return

        self._m5[s].append(bar)
        self._last_open[s] = bar.open_time
        self._fed_opens[s].add(bar.open_time)
        if len(self._fed_opens[s]) > 500:
            self._fed_opens[s] = {
                t for t in self._fed_opens[s] if t >= bar.open_time - timedelta(days=2)
            }
        self._bars_since_refresh[s] += 1
        n = self._bars_since_refresh[s]
        _log.info(
            "confirmed live bar",
            extra={
                "instrument": s,
                "source": source,
                "open": bar.open_time.isoformat(),
                "close": bar.close_time.isoformat(),
                "px": bar.close,
            },
        )
        await self.bus.publish(
            BarClosed(ts=bar.close_time, instrument=s, timeframe=bar.timeframe, bar=bar)
        )

        # höhere TFs je nach Kadenz auffrischen (M15 rollierend aus M5 = billig; H4/D1 selten)
        due = tuple(tf for tf in _HIGHER if n % max(1, self.cfg.higher_rest_every.get(tf, 12)) == 0)
        if due:
            await self._refresh_higher(s, bar.close_time, only=due)
        if n % max(1, self.cfg.derivatives_refresh_every) == 0:
            with contextlib.suppress(Exception):
                self._quote[s] = await self._rest.fetch_quote(s)
            await self._maybe_refresh_derivatives(s, bar.close_time)

        await self._feed(s, bar.close_time, trigger=source)

    async def _feed(self, instrument: str, cutoff: datetime, *, trigger: str) -> None:
        cutoff = ensure_utc(cutoff)
        # genau EIN feed()/Decision je cutoff & Instrument — schützt gegen prime nach Backfill,
        # WS-Doppelemission u. ä. (der prime-Durchlauf nach Recovery liegt oft auf demselben
        # cutoff wie die letzte gebackfillte Bar).
        prev_cut = self._last_fed_cutoff.get(instrument)
        if prev_cut is not None and cutoff <= prev_cut and trigger == "prime":
            return
        self._last_fed_cutoff[instrument] = max(cutoff, prev_cut) if prev_cut else cutoff
        # Fehler-Isolation: ein Fehler in der Pipeline eines Instruments darf den Daemon
        # (und die anderen Instrumente) nicht abreißen. Er wird protokolliert + gezählt.
        try:
            mc = self._build_context(instrument, cutoff)
            step = self._runners[instrument].feed(mc, session_specs=self.cfg.session_specs)
        except Exception:
            self._counts[instrument]["feed_errors"] += 1
            _log.exception(
                "pipeline feed failed — isoliert",
                extra={"instrument": instrument, "cutoff": cutoff.isoformat(), "trigger": trigger},
            )
            return
        self.steps.append(step)
        if len(self.steps) > 5000:
            self.steps = self.steps[-2000:]
        await self._emit_step(instrument, step, trigger)
        self.orders_sent = 0  # Invariante: kein Broker, keine Order

    async def _emit_step(self, instrument: str, step: PaperLiveStep, trigger: str) -> None:
        tick = step.tick
        d = tick.result.decision
        self._counts[instrument]["decisions"] += 1
        await self.bus.publish(
            DecisionMade(
                ts=step.at,
                instrument=instrument,
                decision_type=d.decision.value,
                setup_state=d.setup_state.value,
                score=d.score,
                confidence=d.confidence,
                result=tick.result,
            )
        )
        if tick.signal is not None and (tick.signal.is_new or tick.signal.changed):
            self._counts[instrument]["signals"] += 1
            sig = tick.signal.signal
            await self.bus.publish(
                SignalRevised(
                    ts=step.at,
                    instrument=instrument,
                    signal_id=sig.signal_id,
                    state=sig.state.value,
                    change="new" if tick.signal.is_new else "revised",
                    signal=tick.signal,
                )
            )
        for ae in step.alerts:
            if not ae.delivered:
                continue
            self._counts[instrument]["alerts"] += 1
            await self.bus.publish(
                AlertRaised(
                    ts=step.at,
                    instrument=instrument,
                    alert_type=ae.alert.type.value,
                    message=ae.alert.title,
                    delivered=True,
                    alert=ae,
                )
            )
        for pos, change in ((tick.opened, "OPENED"), (tick.closed, "CLOSED")):
            if pos is not None:
                self._counts[instrument]["paper"] += 1
                await self.bus.publish(
                    PaperPositionChanged(
                        ts=step.at,
                        instrument=instrument,
                        change=change,
                        realized_r=getattr(pos, "realized_r", None),
                        position=pos,
                    )
                )
        if tick.position is not None and tick.position.changed:
            self._counts[instrument]["paper"] += 1
            await self.bus.publish(
                PaperPositionChanged(
                    ts=step.at,
                    instrument=instrument,
                    change=tick.position.event.value,
                    realized_r=getattr(tick.position.position, "realized_r", None),
                    position=tick.position.position,
                )
            )
        _log.info(
            "live tick",
            extra={
                "instrument": instrument,
                "trigger": trigger,
                "cutoff": step.at.isoformat(),
                "decision": d.decision.value,
                "setup_state": d.setup_state.value,
            },
        )

    # ------------------------------------------------------------------ run
    async def prime(self) -> None:
        """Ein sofortiger ``feed()`` je Instrument auf dem Warmup-Stand (Beweis: Pipeline läuft
        auf echten REST-Daten, ohne 5 min auf die erste WS-Bar zu warten)."""
        for s in self.cfg.instruments:
            m5 = self._m5[s]
            if m5:
                await self._feed(s, m5[-1].close_time, trigger="prime")

    def _new_ws(self) -> KrakenWSSource | BybitWSSource | BinanceWSSource:
        if self._ws_factory is not None:
            return self._ws_factory()
        cls: type[KrakenWSSource | BybitWSSource | BinanceWSSource]
        if self.cfg.exchange == "kraken":
            cls = KrakenWSSource
        elif self.cfg.exchange in ("binance", "binance_futures"):
            cls = BinanceWSSource
        else:
            cls = BybitWSSource
        return cls(
            list(self.cfg.instruments),
            self.cfg.base_timeframe,
            max_reconnects=self.cfg.ws_max_reconnects,
        )

    async def backfill(
        self, instrument: str, since: datetime, until: datetime | None = None
    ) -> int:
        """**abgeschlossene** REST-M5-Bars für ``(since, until]`` nachziehen und einspeisen
        (deduped). Deckt die Lücke nach WS-Stall / Sleep / Neustart, soweit der REST-Verlauf
        zurückreicht. Rückgabe: Anzahl tatsächlich eingespeister Bars."""
        s = instrument.upper()
        until = ensure_utc(until) if until is not None else datetime.now(UTC)
        cut = ensure_utc(since)
        try:
            bars = await self._rest.fetch_ohlcv(s, self.cfg.base_timeframe, cut, until)
        except Exception as exc:
            _log.error("backfill fetch failed", extra={"instrument": s, "err": str(exc)})
            return 0
        fed = 0
        for b in sort_ohlcv(list(bars)):
            # nur wirklich abgeschlossene Bars (die noch formende REST-Zeile hat close_time > now)
            if b.close_time <= until:
                before_open = self._last_open[s]
                await self._on_confirmed_bar(b, source="backfill")
                if self._last_open[s] != before_open:
                    fed += 1
        if fed:
            self._counts[s]["rest_backfills"] += 1
        return fed

    async def run(self, *, max_bars: int | None = None, max_seconds: float | None = None) -> None:
        if not self._warm:
            await self.warmup()
        self._started_at = self._started_at or datetime.now(UTC)
        self._stopped = False
        deadline = datetime.now(UTC) + timedelta(seconds=max_seconds) if max_seconds else None
        seen = 0

        async def _ws_supervised() -> None:
            """WS-Stream mit **automatischem Neustart**: wenn `stream()` aufgibt (WS-interne
            Reconnects erschöpft) und der Daemon noch läuft, wird eine frische Verbindung
            aufgebaut. Nach jedem Neustart deckt der REST-Poll-Loop die entstandene Lücke."""
            nonlocal seen
            backoff = 1.0
            while not self._stopped:
                self._ws = self._new_ws()
                try:
                    async for bar in self._ws.stream():
                        if self._stopped:
                            return
                        await self._on_confirmed_bar(bar, source="ws")
                        seen += 1
                        backoff = 1.0
                        if max_bars is not None and seen >= max_bars:
                            await self.stop()
                            return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _log.exception("ws stream crashed", extra={"exchange": self.cfg.exchange})
                if self._stopped:
                    return
                self.ws_restarts += 1
                _log.warning(
                    "ws stream ended — Neustart",
                    extra={"exchange": self.cfg.exchange, "restart": self.ws_restarts},
                )
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 2)

        async def _rest_poll_loop() -> None:
            while not self._stopped:
                await asyncio.sleep(self.cfg.rest_poll_seconds)
                for s in self.cfg.instruments:
                    last = self._last_open[s]
                    if last is None or not self._m5[s]:
                        continue
                    age = (datetime.now(UTC) - self._m5[s][-1].close_time).total_seconds()
                    if age < self.cfg.stale_after_seconds:
                        continue  # WS liefert — kein Fallback nötig
                    _log.warning(
                        "WS stale → REST backfill", extra={"instrument": s, "age_s": round(age)}
                    )
                    await self.backfill(s, last)

        tasks = [
            asyncio.create_task(_ws_supervised()),
            asyncio.create_task(_rest_poll_loop()),
        ]
        try:
            while not self._stopped:
                await asyncio.sleep(0.5)
                if deadline is not None and datetime.now(UTC) >= deadline:
                    await self.stop()
                if all(t.done() for t in tasks):
                    break
        finally:
            await self.stop()
            for t in tasks:
                t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*tasks, return_exceptions=True)

    async def stop(self) -> None:
        self._stopped = True
        if self._ws is not None:
            await self._ws.stop()

    # ------------------------------------------------------------------ status
    def state(self, instrument: str) -> InstrumentState:
        m5 = self._m5[instrument]
        c = self._counts[instrument]
        runner = self._runners[instrument]
        last = m5[-1] if m5 else None
        stale = (
            last is None
            or (datetime.now(UTC) - last.close_time).total_seconds() > self.cfg.stale_after_seconds
        )
        return InstrumentState(
            instrument=instrument,
            last_bar_close=last.close_time if last else None,
            m5_bars=len(m5),
            last_close_price=last.close if last else None,
            last_quote=self._quote.get(instrument),
            decisions=c["decisions"],
            signals=c["signals"],
            alerts=c["alerts"],
            paper_events=c["paper"],
            open_positions=len(runner.engine.open_positions),
            stale=stale,
            quality_blocks=c["qblocks"],
        )

    @property
    def uptime_seconds(self) -> float:
        return (datetime.now(UTC) - self._started_at).total_seconds() if self._started_at else 0.0

    def summary(self) -> dict[str, object]:
        return {
            "exchange": self.cfg.exchange,
            "news_gate": self.cfg.news_gate,
            "orders_sent": self.orders_sent,
            "total_steps": len(self.steps),
            "uptime_seconds": round(self.uptime_seconds, 1),
            "ws_restarts": self.ws_restarts,
            "recovered": self._recovered,
            "instruments": {s: dataclasses.asdict(self.state(s)) for s in self.cfg.instruments},
        }

    # ------------------------------------------------------------------ health / snapshot (M-01)
    def health_report(self) -> PipelineHealth:
        """Aggregierter Live-Health-Zustand für den Supervisor."""
        per: dict[str, InstrumentHealth] = {}
        any_stale = False
        errs = 0
        for s in self.cfg.instruments:
            st = self.state(s)
            any_stale = any_stale or st.stale
            c = self._counts[s]
            errs += c["feed_errors"]
            per[s] = InstrumentHealth(
                instrument=s,
                stale=st.stale,
                last_bar_close=st.last_bar_close,
                m5_bars=st.m5_bars,
                higher_tf={tf.value: len(self._higher[s].get(tf, ())) for tf in _HIGHER},
                open_positions=st.open_positions,
                feed_errors=c["feed_errors"],
                dup_bars=c["dup_bars"],
                qblocks=c["qblocks"],
                rest_backfills=c["rest_backfills"],
            )
        return PipelineHealth(
            ws_restarts=self.ws_restarts,
            any_stale=any_stale,
            orders_sent=self.orders_sent,
            feed_errors_total=errs,
            instruments=per,
        )

    def snapshot(self) -> dict[str, object]:
        """Serialisierbarer Laufzeit-Zustand für ``state.store.SnapshotStore``."""
        from trading_agent.state.recovery import paper_position_to_dict

        insts: dict[str, object] = {}
        for s in self.cfg.instruments:
            eng = self._runners[s].engine
            last = self._last_open[s]
            insts[s] = {
                "last_open": last.isoformat() if last else None,
                "counts": dict(self._counts[s]),
                "open_positions": [paper_position_to_dict(p) for p in eng.open_positions],
                "seen_fill_bar": eng.seen_fill_bars(),
                "recent_fed_opens": sorted(t.isoformat() for t in self._fed_opens[s])[-60:],
            }
        return {
            "exchange": self.cfg.exchange,
            "instruments_cfg": list(self.cfg.instruments),
            "orders_sent": self.orders_sent,
            "ws_restarts": self.ws_restarts,
            "instruments": insts,
        }

    def restore(self, payload: dict[str, object]) -> dict[str, object]:
        """Zustand aus einem Snapshot wieder einspielen. Rückgabe: was rekonstruiert wurde
        (für den Supervisor-Log + den anschließenden Backfill)."""
        from trading_agent.core.time import parse_timestamp
        from trading_agent.state.recovery import paper_position_from_dict

        info: dict[str, object] = {"instruments": {}}
        if payload.get("exchange") != self.cfg.exchange:
            _log.warning("snapshot exchange mismatch — ignoriert")
            return {"restored": False, "reason": "exchange mismatch"}
        insts = payload.get("instruments", {})
        assert isinstance(insts, dict)
        for s in self.cfg.instruments:
            data = insts.get(s)
            if not isinstance(data, dict):
                continue
            last = data.get("last_open")
            if last:
                lo = ensure_utc(parse_timestamp(last))
                self._last_open[s] = lo
                self._fed_opens[s].add(lo)
                self._last_fed_cutoff[s] = lo + timedelta(seconds=self.cfg.base_timeframe.seconds)
            for iso in data.get("recent_fed_opens", []):
                with contextlib.suppress(Exception):
                    self._fed_opens[s].add(ensure_utc(parse_timestamp(iso)))
            counts = data.get("counts", {})
            if isinstance(counts, dict):
                for k, v in counts.items():
                    if k in self._counts[s] and isinstance(v, int):
                        self._counts[s][k] = v
            positions = {}
            for pd in data.get("open_positions", []):
                pos = paper_position_from_dict(pd)
                if pos is not None:
                    positions[pos.signal_id] = pos
            seen_fill = {
                k: ensure_utc(parse_timestamp(v)) for k, v in data.get("seen_fill_bar", {}).items()
            }
            n = self._runners[s].engine.restore_positions(positions, seen_fill)
            info["instruments"][s] = {  # type: ignore[index]
                "last_open": last,
                "open_positions_restored": n,
            }
        self._recovered = True
        return {"restored": True, **info}


def build_rest_provider(exchange: str) -> RestMarketData:
    """Der passende public REST-Adapter (kein Key)."""
    if exchange == "kraken":
        from trading_agent.data.providers.kraken import KrakenDataProvider

        return KrakenDataProvider()
    if exchange == "bybit":
        from trading_agent.data.providers.bybit_public import BybitPublicDataProvider

        return BybitPublicDataProvider()
    if exchange in ("binance", "binance_futures"):
        from trading_agent.data.providers.binance import BinancePublicDataProvider

        return BinancePublicDataProvider(market="futures_usdm")
    if exchange == "binance_spot":
        from trading_agent.data.providers.binance import BinancePublicDataProvider

        return BinancePublicDataProvider(market="spot")
    raise ValueError(f"unbekannte Exchange: {exchange!r}")


__all__ = [
    "InstrumentState",
    "LivePipeline",
    "LivePipelineConfig",
    "RestMarketData",
    "build_rest_provider",
]
