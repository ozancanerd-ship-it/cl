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

import json

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


def test_prompt_nennt_den_maschinenlesbaren_schluessel():
    """Ozan, 09.10. 10:33: ChatGPTs Meinung soll direkt auf die Buy-/Sell-Karte der App
    passen — das klappt nur, wenn der Prompt denselben Schluessel (sym/instrument)
    nennt, den auch die App fuer die Karte benutzt."""
    p = zm.baue_prompt(
        positionen=[{"sym": "METUSD", "name": "Metaplex"}],
        chancen=[{"instrument": "NVDA", "name": "Nvidia"}],
    )
    assert "(sym=METUSD)" in p
    assert "(instrument=NVDA)" in p


def test_strukturierte_json_antwort_wird_pro_position_zugeordnet(monkeypatch):
    """Haelt sich ChatGPT an das verlangte JSON-Format, landet seine Meinung je Position/
    Chance unter genau dem Schluessel, den die App fuer die jeweilige Karte benutzt."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    inhalt = json.dumps(
        {
            "gesamt": "Grundsaetzlich stimme ich der Einschaetzung zu.",
            "je_position": {"METUSD": "Stop zu weit weg, Rueckgang ernst nehmen."},
            "je_chance": {"NVDA": "Score plausibel, aber Volumen schwach."},
        }
    )

    def transport(url, *, json, headers, timeout):
        return {"choices": [{"message": {"content": inhalt}}]}

    r = zm.hole_zweitmeinung("Prompt-Text", transport=transport)
    assert r is not None
    assert r.text == "Grundsaetzlich stimme ich der Einschaetzung zu."
    assert r.je_position == {"METUSD": "Stop zu weit weg, Rueckgang ernst nehmen."}
    assert r.je_chance == {"NVDA": "Score plausibel, aber Volumen schwach."}


def test_antwort_ohne_gueltiges_json_faellt_auf_rohen_text_zurueck_ohne_erfindung(monkeypatch):
    """Haelt sich das Modell NICHT an das JSON-Format, wird nichts pro Position erfunden —
    nur der rohe Text bleibt als Gesamtmeinung stehen (besser eine ehrliche Meinung als
    leere, erfundene Pro-Kaertchen)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")

    def transport(url, *, json, headers, timeout):
        return {"choices": [{"message": {"content": "Kein JSON, einfach Fliesstext."}}]}

    r = zm.hole_zweitmeinung("x", transport=transport)
    assert r is not None
    assert r.text == "Kein JSON, einfach Fliesstext."
    assert r.je_position == {}
    assert r.je_chance == {}


def test_json_mit_nicht_string_werten_wird_still_ausgelassen(monkeypatch):
    """Ein kaputtes Feld (Zahl statt Text, leerer String) wird ausgelassen statt zu
    crashen oder einen falschen Wert zu uebernehmen."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    inhalt = json.dumps(
        {
            "gesamt": "Passt so weit.",
            "je_position": {"METUSD": "Ok.", "XRPUSD": 42, "SOLUSD": "  "},
        }
    )

    def transport(url, *, json, headers, timeout):
        return {"choices": [{"message": {"content": inhalt}}]}

    r = zm.hole_zweitmeinung("x", transport=transport)
    assert r is not None
    assert r.je_position == {"METUSD": "Ok."}


def test_system_prompt_verlangt_begruendung_statt_nur_stempel():
    """Ozan, 09.10. 22:30: "ChatGPT soll wirklich mehr auch dazu sagen, nicht nur ja/
    Widerspruch ... wirklich zusammenarbeiten ... und alles erklaeren." Ein Urteil ohne
    Begruendung ist kein zweiter Blick, nur ein Etikett — die Anweisung muss das
    explizit verlangen, nicht nur hoffen, dass das Modell von sich aus ausfuehrlich
    wird."""
    assert "WORAN" in zm._SYSTEM or "woran" in zm._SYSTEM
    assert "Begruendung" in zm._SYSTEM
    assert "60-100" in zm._SYSTEM  # deutlich mehr als die alten 30 Woerter je Position


def test_max_tokens_reicht_fuer_die_laengeren_antworten(monkeypatch):
    """Mit 60-100 statt 30 Woertern je Position braeuchte das alte Tokenbudget (900)
    nicht mehr — eine Antwort mit vielen Positionen wuerde mitten im JSON abgeschnitten
    und faellt dann unnoetig auf den rohen Text zurueck."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    gesehen = {}

    def transport(url, *, json, headers, timeout):
        gesehen["json"] = json
        return {"choices": [{"message": {"content": "{}"}}]}

    zm.hole_zweitmeinung("x", transport=transport)
    assert gesehen["json"]["max_tokens"] >= 2500


def test_json_antwort_setzt_response_format_im_request(monkeypatch):
    """Die Anfrage verlangt explizit ein JSON-Objekt zurueck — das Modell soll sich nicht
    erst per Prompt-Bitte, sondern auch per API-Parameter ans Format halten."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    gesehen = {}

    def transport(url, *, json, headers, timeout):
        gesehen["json"] = json
        return {"choices": [{"message": {"content": "{}"}}]}

    zm.hole_zweitmeinung("x", transport=transport)
    assert gesehen["json"]["response_format"] == {"type": "json_object"}
