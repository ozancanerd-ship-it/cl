"""Tests: News-Relevanz-Referenztabelle — kein Laufzeit-Verhalten geändert, Fail-safe bleibt hart."""

from __future__ import annotations

from trading_agent.core.enums import AssetClass
from trading_agent.strategy.news_relevance import NEWS_RELEVANCE, is_relevant, relevance_for


def test_all_asset_classes_still_require_feed() -> None:
    # Projekt-Constraint: keine automatische Lockerung ohne OOS-Nachweis
    assert all(r.require_feed for r in NEWS_RELEVANCE.values())


def test_relevance_differs_by_asset_class() -> None:
    fx = relevance_for(AssetClass.FOREX)
    gold = relevance_for(AssetClass.GOLD)
    assert "ECB_RATE" in fx.relevant_event_types  # FX: beide Zentralbanken
    assert "geopolitics" in gold.wants_context
    assert is_relevant(AssetClass.GOLD, "us_cpi")
    assert not is_relevant(AssetClass.CRYPTO, "ecb_press_conf")


def test_every_enum_class_is_mapped() -> None:
    for ac in AssetClass:
        assert ac in NEWS_RELEVANCE
        assert relevance_for(ac).relevant_event_types  # nie leer
