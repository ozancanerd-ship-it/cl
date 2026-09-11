"""Der Auffangkanal darf nur laut werden, wenn es wirklich dringend ist."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from trading_agent.ops.notify import GitHubIssueSink, Notification, Severity


@pytest.fixture
def umgebung(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_testtoken")
    monkeypatch.setenv("GITHUB_REPOSITORY", "jemand/repo")


def _note(sev: Severity) -> Notification:
    return Notification(
        severity=sev,
        title="BTCUSDT — Einstieg erreicht",
        body="Kurs 76.500, Stop 74.000.",
        dedup_key="btc-entry",
        ts=datetime.now(UTC),
    )


def test_ohne_token_nicht_verfuegbar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "jemand/repo")
    assert GitHubIssueSink().available() is False


def test_ohne_repo_nicht_verfuegbar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_testtoken")
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert GitHubIssueSink().available() is False


def test_nur_dringendes_geht_raus(umgebung: None) -> None:
    """Ein Issue je Kursbewegung waere genau der Spam, den wir ausschliessen."""
    gesendet: list[tuple[str, dict[str, Any]]] = []
    sink = GitHubIssueSink(transport=lambda url, payload: gesendet.append((url, payload)))
    sink.deliver(_note(Severity.WARNING))
    assert gesendet == []
    sink.deliver(_note(Severity.CRITICAL))
    assert len(gesendet) == 1


def test_erwaehnung_steht_im_text(umgebung: None) -> None:
    """Ohne Erwaehnung verschickt GitHub keine Benachrichtigung — dann waere alles umsonst."""
    gesendet: list[tuple[str, dict[str, Any]]] = []
    sink = GitHubIssueSink(
        erwaehnen="ozancanerd-ship-it",
        transport=lambda url, payload: gesendet.append((url, payload)),
    )
    sink.deliver(_note(Severity.CRITICAL))
    url, payload = gesendet[0]
    assert url.endswith("/repos/jemand/repo/issues")
    assert payload["body"].startswith("@ozancanerd-ship-it")
    assert "BTCUSDT" in payload["title"]
    assert "keine Order" in payload["body"]


def test_kein_token_im_text(umgebung: None) -> None:
    """Das Token darf nirgends im Inhalt landen — das Repository ist oeffentlich."""
    gesendet: list[tuple[str, dict[str, Any]]] = []
    sink = GitHubIssueSink(transport=lambda url, payload: gesendet.append((url, payload)))
    sink.deliver(_note(Severity.CRITICAL))
    _, payload = gesendet[0]
    assert "ghs_testtoken" not in payload["body"]
    assert "ghs_testtoken" not in payload["title"]
