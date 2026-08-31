"""Deterministischer Replay-Harness — historische Daten laufen **exakt** so durch die Engine
wie später Live-Daten.

```
MarketDataRepository → ReplayClock → MarketContextAssembler → MarketContext
                     → strategy.evaluate() (via PaperLiveRunner) → Signal → Position → Alerts
```

**Point-in-Time-Garantie.** Jeder Replay-Schritt bekommt einen ``cutoff`` (= `close_time` einer
abgeschlossenen M5-Bar). Der ``MarketContextAssembler`` liest **ausschließlich** Daten mit
``as_of = cutoff`` aus dem Repository (`close_time <= cutoff` für Bars, `available_time <= cutoff`
für News/Makro). Der `MarketContext`-Konstruktor wirft zusätzlich bei jeder Bar/News nach dem
`information_cutoff` — doppelter Boden.

**Determinismus.** Kein Wall-Clock, kein RNG. Gleiches Dataset + gleiche Parameter + gleicher
Commit ⇒ bit-genau gleiche `MarketContext`-Folge ⇒ gleiche Decision-Folge.

**Keine Fake-Daten.** Fehlt die geforderte Historie, meldet :func:`validate_dataset` das
eindeutig (`DatasetReport.ok is False` + `missing`), es wird **nichts** synthetisiert.
"""

from __future__ import annotations

import bisect
import dataclasses
import itertools
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta

from trading_agent.analysis.news import build_news_context
from trading_agent.core.enums import AssetClass, Timeframe
from trading_agent.core.models import OHLCV, NewsEvent
from trading_agent.core.time import ensure_utc
from trading_agent.core.types import (
    CrossAssetContext,
    DerivativesContext,
    MarketContext,
    NewsContext,
)
from trading_agent.data.repository import MarketDataRepository

# M15/H4/D1 werden nativ gelesen, wenn im Repo vorhanden — sonst leitet build_mtf_context sie
# aus M5 ab. M1 ist optional (Confirmation-Feed, separater Pfad über strategy.m1_feed).
_HIGHER: tuple[Timeframe, ...] = (Timeframe.M15, Timeframe.H4, Timeframe.D1)


# --------------------------------------------------------------------------------- ReplayClock


class ReplayClock:
    """Deterministischer Schrittgeber über eine **aufsteigend sortierte** Menge von
    ``cutoff``-Zeitpunkten (üblicherweise die `close_time`s der M5-Bars im Testfenster).

    Kein `now()` aus der Wall-Clock — `now()` liefert den aktuellen Replay-Zeitpunkt."""

    def __init__(self, cutoffs: Sequence[datetime]) -> None:
        ordered = sorted(ensure_utc(c) for c in cutoffs)
        for a, b in itertools.pairwise(ordered):
            if a == b:
                raise ValueError("ReplayClock: doppelter cutoff")
        self._cutoffs: tuple[datetime, ...] = tuple(ordered)
        self._i = -1

    @classmethod
    def from_bars(cls, bars: Sequence[OHLCV]) -> ReplayClock:
        return cls([b.close_time for b in bars])

    @classmethod
    def from_range(
        cls, start: datetime, end: datetime, timeframe: Timeframe = Timeframe.M5
    ) -> ReplayClock:
        start, end = ensure_utc(start), ensure_utc(end)
        step = timedelta(seconds=timeframe.seconds)
        out: list[datetime] = []
        t = start + step  # erster cutoff = erste abgeschlossene Bar
        while t <= end:
            out.append(t)
            t += step
        return cls(out)

    def __len__(self) -> int:
        return len(self._cutoffs)

    @property
    def cutoffs(self) -> tuple[datetime, ...]:
        return self._cutoffs

    def now(self) -> datetime:
        if self._i < 0:
            raise RuntimeError("ReplayClock: noch kein Schritt (advance() zuerst)")
        return self._cutoffs[self._i]

    @property
    def exhausted(self) -> bool:
        return self._i >= len(self._cutoffs) - 1

    def advance(self) -> datetime | None:
        if self.exhausted:
            return None
        self._i += 1
        return self._cutoffs[self._i]

    def __iter__(self) -> Iterator[datetime]:
        self._i = -1
        while (nxt := self.advance()) is not None:
            yield nxt


# --------------------------------------------------------------------------------- Dataset-Anforderungen


