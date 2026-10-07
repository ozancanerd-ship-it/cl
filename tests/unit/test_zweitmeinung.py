"""ChatGPT-Zweitmeinung: automatisch im 24/7-Lauf, ohne Fake-Text ohne Schluessel.

WARUM ES DIESEN TEST GIBT

Ozan, 07.10. 23:04: „Ich will, dass er mit dir zusammenarbeitet, ohne dass ich ihn jedes
Mal fragen muss." Die Antwort ist ein zweiter, automatischer Schritt im 24/7-Lauf (nicht
die ChatGPT-Oberflaeche), der dieselben Zahlen wie die App an die OpenAI-API schickt und
die Antwort dort anzeigt. Wie bei jedem Secret-gesteuerten Kanal (``EmailSink`` u.a.) gilt:
kein Key -> nichts geschrieben, kein Fake-Text, kein Absturz.

Festgehalten wird:

1. Ohne ``OPENAI_API_KEY`` liefert ``hole_zweitmeinung`` ``None`` — kein Netzwerkaufruf.
2. Mit Key geht die Anfrage mit dem richtigen Modell und dem Bearer-Header raus, die
   Antwort wird korrekt entpackt.
3. Ein Transport-Fehler (Netzwerk, HTTP-Fehler, kaputte Antwort) liefert ``None``, keine
   Exception nach aussen — der CI-Schritt darf nicht am GPT-Call abstuerzen.
4. Der Prompt nennt Position UND Begruendung — die zweite Meinung soll dieselbe Grundlage
   sehen wie die App, nichts Zusaetzliches, nichts Weggelassenes.
"""

from __future__ import annotations

import pytest

from trading_agent.ops import zweitmeinung as zm


@pytest.fixture(autouse=True)
def _kein_key_aus_der_umgebung(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def test_ohne_key_kein_aufruf():
    aufgerufen = []

    def transport(*a, **k):
        aufgerufen.append(1)
        return {}

    assert zm.verfuegbar() is False
    assert zm.hole_zweitmeinung("irgendwas", transport=transport) is None
    assert not aufgerufen


def test_mit_key_geht_die_anfrage_richtig_raus(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    gesehen = {}

    def transport(url, *, json, headers, timeout):
        gesehen["url"] = url
        gesehen["json"] = json
        gesehen["headers"] = headers
        return {"choices": [{"message": {"content": "  Testantwort.  "}}]}

    assert zm.verfuegbar() is True
    r = zm.hole_zweitmeinung("Prompt-Text", transport=transport)
    assert r is not None
    assert r.text == "Testantwort."
    assert r.modell == zm.MODELL_STANDARD
    assert gesehen["headers"]["Authorization"] == "Bearer sk-test-123"
    assert gesehen["json"]["model"] == zm.MODELL_STANDARD
    assert gesehen["json"]["messages"][-1]["content"] == "Prompt-Text"


def test_transport_fehler_gibt_none_kein_crash(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")

    def kaputt(*a, **k):
        raise ConnectionError("kein Netz")

    assert zm.hole_zweitmeinung("x", transport=kaputt) is None


def test_fehler_wird_diagnostiziert_ohne_schluessel_zu_zeigen(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-geheim-abc123")

    def kaputt(*a, **k):
        raise ConnectionError("kein Netz")

    assert zm.hole_zweitmeinung("x", transport=kaputt) is None
    fehler = zm.letzter_fehler()
    assert fehler is not None
    assert "sk-geheim-abc123" not in fehler.grund
    assert "ConnectionError" in fehler.grund


def test_http_fehler_wird_mit_statuscode_diagnostiziert(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    import httpx

    def unauthorized(*a, **k):
        req = httpx.Request("POST", zm._ENDPUNKT)
        resp = httpx.Response(401, text='{"error": "invalid_api_key"}', request=req)
        raise httpx.HTTPStatusError("401", request=req, response=resp)

    assert zm.hole_zweitmeinung("x", transport=unauthorized) is None
    fehler = zm.letzter_fehler()
    assert fehler is not None
    assert "401" in fehler.grund
    assert "sk-test-123" not in fehler.grund


def test_fehlender_key_setzt_auch_eine_diagnose():
    monkeypatch_los = zm.hole_zweitmeinung("x")
    assert monkeypatch_los is None
    fehler = zm.letzter_fehler()
    assert fehler is not None
    assert "OPENAI_API_KEY" in fehler.grund


def test_erfolgreicher_aufruf_loescht_vorherige_diagnose(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")

    def kaputt(*a, **k):
        raise ConnectionError("kein Netz")

    assert zm.hole_zweitmeinung("x", transport=kaputt) is None
    assert zm.letzter_fehler() is not None

    def ok(url, *, json, headers, timeout):
        return {"choices": [{"message": {"content": "Alles gut."}}]}

    r = zm.hole_zweitmeinung("x", transport=ok)
    assert r is not None
    assert zm.letzter_fehler() is None


def test_kaputte_antwort_gibt_none(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    assert zm.hole_zweitmeinung("x", transport=lambda *a, **k: {}) is None
    assert zm.hole_zweitmeinung("x", transport=lambda *a, **k: {"choices": []}) is None


def test_prompt_nennt_position_und_begruendung():
    p = zm.baue_prompt(
        positionen=[
            {
                "sym": "RAYUSD", "name": "Raydium", "einstieg": 2.23, "kurs": 2.537,
                "gv_pct": 13.8, "note": "WATCH", "score": 37.5, "stop": None, "tp1": None,
                "begruendung": "Score 38, CRV 1:2.82",
            }
        ],
        chancen=[],
    )
    assert "Raydium" in p
    assert "13.8" in p
    assert "Score 38, CRV 1:2.82" in p


def test_prompt_ohne_alles_bleibt_ehrlich():
    p = zm.baue_prompt(positionen=[], chancen=[])
    assert "Keine Positionen" in p
