"""Referenzdaten-Modelle: Instrument-Master, Symbol-Mapping, Sessions, Kalender, Corporate Actions.

Referenzdaten sind eine eigene Domäne mit eigener Lebensdauer und eigenen Quellen. Sie sind
**Multi-Asset** angelegt: Crypto (inkl. Perpetuals), Gold/XAUUSD, Forex, Aktien, ETFs.

Phase 1 liefert die Modelle + eine kleine eingebaute Seed-Menge (``refdata.seed``). Echte
Feeds/Provider für Referenzdaten kommen später.
"""

from __future__ import annotations

from datetime import date, time

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading_agent.core.enums import (
    AssetClass,
    CorporateActionType,
    Exchange,
    SessionName,
    TradingPriority,
)
from trading_agent.core.models import UtcDatetime
from trading_agent.core.version import SCHEMA_VERSION


class RefRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default=SCHEMA_VERSION)


# --------------------------------------------------------------------------------------------
# Symbol-Mapping
# --------------------------------------------------------------------------------------------


class SymbolMapping(RefRecord):
    """Verknüpft ein **kanonisches** Symbol mit dem Symbol einer konkreten Quelle/Börse.

    Kanonisch ist im System immer gesetzt (z.B. ``BTCUSDT``, ``XAUUSD``, ``AAPL``).
    ``provider_symbol`` ist die quellenspezifische Schreibweise (z.B. ``BTC-USD``, ``XBTUSD``).
    """

    canonical: str
    source: str  # Provider-/Exchange-Id, z.B. "bybit", "binance", "synthetic"
    provider_symbol: str
    aliases: tuple[str, ...] = ()


# --------------------------------------------------------------------------------------------
# Sessions & Trading-Calendar
# --------------------------------------------------------------------------------------------


class SessionSpec(RefRecord):
    """Definition eines intraday Liquiditätsfensters in **Börsenlokalzeit**.

    Wird zur Laufzeit DST-sicher nach UTC aufgelöst (``refdata.calendar.resolve_session``).
    ``crosses_midnight=True`` z.B. für die Asia-Session.
    """

    name: SessionName
    tz: str  # IANA, z.B. "Europe/London"
    start: time
    end: time
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)  # 0 = Montag ... 6 = Sonntag
    crosses_midnight: bool = False

    @model_validator(mode="after")
    def _check(self) -> SessionSpec:
        if not self.crosses_midnight and self.end <= self.start:
            raise ValueError(f"Session {self.name}: end <= start ohne crosses_midnight")
        for d in self.weekdays:
            if not 0 <= d <= 6:
                raise ValueError(f"weekday außerhalb 0..6: {d}")
        return self


class HalfDay(RefRecord):
    day: date
    close: time  # vorzeitige Schlusszeit in Börsenlokalzeit


class TradingCalendarSpec(RefRecord):
    """Handelskalender einer Börse / eines Instruments.

    ``is_24_7=True`` (Crypto): immer offen. Sonst definieren ``timezone`` + ``regular_open`` /
    ``regular_close`` + ``weekmask`` die reguläre Handelszeit; ``holidays`` und ``half_days``
    modifizieren einzelne Tage. ``weekend_gap=True`` (Forex): Wochenende geschlossen, aber
    innerhalb der Woche 24h.
    """

    calendar_id: str
    timezone: str = "UTC"
    is_24_7: bool = False
    weekend_gap: bool = False
    weekmask: tuple[int, ...] = (0, 1, 2, 3, 4)
    regular_open: time = time(0, 0)
    regular_close: time = time(23, 59, 59)
    holidays: tuple[date, ...] = ()
    half_days: tuple[HalfDay, ...] = ()

    # Tägliche Wartungs-/Rollover-Pause (z. B. CME-Gold 21:00–22:00 UTC). In ``timezone``.
    # Gilt auch für ``weekend_gap``-Kalender (Forex/XAU). ``None`` = keine Pause.
    daily_break_start: time | None = None
    daily_break_end: time | None = None

    @model_validator(mode="after")
    def _check(self) -> TradingCalendarSpec:
        if self.is_24_7 and self.weekend_gap:
            raise ValueError("is_24_7 und weekend_gap schließen sich aus")
        if (self.daily_break_start is None) != (self.daily_break_end is None):
            raise ValueError("daily_break_start und daily_break_end nur gemeinsam setzen")
        return self


# --------------------------------------------------------------------------------------------
# Corporate Actions
# --------------------------------------------------------------------------------------------


class CorporateAction(RefRecord):
    """Kapitalmaßnahme (v.a. Aktien). Strikt Point-in-Time über ``available_time``.

    * ``SPLIT`` / ``REVERSE_SPLIT`` – ``ratio`` = neue Stücke je altem Stück (z.B. 4.0 = 4:1).
    * ``DIVIDEND`` – ``cash_amount`` je Aktie in Instrument-Quote-Währung.
    * ``SYMBOL_CHANGE`` – ``new_symbol`` gesetzt.
    """

    symbol: str
    action_type: CorporateActionType
    ex_date: UtcDatetime
    available_time: UtcDatetime
    ratio: float | None = None
    cash_amount: float | None = None
    new_symbol: str | None = None

    @model_validator(mode="after")
    def _check(self) -> CorporateAction:
        is_split = self.action_type in (
            CorporateActionType.SPLIT,
            CorporateActionType.REVERSE_SPLIT,
        )
        if is_split and (self.ratio is None or self.ratio <= 0):
            raise ValueError(f"{self.action_type} braucht ratio > 0")
        if self.action_type is CorporateActionType.DIVIDEND and self.cash_amount is None:
            raise ValueError("DIVIDEND braucht cash_amount")
        if self.action_type is CorporateActionType.SYMBOL_CHANGE and not self.new_symbol:
            raise ValueError("SYMBOL_CHANGE braucht new_symbol")
        # Hinweis: available_time DARF vor ex_date liegen (Ankündigung im Voraus). Die
        # Point-in-Time-Regel erzwingt nur, dass eine Maßnahme nie *vor* ihrer available_time
        # angewandt wird – das prüft der Consumer (refdata.corporate_actions), nicht das Modell.
        return self


