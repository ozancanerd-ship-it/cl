"""Tests: Referenzdaten – Instrument-Master, Symbol-Mapping, Kalender, Corporate Actions."""

from __future__ import annotations

from datetime import date, time
from datetime import time as dtime

import pytest
from pydantic import ValidationError

from trading_agent.core.enums import (
    AssetClass,
    CorporateActionType,
    Exchange,
    SessionName,
    TradingPriority,
)
from trading_agent.core.time import parse_timestamp
from trading_agent.refdata.calendar import active_sessions, resolve_session
from trading_agent.refdata.instruments import InstrumentMaster, InstrumentNotFound
from trading_agent.refdata.models import (
    CorporateAction,
    Instrument,
    MarginTier,
    SessionSpec,
)
from trading_agent.refdata.seed import (
    MVP_SYMBOLS,
    build_instrument_master,
    build_symbol_mapper,
    seed_calendars,
    seed_instruments,
    seed_sessions,
)
from trading_agent.refdata.symbols import SymbolMapper, SymbolMappingError

# ---------------------------------------------------------------- Instrument model


def _inst(**kw: object) -> Instrument:
    base = dict(
        canonical_symbol="TESTUSDT",
        asset_class=AssetClass.CRYPTO,
        exchange=Exchange.BYBIT,
        base_currency="TEST",
        quote_currency="USDT",
        tick_size=0.1,
        lot_size=0.001,
    )
    base.update(kw)
    return Instrument(**base)  # type: ignore[arg-type]


class TestInstrumentModel:
    def test_valid(self) -> None:
        i = _inst()
        assert i.tick_size == 0.1

    def test_zero_tick_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _inst(tick_size=0.0)

    def test_perpetual_needs_funding_interval(self) -> None:
        with pytest.raises(ValidationError):
            _inst(is_perpetual=True)
        _inst(is_perpetual=True, funding_interval_hours=8.0)  # ok

    def test_margin_tiers_must_be_sorted(self) -> None:
        with pytest.raises(ValidationError):
            _inst(
                margin_tiers=(
                    MarginTier(notional_floor=100.0, max_leverage=10.0),
                    MarginTier(notional_floor=0.0, max_leverage=20.0),
                )
            )

    def test_delisted_before_listed_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _inst(
                listed_at=parse_timestamp("2024-01-01T00:00:00Z"),
                delisted_at=parse_timestamp("2023-01-01T00:00:00Z"),
            )

    def test_point_in_time_tradeability(self) -> None:
        i = _inst(
            listed_at=parse_timestamp("2021-01-01T00:00:00Z"),
            delisted_at=parse_timestamp("2023-01-01T00:00:00Z"),
        )
        assert not i.is_tradeable_at(parse_timestamp("2020-06-01T00:00:00Z"))
        assert i.is_tradeable_at(parse_timestamp("2022-06-01T00:00:00Z"))
        assert not i.is_tradeable_at(parse_timestamp("2024-06-01T00:00:00Z"))


# ---------------------------------------------------------------- InstrumentMaster


