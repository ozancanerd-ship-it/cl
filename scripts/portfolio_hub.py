#!/usr/bin/env python
"""Portfolio Hub — konsolidiert die **read-only** Accounts (Kraken/Bybit/Binance) und fährt
die volle Portfolio-Intelligence (Masterplan §33–§43).

Kraken-Balance + Bybit-Wallet/Positionen + Binance-Spot-Balance  →  ``account_mapping``  →
``PortfolioHub.consolidate``  →  ``PortfolioIntelligenceEngine.assess``  →  Report:
Equity je Account · Allokation je Asset-Klasse · Position-Ratings (0–100 + Verdikt) ·
Exit-Pläne · Portfolio-Health (GREEN/YELLOW/RED) · Ranking · Rotations-Vorschlag.

**READ-ONLY. Keine Order, keine Trading-Rechte.** Fehlt ein Account-Key → wird übersprungen.
Mark-Preise + Korrelations-Reihen kommen aus den öffentlichen Binance-Endpunkten (kein Key).

    uv run python scripts/portfolio_hub.py            # Text
    uv run python scripts/portfolio_hub.py --json
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from trading_agent.data.providers.binance import BinancePublicDataProvider
from trading_agent.data.providers.trade_republic_manual import DEFAULT_PATH as TR_PATH
from trading_agent.data.providers.trade_republic_manual import (
    load_depot as load_tr_depot,
)
from trading_agent.data.providers.trade_republic_manual import (
    missing_prices as tr_missing_prices,
)
from trading_agent.data.providers.trade_republic_manual import (
    to_account as tr_to_account,
)
from trading_agent.portfolio_intel import PortfolioIntelligenceEngine
from trading_agent.portfolio_intel.account_mapping import (
    map_derivatives_account,
    map_spot_account,
)
from trading_agent.portfolio_intel.models import AccountPortfolio


def _load_env_file() -> None:
    """``.env`` (chmod 600, in ``.gitignore``) → ``os.environ`` — gleiche Konvention wie die
    ``*_account_test.py``-Skripte. Vorhandene Variablen werden **nicht** überschrieben."""
    p = Path(".env")
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))


async def _prices(instruments: set[str]) -> dict[str, float]:
    """Aktuelle Mark-/Last-Preise (öffentlich, kein Key)."""
    out: dict[str, float] = {}
    if not instruments:
        return out
    spot = BinancePublicDataProvider(market="spot")
    fut = BinancePublicDataProvider(market="futures_usdm")
    try:
        for inst in sorted(instruments):
            for prov in (spot, fut):
                with contextlib.suppress(Exception):
                    t = await prov.fetch_ticker_24h(inst)
                    px = float(t.get("lastPrice") or t.get("last") or 0.0)
                    if px > 0:
                        out[inst] = px
                        break
    finally:
        with contextlib.suppress(Exception):
            await spot.aclose()
        with contextlib.suppress(Exception):
            await fut.aclose()
    return out


def _kraken_canon(raw: str) -> str:
    """Kraken-Assetcode → kanonisches Basis-Symbol. Strippt Earn-/Stake-Suffixe (``.F``/``.S``)."""
    from trading_agent.portfolio_intel.account_mapping import _canon

    return _canon(raw.split(".", 1)[0])


async def _kraken(as_of: datetime, prices: dict[str, float]) -> AccountPortfolio | None:
    from trading_agent.data.providers.kraken_account import KrakenAccountAdapter

    a = KrakenAccountAdapter()
    if not a.credentials_ok():
        return None
    try:
        raw = await a.get_balances()
        # Earn-/Stake-Positionen (BTC.F, ETH.S …) auf das Basis-Asset zusammenfassen.
        bal: dict[str, float] = {}
        for k, v in raw.items():
            bal[_kraken_canon(k)] = bal.get(_kraken_canon(k), 0.0) + v
        # Alles in USDT bewerten; EUR-/USD-Cash über EURUSDT umrechnen.
        need = {f"{k}USDT" for k in bal if k not in ("EUR", "USD", "USDT", "USDC")}
        prices.update(await _prices(need | {"EURUSDT"}))
        eurusd = prices.get("EURUSDT", 1.08)
        cash_usdt = bal.pop("EUR", 0.0) * eurusd + bal.pop("USD", 0.0)
        bal["USDT"] = bal.get("USDT", 0.0) + cash_usdt
        return map_spot_account(
            account="kraken",
            as_of=as_of,
            balances=bal,
            prices=prices,
            quote_ccy="USDT",
            read_only_verified=True,
        )
    finally:
        with contextlib.suppress(Exception):
            await a.aclose()


async def _bybit(as_of: datetime, prices: dict[str, float]) -> AccountPortfolio | None:
    from trading_agent.data.providers.bybit_account import BybitAccountAdapter

    a = BybitAccountAdapter()
    if not a.credentials_ok():
        return None
    try:
        w = await a.get_wallet_balance()
        perps = list((await a.get_positions(category="linear")).get("positions") or [])

        # Unified Account: die eigentlichen Coin-Bestaende stehen in ``nonzero_balances``.
        # Bis 2026-09-03 wurden sie hier verworfen und nur ``equity`` + Linear-Perps
        # verwendet — dadurch lief das gesamte Bybit-Wallet als "Cash" mit und keine
        # einzige Bybit-Position wurde bewertet oder gescort.
        coins = dict(w.nonzero_balances or {})
        need = {
            f"{k}USDT"
            for k in coins
            if k.upper() not in ("USDT", "USDC", "BUSD", "USD", "EUR", "DAI")
        }
        if need:
            prices.update(await _prices(need))
        spot = map_spot_account(
            account="bybit",
            as_of=as_of,
            balances=coins,
            prices=prices,
            quote_ccy="USDT",
            read_only_verified=True,
        )
        if not perps:
            return spot

        # Offene Perps zusaetzlich abbilden und die gebundene Margin vom Cash abziehen.
        deriv = map_derivatives_account(
            account="bybit",
            as_of=as_of,
            equity=w.equity,
            positions=perps,
            quote_ccy="USDT",
            read_only_verified=True,
        )
        used = sum(h.cost_basis for h in deriv.holdings)
        return AccountPortfolio(
            account="bybit",
            as_of=as_of,
            cash=round(max(0.0, spot.cash - used), 8),
            holdings=tuple(spot.holdings) + tuple(deriv.holdings),
            currency="USDT",
            read_only_verified=True,
        )
    finally:
        with contextlib.suppress(Exception):
            await a.aclose()


async def _binance(as_of: datetime, prices: dict[str, float]) -> AccountPortfolio | None:
    from trading_agent.data.providers.binance_account import BinanceAccountAdapter

    a = BinanceAccountAdapter()
    if not a.credentials_ok():
        return None
    try:
        s = await a.get_spot_balances()
        need = {f"{k}USDT" for k in s.nonzero_balances if k.upper() not in ("USDT", "USDC", "BUSD")}
        prices.update(await _prices(need))
        return map_spot_account(
            account="binance",
            as_of=as_of,
            balances=s.nonzero_balances,
            prices=prices,
            quote_ccy="USDT",
            read_only_verified=True,
        )
    finally:
        with contextlib.suppress(Exception):
            await a.aclose()


async def _equity_prices_eur(symbols: list[str]) -> dict[str, float]:
    """Aktienkurse in EUR ueber Yahoo. Stumme Quelle heisst: kein Kurs, nicht Kurs null."""
    import httpx

    if not symbols:
        return {}
    out: dict[str, float] = {}
    eurusd = 1.0
    async with httpx.AsyncClient(
        timeout=20, headers={"User-Agent": "Mozilla/5.0 (compatible; trading-agent/0.1)"}
    ) as c:
        with contextlib.suppress(Exception):
            r = (
                await c.get(
                    "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X",
                    params={"interval": "1d", "range": "5d"},
                )
            ).json()
            eurusd = float(r["chart"]["result"][0]["meta"]["regularMarketPrice"])
        for sym in symbols:
            with contextlib.suppress(Exception):
                r = (
                    await c.get(
                        f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                        params={"interval": "1d", "range": "5d"},
                    )
                ).json()
                meta = r["chart"]["result"][0]["meta"]
                px = float(meta["regularMarketPrice"])
                ccy = str(meta.get("currency", "USD")).upper()
                out[sym] = px if ccy == "EUR" else (px / eurusd if eurusd > 0 else px)
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--alerts-journal",
        default="data/repository_real/live/context_alerts.jsonl",
        help="Kontext-Alerts (Portfolio-Risk / Re-Entry) hier anhängen. '' zum Deaktivieren.",
    )
    args = ap.parse_args()
    _load_env_file()
    as_of = datetime.now(UTC)
    prices: dict[str, float] = {}

    results = await asyncio.gather(
        _kraken(as_of, prices),
        _bybit(as_of, prices),
        _binance(as_of, prices),
        return_exceptions=True,
    )
    accounts: list[AccountPortfolio] = []
    skipped: list[str] = []
    for name, r in zip(("kraken", "bybit", "binance"), results, strict=True):
        if isinstance(r, AccountPortfolio):
            accounts.append(r)
        else:
            skipped.append(
                f"{name}: {r if isinstance(r, Exception) else 'kein Key / kein Guthaben'}"
            )

    # Trade Republic hat keine Schnittstelle (docs/TRADE-REPUBLIC-ANBINDUNG.md). Ohne diese
    # Datei ist der Aktienteil des Depots unsichtbar — und damit auch die Frage, ob eine
    # neue Aktienposition ueberhaupt noch ins Risikobudget passt.
    tr_note: str | None = None
    try:
        depot = load_tr_depot()
    except ValueError as exc:
        tr_note = f"Trade Republic: Datei fehlerhaft — {exc}"
        depot = None
    if depot is not None:
        tr_prices = await _equity_prices_eur([q.symbol for q in depot.positions])
        accounts.append(tr_to_account(depot, tr_prices))
        fehlend = tr_missing_prices(depot, tr_prices)
        hinweise = []
        if depot.stale_days > 30:
            hinweise.append(f"Stand ist {depot.stale_days} Tage alt")
        if fehlend:
            hinweise.append(f"kein Kurs fuer {', '.join(fehlend)} — Einstand angesetzt")
        if hinweise:
            tr_note = "Trade Republic: " + "; ".join(hinweise)
    elif tr_note is None:
        tr_note = (
            "Trade Republic: nicht gepflegt — der Aktienteil des Depots fehlt im Gesamtbild "
            f"({TR_PATH})"
        )
    if tr_note:
        skipped.append(tr_note)

    if not accounts:
        print(json.dumps({"error": "kein Account lesbar", "skipped": skipped}, indent=2))
        return 1

    # Preis-Reihen für die Korrelation (letzte ~200 H1-Bars je Instrument)
    from trading_agent.core.enums import Timeframe

    insts = {h.instrument for a in accounts for h in a.holdings}
    series: dict[str, list] = {}
    fut = BinancePublicDataProvider(market="futures_usdm")
    spot = BinancePublicDataProvider(market="spot")
    try:
        for inst in sorted(insts):
            for prov in (spot, fut):
                with contextlib.suppress(Exception):
                    bars = await prov.fetch_ohlcv(inst, Timeframe.H1, _since(as_of), as_of)
                    if len(bars) >= 30:
                        series[inst] = [(b.open_time, b.close) for b in bars[-200:]]
                        break
    finally:
        with contextlib.suppress(Exception):
            await spot.aclose()
        with contextlib.suppress(Exception):
            await fut.aclose()

    report = PortfolioIntelligenceEngine().assess(
        accounts, as_of=as_of, price_series=series or None
    )
    d = report.as_dict()
    d["skipped_accounts"] = skipped

    # Kontext-Alerts: Portfolio-Health YELLOW/RED, harte EXIT/REDUCE-Verdikte, Re-Entry-Watches.
    # Dedup/Cooldown + Fingerprint-Änderungserkennung → kein Spam bei wiederholten Läufen.
    from trading_agent.strategy.alerts import AlertEngine
    from trading_agent.strategy.context_alerts import ContextAlertEmitter

    emitter = ContextAlertEmitter(AlertEngine())
    alert_events = emitter.on_portfolio_report(report, as_of)
    alerts_out = [
        {
            "ts": as_of.isoformat(),
            "type": ae.alert.type.value,
            "severity": ae.alert.severity.value,
            "kind": ae.kind.value,
            "title": ae.alert.title,
            "body": ae.alert.body,
            "evidence": dict(ae.alert.evidence),
        }
        for ae in alert_events
        if ae.delivered
    ]
    d["context_alerts"] = alerts_out
    if args.alerts_journal and alerts_out:
        jp = Path(args.alerts_journal)
        jp.parent.mkdir(parents=True, exist_ok=True)
        with jp.open("a") as fh:
            for row in alerts_out:
                fh.write(json.dumps(row, default=str) + "\n")

    if args.json:
        print(json.dumps(d, indent=2, default=str))
        return 0
    _render(report, skipped)
    for row in alerts_out:
        print(f"  🔔 [{row['severity'].upper()}] {row['title']} — {row['body']}")
    return 0


def _since(now: datetime):
    from datetime import timedelta

    return now - timedelta(days=12)


def _render(report: object, skipped: list[str]) -> None:
    cp = report.consolidated  # type: ignore[attr-defined]
    h = report.health  # type: ignore[attr-defined]
    print(f"\n{'=' * 64}")
    print(f"  PORTFOLIO HUB  ·  {cp.as_of.isoformat()}")
    print(f"{'=' * 64}")
    print(f"  Equity gesamt:  {cp.equity:,.2f}   ·   Cash {cp.cash_pct:.0%}")
    for acc, eq in cp.per_account_equity.items():
        print(f"    {acc:<20} {eq:,.2f}")
    alloc = cp.allocation()
    if alloc:
        print("  Allokation:     " + " · ".join(f"{k.value} {v:.0%}" for k, v in alloc.items()))
    print(f"\n  HEALTH:  {h.score}/100  ({h.grade})")
    for f in h.flags:
        print(f"    ⚠ {f}")
    print(f"\n  {'-' * 58}")
    for r in report.ranking:  # type: ignore[attr-defined]
        print(
            f"  #{r.rank}  {r.instrument:<12} {r.score:>5.1f}/100  {r.verdict.value.upper():<12} "
            f"{r.weight_pct:>5.1f}%"
        )
    for p in report.exit_plans:  # type: ignore[attr-defined]
        if p.kind.value != "none":
            print(
                f"    → {p.instrument}: {p.kind.value.upper()} {p.size_fraction:.0%}  ({p.trigger})"
            )
    rot = report.rotation  # type: ignore[attr-defined]
    if rot is not None:
        print(
            f"\n  ROTATION-VORSCHLAG: {rot.sell_instrument} → {rot.buy_instrument} "
            f"(Edge {rot.edge:.0f})  — {rot.note}"
        )
    if skipped:
        print(f"\n  übersprungen: {'; '.join(skipped)}")
    print(f"{'=' * 64}\n")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
