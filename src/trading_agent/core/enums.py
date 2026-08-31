"""Enumerationen für Data Foundation **und** Strategy Engine.

Phase 1 definierte nur die Daten-/Referenzschicht. Ab Phase 3 (``strategy_version 0.1.1``) kommen
die Strategie-Domänen-Enums hinzu (``Direction``, ``Polarity``, Regime-Achsen, Primitive-Zustände,
``SetupState``, ``DecisionType``, ``NoTradeReason``, ``VetoId`` …). Verbindliche Definitionen:
``docs/strategy/`` (``primitives.md``, ``regime.md``, ``no-trade.md``, ``contradictions.md``,
``setups/SMC-SWEEP-REV-01.md``, ``SPEC-ADDENDUM-0.1.1.md``).
"""

from __future__ import annotations

from enum import StrEnum


class Timeframe(StrEnum):
    """Kerzen-Timeframes. ``seconds`` liefert die Dauer für Alignment/Resampling."""

    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"
    W1 = "W1"

    @property
    def seconds(self) -> int:
        return {
            "M1": 60,
            "M5": 300,
            "M15": 900,
            "M30": 1800,
            "H1": 3600,
            "H4": 14400,
            "D1": 86400,
            "W1": 604800,
        }[self.value]

    @property
    def is_intraday(self) -> bool:
        return self.seconds < 86400

    @classmethod
    def ordered(cls) -> list[Timeframe]:
        """Von klein nach groß."""
        return sorted(cls, key=lambda tf: tf.seconds)


class AssetClass(StrEnum):
    CRYPTO = "crypto"
    ALTCOIN = "altcoin"
    GOLD = "gold"
    FOREX = "forex"
    EQUITY = "equity"
    ETF = "etf"


class Exchange(StrEnum):
    """Handelsplätze / Datenquellen. Erweiterbar."""

    BYBIT = "bybit"
    BINANCE = "binance"
    COINBASE = "coinbase"
    OANDA = "oanda"
    NASDAQ = "nasdaq"
    NYSE = "nyse"
    ARCA = "arca"
    LSE = "lse"
    XETRA = "xetra"
    SYNTHETIC = "synthetic"  # Mock-/Testdaten


class TradingPriority(StrEnum):
    """Priorität für den späteren autonomen Scanner (Phase 5). In Phase 1 nur Metadatum."""

    TIER_1 = "tier_1"  # Gold/XAUUSD, BTC
    TIER_2 = "tier_2"  # liquide Altcoins, ausgewählte liquide Aktien
    TIER_3 = "tier_3"  # ETFs, langfristige Investments


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class SessionName(StrEnum):
    ASIA = "asia"
    LONDON = "london"
    NEW_YORK = "new_york"
    LONDON_NY_OVERLAP = "london_ny_overlap"


class CorporateActionType(StrEnum):
    SPLIT = "split"
    REVERSE_SPLIT = "reverse_split"
    DIVIDEND = "dividend"
    SYMBOL_CHANGE = "symbol_change"
    MERGER = "merger"
    DELISTING = "delisting"


class NewsImpact(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DataQualitySeverity(StrEnum):
    """Schweregrad eines Datenqualitätsbefunds.

    ``CRITICAL`` bedeutet: die spätere Strategy Engine muss auf diesem Instrument/Timeframe
    ``NO_TRADE`` erzwingen. ``WARNING`` = nutzbar, aber protokollieren. ``INFO`` = Hinweis.
    """

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"info": 0, "warning": 1, "critical": 2}[self.value]