class TestInstrumentMaster:
    def test_seed_has_all_asset_classes(self) -> None:
        im = build_instrument_master()
        classes = {i.asset_class for i in im.all()}
        assert {
            AssetClass.CRYPTO,
            AssetClass.ALTCOIN,
            AssetClass.GOLD,
            AssetClass.FOREX,
            AssetClass.EQUITY,
            AssetClass.ETF,
        } <= classes

    def test_mvp_symbols_present(self) -> None:
        im = build_instrument_master()
        for sym in MVP_SYMBOLS:
            assert im.has(sym)

    def test_lookup_and_missing(self) -> None:
        im = build_instrument_master()
        assert im.get("btcusdt").canonical_symbol == "BTCUSDT"
        with pytest.raises(InstrumentNotFound):
            im.get("NOPE")

    def test_duplicate_add_rejected(self) -> None:
        im = InstrumentMaster([_inst()])
        with pytest.raises(ValueError):
            im.add(_inst())

    def test_scan_universe_is_tier1_and_tier2_sorted(self) -> None:
        im = build_instrument_master()
        universe = im.scan_universe()
        prios = [i.trading_priority for i in universe]
        assert TradingPriority.TIER_3 not in prios
        assert prios == sorted(prios, key=lambda p: p.value)
        assert "BTCUSDT" in {i.canonical_symbol for i in universe}
        assert "XAUUSD" in {i.canonical_symbol for i in universe}
        assert "SPY" not in {i.canonical_symbol for i in universe}

    def test_scan_universe_point_in_time(self) -> None:
        im = build_instrument_master()
        # SOLUSDT listed_at 2021 -> nicht im Universum vor 2021
        early = im.scan_universe(at=parse_timestamp("2020-06-01T00:00:00Z"))
        assert "SOLUSDT" not in {i.canonical_symbol for i in early}
        late = im.scan_universe(at=parse_timestamp("2024-06-01T00:00:00Z"))
        assert "SOLUSDT" in {i.canonical_symbol for i in late}


# ---------------------------------------------------------------- SymbolMapper


class TestSymbolMapper:
    def test_seed_roundtrip(self) -> None:
        sm = build_symbol_mapper()
        assert sm.to_provider("BTCUSDT", "bybit") == "BTCUSDT"
        assert sm.to_canonical("XBTUSDT", "bybit") == "BTCUSDT"  # alias
        assert sm.to_canonical("XAU_USD", "oanda") == "XAUUSD"
        assert sm.to_canonical("GOLD", "oanda") == "XAUUSD"  # alias

    def test_case_insensitive(self) -> None:
        sm = build_symbol_mapper()
        assert sm.to_provider("btcusdt", "BYBIT") == "BTCUSDT"

    def test_unknown_raises(self) -> None:
        sm = build_symbol_mapper()
        with pytest.raises(SymbolMappingError):
            sm.to_provider("DOGEUSDT", "bybit")
        with pytest.raises(SymbolMappingError):
            sm.to_canonical("WAT", "bybit")

    def test_duplicate_mapping_rejected(self) -> None:
        from trading_agent.refdata.models import SymbolMapping

        sm = SymbolMapper([SymbolMapping(canonical="X", source="s", provider_symbol="x")])
        with pytest.raises(SymbolMappingError):
            sm.add(SymbolMapping(canonical="X", source="s", provider_symbol="x2"))


# ---------------------------------------------------------------- Calendar


class TestCalendar:
    def test_crypto_always_open(self) -> None:
        cal = seed_calendars()["crypto_24_7"]
        assert cal.is_open(parse_timestamp("2024-06-01T03:00:00Z"))  # Samstag
        assert cal.is_trading_day(date(2024, 12, 25))

    def test_fx_weekend_gap(self) -> None:
        fx = seed_calendars()["fx_weekday_24h"]
        assert not fx.is_open(parse_timestamp("2024-06-01T12:00:00Z"))  # Samstag
        assert not fx.is_open(parse_timestamp("2024-06-02T10:00:00Z"))  # Sonntag früh
        assert fx.is_open(parse_timestamp("2024-06-02T23:00:00Z"))  # Sonntag 23:00 -> offen
        assert fx.is_open(parse_timestamp("2024-06-05T12:00:00Z"))  # Mittwoch
        assert not fx.is_open(parse_timestamp("2024-06-07T23:00:00Z"))  # Freitag 23:00 -> zu

    def test_xau_spot_daily_break_and_weekend(self) -> None:
        xau = seed_calendars()["xau_spot"]
        assert xau.is_open(parse_timestamp("2024-06-05T15:00:00Z"))  # Mittwoch London/NY
        # tägliche CME-Pause 21:00–22:00 UTC
        assert not xau.is_open(parse_timestamp("2024-06-05T21:30:00Z"))
        assert xau.is_open(parse_timestamp("2024-06-05T22:30:00Z"))  # nach der Pause wieder offen
        assert not xau.is_open(parse_timestamp("2024-06-08T15:00:00Z"))  # Samstag

    def test_fx_majors_have_pip_and_swap(self) -> None:
        by_sym = {i.canonical_symbol: i for i in seed_instruments()}
        for sym in ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD"):
            assert by_sym[sym].pip_size is not None and by_sym[sym].pip_size > 0
            assert by_sym[sym].swap_basis == "points_per_lot_per_day"
        assert by_sym["USDJPY"].pip_size == 0.01
        assert by_sym["EURUSD"].pip_size == 0.0001

    def test_us_equity_hours_and_holiday(self) -> None:
        us = seed_calendars()["us_equity"]
        # Mittwoch 15:00 UTC = 11:00 ET -> offen
        assert us.is_open(parse_timestamp("2024-06-05T15:00:00Z"))
        # 21:00 UTC = 17:00 ET -> zu
        assert not us.is_open(parse_timestamp("2024-06-05T21:00:00Z"))
        # 4. Juli -> Feiertag
        assert not us.is_open(parse_timestamp("2024-07-04T15:00:00Z"))
        # Samstag -> zu
        assert not us.is_open(parse_timestamp("2024-06-08T15:00:00Z"))

    def test_next_open(self) -> None:
        us = seed_calendars()["us_equity"]
        # Samstag -> nächstes Open Montag 13:30 UTC
        nxt = us.next_open(parse_timestamp("2024-06-08T12:00:00Z"))
        assert nxt is not None
        assert nxt == parse_timestamp("2024-06-10T13:30:00Z")