@dataclasses.dataclass(frozen=True, slots=True)
class DatasetRequirements:
    """Was der Backtest an Historie **braucht**. Siehe `docs/HISTORICAL_DATA_PLAN.md`."""

    instruments: tuple[str, ...]
    base_timeframe: Timeframe = Timeframe.M5
    min_days: int = 180
    warmup_bars: int = 300  # zusätzliche M5-Bars vor `start` für die M5-Analyse
    require_native_higher: bool = False  # True ⇒ M15/H4/D1 müssen im Repo liegen (nicht abgeleitet)
    # wenn require_native_higher: wie viele Bars je höhere TF **vor** `start` verfügbar sein müssen
    higher_min_bars: dict[Timeframe, int] = dataclasses.field(
        default_factory=lambda: {Timeframe.M15: 400, Timeframe.H4: 260, Timeframe.D1: 200}
    )
    require_m1: bool = False  # True ⇒ M1 für die Confirmation muss vorhanden sein
    require_news_feed: bool = False  # True ⇒ es muss ein News-Dataset geben
    check_continuity: bool = True  # interne Lücken in M5 + nativen höheren TFs melden (als notes)
    continuity_tolerance: float = 0.002  # erlaubter Fehlbetrag an M5-Bars (Anteil), bevor gemeldet


@dataclasses.dataclass(frozen=True, slots=True)
class DatasetGap:
    instrument: str
    timeframe: Timeframe
    reason: str


@dataclasses.dataclass(frozen=True, slots=True)
class DatasetReport:
    ok: bool
    checked_at: datetime
    start: datetime
    end: datetime
    covered: dict[str, tuple[datetime, datetime] | None]
    missing: tuple[DatasetGap, ...]
    notes: tuple[str, ...]

    def raise_if_incomplete(self) -> None:
        if not self.ok:
            lines = [f"- {g.instrument}/{g.timeframe.value}: {g.reason}" for g in self.missing]
            raise DatasetIncompleteError(
                "Historische Daten unvollständig — KEINE Daten erfunden. Fehlt:\n"
                + "\n".join(lines)
            )


class DatasetIncompleteError(RuntimeError):
    pass


def validate_dataset(
    repo: MarketDataRepository,
    req: DatasetRequirements,
    *,
    start: datetime,
    end: datetime,
) -> DatasetReport:
    """Prüft **vor** dem Replay, ob das Repository die geforderte Historie hergibt.
    Meldet Lücken eindeutig; synthetisiert nichts."""
    start, end = ensure_utc(start), ensure_utc(end)
    missing: list[DatasetGap] = []
    notes: list[str] = []
    covered: dict[str, tuple[datetime, datetime] | None] = {}

    span_days = (end - start).total_seconds() / 86400.0
    if span_days + 1e-6 < req.min_days:
        notes.append(
            f"Backtest-Fenster {span_days:.1f} T < gefordert {req.min_days} T "
            f"(nur ein Hinweis, kein harter Fehler)."
        )

    warmup_delta = timedelta(seconds=req.base_timeframe.seconds * req.warmup_bars)
    need_from = start - warmup_delta

    for inst in req.instruments:
        cov = repo.ohlcv_coverage(inst, req.base_timeframe)
        covered[f"{inst}/{req.base_timeframe.value}"] = cov
        if cov is None:
            missing.append(DatasetGap(inst, req.base_timeframe, "keine Abdeckung im Repository"))
            continue
        cov_lo, cov_hi = cov
        if cov_lo > need_from:
            missing.append(
                DatasetGap(
                    inst,
                    req.base_timeframe,
                    f"beginnt {cov_lo.isoformat()}, gebraucht ab {need_from.isoformat()} "
                    f"(inkl. {req.warmup_bars} Warmup-Bars)",
                )
            )
        if cov_hi < end - timedelta(seconds=req.base_timeframe.seconds):
            missing.append(
                DatasetGap(
                    inst,
                    req.base_timeframe,
                    f"endet {cov_hi.isoformat()}, gebraucht bis {end.isoformat()}",
                )
            )

        if req.require_native_higher:
            for tf in _HIGHER:
                hcov = repo.ohlcv_coverage(inst, tf)
                if hcov is None:
                    missing.append(DatasetGap(inst, tf, "native höhere Timeframe fehlt"))
                    continue
                need_bars = req.higher_min_bars.get(tf, 200)
                need_h_from = start - timedelta(seconds=tf.seconds * need_bars)
                if hcov[0] > need_h_from:
                    missing.append(
                        DatasetGap(
                            inst,
                            tf,
                            f"beginnt {hcov[0].isoformat()}, gebraucht ab {need_h_from.isoformat()} "
                            f"({need_bars} Bars Vorlauf)",
                        )
                    )
        if req.require_m1 and repo.ohlcv_coverage(inst, Timeframe.M1) is None:
            missing.append(DatasetGap(inst, Timeframe.M1, "M1 für Confirmation fehlt"))

        if req.check_continuity:
            notes.extend(_continuity_notes(repo, inst, req, start, need_from, end))

    if req.require_news_feed:
        try:
            probe = repo.read_news(start, end)
        except Exception:
            probe = []
        if not probe:
            missing.append(
                DatasetGap(
                    "*", req.base_timeframe, "News-Feed gefordert, aber kein Event im Fenster"
                )
            )

    return DatasetReport(
        ok=not missing,
        checked_at=end,
        start=start,
        end=end,
        covered=covered,
        missing=tuple(missing),
        notes=tuple(notes),
    )


