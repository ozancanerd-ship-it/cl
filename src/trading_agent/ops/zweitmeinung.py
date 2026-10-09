"""Zweitmeinung von ChatGPT (OpenAI-API) — Ozans Wunsch, 07.10.: „ich frag dich immer,
du sollst mit ihm zusammenarbeiten, ohne dass ich ihn ansprechen muss."

Ehrlich eingeordnet, bevor irgendwer sich darauf verlässt: das hier ist **kein zweiter
Algorithmus mit eigener Datenbasis**, sondern ein zweites Sprachmodell, das denselben Text
liest, den auch dieses System schreibt — kein unabhängiger Beleg, sondern ein zusätzlicher
Blickwinkel. Es ersetzt keine Studie (Backtest/OOS/Monte-Carlo) und erzeugt kein Signal.
Nur aktiv, wenn ``OPENAI_API_KEY`` gesetzt ist (Secret, nie im Code/Log) — fehlt er, bleibt
das Feld einfach leer, kein Fake-Text.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from trading_agent.security.secrets import get_secret

MODELL_STANDARD = "gpt-4o-mini"
_ENDPUNKT = "https://api.openai.com/v1/chat/completions"

# Ozan, 09.10. 10:33: „integrier chatgpt mehr in der app, sehe auch seine meinungen bei
# den buy und sell alarms ... deine und seine will ich sehen und arbeitest mit ihm zsm."
# Deshalb antwortet ChatGPT jetzt nicht mehr nur mit EINEM Fliesstext, sondern mit einer
# Gesamteinschaetzung PLUS je einer kurzen Zeile pro Position/Chance, erkennbar an genau
# dem Schluessel (``sym``/``instrument``), den die App selbst schon fuer jede Karte
# benutzt — so laesst sich seine Meinung direkt neben Claudes Urteil auf dieselbe Karte
# setzen, statt irgendwo separat zu stehen. Haelt sich das Modell nicht an das JSON-Format,
# gibt es KEINEN erfundenen Pro-Position-Text — nur den Gesamttext faellt dann zurueck auf
# die rohe Antwort (besser eine einzige ehrliche Meinung als lauter leere Pro-Kaertchen).
_SYSTEM = (
    "Du bist eine zweite, unabhaengige Meinung neben einem bereits bestehenden "
    "Trading-Analyse-System (Claude). Du bekommst dessen aktuelle Einschaetzung zu "
    "Depot-Positionen und/oder Markt-Chancen, jede mit einem Schluessel in Klammern, "
    "z. B. (sym=METUSD) oder (instrument=NVDA). Antworte NUR mit einem einzigen gueltigen "
    "JSON-Objekt, kein Text davor oder danach, exakt mit diesen Feldern:\n"
    '{"gesamt": "<Gesamteinschaetzung auf Deutsch, max. 60 Woerter>", '
    '"je_position": {"<sym>": "<max. 30 Woerter je Position>", ...}, '
    '"je_chance": {"<instrument>": "<max. 30 Woerter je Chance>", ...}}\n'
    "In 'gesamt' sag knapp, wo du grundsaetzlich zustimmst oder widersprichst und was dir "
    "an der Analyse insgesamt fehlt oder zu schwach belegt scheint. In 'je_position' und "
    "'je_chance' gib fuer JEDEN dir vorgelegten Schluessel eine eigene kurze Zeile — "
    "zustimmend, widersprechend oder abwartend, konkret auf diese eine Position/Chance "
    "bezogen. Lass ein Feld nur weg, wenn dir dazu wirklich nichts einfaellt. Keine "
    "Finanzberatung, keine Kaufempfehlung — nur eine fachliche Einschaetzung der "
    "vorgelegten Analyse."
)


class _Transport(Protocol):
    def __call__(self, url: str, *, json: dict, headers: dict, timeout: float) -> Any: ...


def verfuegbar() -> bool:
    """True, wenn ein OPENAI_API_KEY-Secret hinterlegt ist."""
    return get_secret("OPENAI_API_KEY", allow_keychain=False).present


@dataclass(frozen=True, slots=True)
class Zweitmeinung:
    text: str
    modell: str
    erzeugt: str  # ISO-Zeitstempel
    je_position: dict[str, str] = field(default_factory=dict)
    je_chance: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ZweitmeinungFehler:
    """Diagnose eines gescheiterten Aufrufs — niemals den Schluessel enthaltend."""

    grund: str


def baue_prompt(*, positionen: list[dict[str, Any]], chancen: list[dict[str, Any]]) -> str:
    """Kompakter Text aus Depot-Positionen und Top-Chancen — dieselben Zahlen, die auch
    in der App stehen, nichts Zusaetzliches erfunden."""
    teile: list[str] = []
    if positionen:
        teile.append("GEHALTENE POSITIONEN:")
        for p in positionen:
            teile.append(
                f"- (sym={p.get('sym', '?')}) {p.get('name', p.get('sym', '?'))}: "
                f"Einstieg {p.get('einstieg')}, jetzt {p.get('kurs')} ({p.get('gv_pct', '—')} %), "
                f"Note {p.get('note', '—')} (Score {p.get('score', '—')}), "
                f"Stop {p.get('stop', 'keiner')}, Ziel {p.get('tp1', 'keins')}, "
                f"Begruendung: {p.get('begruendung', '—')}"
            )
    if chancen:
        teile.append("\nTOP-CHANCEN IM SCAN (noch nicht im Depot):")
        for c in chancen:
            teile.append(
                f"- (instrument={c.get('instrument', '?')}) {c.get('name', c.get('instrument', '?'))}: "
                f"Score {c.get('score', '—')}, Note {c.get('note', '—')}, "
                f"Richtung {c.get('richtung', '—')}, Begruendung: {c.get('begruendung', '—')}"
            )
    if not teile:
        teile.append("Keine Positionen und keine Top-Chancen aktuell vorhanden.")
    return "\n".join(teile)


def hole_zweitmeinung(
    prompt: str,
    *,
    modell: str = MODELL_STANDARD,
    transport: _Transport | None = None,
) -> Zweitmeinung | None:
    """Fragt die OpenAI-API. ``None`` bei fehlendem Key oder Fehler — kein Fake-Text,
    kein Crash (der aufrufende CI-Schritt laeuft mit ``continue-on-error``).

    Die Fehlerdiagnose (ohne Schluessel) steht danach in ``letzter_fehler()``."""
    global _LETZTER_FEHLER
    _LETZTER_FEHLER = None
    key = get_secret("OPENAI_API_KEY", allow_keychain=False)
    if not key.present:
        _LETZTER_FEHLER = ZweitmeinungFehler(grund="kein OPENAI_API_KEY hinterlegt")
        return None
    sender = transport
    if sender is None:
        import httpx

        def sender(url: str, *, json: dict, headers: dict, timeout: float) -> Any:  # type: ignore[no-redef]
            r = httpx.post(url, json=json, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r.json()

    try:
        antwort = sender(
            _ENDPUNKT,
            json={
                "model": modell,
                "messages": [
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 900,
                "temperature": 0.3,
                "response_format": {"type": "json_object"},
            },
            headers={"Authorization": f"Bearer {key.reveal()}", "Content-Type": "application/json"},
            timeout=30.0,
        )
    except Exception as exc:
        _LETZTER_FEHLER = ZweitmeinungFehler(grund=_diagnose(exc))
        return None
    try:
        text = antwort["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        _LETZTER_FEHLER = ZweitmeinungFehler(
            grund=f"Antwort ohne verwertbaren Text: {_ohne_schluessel(antwort)!r}"
        )
        return None
    if not text:
        _LETZTER_FEHLER = ZweitmeinungFehler(grund="Antwort war leer")
        return None
    gesamt, je_position, je_chance = _parse_antwort(text)
    return Zweitmeinung(
        text=gesamt,
        modell=modell,
        erzeugt=datetime.now(UTC).isoformat(),
        je_position=je_position,
        je_chance=je_chance,
    )


def _parse_antwort(text: str) -> tuple[str, dict[str, str], dict[str, str]]:
    """Versucht, die Antwort als das verlangte JSON zu lesen. Gelingt das nicht — Modell
    haelt sich nicht ans Format, oder liefert kein Objekt — faellt NUR der Gesamttext auf
    die rohe Antwort zurueck; es werden NIE Pro-Position-Meinungen erfunden, die das
    Modell nicht tatsaechlich so geliefert hat."""
    try:
        daten = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text, {}, {}
    if not isinstance(daten, dict):
        return text, {}, {}
    gesamt = daten.get("gesamt")
    if not isinstance(gesamt, str) or not gesamt.strip():
        gesamt = text
    je_position = daten.get("je_position")
    je_chance = daten.get("je_chance")
    return (
        gesamt.strip(),
        _nur_text_werte(je_position),
        _nur_text_werte(je_chance),
    )


def _nur_text_werte(wert: Any) -> dict[str, str]:
    """Nur echte, nicht-leere String-Werte uebernehmen — alles andere (fehlendes Feld,
    falscher Typ, leere Zeile) wird stillschweigend ausgelassen statt zu crashen."""
    if not isinstance(wert, dict):
        return {}
    return {
        str(k): v.strip()
        for k, v in wert.items()
        if isinstance(v, str) and v.strip()
    }


_LETZTER_FEHLER: ZweitmeinungFehler | None = None


def letzter_fehler() -> ZweitmeinungFehler | None:
    """Diagnose des letzten gescheiterten ``hole_zweitmeinung``-Aufrufs, oder ``None``."""
    return _LETZTER_FEHLER


def _ohne_schluessel(wert: Any) -> Any:
    """Kuerzt eine Antwort fuer die Diagnose — nie den Schluessel, nie zu lang."""
    text = str(wert)
    return text[:300]


def _diagnose(exc: Exception) -> str:
    """Fehlerursache ohne jeden sensiblen Wert — nur Statuscode/Fehlertyp."""
    try:
        import httpx

        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            body = _ohne_schluessel(exc.response.text)
            deutung = {
                401: "Schluessel ungueltig oder abgelaufen (401)",
                403: "Zugriff verweigert (403) — Projekt/Organisation ohne Freigabe?",
                429: "Rate-Limit oder kein Zahlungsmittel hinterlegt (429)",
                500: "OpenAI-Serverfehler (500)",
                503: "OpenAI ueberlastet (503)",
            }.get(code, f"HTTP {code}")
            return f"{deutung} — Antwort: {body}"
        if isinstance(exc, httpx.TimeoutException):
            return "Zeitueberschreitung (30s) beim Aufruf von OpenAI"
        if isinstance(exc, httpx.RequestError):
            return f"Netzwerkfehler: {type(exc).__name__}: {exc}"
    except ImportError:
        pass
    return f"{type(exc).__name__}: {exc}"
