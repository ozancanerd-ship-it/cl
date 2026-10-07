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

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from trading_agent.security.secrets import get_secret

MODELL_STANDARD = "gpt-4o-mini"
_ENDPUNKT = "https://api.openai.com/v1/chat/completions"

_SYSTEM = (
    "Du bist eine zweite, unabhaengige Meinung neben einem bereits bestehenden "
    "Trading-Analyse-System (Claude). Du bekommst dessen aktuelle Einschaetzung zu "
    "Depot-Positionen und/oder Markt-Chancen. Antworte auf Deutsch, knapp (max. 200 "
    "Woerter), konkret. Sag explizit, wo du zustimmst, wo du widersprichst und warum, "
    "und was dir an der Analyse fehlt oder zu schwach belegt scheint. Keine Finanzberatung, "
    "keine Kaufempfehlung — nur eine fachliche Einschaetzung der vorgelegten Analyse. "
    "Wenn du nichts Substanzielles beizutragen hast, sag das auch so."
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
                f"- {p.get('name', p.get('sym', '?'))}: Einstieg {p.get('einstieg')}, "
                f"jetzt {p.get('kurs')} ({p.get('gv_pct', '—')} %), "
                f"Note {p.get('note', '—')} (Score {p.get('score', '—')}), "
                f"Stop {p.get('stop', 'keiner')}, Ziel {p.get('tp1', 'keins')}, "
                f"Begruendung: {p.get('begruendung', '—')}"
            )
    if chancen:
        teile.append("\nTOP-CHANCEN IM SCAN (noch nicht im Depot):")
        for c in chancen:
            teile.append(
                f"- {c.get('name', c.get('instrument', '?'))}: Score {c.get('score', '—')}, "
                f"Note {c.get('note', '—')}, Richtung {c.get('richtung', '—')}, "
                f"Begruendung: {c.get('begruendung', '—')}"
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
                "max_tokens": 500,
                "temperature": 0.3,
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
    return Zweitmeinung(text=text, modell=modell, erzeugt=datetime.now(UTC).isoformat())


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
