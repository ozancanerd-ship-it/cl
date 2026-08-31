"""Welche News/Macro-Ereignisse für welche Asset-Klasse zählen — **Referenztabelle**.

Der News-Fail-safe (``NoTradeParams.require_news_feed``) blockt aktuell **global** jeden Entry,
wenn kein PIT-News-Feed da ist (``docs/CONTINUOUS_IMPROVEMENT.md`` §6j: 100 % Block). Diese
Tabelle hält fest, *welche* Ereignisse pro Asset-Klasse überhaupt relevant sind — als
Vorbereitung dafür, den Fail-safe später **pro Asset-Klasse** zu differenzieren.

**WICHTIG (Projekt-Constraint):** keine automatische Lockerung ohne OOS-Nachweis. Alle
``require_feed`` bleiben ``True`` (= aktuelles konservatives Verhalten), bis eine OOS-Analyse
zeigt, dass eine Differenzierung die Entry-Qualität nicht verschlechtert. Diese Datei ändert
**kein** Laufzeitverhalten — sie dokumentiert die Absicht und stellt den Hook bereit.
"""

from __future__ import annotations

import dataclasses

from trading_agent.core.enums import AssetClass

# kanonische Event-Typen (vgl. data.providers.news_calendar.CANONICAL_EVENTS)
_MACRO_USD = frozenset(
    {"FOMC_RATE", "FOMC_MINUTES", "US_CPI", "US_CORE_CPI", "US_PCE", "US_CORE_PCE", "US_NFP"}
)
_MACRO_WIDE = _MACRO_USD | frozenset({"US_UNEMPLOYMENT", "US_GDP", "US_RETAIL_SALES"})
_CENTRAL_BANKS = frozenset({"FOMC_RATE", "FOMC_MINUTES", "ECB_RATE", "ECB_PRESS_CONF"})


@dataclasses.dataclass(frozen=True, slots=True)
class NewsRelevance:
    asset_class: AssetClass
    relevant_event_types: frozenset[str]
    # Fail-safe-Strenge, wenn KEIN Feed verfügbar ist. True = blocken (aktuell für alle).
    require_feed: bool
    # Zusätzlicher Kontext, der für diese Klasse wünschenswert ist (noch nicht verdrahtet).
    wants_context: tuple[str, ...]
    note: str


NEWS_RELEVANCE: dict[AssetClass, NewsRelevance] = {
    AssetClass.CRYPTO: NewsRelevance(
        AssetClass.CRYPTO,
        _MACRO_USD,  # Krypto reagiert stark auf US-Makro/Risk-off; dazu Krypto-spezifisch
        require_feed=True,
        wants_context=("crypto_headlines", "token_unlocks", "exchange_events", "risk_off_flag"),
        note="US-Makro (Zinsen/Inflation/Risk-off) + krypto-spezifische Ereignisse "
        "(Token-Unlocks, Exchange-Halts, Regulierung). Krypto-Headlines nur, wenn PIT verfügbar.",
    ),
    AssetClass.ALTCOIN: NewsRelevance(
        AssetClass.ALTCOIN,
        _MACRO_USD,
        require_feed=True,
        wants_context=("crypto_headlines", "token_unlocks", "chain_specific", "risk_off_flag"),
        note="wie Crypto, zusätzlich chain-/projekt-spezifische Ereignisse (höhere Idio-Vol).",
    ),
    AssetClass.GOLD: NewsRelevance(
        AssetClass.GOLD,
        _MACRO_WIDE | frozenset({"ECB_RATE"}),
        require_feed=True,
        wants_context=("dxy", "us_real_yields", "geopolitics", "central_bank_speak"),
        note="Makro / Dollar / Realzinsen / Geopolitik dominieren. Company-News irrelevant.",
    ),
    AssetClass.FOREX: NewsRelevance(
        AssetClass.FOREX,
        _CENTRAL_BANKS | _MACRO_WIDE,
        require_feed=True,
        wants_context=("central_bank_calendar", "rate_differentials", "pmi", "employment"),
        note="Zentralbanken + Makro-Überraschungen sind der Haupttreiber; pro Währungspaar "
        "beide Seiten (z. B. EURUSD → Fed UND EZB).",
    ),
    AssetClass.EQUITY: NewsRelevance(
        AssetClass.EQUITY,
        _MACRO_USD | frozenset({"US_GDP", "US_RETAIL_SALES"}),
        require_feed=True,
        wants_context=("earnings_calendar", "guidance", "sector_news", "index_events"),
        note="unternehmens-/sektor-spezifische News (Earnings, Guidance) zusätzlich zu Makro; "
        "Earnings-Blackout je Symbol nötig.",
    ),
    AssetClass.ETF: NewsRelevance(
        AssetClass.ETF,
        _MACRO_WIDE,
        require_feed=True,
        wants_context=("index_rebalance", "macro_calendar", "sector_rotation"),
        note="Makro-getrieben wie der zugrunde liegende Index; keine Einzel-Earnings.",
    ),
}


def relevance_for(asset_class: AssetClass) -> NewsRelevance:
    return NEWS_RELEVANCE.get(
        asset_class,
        NewsRelevance(
            asset_class,
            _MACRO_WIDE,
            require_feed=True,
            wants_context=(),
            note="Default: volle Makro-Liste, Fail-safe blockend.",
        ),
    )


def is_relevant(asset_class: AssetClass, event_type: str) -> bool:
    return event_type.strip().upper() in relevance_for(asset_class).relevant_event_types


__all__ = ["NEWS_RELEVANCE", "NewsRelevance", "is_relevant", "relevance_for"]
