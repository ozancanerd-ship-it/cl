"""ops/notify — Web Push, der Weg fuer Alarme bei geschlossener App.

Warum getestet: der teuerste Fehler dieses Projekts war ein Versandweg, der still
nichts tat. Die Telegram-Secrets fehlten wochenlang, und niemand hat es gemerkt, weil
nirgends stand „nicht konfiguriert". Diese Tests halten fest, dass eine unvollstaendig
eingerichtete Senke sich als *nicht verfuegbar* meldet — statt so zu tun, als haette
sie zugestellt.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from trading_agent.ops.notify import VAPID_PUBLIC, Notification, Severity, WebPushSink

_ABO = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "p", "auth": "a"}}


@pytest.fixture(autouse=True)
def _sauber(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PUSH_ABOS", raising=False)
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)


def test_ohne_secrets_nicht_verfuegbar() -> None:
    assert WebPushSink().available() is False


def test_schluessel_ohne_abo_ist_nicht_verfuegbar(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ein Schluessel allein sagt noch nicht, wohin gesendet werden soll."""
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "geheim")
    assert WebPushSink().available() is False


def test_abo_als_einzelnes_objekt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wer ein Abo aus dem Browser kopiert, bekommt genau ein Objekt — ohne Klammern."""
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "geheim")
    monkeypatch.setenv("PUSH_ABOS", json.dumps(_ABO))
    s = WebPushSink()
    assert s.available()
    assert s.abos() == [_ABO]


def test_abo_als_liste(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "geheim")
    monkeypatch.setenv("PUSH_ABOS", json.dumps([_ABO, _ABO]))
    assert len(WebPushSink().abos()) == 2


def test_abos_untereinander_ohne_klammern(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "geheim")
    monkeypatch.setenv("PUSH_ABOS", json.dumps(_ABO) + "\n" + json.dumps(_ABO))
    assert len(WebPushSink().abos()) == 2


def test_muell_im_secret_bringt_nichts_zum_absturz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "geheim")
    monkeypatch.setenv("PUSH_ABOS", "das ist kein json")
    s = WebPushSink()
    assert s.abos() == []
    assert s.available() is False


def test_nutzlast_traegt_titel_text_und_dringlichkeit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "geheim")
    monkeypatch.setenv("PUSH_ABOS", json.dumps(_ABO))
    raus: list[tuple[Any, str]] = []
    s = WebPushSink(transport=lambda abo, daten: raus.append((abo, daten)))
    s.deliver(Notification(Severity.CRITICAL, "A+ BUY BTCUSDT", "Einstieg 60000"))
    assert len(raus) == 1
    d = json.loads(raus[0][1])
    assert d["titel"] == "A+ BUY BTCUSDT"
    assert d["text"] == "Einstieg 60000"
    assert d["dringend"] is True


def test_unter_der_schwelle_wird_nichts_gesendet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "geheim")
    monkeypatch.setenv("PUSH_ABOS", json.dumps(_ABO))
    raus: list[Any] = []
    s = WebPushSink(min_severity=Severity.CRITICAL, transport=lambda a, d: raus.append(d))
    s.deliver(Notification(Severity.INFO, "nur eine Notiz"))
    assert raus == []


def test_oeffentlicher_schluessel_ist_gueltiges_base64url() -> None:
    """Der Schluessel landet in der Seite; ein Tippfehler dort waere lautlos toedlich."""
    import base64

    roh = base64.urlsafe_b64decode(VAPID_PUBLIC + "=" * ((4 - len(VAPID_PUBLIC) % 4) % 4))
    assert len(roh) == 65, "unkomprimierter P-256-Punkt"
    assert roh[0] == 0x04