class DataQualityCode(StrEnum):
    """Stabile Codes für Datenqualitätsbefunde (append-only, nie umbenennen)."""

    MISSING_BAR = "missing_bar"
    DUPLICATE_BAR = "duplicate_bar"
    OUT_OF_ORDER = "out_of_order"
    INVALID_OHLC = "invalid_ohlc"
    INVALID_VOLUME = "invalid_volume"
    STALE_DATA = "stale_data"
    TIMESTAMP_NOT_UTC = "timestamp_not_utc"
    TIMESTAMP_MISALIGNED = "timestamp_misaligned"
    TIMESTAMP_IN_FUTURE = "timestamp_in_future"
    DST_AMBIGUOUS = "dst_ambiguous"
    GAP = "gap"
    SYMBOL_MISMATCH = "symbol_mismatch"
    TIMEFRAME_MISMATCH = "timeframe_mismatch"
    EMPTY_SERIES = "empty_series"
    FEED_UNHEALTHY = "feed_unhealthy"


class ProviderHealth(StrEnum):
    """Zustand eines Datenproviders. Jeder Provider MUSS diese drei Zustände liefern können."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"

    @property
    def rank(self) -> int:
        return {"healthy": 0, "degraded": 1, "unavailable": 2}[self.value]


class DataKind(StrEnum):
    """Art eines Marktdatenstroms – für Provider-Registry und Repository-Partitionierung."""

    OHLCV = "ohlcv"
    QUOTE = "quote"
    TRADE = "trade"
    ORDERBOOK = "orderbook"
    FUNDING = "funding"
    OPEN_INTEREST = "open_interest"
    NEWS = "news"
    MACRO = "macro"


# ============================================================================================
# Strategy Engine — Domänen-Enums (Phase 3, strategy_version 0.1.1)
# ============================================================================================


class Direction(StrEnum):
    """Handelsrichtung ``D`` eines Setups (``SMC-SWEEP-REV-01`` §0)."""

    LONG = "long"
    SHORT = "short"

    @property
    def opposite(self) -> Direction:
        return Direction.SHORT if self is Direction.LONG else Direction.LONG

    @property
    def side(self) -> Side:
        """Broker-Order-Seite für den Entry in diese Richtung."""
        return Side.BUY if self is Direction.LONG else Side.SELL

    @property
    def sign(self) -> int:
        return 1 if self is Direction.LONG else -1


class Bias(StrEnum):
    """HTF-Bias (``SMC-SWEEP-REV-01`` §2). ``NONE`` ⇒ ``NO_TRADE``."""

    LONG = "long"
    SHORT = "short"
    NONE = "none"

    def as_direction(self) -> Direction | None:
        if self is Bias.LONG:
            return Direction.LONG
        if self is Bias.SHORT:
            return Direction.SHORT
        return None


class Polarity(StrEnum):
    """Polarität von Struktur-Brüchen, Displacement, Zonen, Liquidität."""

    BULLISH = "bullish"
    BEARISH = "bearish"

    @property
    def opposite(self) -> Polarity:
        return Polarity.BEARISH if self is Polarity.BULLISH else Polarity.BULLISH

    @classmethod
    def of(cls, direction: Direction) -> Polarity:
        return cls.BULLISH if direction is Direction.LONG else cls.BEARISH


class MarketSide(StrEnum):
    """Seite einer Liquiditäts-Ansammlung (``primitives.md`` §4)."""

    BUY_SIDE = "buy_side"  # über Hochs — Buy-Stops
    SELL_SIDE = "sell_side"  # unter Tiefs — Sell-Stops

    @classmethod
    def against(cls, direction: Direction) -> MarketSide:
        """Die Liquidität, die *entgegen* ``D`` liegt (das Setup-Ziel des Sweeps)."""
        return cls.SELL_SIDE if direction is Direction.LONG else cls.BUY_SIDE


# ---- Market Regime (regime.md) -------------------------------------------------------------


class RegimeDirectional(StrEnum):
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    RANGE = "range"
    UNCLEAR = "unclear"
    CONFLICTING = "conflicting"


class RegimeVolatility(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    EXTREME = "extreme"


class RegimePhase(StrEnum):
    EXPANSION = "expansion"
    COMPRESSION = "compression"
    NEUTRAL = "neutral"


class ExpansionDirection(StrEnum):
    UP = "up"
    DOWN = "down"
    NONE = "none"


# ---- Macro / News (analysis/macro.py, analysis/news.py) ----------------------------------


class MacroTrend(StrEnum):
    """Richtung einer makroökonomischen Zeitreihe über das Bewertungsfenster."""

    RISING = "rising"
    FALLING = "falling"
    STABLE = "stable"
    UNKNOWN = "unknown"  # keine (ausreichende) PIT-Historie — kein Fake


class MacroRateCycle(StrEnum):
    """Geldpolitischer Zyklus der relevanten Zentralbank (Leitzins-Pfad)."""

    TIGHTENING = "tightening"
    EASING = "easing"
    HOLD = "hold"
    UNKNOWN = "unknown"


class MacroRiskSentiment(StrEnum):
    """Aggregiertes Risiko-Sentiment aus Cross-Asset-Proxies (VIX / DXY / Yields)."""

    RISK_ON = "risk_on"
    RISK_OFF = "risk_off"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


# ---- Primitives (primitives.md) -----------------------------------------------------------


class SwingType(StrEnum):
    SWING_HIGH = "swing_high"
    SWING_LOW = "swing_low"


class SwingLabel(StrEnum):
    """HH/HL/LH/LL bzw. EQUAL innerhalb Toleranz (``primitives.md`` §1)."""

    HH = "hh"
    HL = "hl"
    LH = "lh"
    LL = "ll"
    EQUAL = "equal"


class StructureBreakKind(StrEnum):
    BOS = "bos"  # Bruch in Trendrichtung
    CHOCH = "choch"  # erster Bruch gegen die Struktur


class StructureOrigin(StrEnum):
    """Herkunft eines ``StructureBreak`` (``primitives.md`` §2)."""

    TREND = "trend"  # Bruch eines Swings innerhalb gerichteter Struktur
    RANGE = "range"  # Close jenseits einer Range-Grenze (§2.3, keine gerichtete Struktur)


class LiquidityType(StrEnum):
    SWING_HIGH = "swing_high"
    SWING_LOW = "swing_low"
    EQUAL_HIGHS = "equal_highs"
    EQUAL_LOWS = "equal_lows"
    PDH = "pdh"
    PDL = "pdl"
    PWH = "pwh"
    PWL = "pwl"
    SESSION_HIGH = "session_high"
    SESSION_LOW = "session_low"
    RANGE_HIGH = "range_high"
    RANGE_LOW = "range_low"


class LiquidityState(StrEnum):
    UNSWEPT = "unswept"
    SWEPT = "swept"
    BROKEN = "broken"


class ZoneKind(StrEnum):
    FVG = "fvg"
    IFVG = "ifvg"
    ORDER_BLOCK = "order_block"
    BREAKER = "breaker"


class OrderBlockZone(StrEnum):
    """Zonen-Definition eines Order Blocks (``primitives.ob.zone``, ``primitives.md`` §10)."""

    FULL_RANGE = "full_range"  # [low, high] der OB-Bar (PROPOSED DEFAULT)
    BODY = "body"  # [min(open,close), max(open,close)]
    OPEN_TO_EXTREME = "open_to_extreme"  # bull: [low, open] · bear: [open, high]


class ZoneState(StrEnum):
    UNMITIGATED = "unmitigated"
    PARTIAL = "partial"
    MITIGATED = "mitigated"
    STALE = "stale"
    INVERTED = "inverted"


class PDZone(StrEnum):
    DISCOUNT = "discount"
    EQUILIBRIUM = "equilibrium"
    PREMIUM = "premium"


class PDReference(StrEnum):
    DEALING_RANGE = "dealing_range"
    LAST_IMPULSE_LEG = "last_impulse_leg"
    SESSION_RANGE = "session_range"
    SWEPT_LEG = "swept_leg"


# ---- Setup State Machine (SMC-SWEEP-REV-01 §24) ------------------------------------------


class SetupState(StrEnum):
    SCANNING = "scanning"
    BIAS_SET = "bias_set"
    LIQUIDITY_IDENTIFIED = "liquidity_identified"
    SWEPT = "swept"
    RECLAIMED = "reclaimed"
    DISPLACED = "displaced"
    STRUCTURE_SHIFTED = "structure_shifted"
    ARMED = "armed"
    TRIGGERED = "triggered"
    MANAGED = "managed"
    CLOSED = "closed"
    REVIEW = "review"

    @property
    def is_forming(self) -> bool:
        """Kette lebt, aber noch nicht ``ARMED`` ⇒ ``evaluate()`` liefert ``WAIT`` (C6)."""
        return self in {
            SetupState.BIAS_SET,
            SetupState.LIQUIDITY_IDENTIFIED,
            SetupState.SWEPT,
            SetupState.RECLAIMED,
            SetupState.DISPLACED,
            SetupState.STRUCTURE_SHIFTED,
        }


class DisplayAlias(StrEnum):
    """Anzeige-Aliase für Dashboard/Scanner (``SMC-SWEEP-REV-01`` §24.1). Nicht die interne FSM."""

    WATCH = "watch"
    DEVELOPING = "developing"
    ARMED = "armed"
    CONFIRMED = "confirmed"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"

    @classmethod
    def of(cls, state: SetupState) -> DisplayAlias:
        return {
            SetupState.SCANNING: cls.WATCH,
            SetupState.BIAS_SET: cls.WATCH,
            SetupState.LIQUIDITY_IDENTIFIED: cls.DEVELOPING,
            SetupState.SWEPT: cls.DEVELOPING,
            SetupState.RECLAIMED: cls.DEVELOPING,
            SetupState.DISPLACED: cls.DEVELOPING,
            SetupState.STRUCTURE_SHIFTED: cls.DEVELOPING,
            SetupState.ARMED: cls.ARMED,
            SetupState.TRIGGERED: cls.CONFIRMED,
            SetupState.MANAGED: cls.CONFIRMED,
            SetupState.CLOSED: cls.EXPIRED,
            SetupState.REVIEW: cls.EXPIRED,
        }[state]


class ConfirmationPattern(StrEnum):
    """Confirmation-Entry-Muster (``SPEC-ADDENDUM-0.1.1.md`` §2)."""

    ENGULFING = "engulfing"
    PIN = "pin"
    MINOR_CHOCH = "minor_choch"


class EntryMode(StrEnum):
    LIMIT_AT_PROXIMAL_EDGE = "limit_at_proximal_edge"
    LIMIT_AT_MID = "limit_at_mid"
    CONFIRMATION_MARKET = "confirmation_market"


# ---- Decision output (SPEC-ADDENDUM-0.1.1 §1) --------------------------------------------


class DecisionType(StrEnum):
    BUY = "buy"
    SELL = "sell"
    WAIT = "wait"
    NO_TRADE = "no_trade"

    @classmethod
    def entry(cls, direction: Direction) -> DecisionType:
        return cls.BUY if direction is Direction.LONG else cls.SELL


class RiskTier(StrEnum):
    A_PLUS = "A+"
    A = "A"
    B = "B"
    NO_TRADE = "NO_TRADE"


class VetoId(StrEnum):
    """Harte Vetos (``contradictions.md`` §4, ``SMC-SWEEP-REV-01`` §23). Laufen VOR dem Score."""

    V1 = "V1"  # HTF-Bias-Konflikt (D1/H4 gegensätzlich)
    V2 = "V2"  # Entry-Location falsch (nicht Discount/Premium des swept_leg)
    V3 = "V3"  # Regime untauglich (EXTREME / UNCLEAR / verbotene phase)
    V4 = "V4"  # News (HIGH-Impact-Blackout / Pre-Positioning-Ban / Feed-Ausfall)
    V5 = "V5"  # Kein echter Sweep (wurde Breakout)
    V6 = "V6"  # Daten unsicher (data_confidence < floor)
    V7 = "V7"  # Ausführung untauglich (Spread/Slippage/Tiefe/Datenalter)
    V8 = "V8"  # RR ungenügend
    V9 = "V9"  # Korrelierte Exposure (pass-through ohne portfolio_context, 0.1.1 C9)
    V10 = "V10"  # Kein regelkonformer SL definierbar


class NoTradeReason(StrEnum):
    """Stabil versioniert — neue Gründe nur ANHÄNGEN, nie umbenennen (``no-trade.md``)."""

    # [1] System / Safety
    KILL_SWITCH_GLOBAL = "kill_switch_global"
    KILL_SWITCH_BROKER = "kill_switch_broker"
    KILL_SWITCH_ASSET = "kill_switch_asset"
    KILL_SWITCH_STRATEGY = "kill_switch_strategy"
    KILL_SWITCH_DATA = "kill_switch_data"
    SYSTEM_STARTING_UP = "system_starting_up"
    RECONCILIATION_PENDING = "reconciliation_pending"
    UNHANDLED_ERROR_STATE = "unhandled_error_state"
    # [2] Daten
    DATA_INCOMPLETE = "data_incomplete"
    DATA_STALE = "data_stale"
    DATA_GAP_RECENT = "data_gap_recent"
    DATA_DUPLICATE = "data_duplicate"
    DATA_TIMESTAMP_INVALID = "data_timestamp_invalid"
    DATA_PRICE_ANOMALY = "data_price_anomaly"
    DATA_SOURCE_UNHEALTHY = "data_source_unhealthy"
    DATA_CONFIDENCE_FLOOR = "data_confidence_floor"
    CLOCK_DRIFT = "clock_drift"
    # [3] Regime
    REGIME_UNCLEAR = "regime_unclear"
    REGIME_CONFLICTING = "regime_conflicting"
    REGIME_VOL_EXTREME = "regime_vol_extreme"
    REGIME_VOL_TOO_LOW = "regime_vol_too_low"
    REGIME_COMPRESSION = "regime_compression"
    REGIME_COOLDOWN = "regime_cooldown"
    REGIME_NOT_ALLOWED_FOR_SETUP = "regime_not_allowed_for_setup"
    # [4] Zeit / Session
    SESSION_NOT_ALLOWED = "session_not_allowed"
    SESSION_OPEN_BUFFER = "session_open_buffer"
    WEEKEND = "weekend"
    PRE_WEEKEND_BUFFER = "pre_weekend_buffer"
    MARKET_CLOSED = "market_closed"
    ROLLOVER_WINDOW = "rollover_window"
    # [5] News
    NEWS_BLACKOUT_HIGH = "news_blackout_high"
    NEWS_BLACKOUT_MEDIUM = "news_blackout_medium"
    NEWS_PRE_POSITIONING_BAN = "news_pre_positioning_ban"
    NEWS_FEED_UNAVAILABLE = "news_feed_unavailable"
    NEWS_RISK_OFF_FLAG = "news_risk_off_flag"
    # [6] Risk / Portfolio
    DAILY_LOSS_LIMIT = "daily_loss_limit"
    WEEKLY_LOSS_LIMIT = "weekly_loss_limit"
    MAX_DRAWDOWN = "max_drawdown"
    MAX_TRADES_TODAY = "max_trades_today"
    MAX_OPEN_POSITIONS = "max_open_positions"
    MAX_TOTAL_EXPOSURE = "max_total_exposure"
    MAX_CORRELATED_EXPOSURE = "max_correlated_exposure"
    PORTFOLIO_HEAT = "portfolio_heat"
    RISK_BUDGET_EXHAUSTED = "risk_budget_exhausted"
    SIZE_BELOW_MIN = "size_below_min"
    INSUFFICIENT_MARGIN = "insufficient_margin"
    LIQUIDATION_TOO_CLOSE = "liquidation_too_close"
    FUNDING_COST_EXCESSIVE = "funding_cost_excessive"
    LOSS_STREAK_REVIEW = "loss_streak_review"
    # [7] Strategy-State
    DUPLICATE_POSITION = "duplicate_position"
    DUPLICATE_ARMED_SETUP = "duplicate_armed_setup"
    OPPOSITE_POSITION_OPEN = "opposite_position_open"
    COOLDOWN_AFTER_STOP = "cooldown_after_stop"
    COOLDOWN_AFTER_SWEEP_FAIL = "cooldown_after_sweep_fail"
    SETUP_VERSION_MISMATCH = "setup_version_mismatch"
    # [8] Execution
    SPREAD_TOO_WIDE = "spread_too_wide"
    SLIPPAGE_ESTIMATE_HIGH = "slippage_estimate_high"
    LIQUIDITY_THIN = "liquidity_thin"
    API_DEGRADED = "api_degraded"
    DATA_AGE_EXECUTION = "data_age_execution"
    # Setup-spezifisch (SMC-SWEEP-REV-01 §22)
    BIAS_NONE = "bias_none"
    BIAS_TOO_WEAK = "bias_too_weak"
    NO_QUALIFYING_LIQUIDITY = "no_qualifying_liquidity"
    SWEEP_BECAME_BREAKOUT = "sweep_became_breakout"
    NO_RECLAIM = "no_reclaim"
    NO_DISPLACEMENT = "no_displacement"
    NO_STRUCTURE_SHIFT = "no_structure_shift"
    NO_ENTRY_ZONE = "no_entry_zone"
    ENTRY_WRONG_SIDE_OF_EQUILIBRIUM = "entry_wrong_side_of_equilibrium"
    SL_TOO_WIDE = "sl_too_wide"
    SL_TOO_TIGHT = "sl_too_tight"
    RR_BELOW_MIN = "rr_below_min"
    CONFIDENCE_BELOW_MIN = "confidence_below_min"
    SCORE_BELOW_B = "score_below_b"
    NEWS_BLACKOUT = "news_blackout"
    SESSION_FILTER = "session_filter"
    EXECUTION_FILTER = "execution_filter"
    PORTFOLIO_CORRELATION = "portfolio_correlation"
    DUPLICATE_EXPOSURE = "duplicate_exposure"
    # Kandidaten-Invalidierung während ARMED (invalidation.md §2)
    CANDIDATE_INVALIDATED = "candidate_invalidated"
    CANDIDATE_EXPIRED = "candidate_expired"
    # Contradiction-Matrix (contradictions.md §4 — matrix-eigene harte Ausgänge)
    OPPOSING_LIQUIDITY_BREAKOUT = "opposing_liquidity_breakout"  # C1
    MESSY_LIQUIDITY = "messy_liquidity"  # C2
    ENTRY_INTO_OPPOSING_HTF_ZONE = "entry_into_opposing_htf_zone"  # C9 (Überlappung > 50 %)
    COUNTER_SETUP_CONFLICT = "counter_setup_conflict"  # C12


__all__ = [
    "AssetClass",
    "Bias",
    "ConfirmationPattern",
    "CorporateActionType",
    "DataKind",
    "DataQualityCode",
    "DataQualitySeverity",
    "DecisionType",
    "Direction",
    "DisplayAlias",
    "EntryMode",
    "Exchange",
    "ExpansionDirection",
    "LiquidityState",
    "LiquidityType",
    "MacroRateCycle",
    "MacroRiskSentiment",
    "MacroTrend",
    "MarketSide",
    "NewsImpact",
    "NoTradeReason",
    "OrderBlockZone",
    "PDReference",
    "PDZone",
    "Polarity",
    "ProviderHealth",
    "RegimeDirectional",
    "RegimePhase",
    "RegimeVolatility",
    "RiskTier",
    "SessionName",
    "SetupState",
    "Side",
    "StructureBreakKind",
    "StructureOrigin",
    "SwingLabel",
    "SwingType",
    "Timeframe",
    "TradingPriority",
    "VetoId",
    "ZoneKind",
    "ZoneState",
]
