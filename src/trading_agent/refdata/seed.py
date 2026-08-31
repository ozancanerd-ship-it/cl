"""Eingebaute Seed-Referenzdaten.

Zweck: die komplette Data Foundation ist **lokal ohne externe Accounts** lauffähig und testbar.
MVP-Datenpfad: ``BTCUSDT`` + ``ETHUSDT``. Reale Crypto-Historie zusätzlich: ``SOLUSDT``,
``BNBUSDT``, ``XRPUSDT``, ``DOGEUSDT`` (Binance-Vision-Bulk). Architektonisch mit angelegt:
``XAUUSD`` (Gold), ``AAPL`` (Aktie), ``SPY`` (ETF), ``EURUSD`` (Forex).

Diese Werte sind grobe, plausible Startwerte – **keine** verbindlichen Kontraktspezifikationen.
Echte Instrumentdaten (Bybit etc.) kommen ab Phase 9.
"""

from __future__ import annotations

from datetime import time

from trading_agent.core.enums import AssetClass, Exchange, SessionName, TradingPriority
from trading_agent.core.time import parse_timestamp
from trading_agent.refdata.calendar import TradingCalendar
from trading_agent.refdata.instruments import InstrumentMaster
from trading_agent.refdata.models import (
    FeeSchedule,
    Instrument,
    MarginTier,
    SessionSpec,
    SymbolMapping,
    TradingCalendarSpec,
)
from trading_agent.refdata.symbols import SymbolMapper

# --------------------------------------------------------------------------------------------
# Kalender
# --------------------------------------------------------------------------------------------

CALENDARS: dict[str, TradingCalendarSpec] = {
    "always_open": TradingCalendarSpec(calendar_id="always_open", is_24_7=True),
    "crypto_24_7": TradingCalendarSpec(calendar_id="crypto_24_7", is_24_7=True),
    "fx_weekday_24h": TradingCalendarSpec(
        calendar_id="fx_weekday_24h", timezone="UTC", weekend_gap=True
    ),
    "xau_spot": TradingCalendarSpec(
        calendar_id="xau_spot",
        timezone="UTC",
        weekend_gap=True,
        # CME/Globex-Gold: tägliche Pause 21:00–22:00 UTC (17:00–18:00 ET).
        daily_break_start=time(21, 0),
        daily_break_end=time(22, 0),
    ),
    "us_equity": TradingCalendarSpec(
        calendar_id="us_equity",
        timezone="America/New_York",
        weekmask=(0, 1, 2, 3, 4),
        regular_open=time(9, 30),
        regular_close=time(16, 0),
        holidays=(
            parse_timestamp("2024-01-01").date(),
            parse_timestamp("2024-07-04").date(),
            parse_timestamp("2024-12-25").date(),
        ),
    ),
}


def seed_calendars() -> dict[str, TradingCalendar]:
    return {cid: TradingCalendar(spec) for cid, spec in CALENDARS.items()}


# --------------------------------------------------------------------------------------------
# Sessions (Börsenlokalzeit; DST-sicher aufgelöst zur Laufzeit)
# --------------------------------------------------------------------------------------------

SESSIONS: list[SessionSpec] = [
    SessionSpec(name=SessionName.ASIA, tz="Asia/Tokyo", start=time(9, 0), end=time(15, 0)),
    SessionSpec(name=SessionName.LONDON, tz="Europe/London", start=time(8, 0), end=time(16, 30)),
    SessionSpec(
        name=SessionName.NEW_YORK, tz="America/New_York", start=time(9, 30), end=time(16, 0)
    ),
]


def seed_sessions() -> list[SessionSpec]:
    return list(SESSIONS)


# --------------------------------------------------------------------------------------------
# Instrumente
# --------------------------------------------------------------------------------------------

_CRYPTO_MARGIN_TIERS = (
    MarginTier(notional_floor=0.0, max_leverage=100.0, maintenance_margin_rate=0.005),
    MarginTier(notional_floor=50_000.0, max_leverage=50.0, maintenance_margin_rate=0.01),
    MarginTier(notional_floor=250_000.0, max_leverage=20.0, maintenance_margin_rate=0.025),
)