def _continuity_notes(
    repo: MarketDataRepository,
    inst: str,
    req: DatasetRequirements,
    start: datetime,
    need_from: datetime,
    end: datetime,
) -> list[str]:
    """Interne Lücken in der Basis-TF + nativen höheren TFs (informativ, kein harter Fehler).

    24/7-agnostisch: prüft nur, ob zwischen zwei aufeinanderfolgenden Bars **mehr** als ein
    Intervall liegt. Für nicht-24/7-Assets erzeugt der Wochenend-Gap hier erwartbar Hinweise —
    darum ``note``, nicht ``missing``.
    """
    out: list[str] = []
    base = req.base_timeframe
    try:
        bars = repo.read_ohlcv(inst, base, need_from, end, as_of=end)
    except Exception:
        return out
    if bars:
        expected = int((end - need_from).total_seconds() // base.seconds)
        have = len(bars)
        if expected > 0 and (expected - have) / expected > req.continuity_tolerance:
            out.append(
                f"{inst}/{base.value}: {have}/{expected} Bars "
                f"({(expected - have)} fehlen, > {req.continuity_tolerance:.1%}) — interne Lücke prüfen"
            )
        worst: tuple[datetime, datetime, int] | None = None
        for a, b in itertools.pairwise(bars):
            missing_bars = int((b.open_time - a.open_time).total_seconds() // base.seconds) - 1
            if missing_bars > 0 and (worst is None or missing_bars > worst[2]):
                worst = (a.open_time, b.open_time, missing_bars)
        if worst is not None:
            out.append(
                f"{inst}/{base.value}: größte Lücke {worst[2]} Bars "
                f"({worst[0].isoformat()} → {worst[1].isoformat()})"
            )

    if req.require_native_higher:
        for tf in _HIGHER:
            # so weit zurück, wie der Assembler diese TF tatsächlich liest (higher_min_bars)
            tf_from = start - timedelta(seconds=tf.seconds * req.higher_min_bars.get(tf, 200))
            try:
                hb = repo.read_ohlcv(inst, tf, tf_from, end, as_of=end)
            except Exception:
                continue
            holes = [
                (a.open_time, b.open_time)
                for a, b in itertools.pairwise(hb)
                if (b.open_time - a.open_time).total_seconds() > tf.seconds
            ]
            if holes:
                first = holes[0]
                out.append(
                    f"{inst}/{tf.value}: {len(holes)} interne Lücke(n), erste "
                    f"{first[0].isoformat()} → {first[1].isoformat()}"
                )
    return out


# --------------------------------------------------------------------------------- Assembler


def _default_higher_warmup() -> dict[Timeframe, int]:
    # komfortabel über MtfParams.min_bars (M15:200, H4:120, D1:120) — begrenzt das
    # Fenster, das build_mtf_context pro Tick re-analysiert.
    return {Timeframe.M15: 400, Timeframe.H4: 260, Timeframe.D1: 200}


@dataclasses.dataclass(frozen=True, slots=True)
class AssemblerConfig:
    instrument: str
    base_timeframe: Timeframe = Timeframe.M5
    warmup_bars: int = 300  # M5-Vorlauf (für die M5-Analyse selbst)
    read_native_higher: bool = True  # M15/H4/D1 aus dem Repo lesen, wenn vorhanden
    higher_warmup_bars: dict[Timeframe, int] = dataclasses.field(
        default_factory=_default_higher_warmup
    )
    news_lookback_days: int = 7
    news_lookahead_days: int = 7  # geplante Events (scheduled) bis hierhin sichtbar (nur Kalender)
    news_feed_available: bool = False  # nur True setzen, wenn ein echtes News-Dataset existiert
    asset_class: AssetClass = AssetClass.CRYPTO  # steuert die asset-spezifische News-Relevanz
    fixed_spread: float | None = None  # Paper: fixe Spanne; echte Spanne braucht Orderbuch
    account_equity: float | None = None


class MarketContextAssembler:
    """Baut zu einem ``cutoff`` einen `MarketContext` — strikt PIT.

    Die Serien werden beim ersten ``at()`` (bzw. via :meth:`bind`) **einmal** aus dem Repository
    geladen und danach nur noch **in-memory geslict** (bisect). So skaliert der Replay auf
    ≥ 180 Tage M5, ohne pro Tick das Parquet-File zu lesen. Das PIT-Verhalten ist identisch:
    jede Bar mit ``close_time <= cutoff`` und ``open_time >= cutoff - warmup``.
    """

    def __init__(self, repo: MarketDataRepository, config: AssemblerConfig) -> None:
        self._repo = repo
        self._c = config
        self._warmup = timedelta(seconds=config.base_timeframe.seconds * config.warmup_bars)
        self._bound = False
        self._m5: list[OHLCV] = []
        self._m5_close: list[datetime] = []  # parallele Schlüsselliste für bisect
        self._m5_open: list[datetime] = []
        # je höhere TF: (bars, close_times, open_times)
        self._higher: dict[Timeframe, tuple[list[OHLCV], list[datetime], list[datetime]]] = {}
        self._news: list[NewsEvent] = []
        self._news_avail: list[datetime] = []
        self._funding: list[tuple[datetime, float]] = []

    # ---- Vorladen -----------------------------------------------------------------
    def bind(self, start: datetime, end: datetime) -> None:
        """Lädt alle nötigen Serien für ``[start - warmup, end]`` **einmal**."""
        start, end = ensure_utc(start), ensure_utc(end)
        inst = self._c.instrument
        base = self._c.base_timeframe
        lo = start - self._warmup - timedelta(seconds=base.seconds)
        hi = end + timedelta(seconds=base.seconds)

        self._m5 = list(self._repo.read_ohlcv(inst, base, lo, hi))
        self._m5_close = [b.close_time for b in self._m5]
        self._m5_open = [b.open_time for b in self._m5]

        if self._c.read_native_higher:
            for tf in _HIGHER:
                if self._repo.ohlcv_coverage(inst, tf) is None:
                    continue
                back = self._c.higher_warmup_bars.get(tf, 200)
                bars = list(
                    self._repo.read_ohlcv(
                        inst,
                        tf,
                        lo - timedelta(seconds=tf.seconds * (back + 2)),
                        hi + timedelta(seconds=tf.seconds),
                    )
                )
                if bars:
                    self._higher[tf] = (
                        bars,
                        [b.close_time for b in bars],
                        [b.open_time for b in bars],
                    )

        if self._c.news_feed_available:
            try:
                self._news = list(
                    self._repo.read_news(
                        start - timedelta(days=self._c.news_lookback_days),
                        end + timedelta(days=self._c.news_lookahead_days),
                    )
                )
            except Exception:
                self._news = []
            self._news.sort(key=lambda e: ensure_utc(e.available_time))
            self._news_avail = [ensure_utc(e.available_time) for e in self._news]

        try:
            fund = self._repo.read_funding(inst, start - timedelta(days=3), end + timedelta(days=1))
        except Exception:
            fund = []
        self._funding = sorted((ensure_utc(f.ts), f.rate) for f in fund)
        self._bound = True

    def at(self, cutoff: datetime) -> MarketContext:
        cutoff = ensure_utc(cutoff)
        if not self._bound:
            self.bind(cutoff, cutoff)

        inst = self._c.instrument
        base = self._c.base_timeframe
        warm_from = cutoff - self._warmup

        m5 = self._slice(self._m5, self._m5_close, self._m5_open, cutoff, warm_from)
        if not m5:
            raise DatasetIncompleteError(
                f"{inst}/{base.value}: keine Bars <= {cutoff.isoformat()} — kein Fake."
            )

        series: dict[Timeframe, tuple[OHLCV, ...]] = {base: tuple(m5)}
        for tf, (bars, closes, opens) in self._higher.items():
            back = self._c.higher_warmup_bars.get(tf, 200)
            tf_from = cutoff - timedelta(seconds=tf.seconds * back)
            hi = self._slice(bars, closes, opens, cutoff, tf_from)
            if hi:
                series[tf] = tuple(hi)

        return MarketContext(
            instrument=inst,
            base_timeframe=base,
            information_cutoff=cutoff,
            series=series,
            spread=self._c.fixed_spread,
            account_equity=self._c.account_equity,
            derivatives=self._derivatives_context(cutoff),
            cross_asset=CrossAssetContext(),  # Slot — echte Quelle später (Schritt 4/5 Datenplan)
            news=self._news_context(cutoff),
        )

    @staticmethod
    def _slice(
        bars: list[OHLCV],
        closes: list[datetime],
        opens: list[datetime] | None,
        cutoff: datetime,
        warm_from: datetime,
    ) -> list[OHLCV]:
        hi = bisect.bisect_right(closes, cutoff)  # letzte Bar mit close_time <= cutoff
        lo = 0 if opens is None else bisect.bisect_left(opens, warm_from)
        return bars[lo:hi]

    # ---- Zusatzdaten (PIT, leer wenn keine echte Quelle) --------------------
    def _news_context(self, cutoff: datetime) -> NewsContext:
        if not self._c.news_feed_available:
            return NewsContext()  # feed_as_of=None ⇒ fail-safe NO_TRADE (NEWS_FEED_UNAVAILABLE)
        idx = bisect.bisect_right(self._news_avail, cutoff)
        # asset-spezifische Relevanz + Blackout/Pre-Positioning/risk_off (analysis.news, PIT)
        return build_news_context(
            self._news[:idx],
            cutoff=cutoff,
            asset_class=self._c.asset_class,
            instrument=self._c.instrument,
        )

    def _derivatives_context(self, cutoff: datetime) -> DerivativesContext:
        # Funding-Slot: nur füllen, wenn echte Funding-Historie da ist. Sonst leer (kein Fake).
        if not self._funding:
            return DerivativesContext()
        idx = bisect.bisect_right([ts for ts, _ in self._funding], cutoff)
        if idx == 0:
            return DerivativesContext()
        ts, rate = self._funding[idx - 1]
        return DerivativesContext(funding_rate=rate, funding_rate_as_of=ts)


# --------------------------------------------------------------------------------- Harness


@dataclasses.dataclass(frozen=True, slots=True)
class ReplayResult:
    instrument: str
    steps: int
    first_cutoff: datetime | None
    last_cutoff: datetime | None


class ReplayHarness:
    """Verbindet `ReplayClock` + `MarketContextAssembler` + einen `feed(mc)`-Consumer
    (typischerweise `strategy.paper_live.PaperLiveRunner`)."""

    def __init__(
        self,
        clock: ReplayClock,
        assembler: MarketContextAssembler,
    ) -> None:
        self._clock = clock
        self._assembler = assembler

    def run(self, feed: object) -> ReplayResult:
        """``feed`` muss eine ``feed(MarketContext) -> ...``-Methode haben."""
        fn = feed.feed  # type: ignore[attr-defined]
        n = 0
        first: datetime | None = None
        last: datetime | None = None
        for cutoff in self._clock:
            mc = self._assembler.at(cutoff)
            fn(mc)
            n += 1
            first = first or cutoff
            last = cutoff
        return ReplayResult(
            instrument=self._assembler._c.instrument,
            steps=n,
            first_cutoff=first,
            last_cutoff=last,
        )


__all__ = [
    "AssemblerConfig",
    "DatasetGap",
    "DatasetIncompleteError",
    "DatasetReport",
    "DatasetRequirements",
    "MarketContextAssembler",
    "ReplayClock",
    "ReplayHarness",
    "ReplayResult",
    "validate_dataset",
]
