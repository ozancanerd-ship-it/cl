"""Alarme aus dem Gesamtmarkt-Scan — und die Bruecke vom Live-Bus zu ``ops/notify``.

Zwei Dinge, die vorher gefehlt haben:

* :class:`ScanWaechter` vergleicht den aktuellen Scan mit dem letzten und meldet nur die
  **Aenderung**. Ozans Regel: „nicht bei irgendwie jedem 200 mal Alarm bekommen, sondern bei
  dem perfekten Alarm". Also kein „BTC ist immer noch WATCH" — sondern: ein Setup ist neu da,
  ein Setup ist weggebrochen, die Nummer 1 hat gewechselt, ein Chart zieht deutlich an.
* :class:`AlertBruecke` haengt den Live-Daemon an einen :class:`~trading_agent.ops.notify.Notifier`.
  Bis hierher landeten ``AlertRaised``-Events nur im Audit-Log und damit auf keinem Telefon.

**Wichtig — kein Alarm auf einem kaputten Scan.** Faellt eine Anlageklasse aus (Geosperre,
stumme Kursquelle), sind ihre Instrumente *unbekannt*, nicht *verschwunden*. Der alte Stand
wird unveraendert weitergetragen. Sonst meldet das System „Setup entfallen", obwohl nur die
Datenquelle geschwiegen hat — genau der Fehler, der uns beim Tagesplan schon zweimal
untergekommen ist.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from trading_agent.ops.notify import Notification, Notifier, Severity

# Ein Urteil ist "handelbar", wenn es A oder besser ist.
_RANG = {"NO_TRADE": 0, "WATCH": 1, "A": 2, "A_PLUS": 3}

#: Dieselbe Meldung fuer dasselbe Instrument fruehestens nach dieser Zeit wieder.
ABKUEHLUNG = timedelta(hours=12)

#: Ab diesem Score-Sprung zwischen zwei Laeufen gilt ein Chart als "zieht an".
SCHUB_PUNKTE = 20.0

#: Ein Rangwechsel ist nur eine Meldung wert, wenn die neue Nummer 1 auch etwas taugt.
RANG1_MINDESTSCORE = 70.0


@dataclass(frozen=True, slots=True)
class ScanAlert:
    """Eine einzelne Aenderung, die eine Meldung wert ist."""

    art: str  # NEUES_SETUP | ENTFALLEN | RANG1 | SCHUB
    instrument: str
    severity: Severity
    titel: str
    text: str
    dedup_key: str

    def as_notification(self, ts: datetime | None = None) -> Notification:
        return Notification(
            severity=self.severity,
            title=self.titel,
            body=self.text,
            dedup_key=self.dedup_key,
            ts=ts or datetime.now(UTC),
        )


def _zeile(c: dict[str, Any]) -> str:
    """Die Kurzfassung eines Setups — genug, um am Telefon zu entscheiden."""
    richtung = {"long": "LONG", "short": "SHORT"}.get(str(c.get("richtung")), "?")
    teile = [f"{c['instrument']}  {richtung}  {c.get('score', 0):.1f}/100  [{c.get('urteil')}]"]
    if c.get("kurs") is not None:
        teile.append(f"Kurs      {c['kurs']:g}")
    for name, key in (("Ziel 1", "ziel"), ("Ziel 2", "tp2"), ("Ziel 3", "tp3")):
        if c.get(key) is not None:
            teile.append(f"{name}    {c[key]:g}")
    if c.get("invalidierung") is not None:
        teile.append(f"Falsch ab {c['invalidierung']:g}")
    if c.get("rr") is not None:
        teile.append(f"CRV       1:{c['rr']:.2f}")
    if c.get("bewegung_pct") is not None:
        teile.append(f"Weg       {c['bewegung_pct']:+.2f} %")
    if c.get("headline"):
        teile.append("")
        teile.append(str(c["headline"]))
    return "\n".join(teile)


def _kandidaten(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(c["instrument"]): c for c in doc.get("gesamt", []) if c.get("instrument")}


def _ausgefallene_klassen(doc: dict[str, Any]) -> set[str]:
    """Klassen, die diesmal nichts geliefert haben — deren Instrumente gelten als unbekannt."""
    fehler = doc.get("fehler") or {}
    anzahl = doc.get("anzahl") or {}
    aus = {str(k) for k in fehler}
    for klasse, n in anzahl.items():
        if not n:
            aus.add(str(klasse))
    return aus


def _instrumente_der_klassen(stand: dict[str, Any], klassen: set[str]) -> set[str]:
    zuordnung = stand.get("klasse_je_instrument") or {}
    return {i for i, k in zuordnung.items() if k in klassen}


class ScanWaechter:
    """Vergleicht zwei Scans und gibt die Aenderungen zurueck, die eine Meldung wert sind."""

    def __init__(self, *, abkuehlung: timedelta = ABKUEHLUNG) -> None:
        self.abkuehlung = abkuehlung

    # ------------------------------------------------------------------ Kern
    def pruefen(
        self,
        doc: dict[str, Any],
        stand: dict[str, Any] | None,
        *,
        jetzt: datetime | None = None,
    ) -> tuple[list[ScanAlert], dict[str, Any]]:
        """Gibt (Alarme, neuer Stand) zurueck. Der Stand gehoert in eine Datei."""
        jetzt = jetzt or datetime.now(UTC)
        alt = dict(stand or {})
        alt_inst: dict[str, Any] = dict(alt.get("instrumente") or {})
        gemeldet: dict[str, str] = dict(alt.get("gemeldet") or {})

        neu = _kandidaten(doc)
        stumm = _instrumente_der_klassen(alt, _ausgefallene_klassen(doc))

        alarme: list[ScanAlert] = []

        for name, c in neu.items():
            vorher = alt_inst.get(name) or {}
            urteil_neu = str(c.get("urteil") or "NO_TRADE")
            urteil_alt = str(vorher.get("urteil") or "NO_TRADE")
            score_neu = float(c.get("score") or 0.0)
            score_alt = float(vorher.get("score") or 0.0)
            richtung_neu = str(c.get("richtung"))
            richtung_alt = str(vorher.get("richtung"))

            hoch_neu, hoch_alt = _RANG.get(urteil_neu, 0), _RANG.get(urteil_alt, 0)

            # 1. Ein Setup ist neu da — oder hat die Richtung gedreht.
            if hoch_neu >= 2 and (hoch_neu > hoch_alt or richtung_neu != richtung_alt):
                sev = Severity.CRITICAL if urteil_neu == "A_PLUS" else Severity.WARNING
                marke = "A+ SETUP" if urteil_neu == "A_PLUS" else "A SETUP"
                alarme.append(
                    ScanAlert(
                        art="NEUES_SETUP",
                        instrument=name,
                        severity=sev,
                        titel=f"{marke}  {name}",
                        text=_zeile(c),
                        dedup_key=f"setup:{name}:{urteil_neu}:{richtung_neu}",
                    )
                )
                continue

            # 2. Ein Setup ist weggebrochen — das ist die Invalidierung.
            if hoch_alt >= 2 and hoch_neu < 2:
                alarme.append(
                    ScanAlert(
                        art="ENTFALLEN",
                        instrument=name,
                        severity=Severity.WARNING,
                        titel=f"Setup entfallen  {name}",
                        text=(
                            f"{name} war {urteil_alt} ({score_alt:.1f}), ist jetzt "
                            f"{urteil_neu} ({score_neu:.1f}).\nKein Einstieg mehr."
                        ),
                        dedup_key=f"weg:{name}:{urteil_alt}",
                    )
                )
                continue

            # 3. Ein Chart zieht deutlich an, ohne schon A zu sein — Vorwarnung.
            if score_neu - score_alt >= SCHUB_PUNKTE and score_neu >= 50.0 and vorher:
                alarme.append(
                    ScanAlert(
                        art="SCHUB",
                        instrument=name,
                        severity=Severity.INFO,
                        titel=f"Zieht an  {name}  {score_alt:.0f} -> {score_neu:.0f}",
                        text=_zeile(c),
                        dedup_key=f"schub:{name}:{int(score_neu // 10)}",
                    )
                )

        # 4. Die Nummer 1 des Gesamtmarkts hat gewechselt.
        rang1_alt = str(alt.get("rang1") or "")
        beste = max(neu.values(), key=lambda c: float(c.get("score") or 0.0), default=None)
        rang1_neu = str(beste["instrument"]) if beste else ""
        # Wenn dasselbe Instrument gerade schon als neues Setup gemeldet wurde, ist die
        # Rangmeldung nur dieselbe Nachricht ein zweites Mal.
        schon_gemeldet = {a.instrument for a in alarme if a.art == "NEUES_SETUP"}
        if (
            beste
            and rang1_neu
            and rang1_neu != rang1_alt
            and rang1_neu not in schon_gemeldet
            and float(beste.get("score") or 0.0) >= RANG1_MINDESTSCORE
        ):
            alarme.append(
                ScanAlert(
                    art="RANG1",
                    instrument=rang1_neu,
                    severity=Severity.WARNING,
                    titel=f"Neue Nummer 1 im Markt: {rang1_neu}",
                    text=_zeile(beste),
                    dedup_key=f"rang1:{rang1_neu}",
                )
            )

        alarme = self._abkuehlen(alarme, gemeldet, jetzt)

        # Stand fortschreiben. Stumme Klassen behalten ihren alten Eintrag.
        inst_neu: dict[str, Any] = {}
        for name, c in neu.items():
            inst_neu[name] = {
                "urteil": c.get("urteil"),
                "score": round(float(c.get("score") or 0.0), 1),
                "richtung": c.get("richtung"),
            }
        for name in stumm:
            if name not in inst_neu and name in alt_inst:
                inst_neu[name] = alt_inst[name]

        klasse_je_instrument = dict(alt.get("klasse_je_instrument") or {})
        for klasse, liste in (doc.get("klassen") or {}).items():
            for c in liste:
                klasse_je_instrument[str(c["instrument"])] = str(klasse)

        neuer_stand = {
            "stand": jetzt.isoformat(),
            "instrumente": inst_neu,
            "klasse_je_instrument": klasse_je_instrument,
            "rang1": rang1_neu or rang1_alt,
            "gemeldet": gemeldet,
            "stumme_klassen": sorted(_ausgefallene_klassen(doc)),
        }
        return alarme, neuer_stand

    def _abkuehlen(
        self, alarme: list[ScanAlert], gemeldet: dict[str, str], jetzt: datetime
    ) -> list[ScanAlert]:
        """Dieselbe Meldung nicht zweimal in der Abkuehlzeit."""
        raus: list[ScanAlert] = []
        for a in alarme:
            zuletzt = gemeldet.get(a.dedup_key)
            if zuletzt:
                try:
                    t = datetime.fromisoformat(zuletzt)
                except ValueError:
                    t = None
                if t is not None and jetzt - t < self.abkuehlung:
                    continue
            gemeldet[a.dedup_key] = jetzt.isoformat()
            raus.append(a)
        # alte Eintraege aufraeumen, damit die Datei nicht endlos waechst
        grenze = jetzt - self.abkuehlung * 4
        for key in [k for k, v in gemeldet.items() if _vor(v, grenze)]:
            del gemeldet[key]
        return raus


def _vor(iso: str, grenze: datetime) -> bool:
    try:
        return datetime.fromisoformat(iso) < grenze
    except ValueError:
        return True


class AlertBruecke:
    """Haengt ``AlertRaised`` des Live-Bus an einen :class:`Notifier`.

    Vorher landeten die Alerts des Daemons ausschliesslich im Audit-Log. Ein Alarm, den
    niemand sieht, ist kein Alarm.
    """

    #: Welcher Alert-Typ wie dringend ist (Werte aus ``strategy.alerts.AlertType``).
    #: Alles Unbekannte ist INFO und geht damit unter ``min_severity`` nicht raus.
    STUFEN: ClassVar[dict[str, Severity]] = {
        "new_a_plus_setup": Severity.CRITICAL,
        "buy": Severity.CRITICAL,
        "sell": Severity.CRITICAL,
        "entry_changed": Severity.WARNING,
        "sl_changed": Severity.WARNING,
        "tp_reached": Severity.WARNING,
        "partial_tp": Severity.WARNING,
        "setup_invalidated": Severity.WARNING,
        "exit_required": Severity.CRITICAL,
        "re_entry_setup": Severity.WARNING,
        "risk_limit": Severity.CRITICAL,
        "portfolio_risk": Severity.CRITICAL,
        "high_impact_news": Severity.WARNING,
        "broker_disconnected": Severity.CRITICAL,
        # Bewusst INFO: die kommen im Minutentakt und gehoeren ins Log, nicht aufs Telefon.
        "signal_strengthened": Severity.INFO,
        "signal_weakened": Severity.INFO,
        "tp_changed": Severity.INFO,
        "data_stale": Severity.INFO,
        "data_quality_failure": Severity.INFO,
    }

    def __init__(self, notifier: Notifier, *, min_severity: Severity = Severity.WARNING) -> None:
        self.notifier = notifier
        self.min_severity = min_severity
        self.gesehen = 0
        self.geschickt = 0

    def attach(self, bus: Any) -> AlertBruecke:
        from trading_agent.runtime.events import AlertRaised

        bus.subscribe(AlertRaised, self._on_alert)
        return self

    async def _on_alert(self, ev: Any) -> None:
        self.gesehen += 1
        art = str(getattr(ev, "alert_type", "") or "")
        sev = self.STUFEN.get(art, Severity.INFO)
        if sev < self.min_severity:
            return
        instrument = str(getattr(ev, "instrument", "") or "")
        body = str(getattr(ev, "message", "") or getattr(ev, "detail", "") or "")
        if self.notifier.notify(
            Notification(
                severity=sev,
                title=f"{art}  {instrument}".strip(),
                body=body,
                dedup_key=f"{art}:{instrument}",
                ts=getattr(ev, "ts", None) or datetime.now(UTC),
            )
        ):
            self.geschickt += 1


def als_text(alarme: Iterable[ScanAlert]) -> str:
    """Alle Alarme in einen Block — fuer die Konsole und den CI-Log."""
    teile = [f"[{a.severity.name}] {a.titel}\n{a.text}" for a in alarme]
    return "\n\n".join(teile) if teile else "keine Aenderung"


__all__ = [
    "ABKUEHLUNG",
    "RANG1_MINDESTSCORE",
    "SCHUB_PUNKTE",
    "AlertBruecke",
    "ScanAlert",
    "ScanWaechter",
    "als_text",
]