# --------------------------------------------------------------------------------------------
# Fees & Margin
# --------------------------------------------------------------------------------------------


class FeeSchedule(RefRecord):
    maker_bps: float = 2.0
    taker_bps: float = 5.5

    @model_validator(mode="after")
    def _check(self) -> FeeSchedule:
        if self.maker_bps < 0 or self.taker_bps < 0:
            raise ValueError("Gebühren dürfen nicht negativ sein")
        return self


class MarginTier(RefRecord):
    """Ein Margin-Tier (v.a. Crypto-Perps). Nach ``notional_floor`` aufsteigend."""

    notional_floor: float = 0.0
    max_leverage: float = 1.0
    maintenance_margin_rate: float = 0.005

    @model_validator(mode="after")
    def _check(self) -> MarginTier:
        if self.notional_floor < 0:
            raise ValueError("notional_floor < 0")
        if self.max_leverage < 1:
            raise ValueError("max_leverage < 1")
        if not 0 < self.maintenance_margin_rate < 1:
            raise ValueError("maintenance_margin_rate außerhalb (0,1)")
        return self


# --------------------------------------------------------------------------------------------
# Instrument-Master
# --------------------------------------------------------------------------------------------


class Instrument(RefRecord):
    """Ein handelbares Instrument – der brokerunabhängige Referenzdatensatz.

    Preis-/Größenregeln (``tick_size``, ``lot_size``, ``min_notional``), Kontraktdaten
    (``contract_multiplier``, ``is_inverse``, Perp-Flag) sowie Risiko-/Ausführungs-Metadaten
    (``fees``, ``margin_tiers``, ``max_leverage``) sind hier gebündelt. Die spätere Risk Engine
    (Phase 4) liest daraus – Phase 1 legt nur die Struktur an.
    """

    canonical_symbol: str
    asset_class: AssetClass
    exchange: Exchange
    base_currency: str
    quote_currency: str
    settle_currency: str | None = None

    tick_size: float = Field(gt=0)
    lot_size: float = Field(gt=0)
    min_notional: float = Field(default=0.0, ge=0)
    price_precision: int = Field(default=2, ge=0)
    size_precision: int = Field(default=8, ge=0)

    contract_multiplier: float = Field(default=1.0, gt=0)
    is_perpetual: bool = False
    is_inverse: bool = False
    funding_interval_hours: float | None = None

    # FX / Metalle: Pip-Größe (z. B. 0.0001 EURUSD, 0.01 USDJPY, 0.1 XAUUSD) und Broker-Swap
    # (Rollover) in **Punkten je Lot je Nacht**, long/short getrennt. ``None`` / 0.0 = nicht
    # gesetzt (Crypto-Perps nutzen stattdessen ``funding_interval_hours``). Rein deskriptiv —
    # die Strategy Engine liest diese Felder nicht, nur Kosten-/Risikomodell und Reporting.
    pip_size: float | None = None
    swap_long_points: float = 0.0
    swap_short_points: float = 0.0
    swap_basis: str = "points_per_lot_per_day"

    trading_priority: TradingPriority = TradingPriority.TIER_3
    calendar_id: str = "always_open"

    fees: FeeSchedule = Field(default_factory=FeeSchedule)
    margin_tiers: tuple[MarginTier, ...] = (MarginTier(),)
    max_leverage: float = Field(default=1.0, ge=1)

    is_active: bool = True
    listed_at: UtcDatetime | None = None
    delisted_at: UtcDatetime | None = None
    tags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> Instrument:
        if self.is_perpetual and self.funding_interval_hours is None:
            raise ValueError("Perpetual-Instrument braucht funding_interval_hours")
        if (
            self.delisted_at is not None
            and self.listed_at is not None
            and self.delisted_at <= self.listed_at
        ):
            raise ValueError("delisted_at <= listed_at")
        floors = [t.notional_floor for t in self.margin_tiers]
        if floors != sorted(floors):
            raise ValueError("margin_tiers müssen nach notional_floor aufsteigend sein")
        return self

    def is_tradeable_at(self, moment: UtcDatetime) -> bool:
        """Point-in-Time: war das Instrument zum Zeitpunkt ``moment`` gelistet & aktiv?

        Schützt Backtests vor Survivorship-Bias (kein Handel in noch nicht gelisteten oder
        bereits delisteten Instrumenten).
        """
        if self.listed_at is not None and moment < self.listed_at:
            return False
        if self.delisted_at is not None and moment >= self.delisted_at:
            return False
        return self.is_active


__all__ = [
    "CorporateAction",
    "FeeSchedule",
    "HalfDay",
    "Instrument",
    "MarginTier",
    "RefRecord",
    "SessionSpec",
    "SymbolMapping",
    "TradingCalendarSpec",
]