def seed_instruments() -> list[Instrument]:
    return [
        Instrument(
            canonical_symbol="BTCUSDT",
            asset_class=AssetClass.CRYPTO,
            exchange=Exchange.BYBIT,
            base_currency="BTC",
            quote_currency="USDT",
            settle_currency="USDT",
            tick_size=0.1,
            lot_size=0.001,
            min_notional=5.0,
            price_precision=1,
            size_precision=3,
            is_perpetual=True,
            funding_interval_hours=8.0,
            trading_priority=TradingPriority.TIER_1,
            calendar_id="crypto_24_7",
            fees=FeeSchedule(maker_bps=2.0, taker_bps=5.5),
            margin_tiers=_CRYPTO_MARGIN_TIERS,
            max_leverage=100.0,
            listed_at=parse_timestamp("2019-01-01T00:00:00Z"),
            tags=("mvp", "tier1"),
        ),
        Instrument(
            canonical_symbol="ETHUSDT",
            asset_class=AssetClass.CRYPTO,
            exchange=Exchange.BYBIT,
            base_currency="ETH",
            quote_currency="USDT",
            settle_currency="USDT",
            tick_size=0.01,
            lot_size=0.01,
            min_notional=5.0,
            price_precision=2,
            size_precision=2,
            is_perpetual=True,
            funding_interval_hours=8.0,
            trading_priority=TradingPriority.TIER_2,
            calendar_id="crypto_24_7",
            fees=FeeSchedule(maker_bps=2.0, taker_bps=5.5),
            margin_tiers=_CRYPTO_MARGIN_TIERS,
            max_leverage=100.0,
            listed_at=parse_timestamp("2019-01-01T00:00:00Z"),
            tags=("mvp", "tier2"),
        ),
        Instrument(
            canonical_symbol="SOLUSDT",
            asset_class=AssetClass.ALTCOIN,
            exchange=Exchange.BYBIT,
            base_currency="SOL",
            quote_currency="USDT",
            settle_currency="USDT",
            tick_size=0.001,
            lot_size=0.1,
            min_notional=5.0,
            price_precision=3,
            size_precision=1,
            is_perpetual=True,
            funding_interval_hours=8.0,
            trading_priority=TradingPriority.TIER_2,
            calendar_id="crypto_24_7",
            margin_tiers=_CRYPTO_MARGIN_TIERS,
            max_leverage=50.0,
            listed_at=parse_timestamp("2021-01-01T00:00:00Z"),
            tags=("tier2", "altcoin"),
        ),
        Instrument(
            canonical_symbol="BNBUSDT",
            asset_class=AssetClass.ALTCOIN,
            exchange=Exchange.BINANCE,
            base_currency="BNB",
            quote_currency="USDT",
            settle_currency="USDT",
            tick_size=0.01,
            lot_size=0.01,
            min_notional=5.0,
            price_precision=2,
            size_precision=2,
            is_perpetual=True,
            funding_interval_hours=8.0,
            trading_priority=TradingPriority.TIER_2,
            calendar_id="crypto_24_7",
            margin_tiers=_CRYPTO_MARGIN_TIERS,
            max_leverage=50.0,
            listed_at=parse_timestamp("2019-01-01T00:00:00Z"),
            tags=("tier2", "altcoin"),
        ),
        Instrument(
            canonical_symbol="XRPUSDT",
            asset_class=AssetClass.ALTCOIN,
            exchange=Exchange.BINANCE,
            base_currency="XRP",
            quote_currency="USDT",
            settle_currency="USDT",
            tick_size=0.0001,
            lot_size=1.0,
            min_notional=5.0,
            price_precision=4,
            size_precision=0,
            is_perpetual=True,
            funding_interval_hours=8.0,
            trading_priority=TradingPriority.TIER_2,
            calendar_id="crypto_24_7",
            margin_tiers=_CRYPTO_MARGIN_TIERS,
            max_leverage=50.0,
            listed_at=parse_timestamp("2019-01-01T00:00:00Z"),
            tags=("tier2", "altcoin"),
        ),
        Instrument(
            canonical_symbol="DOGEUSDT",
            asset_class=AssetClass.ALTCOIN,
            exchange=Exchange.BINANCE,
            base_currency="DOGE",
            quote_currency="USDT",
            settle_currency="USDT",
            tick_size=0.00001,
            lot_size=1.0,
            min_notional=5.0,
            price_precision=5,
            size_precision=0,
            is_perpetual=True,
            funding_interval_hours=8.0,
            trading_priority=TradingPriority.TIER_3,
            calendar_id="crypto_24_7",
            margin_tiers=_CRYPTO_MARGIN_TIERS,
            max_leverage=50.0,
            listed_at=parse_timestamp("2020-07-01T00:00:00Z"),
            tags=("tier3", "altcoin"),
        ),
        Instrument(
            canonical_symbol="XAUUSD",
            asset_class=AssetClass.GOLD,
            exchange=Exchange.OANDA,
            base_currency="XAU",
            quote_currency="USD",
            tick_size=0.01,
            lot_size=0.01,
            min_notional=0.0,
            price_precision=2,
            size_precision=2,
            contract_multiplier=1.0,
            trading_priority=TradingPriority.TIER_1,
            calendar_id="xau_spot",
            fees=FeeSchedule(maker_bps=0.0, taker_bps=3.0),
            max_leverage=20.0,
            pip_size=0.1,
            swap_long_points=-0.5,
            swap_short_points=0.2,
            tags=("tier1", "metal"),
        ),
        Instrument(
            canonical_symbol="XAUUSDT",
            asset_class=AssetClass.GOLD,
            exchange=Exchange.BINANCE,
            base_currency="XAU",
            quote_currency="USDT",
            settle_currency="USDT",
            tick_size=0.01,
            lot_size=0.001,
            min_notional=5.0,
            price_precision=2,
            size_precision=3,
            is_perpetual=True,
            funding_interval_hours=8.0,  # Binance TradiFi-Perp; Funding aktuell 0.0
            trading_priority=TradingPriority.TIER_1,
            calendar_id="xau_spot",  # session-gated wie Spot-Gold (Daten fließen 24/7)
            fees=FeeSchedule(maker_bps=2.0, taker_bps=5.0),
            max_leverage=20.0,
            pip_size=0.1,
            tags=("tier1", "metal", "perp", "binance"),
        ),
        Instrument(
            canonical_symbol="EURUSD",
            asset_class=AssetClass.FOREX,
            exchange=Exchange.OANDA,
            base_currency="EUR",
            quote_currency="USD",
            tick_size=0.00001,
            lot_size=1000.0,
            min_notional=0.0,
            price_precision=5,
            size_precision=0,
            trading_priority=TradingPriority.TIER_3,
            calendar_id="fx_weekday_24h",
            fees=FeeSchedule(maker_bps=0.0, taker_bps=0.8),
            max_leverage=30.0,
            pip_size=0.0001,
            swap_long_points=-0.7,
            swap_short_points=0.1,
            tags=("forex",),
        ),
        Instrument(
            canonical_symbol="GBPUSD",
            asset_class=AssetClass.FOREX,
            exchange=Exchange.OANDA,
            base_currency="GBP",
            quote_currency="USD",
            tick_size=0.00001,
            lot_size=1000.0,
            min_notional=0.0,
            price_precision=5,
            size_precision=0,
            trading_priority=TradingPriority.TIER_3,
            calendar_id="fx_weekday_24h",
            fees=FeeSchedule(maker_bps=0.0, taker_bps=0.9),
            max_leverage=30.0,
            pip_size=0.0001,
            swap_long_points=-0.9,
            swap_short_points=0.2,
            tags=("forex",),
        ),
        Instrument(
            canonical_symbol="USDJPY",
            asset_class=AssetClass.FOREX,
            exchange=Exchange.OANDA,
            base_currency="USD",
            quote_currency="JPY",
            tick_size=0.001,
            lot_size=1000.0,
            min_notional=0.0,
            price_precision=3,
            size_precision=0,
            trading_priority=TradingPriority.TIER_3,
            calendar_id="fx_weekday_24h",
            fees=FeeSchedule(maker_bps=0.0, taker_bps=0.9),
            max_leverage=30.0,
            pip_size=0.01,
            swap_long_points=0.3,
            swap_short_points=-1.1,
            tags=("forex",),
        ),
        Instrument(
            canonical_symbol="AAPL",
            asset_class=AssetClass.EQUITY,
            exchange=Exchange.NASDAQ,
            base_currency="AAPL",
            quote_currency="USD",
            tick_size=0.01,
            lot_size=1.0,
            min_notional=0.0,
            price_precision=2,
            size_precision=0,
            trading_priority=TradingPriority.TIER_2,
            calendar_id="us_equity",
            fees=FeeSchedule(maker_bps=0.0, taker_bps=1.0),
            max_leverage=2.0,
            listed_at=parse_timestamp("1980-12-12T00:00:00Z"),
            tags=("tier2", "equity"),
        ),
        Instrument(
            canonical_symbol="SPY",
            asset_class=AssetClass.ETF,
            exchange=Exchange.ARCA,
            base_currency="SPY",
            quote_currency="USD",
            tick_size=0.01,
            lot_size=1.0,
            min_notional=0.0,
            price_precision=2,
            size_precision=0,
            trading_priority=TradingPriority.TIER_3,
            calendar_id="us_equity",
            fees=FeeSchedule(maker_bps=0.0, taker_bps=1.0),
            max_leverage=2.0,
            listed_at=parse_timestamp("1993-01-29T00:00:00Z"),
            tags=("etf", "long_term"),
        ),
    ]


