"""Stops fuer Ozans eigene Positionen — gesetzt, nachgezogen und ueberwacht, rund um die Uhr.

Ozan, 27.09.: „Die Stops auf meinem Portfolio 24/7 analysieren und immer neu setzen und
mir Bescheid sagen, wenn es angekommen ist."

Bis dahin konnte das nur die App: sie setzte Stops aus der Analyse und zog sie nach —
aber nur, solange sie offen war. Der Waechter auf GitHub kannte nur Stops, die schon im
Depot-Text standen, und zog nichts nach. Wer die App zwei Tage nicht oeffnete, hatte zwei
Tage lang keinen nachgezogenen Stop.

Hier steht dieselbe Rechnung wie in der App (``stopPflege`` in ``site/template.html``),
damit Server und App dieselbe Marke nennen. Zwei Rechnungen, die sich widersprechen,
hatten wir schon einmal (16.09.) — mit einem echten Verkauf als Folge.

DIE REGELN — IN DIESER REIHENFOLGE

1. **Knock-out zuerst.** Ein Turbo, dessen Basiswert nahe an der Schwelle steht, wird
   wertlos, nicht „im Minus". Dagegen hilft kein Stop.
2. **Stop gerissen** → verkaufen. Keine Nachverhandlung.
3. **Ziel erreicht** → ein Drittel verkaufen; nach Ziel 1 wandert der Stop auf den
   Einstieg, nach Ziel 2 auf Ziel 1.
4. **Stop setzen / nachziehen.** Kandidat ist der Halte-Stop (Chandelier,
   ``scanner/halte_stop.py``) — der einzige gemessene. Die Invalidierung eines Setups zaehlt
   nur bei einer Position, die ueber ein Signal gekauft wurde (``plan.gekauft``), und nur,
   wenn das Setup in dieselbe Richtung zeigt (seit 01.10., siehe ``_kandidaten``). Genommen
   wird die engere gueltige Marke. Ein Stop wandert NUR in Richtung Sicherheit und nie auf oder ueber den
   Kurs. Kleine Schritte (< 0,4 %) werden nicht gemacht — die Ordergebuehr frisst sie.

Turbos werden auf dem **Basiswert** gefuehrt: „wenn TSMC unter X faellt, raus". Eine
Marke auf den Schein waere mit Ozans Eintragung (ein Posten fuer alle Scheine) keine Zahl,
die er beim Broker eintragen kann.

Dieses Modul ist rein: kein Netz, keine Dateien. Es bekommt die Scan-Zeilen und einen
Stand und gibt den neuen Stand samt Ereignissen zurueck.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import Any

from trading_agent.refdata import zahlen as zahlen_ref

#: Unter diesem Schritt lohnt das Nachziehen die Ordergebuehr nicht (wie in der App).
TRAIL_MIN_SCHRITT_PCT = 0.4
#: Gemeldet wird ein Nachziehen erst ab diesem Abstand zur zuletzt GEMELDETEN Marke. Der
#: Chandelier ruckt fast jeden Tag ein Stueck nach oben; fuenfzehn Positionen mal jeden
#: Tag eine Nachricht waere genau der Laerm, den Ozan nicht will.
MELDE_SCHRITT_PCT = 2.0
#: Puffer bis zum Knock-out in Prozent des Basiskurses (wie in der App).
KO_PUFFER_ENG = 12.0
KO_PUFFER_KRITISCH = 6.0
#: Quartalszahlen melden, wenn sie in so vielen Tagen anstehen.
ZAHLEN_MELDEN_TAGE = 2

_BAR = re.compile(r"^(USDC|USDT|USD|EUR|BUSD|DAI|FDUSD|TUSD|EURC)$", re.I)
_SCHEIN = re.compile(r"^(TURBO|KO|MINI|FAKTOR|OPEN|CALL|PUT)-|-(LONG|SHORT)$", re.I)
_ENDUNGEN = ("USDT", "USDC", "USD", "EUR")

#: Aeltere Depot-Eintraege ohne ``basis``-Feld (wie in der App, ``BASISWERT``).
BASISWERT: dict[str, tuple[str, str]] = {
    "TURBO-TSMC-LONG": ("TSM", "long"),
    "TURBO-NETFLIX-LONG": ("NFLX", "long"),
    "TURBO-META-LONG": ("META", "long"),
}


# --------------------------------------------------------------------------- Hilfen


def _zahl(x: Any) -> float | None:
    if isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def muenze(sym: str) -> str:
    s = str(sym or "").upper().split("-")[0]
    for q in _ENDUNGEN:
        if s.endswith(q) and len(s) > len(q):
            return s[: -len(q)]
    return s


def ist_schein(pos: dict[str, Any]) -> bool:
    """Turbo, Knock-out, Faktor — alles, was ueber einen Basiswert laeuft."""
    if pos.get("ko") is not None or pos.get("basis"):
        return True
    return bool(_SCHEIN.search(str(pos.get("sym") or "")))


def ist_bar(pos: dict[str, Any]) -> bool:
    if _BAR.match(str(pos.get("sym") or "").strip()):
        return True
    return bool(re.search(r"\bcash\b|bargeld|guthaben", str(pos.get("konto") or ""), re.I))


def ziele_gelten(plan: dict[str, Any] | None) -> bool:
    """Gehoeren die Ziele im Plan zu dieser Position? Nur bei einem Kauf ueber die
    Signalkarte (``gekauft``) oder wenn Ozan sie selbst gesetzt bzw. uebernommen hat."""
    if not plan:
        return False
    if plan.get("gekauft"):
        return True
    return str(plan.get("quelle") or "") in {"selbst", "analyse", "selbst_nachgezogen"}


def schluessel(pos: dict[str, Any]) -> str:
    """Wie ``posSchluessel`` in der App: Symbol plus Konto."""
    return str(pos.get("sym") or "").upper() + "|" + str(pos.get("konto") or "").lower()


def pos_waehrung(pos: dict[str, Any]) -> str:
    if pos.get("waehrung"):
        return str(pos["waehrung"]).upper()
    m = re.search(r"(USDT|USDC|USD|EUR)$", str(pos.get("sym") or "").upper())
    if m:
        return "EUR" if m.group(1) == "EUR" else "USD"
    if re.search(r"trade\s*republic", str(pos.get("konto") or ""), re.I):
        return "EUR"
    return "USD"


def zeile_waehrung(row: dict[str, Any]) -> str:
    return "EUR" if row.get("waehrung") == "EUR" else "USD"


def umrechnen(wert: float | None, von: str, nach: str, eurusd: float | None) -> float | None:
    if wert is None or von == nach:
        return wert
    if not eurusd:
        return None  # lieber keine Zahl als eine in der falschen Waehrung
    return wert / eurusd if nach == "EUR" else wert * eurusd


class ScanIndex:
    """Scan-Zeilen nach Instrument und nach Muenze."""

    def __init__(self, scan: dict[str, Any]) -> None:
        self.eurusd = _zahl(scan.get("eurusd"))
        self.genau: dict[str, dict[str, Any]] = {}
        self.je_muenze: dict[str, dict[str, Any]] = {}
        for r in scan.get("gesamt") or []:
            name = str(r.get("instrument") or "").upper()
            if not name:
                continue
            self.genau.setdefault(name, r)
            self.je_muenze.setdefault(muenze(name), r)

    def zeile(self, sym: str, *, nur_genau: bool = False) -> dict[str, Any] | None:
        s = str(sym or "").upper()
        if s in self.genau:
            return self.genau[s]
        if nur_genau:
            return None
        return self.je_muenze.get(muenze(s))


def basis_von(pos: dict[str, Any]) -> tuple[str, str] | None:
    if pos.get("basis"):
        return str(pos["basis"]).upper(), str(pos.get("basis_richtung") or "long")
    s = str(pos.get("sym") or "").upper()
    if s in BASISWERT:
        return BASISWERT[s]
    teile = [t for t in re.split(r"[^A-Z0-9]+", s) if t]
    kandidaten = [
        t
        for t in teile
        if t not in {"TURBO", "KO", "MINI", "FAKTOR", "OPEN", "END", "LONG", "SHORT", "CALL", "PUT"}
    ]
    if len(kandidaten) == 1:
        return kandidaten[0], "long" if s.endswith("LONG") or "SHORT" not in s else "short"
    return None


def stand_kennung(positionen: list[dict[str, Any]]) -> str:
    """Kurzer Fingerabdruck des Depots — gleich gerechnet wie in der App (Sync-Reiter).

    Er verraet nichts (Symbol, Konto und Stueckzahl gehen gehasht ein), zeigt der App aber,
    ob der Waechter auf GitHub denselben Stand kennt wie das Geraet.
    """
    teile = sorted(
        f"{str(p.get('sym') or '').upper()}|{str(p.get('konto') or '').lower()}|"
        f"{(_zahl(p.get('menge')) or 0.0):.6f}"
        for p in positionen
        if p.get("sym")
    )
    return hashlib.sha256(";".join(teile).encode("utf-8")).hexdigest()[:12]


# --------------------------------------------------------------------------- Bewertung


@dataclass
class Lage:
    """Was ueber eine Position gerade bekannt ist."""

    pos: dict[str, Any]
    key: str
    name: str
    lang: bool
    #: Auf welcher Ebene der Stop gefuehrt wird: ``"position"`` (Kurs der Position, in
    #: ihrer Waehrung) oder ``"basis"`` (Kurs des Basiswerts, in dessen Waehrung).
    ebene: str
    kurs: float | None  # auf der Ebene des Stops
    waehrung: str  # der Ebene
    row: dict[str, Any] | None
    ko: float | None = None
    ko_puffer_pct: float | None = None
    ausgeknockt: bool = False
    kandidaten: list[tuple[float, str]] = field(default_factory=list)
    hinweis: str = ""
    eurusd: float | None = None
    #: Naechster Quartalszahlen-Termin (bei Scheinen: des Basiswerts), aus der Scan-Zeile.
    zahlen: dict[str, Any] | None = None


def _kandidaten(
    row: dict[str, Any], lang: bool, kurs: float, conv: Any, *, mit_setup: bool = False
) -> list[tuple[float, str]]:
    """Gueltige Stopmarken aus der Analyse — schon auf der Ebene der Position.

    Bis 01.10. zaehlte jede Invalidierung, die auf der richtigen SEITE des Kurses lag —
    in der Annahme, die eines Short-Setups liege immer ueber dem Kurs. Hat der Kurs die
    Short-These aber gerade ueberrollt, liegt sie darunter und wurde zum Long-Stop direkt
    unter dem Kurs (Sei: Stop 0,07243 bei Kurs 0,0725, „Stop gerissen — alles verkaufen").
    Jetzt: Richtung des Setups muss passen, und Setup-Marken gelten nur fuer Positionen aus
    einem Signal (``mit_setup``). Fuer gehaltene Positionen ist der Halte-Stop der einzige
    gemessene Stop; eine Setup-Marke ist fuer einen neuen Einstieg gebaut und zu eng.
    """
    aus: list[tuple[float, str]] = []
    passt = str(row.get("richtung") or "") == ("long" if lang else "short")
    inv = conv(_zahl(row.get("invalidierung"))) if mit_setup and passt else None
    if inv is not None and (inv < kurs if lang else inv > kurs):
        aus.append((inv, "die laufende Analyse (Invalidierung des Setups)"))
    halte = row.get("halte") or {}
    ch = conv(_zahl(halte.get("stop_long" if lang else "stop_short")))
    if ch is not None and ch > 0 and (ch < kurs if lang else ch > kurs):
        k = _zahl(halte.get("k")) or 5.0
        aus.append(
            (
                ch,
                f"Halte-Stop ({k:g}× Tagesschwankung unter dem 22-Tage-Hoch)"
                if lang
                else f"Halte-Stop ({k:g}× Tagesschwankung über dem 22-Tage-Tief)",
            )
        )
    return aus


def lage_fuer(pos: dict[str, Any], idx: ScanIndex) -> Lage:
    lang = not (
        (pos.get("plan") or {}).get("richtung") == "short" or pos.get("hebel_richtung") == "short"
    )
    key = schluessel(pos)
    sym = str(pos.get("sym") or "")

    if ist_schein(pos):
        bv = basis_von(pos)
        # Erst der exakte Treffer (Aktien stehen mit ihrem Kuerzel im Scan), erst danach die
        # Muenzensuche — sonst landet ein Turbo auf TSMC bei einem gleichnamigen Coin.
        brow = (idx.zeile(bv[0], nur_genau=True) or idx.zeile(bv[0])) if bv else None
        name = str((brow or {}).get("name") or (bv[0] if bv else sym))
        if brow is None or _zahl(brow.get("kurs")) is None:
            return Lage(
                pos,
                key,
                name,
                lang,
                "basis",
                None,
                "USD",
                brow,
                hinweis="Basiswert nicht im Scan — kein Kurs, kein Stop",
            )
        basis_lang = (bv[1] if bv else "long") == "long"
        # Long-Turbo auf steigenden Basiswert: Stop unter dem Basiskurs.
        lang_basis = (
            basis_lang
            if pos.get("hebel_richtung") is None
            else (str(pos.get("hebel_richtung")) == "long")
        )
        bk = float(brow["kurs"])
        lg = Lage(pos, key, name, lang_basis, "basis", bk, zeile_waehrung(brow), brow)
        lg.zahlen = (brow.get("info") or {}).get("zahlen")
        ko = _zahl(pos.get("ko"))
        if ko is not None and ko > 0:
            lg.ko = ko
            abstand = (bk - ko) if lang_basis else (ko - bk)
            lg.ausgeknockt = abstand <= 0
            lg.ko_puffer_pct = abstand / bk * 100
        lg.kandidaten = [
            (m, g)
            for m, g in _kandidaten(brow, lang_basis, bk, lambda v: v)
            # Ein Stop jenseits der Knock-out-Schwelle schuetzt nichts: vorher ist der
            # Schein ohnehin weg.
            if ko is None or (m > ko if lang_basis else m < ko)
        ]
        if ko is not None and not lg.kandidaten and _kandidaten(brow, lang_basis, bk, lambda v: v):
            lg.hinweis = (
                "jede Stopmarke der Analyse liegt jenseits der Knock-out-Schwelle — "
                "der Knock-out ist hier der Stop"
            )
        return lg

    row = idx.zeile(sym)
    w = pos_waehrung(pos)
    name = str((row or {}).get("name") or sym)
    if row is None or _zahl(row.get("kurs")) is None:
        return Lage(
            pos,
            key,
            name,
            lang,
            "position",
            None,
            w,
            row,
            hinweis="nicht im Scan — kein Kurs, kein Stop",
        )
    rw = zeile_waehrung(row)
    eurusd = _zahl(row.get("eurusd")) or idx.eurusd

    def conv(v: float | None) -> float | None:
        return umrechnen(v, rw, w, eurusd)

    kurs = conv(_zahl(row.get("kurs")))
    lg = Lage(pos, key, name, lang, "position", kurs, w, row, eurusd=eurusd)
    lg.zahlen = (row.get("info") or {}).get("zahlen")
    if kurs is not None:
        gekauft = bool((pos.get("plan") or {}).get("gekauft"))
        lg.kandidaten = _kandidaten(row, lang, kurs, conv, mit_setup=gekauft)
    return lg


# --------------------------------------------------------------------------- Pflege


@dataclass
class Ereignis:
    art: str  # stop | ziel | ko | puffer_kritisch | puffer_eng | stop_neu | stop_nach | entwarnung
    key: str
    name: str
    text: str
    dringend: bool
    marke: str = ""
    werte: dict[str, Any] = field(default_factory=dict)

    @property
    def merker(self) -> str:
        return f"{self.key}|{self.art}|{self.marke}"


def _fmt(x: float | None) -> str:
    if x is None:
        return "—"
    a = abs(x)
    d = 2 if a >= 100 else 3 if a >= 1 else 5 if a >= 0.01 else 8
    return f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(x: float) -> str:
    return f"{x:.1f}".replace(".", ",") + " %"


def _zeichen(w: str) -> str:
    return " €" if w == "EUR" else " $"


def pflegen(
    pos: dict[str, Any], lg: Lage, alt: dict[str, Any] | None
) -> tuple[dict[str, Any], list[Ereignis]]:
    """Neuer Stand fuer eine Position plus das, was zu melden ist.

    ``alt`` ist der bisherige Stand dieser Position (oder ``None``):
    ``{"stop", "ebene", "quelle", "gemeldet_stop", "ziele": {...}, "erreicht": [...],
    "ausgestoppt": iso|None}``.
    """
    st: dict[str, Any] = dict(alt or {})
    st.setdefault("erreicht", [])
    ev: list[Ereignis] = []
    z = _zeichen(lg.waehrung)
    wer = f"{lg.name} ({pos.get('sym') or ''!s})" if lg.name != pos.get("sym") else lg.name
    wo = str(pos.get("konto") or "").strip()
    wo_satz = f" bei {wo}" if wo else ""

    if lg.kurs is None:
        st["hinweis"] = lg.hinweis
        return st, ev
    kurs = lg.kurs
    lang = lg.lang

    def besser(a: float, b: float) -> bool:
        """Liegt ``a`` weiter in Sicherheit als ``b``?"""
        return a > b if lang else a < b

    ebene_text = (
        f"{lg.name} steht bei {_fmt(kurs)}{z}" if lg.ebene == "basis" else f"Kurs {_fmt(kurs)}{z}"
    )

    # 0 — Knock-out. Vor allem anderen.
    if lg.ko is not None:
        if lg.ausgeknockt:
            ev.append(
                Ereignis(
                    "ko",
                    lg.key,
                    wer,
                    f"{lg.name} steht bei {_fmt(kurs)}{z}, die Knock-out-Schwelle lag bei "
                    f"{_fmt(lg.ko)}{z}. Der Schein ist verfallen — im Depot{wo_satz} prüfen und "
                    "in der App austragen.",
                    True,
                )
            )
            return st, ev
        p = lg.ko_puffer_pct
        if p is not None and p < KO_PUFFER_KRITISCH:
            ev.append(
                Ereignis(
                    "puffer_kritisch",
                    lg.key,
                    wer,
                    f"Nur noch {_pct(p)} bis zum Knock-out ({lg.name} {_fmt(kurs)}{z}, Schwelle "
                    f"{_fmt(lg.ko)}{z}). Das ist eine normale Tagesbewegung — ganz raus{wo_satz}, "
                    "bevor die Schwelle es über Nacht erledigt.",
                    True,
                )
            )
        elif p is not None and p < KO_PUFFER_ENG:
            ev.append(
                Ereignis(
                    "puffer_eng",
                    lg.key,
                    wer,
                    f"Puffer bis zum Knock-out auf {_pct(p)} geschmolzen ({lg.name} "
                    f"{_fmt(kurs)}{z}, Schwelle {_fmt(lg.ko)}{z}). Die Hälfte rausnehmen "
                    "halbiert, was ein Knock-out kosten kann.",
                    True,
                )
            )

    # Quartalszahlen in den naechsten zwei Tagen: einmal je Termin. Dringend, wenn ein
    # Schein weniger Puffer zum Knock-out hat, als diese Aktie an ihren Terminen schon
    # gesprungen ist (gemessen 2024–26, refdata/zahlen.py) — mindestens aber als jeder
    # zehnte Termin aller Aktien.
    zt = lg.zahlen or {}
    tage = zt.get("tage")
    if isinstance(tage, int) and 0 <= tage <= ZAHLEN_MELDEN_TAGE and zt.get("datum"):
        p = lg.ko_puffer_pct
        basis_sym = str((lg.row or {}).get("instrument") or "")
        grenze = zahlen_ref.typisch_grosse_luecke(basis_sym)
        knapp = p is not None and p < grenze
        bew = zt.get("bewegung") or zahlen_ref.je_aktie(basis_sym)
        wann = str(zt["datum"])
        zeit = f" ({zt['zeit']})" if zt.get("zeit") else ""
        historie = (
            f" An den letzten {bew['n']} Terminen bewegte sich {lg.name} im Mittel "
            f"{_pct(float(bew['median']))}, höchstens {_pct(float(bew['max']))}."
            if bew
            else ""
        )
        text = f"{lg.name} legt am {wann}{zeit} Quartalszahlen vor.{historie} " + (
            f"Dein Puffer zum Knock-out ist {_pct(p)} — eine Zahlen-Lücke dieser Größe hat es "
            "schon gegeben. Entscheide vorher, ob du den Schein über den Termin hältst."
            if knapp and p is not None
            else "Ein Stop schützt nicht vor einer Kurslücke über Nacht — entscheide vorher, "
            "ob du voll investiert durch den Termin gehst."
        )
        ev.append(
            Ereignis("zahlen_ko" if knapp else "zahlen", lg.key, wer, text, knapp, marke=wann)
        )

    plan = pos.get("plan") or {}
    # Einmalige Korrektur (01.10.): Stops aus einer Setup-Marke auf Positionen, die nicht
    # ueber ein Signal gekauft wurden, waren zu eng und teils aus der falschen Richtung
    # (siehe ``_kandidaten``). Sie fallen weg — auch ein darauf beruhendes „ausgestoppt" —,
    # und der Halte-Stop wird neu gesetzt. Selbst gesetzte Stops bleiben unberuehrt.
    korrektur = False
    war_ausgestoppt = bool(st.get("ausgestoppt"))
    if (
        lg.ebene == "position"
        and not plan.get("gekauft")
        and int(st.get("regel") or 1) < 2
        and "Invalidierung" in str(st.get("quelle") or "")
    ):
        for k in ("stop", "gemeldet_stop", "ausgestoppt", "quelle"):
            st.pop(k, None)
        korrektur = True
    st["regel"] = 2
    if st.get("ausgestoppt"):
        # Nach dem Stop wird nicht neu gesetzt: die Position gehoert verkauft. Kommt der
        # Wert wieder, ist das ein Fall fuer die Wiedereinstiegs-Liste, nicht fuer einen
        # Stop, der sich stillschweigend unter den Kurs legt.
        return st, ev

    stop = _zahl(st.get("stop"))
    if st.get("ebene") and st.get("ebene") != lg.ebene:
        stop = None  # Ebene gewechselt (alter Eintrag) — neu beginnen
    plan_ebene = str(plan.get("ebene") or "position")
    plan_stop = _zahl(plan.get("stop")) if plan_ebene == lg.ebene else None
    # Eine Setup-Marke, die die App frueher in den Depot-Text geschrieben hat, gilt aus
    # demselben Grund nicht mehr (selbst gesetzte oder nachgezogene Stops schon).
    if (
        plan_stop is not None
        and plan.get("art") not in ("halte", "einstand")
        and plan.get("quelle") == "app"
        and not plan.get("gekauft")
        and not plan.get("korrigiert")
    ):
        plan_stop = None
    # Den Stop aus dem Depot-Text (die App hat ihn gesetzt oder Ozan selbst) nie
    # unterbieten: der hoehere von beiden gilt.
    if plan_stop is not None and (stop is None or besser(plan_stop, stop)):
        stop = plan_stop
        st["quelle"] = "depot"

    # 1 — Stop gerissen.
    if stop is not None and (kurs <= stop if lang else kurs >= stop):
        st["stop"] = stop
        st["ebene"] = lg.ebene
        st["ausgestoppt"] = True
        ev.append(
            Ereignis(
                "stop",
                lg.key,
                wer,
                f"{ebene_text}, der Stop lag bei {_fmt(stop)}{z}. Verkaufen{wo_satz} — die Idee "
                "ist widerlegt, hier wird nicht nachverhandelt. Danach kommt der Wert auf die "
                "Wiedereinstiegs-Liste.",
                True,
                marke=_fmt(stop),
            )
        )
        return st, ev

    # 2 — Ziele: nur, wenn sie zu DIESER Position gehoeren (wie ``zieleGelten`` in der
    # App). Fuer eine von Hand eingetragene Position waren das bis 28.09. die Ziele
    # eines fremden, kurzfristigen Setups — mit „ein Drittel verkaufen" als Folge.
    ziele: dict[str, Any] = {}
    if lg.ebene == "position" and ziele_gelten(plan):
        for m in ("tp1", "tp2", "tp3"):
            if plan.get(m) is not None:
                ziele[m] = _zahl(plan.get(m))
    erledigt = set(pos.get("erledigt") or []) | set(st.get("erreicht") or [])
    menge = _zahl(pos.get("menge"))
    offen = 3 - len({"TP1", "TP2", "TP3"} & erledigt)
    for marke, teil in (("TP1", "ein Drittel"), ("TP2", "das zweite Drittel"), ("TP3", "den Rest")):
        ziel = _zahl(ziele.get(marke.lower()))
        if ziel is None or marke in erledigt:
            continue
        if menge and menge > 0 and offen > 0:
            stk = menge / offen
            teil += f" (ca. {stk:.4g} Stück von {menge:.6g})"
        if kurs >= ziel if lang else kurs <= ziel:
            erledigt.add(marke)
            st["erreicht"] = sorted(set(st.get("erreicht") or []) | {marke})
            danach = (
                " Stop danach auf deinen Einstieg."
                if marke == "TP1"
                else " Stop danach auf Ziel 1."
                if marke == "TP2"
                else " Damit ist der Trade zu."
            )
            ev.append(
                Ereignis(
                    "ziel",
                    lg.key,
                    wer,
                    f"{marke} erreicht ({_fmt(kurs)}{z}, Ziel {_fmt(ziel)}{z}) — {teil} "
                    f"verkaufen{wo_satz}.{danach}",
                    True,
                    marke=marke,
                )
            )
        break  # immer nur das naechste Ziel pruefen

    # 3 — Stop setzen / nachziehen.
    kand = list(lg.kandidaten)
    einstieg = _zahl(pos.get("einstieg")) if lg.ebene == "position" else None
    if "TP1" in erledigt and einstieg is not None:
        kand.append((einstieg, "Ziel 1 erreicht — ab dem Einstieg kostet die Position nichts mehr"))
    if "TP2" in erledigt and _zahl(ziele.get("tp1")) is not None:
        kand.append((float(ziele["tp1"]), "Ziel 2 erreicht — Stop auf Ziel 1"))
    kand = [(m, g) for m, g in kand if (m < kurs if lang else m > kurs)]
    if kand:
        best = max(kand, key=lambda x: x[0]) if lang else min(kand, key=lambda x: x[0])
        neu, grund = best
        if stop is None:
            st.update(stop=neu, ebene=lg.ebene, quelle=grund)
            ev.append(
                Ereignis(
                    "stop_neu",
                    lg.key,
                    wer,
                    f"Stop {'auf ' + lg.name + ' ' if lg.ebene == 'basis' else ''}bei "
                    f"{_fmt(neu)}{z} ({_pct(abs(kurs - neu) / kurs * 100)} "
                    f"{'unter' if lang else 'über'} dem Kurs von {_fmt(kurs)}{z}) — {grund}."
                    + (
                        " Korrektur: die frühere, zu enge Marke aus einem Setup gilt nicht "
                        "mehr — ein Stop-Alarm, der darauf beruhte, ist hinfällig."
                        if korrektur
                        else ""
                    ),
                    False,
                    werte={"stop": neu, "kurs": kurs, "ebene": lg.ebene},
                )
            )
            stop = neu
        elif besser(neu, stop) and abs(neu - stop) / kurs * 100 >= TRAIL_MIN_SCHRITT_PCT:
            st.update(stop=neu, ebene=lg.ebene, quelle=grund)
            stop = neu
    st["stop"] = stop
    st["ebene"] = lg.ebene
    if korrektur and war_ausgestoppt:
        # Ein „Stop gerissen" ist schon rausgegangen — auf einer Marke, die nicht haette
        # gelten duerfen. Das muss Ozan genauso deutlich hoeren wie den Alarm selbst.
        ev.append(
            Ereignis(
                "entwarnung",
                lg.key,
                wer,
                "Der frühere Stop-Alarm war hinfällig: die Marke kam aus einem kurzfristigen "
                "Setup und lag zu eng (teils aus der Gegenrichtung). Nicht deswegen verkaufen. "
                + (
                    f"Der gemessene Halte-Stop liegt bei {_fmt(stop)}{z}."
                    if stop is not None
                    else "Die Analyse gibt gerade keine neue Marke her."
                ),
                True,
            )
        )
    if stop is None:
        st["hinweis"] = lg.hinweis or "die Analyse gibt gerade keine Marke her"
        return st, ev

    # Nachziehen melden — aber nur in spuerbaren Schritten.
    gemeldet = _zahl(st.get("gemeldet_stop"))
    if gemeldet is None:
        st["gemeldet_stop"] = stop
    elif besser(stop, gemeldet) and abs(stop - gemeldet) / kurs * 100 >= MELDE_SCHRITT_PCT:
        ev.append(
            Ereignis(
                "stop_nach",
                lg.key,
                wer,
                f"Stop {'auf ' + lg.name + ' ' if lg.ebene == 'basis' else ''}von "
                f"{_fmt(gemeldet)}{z} auf {_fmt(stop)}{z} nachziehen{wo_satz} "
                f"({_pct(abs(kurs - stop) / kurs * 100)} {'unter' if lang else 'über'} dem Kurs) — "
                f"{st.get('quelle') or 'nachgezogen'}.",
                False,
                marke=_fmt(stop),
                werte={"von": gemeldet, "stop": stop, "kurs": kurs},
            )
        )
        st["gemeldet_stop"] = stop
    return st, ev


def pruefe_depot(
    positionen: list[dict[str, Any]], scan: dict[str, Any], stand: dict[str, Any]
) -> tuple[dict[str, Any], list[Ereignis], dict[str, Any]]:
    """Das ganze Depot einmal durch. Gibt (neuer Stand, Ereignisse, Uebersicht) zurueck.

    ``stand``: ``{"positionen": {key: {...}}, "gemeldet": {merker: iso}}``.
    Die Uebersicht ist oeffentlich-tauglich (nur Zaehler).
    """
    idx = ScanIndex(scan)
    alt_pos = dict((stand or {}).get("positionen") or {})
    neu_pos: dict[str, Any] = {}
    alle: list[Ereignis] = []
    mit_stop = ohne_kurs = 0
    for pos in positionen:
        if not isinstance(pos, dict) or not pos.get("sym") or ist_bar(pos):
            continue
        lg = lage_fuer(pos, idx)
        key = lg.key
        alt = alt_pos.get(key)
        # Hat sich die Stueckzahl geaendert (nachgekauft, teilverkauft, neu eingestiegen),
        # beginnt die Buchfuehrung dieser Position von vorn — ein alter "ausgestoppt"-
        # Merker wuerde sonst einen neuen Einstieg fuer immer stumm schalten.
        menge = _zahl(pos.get("menge"))
        if alt and _zahl(alt.get("menge")) != menge and alt.get("ausgestoppt"):
            # Nach einem Stop neu gekauft (oder die Menge von Hand korrigiert): die alte,
            # ueber dem Kurs liegende Marke gilt nicht mehr. Erreichte Ziele bleiben —
            # sonst meldete sich nach einem Teilverkauf dasselbe Ziel noch einmal.
            alt = {
                k: v
                for k, v in alt.items()
                if k not in ("ausgestoppt", "stop", "gemeldet_stop", "quelle")
            }
        st, ev = pflegen(pos, lg, alt)
        st["menge"] = menge
        neu_pos[key] = st
        alle.extend(ev)
        if st.get("stop") is not None:
            mit_stop += 1
        if lg.kurs is None:
            ohne_kurs += 1
    uebersicht = {
        "positionen": len(neu_pos),
        "mit_stop": mit_stop,
        "ohne_kurs": ohne_kurs,
    }
    return (
        {"positionen": neu_pos, "gemeldet": dict((stand or {}).get("gemeldet") or {})},
        alle,
        uebersicht,
    )


__all__ = [
    "BASISWERT",
    "KO_PUFFER_ENG",
    "KO_PUFFER_KRITISCH",
    "MELDE_SCHRITT_PCT",
    "TRAIL_MIN_SCHRITT_PCT",
    "Ereignis",
    "Lage",
    "ScanIndex",
    "basis_von",
    "ist_schein",
    "lage_fuer",
    "muenze",
    "pflegen",
    "pos_waehrung",
    "pruefe_depot",
    "schluessel",
    "stand_kennung",
    "ziele_gelten",
]