class TestSessionResolution:
    def test_summer_offsets(self) -> None:
        specs = {s.name: s for s in seed_sessions()}
        london = resolve_session(specs[SessionName.LONDON], date(2024, 6, 3))
        assert london.start == parse_timestamp("2024-06-03T07:00:00Z")  # BST
        ny = resolve_session(specs[SessionName.NEW_YORK], date(2024, 6, 3))
        assert ny.start == parse_timestamp("2024-06-03T13:30:00Z")  # EDT

    def test_winter_offsets_differ(self) -> None:
        specs = {s.name: s for s in seed_sessions()}
        london_w = resolve_session(specs[SessionName.LONDON], date(2024, 1, 15))
        assert london_w.start == parse_timestamp("2024-01-15T08:00:00Z")  # GMT

    def test_active_sessions(self) -> None:
        names = {
            w.name
            for w in active_sessions(seed_sessions(), parse_timestamp("2024-06-03T14:00:00Z"))
        }
        assert SessionName.LONDON in names
        assert SessionName.NEW_YORK in names
        assert SessionName.ASIA not in names


class TestCorporateAction:
    def test_split_needs_ratio(self) -> None:
        with pytest.raises(ValidationError):
            CorporateAction(
                symbol="AAPL",
                action_type=CorporateActionType.SPLIT,
                ex_date="2024-06-10T00:00:00Z",
                available_time="2024-05-01T00:00:00Z",
            )

    def test_valid_split_announced_early(self) -> None:
        ca = CorporateAction(
            symbol="AAPL",
            action_type=CorporateActionType.SPLIT,
            ex_date="2024-06-10T00:00:00Z",
            available_time="2024-05-01T00:00:00Z",
            ratio=4.0,
        )
        assert ca.ratio == 4.0

    def test_dividend_needs_amount(self) -> None:
        with pytest.raises(ValidationError):
            CorporateAction(
                symbol="AAPL",
                action_type=CorporateActionType.DIVIDEND,
                ex_date="2024-06-10T00:00:00Z",
                available_time="2024-06-01T00:00:00Z",
            )


class TestSessionSpec:
    def test_crosses_midnight_allows_end_before_start(self) -> None:
        SessionSpec(
            name=SessionName.ASIA,
            tz="Asia/Tokyo",
            start=time(22, 0),
            end=dtime(6, 0),
            crosses_midnight=True,
        )

    def test_end_before_start_without_flag_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SessionSpec(name=SessionName.ASIA, tz="Asia/Tokyo", start=time(22, 0), end=dtime(6, 0))