# --------------------------------------------------------------------------------------------
# Symbol-Mappings
# --------------------------------------------------------------------------------------------


def seed_symbol_mappings() -> list[SymbolMapping]:
    out: list[SymbolMapping] = []
    # synthetic (Mock-Provider) verwendet die kanonische Schreibweise 1:1
    for sym in (
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        "BNBUSDT",
        "XRPUSDT",
        "DOGEUSDT",
        "XAUUSD",
        "XAUUSDT",
        "EURUSD",
        "GBPUSD",
        "USDJPY",
        "AAPL",
        "SPY",
    ):
        out.append(SymbolMapping(canonical=sym, source="synthetic", provider_symbol=sym))
    # bybit
    out += [
        SymbolMapping(
            canonical="BTCUSDT",
            source="bybit",
            provider_symbol="BTCUSDT",
            aliases=("BTC-USDT", "XBTUSDT"),
        ),
        SymbolMapping(
            canonical="ETHUSDT", source="bybit", provider_symbol="ETHUSDT", aliases=("ETH-USDT",)
        ),
        SymbolMapping(
            canonical="SOLUSDT", source="bybit", provider_symbol="SOLUSDT", aliases=("SOL-USDT",)
        ),
    ]
    # binance vision (bulk klines) — kanonisch 1:1
    out += [
        SymbolMapping(canonical=sym, source="binance_vision", provider_symbol=sym)
        for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT")
    ]
    # oanda / cTrader (v20-artige Schreibweise mit Unterstrich)
    for canon, prov in (
        ("XAUUSD", "XAU_USD"),
        ("EURUSD", "EUR_USD"),
        ("GBPUSD", "GBP_USD"),
        ("USDJPY", "USD_JPY"),
    ):
        aliases = ("GOLD", "GC") if canon == "XAUUSD" else ()
        out.append(
            SymbolMapping(canonical=canon, source="oanda", provider_symbol=prov, aliases=aliases)
        )
        out.append(SymbolMapping(canonical=canon, source="ctrader", provider_symbol=prov))
    # Dukascopy-Bulk (historisch) — Symbol ohne Trenner, wie im .bi5-Pfad
    for canon in ("XAUUSD", "EURUSD", "GBPUSD", "USDJPY"):
        out.append(SymbolMapping(canonical=canon, source="dukascopy", provider_symbol=canon))
    # Yahoo Finance (nur indikativ/verzögert, Pipeline-Validierung)
    for canon, prov in (
        ("XAUUSD", "GC=F"),
        ("EURUSD", "EURUSD=X"),
        ("GBPUSD", "GBPUSD=X"),
        ("USDJPY", "JPY=X"),
    ):
        out.append(SymbolMapping(canonical=canon, source="yahoo", provider_symbol=prov))
    # Tokenisiertes Gold als sofort verfügbarer XAUUSD-Live-Proxy (Basis-Spread beachten).
    out += [
        SymbolMapping(
            canonical="XAUUSD", source="bybit", provider_symbol="XAUTUSDT", aliases=("XAUT", "PAXG")
        ),
        SymbolMapping(canonical="XAUUSD", source="kraken", provider_symbol="XAUT/USD"),
    ]
    # Binance USD-M-Futures: echtes XAUUSDT (TradiFi-Perp). Spot hat stattdessen PAXGUSDT.
    out += [
        SymbolMapping(canonical="XAUUSDT", source="binance", provider_symbol="XAUUSDT"),
        SymbolMapping(
            canonical="XAUUSD", source="binance", provider_symbol="XAUUSDT", aliases=("XAUUSDT",)
        ),
        SymbolMapping(canonical="XAUUSD", source="binance_spot", provider_symbol="PAXGUSDT"),
        SymbolMapping(canonical="BTCUSDT", source="binance", provider_symbol="BTCUSDT"),
        SymbolMapping(canonical="ETHUSDT", source="binance", provider_symbol="ETHUSDT"),
    ]
    return out


# --------------------------------------------------------------------------------------------
# Bequeme Builder
# --------------------------------------------------------------------------------------------

MVP_SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT")


def build_instrument_master() -> InstrumentMaster:
    return InstrumentMaster(seed_instruments())


def build_symbol_mapper() -> SymbolMapper:
    return SymbolMapper(seed_symbol_mappings())


__all__ = [
    "CALENDARS",
    "MVP_SYMBOLS",
    "SESSIONS",
    "build_instrument_master",
    "build_symbol_mapper",
    "seed_calendars",
    "seed_instruments",
    "seed_sessions",
    "seed_symbol_mappings",
]
