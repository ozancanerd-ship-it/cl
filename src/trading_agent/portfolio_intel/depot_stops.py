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

from trading_agent.portfolio_intel import gewinnschutz as gs_mod
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
    # stop | ziel | ziel_vorlaeufig | ziel_zurueck | nachkauf | nachkauf_vorlaeufig | nachkauf_zurueck
    # | ko | puffer_kritisch | puffer_eng | stop_neu | stop_nach | entwarnung
    art: str
    key: str
    name: str
    text: str
    dringend: bool
    marke: str = ""
    werte: dict[str, Any] = field(default_factory=dict)

    @property
    def merker(self) -> str:
        return f"{self.key}|{self.art}|{self.marke}"


def ema_wert(werte: list[float], n: int) -> float | None:
    """Letzter EMA-Wert (Start mit dem ersten Wert), ``None`` bei zu wenig Daten."""
    if len(werte) < n:
        return None
    a, e = 2 / (n + 1), werte[0]
    for v in werte[1:]:
        e = a * v + (1 - a) * e
    return e


def trend_von(closes: list[float]) -> str | None:
    """Grobe Trendlage einer Zeiteinheit: ``auf`` (Kurs und EMA 50 ueber EMA 200), ``ab``
    (beides darunter), sonst ``gemischt``. ``None``, wenn weniger als 210 Schluesse da sind."""
    if len(closes) < 210:
        return None
    e50, e200 = ema_wert(closes, 50), ema_wert(closes, 200)
    if e50 is None or e200 is None:
        return None
    c = closes[-1]
    if c > e200 and e50 > e200:
        return "auf"
    if c < e200 and e50 < e200:
        return "ab"
    return "gemischt"


def rsi14(closes: list[float]) -> float | None:
    """RSI(14) nach Wilder, ``None`` bei zu wenig Daten."""
    if len(closes) < 30:
        return None
    d = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    g = sum(max(x, 0) for x in d[:14]) / 14
    v = sum(max(-x, 0) for x in d[:14]) / 14
    for x in d[14:]:
        g = (g * 13 + max(x, 0)) / 14
        v = (v * 13 + max(-x, 0)) / 14
    return 100.0 if v == 0 else 100 - 100 / (1 + g / v)


_TREND_TXT = {"auf": "aufwärts", "ab": "abwärts", "gemischt": "uneins"}


def lage_zeile(
    lang: bool,
    kurs: float,
    ziel_next: float | None,
    stop: float | None,
    kontext: dict[str, Any] | None,
    z: str,
    aktion: str = "verkauf",
) -> str:
    """Die Lage hinter einem Teilverkauf- oder Nachkauf-Hinweis: Trend D1/H4, Momentum, verbleibendes
    Upside, Abstand zum Stop. Ein bestaetigter Stundenschluss ist ein Anlass zum Pruefen —
    kein Befehl; Widersprueche zum Trend stehen im Text statt verschwiegen zu werden."""
    k = kontext or {}
    d1, h4 = k.get("d1"), k.get("h4")
    teile: list[str] = []
    if d1 is None and h4 is None:
        teile.append("Trend D1/H4: keine Daten")
    else:
        t = f"Trend D1 {_TREND_TXT.get(d1, '—')}, H4 {_TREND_TXT.get(h4, '—')}"
        if h4 is None:
            t += " [Einschränkung: H4-Daten fehlen, nur D1 geprüft]"
        elif d1 is None:
            t += " [Einschränkung: D1-Daten fehlen, nur H4 geprüft]"
        dafuer = "auf" if lang else "ab"
        dagegen = "ab" if lang else "auf"
        if aktion == "nachkauf":
            if dagegen in (d1, h4):
                t += " — Konflikt: der Trend läuft gegen den Nachkauf"
            elif d1 == dafuer and h4 == dafuer:
                t += " — Trend stützt den Nachkauf"
            else:
                t += " — Trend uneinheitlich"
        elif d1 == dafuer and h4 == dafuer:
            t += " — Konflikt: der Trend läuft weiter in Richtung deiner Position, ein Teilverkauf kostet Upside"
        elif dagegen in (d1, h4):
            t += " — Trend dreht gegen die Position, das stützt den Teilverkauf"
        else:
            t += " — Trend uneinheitlich"
        teile.append(t)
    rsi = k.get("rsi_h1")
    if rsi is not None:
        teile.append(f"RSI (1 Std.) {rsi:.0f}")
    if ziel_next is not None and kurs:
        teile.append(f"bis zum nächsten Ziel {_fmt(ziel_next)}{z} noch {abs(ziel_next - kurs) / kurs * 100:.1f} %".replace(".", ","))
    if stop is not None and kurs:
        teile.append(f"Stop {_fmt(stop)}{z} ({abs(kurs - stop) / kurs * 100:.1f} % entfernt)".replace(".", ","))
    return " Lage: " + "; ".join(teile) + ". Das ist ein Hinweis zum Prüfen, keine Pflicht."


def _gewinnschutz(
    lg: Lage,
    pos: dict[str, Any],
    st: dict[str, Any],
    kontext: dict[str, Any] | None,
    kurs: float,
    lang: bool,
    menge: float | None,
    stop: float | None,
    ziele: dict[str, Any],
    erledigt: set[str],
    z: str,
    wer: str,
) -> list[Ereignis]:
    """Gewinnschutz fuer eine Position im Plus (Bausteine: ``gewinnschutz.py``).

    Hier nur die Buchfuehrung: zweistufig und restart-fest im Stand ``st["gs"]``.
    Ein Urteil ueber HOLD wird erst gemeldet, wenn es nach einem WEITEREN abgeschlossenen
    Stundenschluss unveraendert gilt (kein Alarm aus einer einzelnen Kerze). Fehlen frische
    Stundendaten, bleibt es beim bisherigen bestaetigten Stand — nie eine Bestaetigung aus
    veralteten Daten. Abstufungen (schwaecher werdende Schwaeche) laufen ebenso verzoegert
    und still; gemeldet wird nur die Verschlechterung. Der Stop-Alarm bleibt davon unberuehrt."""
    ktx = kontext or {}
    ein = _zahl(pos.get("einstieg"))
    ziel_next = next((_zahl(ziele.get(m.lower())) for m in ("TP1", "TP2", "TP3") if m not in erledigt and _zahl(ziele.get(m.lower())) is not None), None)
    plan_stop = stop if stop is not None else _zahl((pos.get("plan") or {}).get("stop"))
    zt = (lg.zahlen or {}).get("tage")
    bew = gs_mod.bewerte(
        lang=lang,
        kurs=kurs,
        einstieg=ein,
        hoch_gv_pct=_zahl(st.get("hoch_gv_pct")),
        menge=menge,
        stop=plan_stop,
        ziel_next=ziel_next,
        kerzen=ktx.get("k"),
        trend={"d1": ktx.get("d1"), "h4": ktx.get("h4")},
        zahlen_tage=zt if isinstance(zt, int) else None,
        z=z,
    )
    gs = dict(st.get("gs") or {})
    bar = ktx.get("h1_ts")
    bar = float(bar) if isinstance(bar, int | float) else None
    ev: list[Ereignis] = []
    if bew.urteil == gs_mod.UNBEWERTET:
        gs["anzeige"] = {**bew.als_dict(), "bestaetigt": gs.get("stufe", gs_mod.HOLD)}
        st["gs"] = gs
        return ev
    gewinn_eur = None
    if ein and menge:
        roh = ((kurs - ein) if lang else (ein - kurs)) * menge
        gewinn_eur = umrechnen(roh, lg.waehrung, "EUR", lg.eurusd)
    neu = bew.urteil
    best = str(gs.get("stufe") or gs_mod.HOLD)
    if neu == best:
        gs.pop("kand", None)
        gs.pop("kand_ts", None)
    elif bar is None:
        pass  # keine frische Stundenkerze: weder bestaetigen noch zuruecksetzen
    elif gs.get("kand") == neu and gs.get("kand_ts") is not None and bar > float(gs["kand_ts"]):
        # nach einem weiteren Stundenschluss unveraendert ⇒ bestaetigt
        hoeher = gs_mod.RANG[neu] > gs_mod.RANG[best]
        gs["stufe"] = neu
        gs.pop("kand", None)
        gs.pop("kand_ts", None)
        if neu == gs_mod.HOLD:
            gs["zyklus"] = int(gs.get("zyklus") or 0) + 1
        if hoeher:
            titel = {
                gs_mod.PROFIT_PROTECTION: "GEWINNSCHUTZ prüfen",
                gs_mod.PARTIAL: "TEILGEWINN MITNEHMEN prüfen",
                gs_mod.EXIT: "AUSSTIEG prüfen",
            }[neu]
            eur = f" (ca. {_fmt(gewinn_eur)} €)" if gewinn_eur is not None else ""
            teile = [
                f"{titel}: {lg.name} — Gewinn {_pct(bew.gewinn_pct or 0)}{eur}, Höchstgewinn {_pct(bew.hoch_pct or 0)}, davon {_pct(bew.rueckgang_pct or 0)} zurück.",
                bew.aktion,
                "Schwächesignale: " + (bew.begruendung or "—") + ".",
            ]
            if bew.upside_pct is not None:
                teile.append(f"Restpotenzial bis zum nächsten Ziel ca. {_pct(bew.upside_pct)}.")
            if bew.staerken:
                teile.append("Dagegen spricht: " + "; ".join(bew.staerken) + ".")
            bias = ", ".join(f"{k.upper()} {_TREND_TXT.get(v, 'fehlt')}" for k, v in bew.bias.items())
            teile.append(f"Bias: {bias}.")
            teile.append(bew.neubewertung)
            if bew.fehlt:
                teile.append("Fehlt: " + "; ".join(bew.fehlt) + ".")
            teile.append("Ein Hinweis zum Prüfen, kein Auftrag — die Entscheidung bleibt bei dir.")
            ev.append(
                Ereignis(
                    {gs_mod.PROFIT_PROTECTION: "gewinnschutz", gs_mod.PARTIAL: "gewinnschutz_teil", gs_mod.EXIT: "gewinnschutz_exit"}[neu],
                    lg.key,
                    wer,
                    " ".join(teile),
                    neu in (gs_mod.PARTIAL, gs_mod.EXIT),
                    marke=f"GS-{neu}-{int(gs.get('zyklus') or 0)}",
                )
            )
    else:
        gs["kand"], gs["kand_ts"] = neu, bar  # erster Befund: vorlaeufig, wartet auf den naechsten Schluss
    gs["anzeige"] = {**bew.als_dict(), "bestaetigt": gs.get("stufe", gs_mod.HOLD), "vorlaeufig": gs.get("kand")}
    st["gs"] = gs
    return ev


NACHKAUF_MIN_CRV = 2.0
#: Risikogrenzen fuer einen Nachkauf (in % des gesamten Depotwerts, nach dem Nachkauf):
#: Verlust der Position bis zu ihrem Stop, Summe aller offenen Stop-Risiken, Gewicht der Position.
NACHKAUF_MAX_POS_RISIKO_PCT = 1.0
NACHKAUF_MAX_PF_RISIKO_PCT = 3.0
NACHKAUF_MAX_GEWICHT_PCT = 25.0


def depot_risiko(
    positionen: list[dict[str, Any]], idx: ScanIndex, alt_pos: dict[str, Any]
) -> dict[str, Any]:
    """Wert und offenes Stop-Risiko des Depots in EUR (nur Positionen auf Positions-Ebene, die
    sich umrechnen lassen). ``vollstaendig`` ist False, wenn Positionen fehlen — dann gilt
    die Risikopruefung als nicht belastbar."""
    wert = risiko = 0.0
    je: dict[str, tuple[float, float]] = {}
    fehlt = 0
    for pos in positionen:
        if not isinstance(pos, dict) or not pos.get("sym") or ist_bar(pos):
            continue
        lg = lage_fuer(pos, idx)
        m = _zahl(pos.get("menge"))
        if lg.ebene != "position" or lg.kurs is None or not m:
            fehlt += 1
            continue
        w = umrechnen(lg.kurs * m, lg.waehrung, "EUR", lg.eurusd)
        plan = pos.get("plan") or {}
        stp = (
            _zahl(plan.get("stop")) if str(plan.get("ebene") or "position") == "position" else None
        ) or _zahl((alt_pos.get(lg.key) or {}).get("stop"))
        r = umrechnen(abs(lg.kurs - stp) * m, lg.waehrung, "EUR", lg.eurusd) if stp is not None else None
        if w is None:
            fehlt += 1
            continue
        wert += w
        if r is None:
            fehlt += 1  # ohne Stop ist das Risiko unbekannt
            r = w  # vorsichtig: ganzer Wert gilt als gefaehrdet
        risiko += r
        je[lg.key] = (w, r)
    return {"wert": wert, "risiko": risiko, "je": je, "vollstaendig": fehlt == 0}


def nachkauf_risiko_grund(depot: dict[str, Any] | None, key: str, menge: float | None) -> str | None:
    """None = Risiko im Rahmen. Sonst der Grund, warum NICHT bestaetigt wird."""
    if not depot or not depot.get("wert") or not menge or menge <= 0:
        return "Das Depotrisiko ist nicht prüfbar (Depotwert/Menge fehlt) — nicht bestätigt."
    w, r = (depot.get("je") or {}).get(key, (None, None))
    if w is None:
        return "Das Depotrisiko ist nicht prüfbar (Position nicht umrechenbar) — nicht bestätigt."
    if not depot.get("vollstaendig"):
        return "Das Depotrisiko ist unvollständig (Positionen ohne Kurs, Stop oder Umrechnung) — nicht bestätigt."
    f = 1 / 3  # Nachkauf = ein Drittel der Menge
    # Nachkauf-Stueck zum jetzigen Kurs, Stop wie bisher.
    zu_w, zu_r = w * f, r * f if r else 0.0
    gesamt_w = depot["wert"] + zu_w
    pos_r = (r + zu_r) / gesamt_w * 100
    pf_r = (depot["risiko"] + zu_r) / gesamt_w * 100
    gew = (w + zu_w) / gesamt_w * 100
    if pos_r > NACHKAUF_MAX_POS_RISIKO_PCT:
        return f"Das Risiko der Position wäre nach dem Nachkauf {pos_r:.1f} % des Depots (Grenze {NACHKAUF_MAX_POS_RISIKO_PCT:.0f} %) — nicht bestätigt.".replace(".", ",")
    if pf_r > NACHKAUF_MAX_PF_RISIKO_PCT:
        return f"Das gesamte offene Stop-Risiko wäre {pf_r:.1f} % des Depots (Grenze {NACHKAUF_MAX_PF_RISIKO_PCT:.0f} %) — nicht bestätigt.".replace(".", ",")
    if gew > NACHKAUF_MAX_GEWICHT_PCT:
        return f"Die Position hätte danach {gew:.0f} % Depotgewicht (Grenze {NACHKAUF_MAX_GEWICHT_PCT:.0f} %) — nicht bestätigt."
    return None


def _nachkauf(
    row: dict[str, Any] | None,
    lang: bool,
    kurs: float,
    close: float | None,
    kontext: dict[str, Any] | None,
    menge: float | None,
    st: dict[str, Any],
    z: str,
    wer: str,
    key: str,
    depot: dict[str, Any] | None = None,
) -> list[Ereignis]:
    """Nachkauf-Hinweis fuer eine gehaltene Position — zweistufig, nie automatisch.

    Nur wenn die Analyse fuer genau diesen Wert in DIESELBE Richtung ein Setup hat, das das
    Alarm-Tor besteht (Trading-These gilt), die Invalidierung nicht gerissen ist und das
    Chance-Risiko vom JETZIGEN Kurs aus noch >= 2 ist (kein Hinterherlaufen). Beruehren der
    Einstiegsmarke = vorlaeufig; bestaetigt erst, wenn ein frischer, abgeschlossener
    Stundenschluss darueber liegt UND D1/H4 nicht dagegen stehen. Ein Trend-Konflikt wird
    angezeigt, nicht verschwiegen — und bestaetigt nichts."""
    if not row:
        return []
    plan = row.get("plan") or {}
    ein, stp = _zahl(plan.get("einstieg")), _zahl(plan.get("stop"))
    ziel = _zahl(plan.get("tp2")) or _zahl(plan.get("tp1"))
    tor = row.get("alarm") or {}
    if row.get("richtung") != ("long" if lang else "short") or not row.get("handelbar") or tor.get("ja") is not True:
        return []
    if ein is None or stp is None or ziel is None:
        return []
    if (lang and kurs <= stp) or (not lang and kurs >= stp):
        return []  # Invalidierung gerissen — kein Nachkauf
    risiko = abs(kurs - stp)
    upside = (ziel - kurs) if lang else (kurs - ziel)
    if risiko <= 0 or upside <= 0 or upside / risiko < NACHKAUF_MIN_CRV:
        return []
    zust = dict(st.get("nachkauf") or {})
    k = f"{ein:.8g}"
    alt = zust.get(k)
    beruehrt = kurs >= ein if lang else kurs <= ein
    if not beruehrt:
        if alt == "vorlaeufig":
            del zust[k]
            st["nachkauf"] = zust
            return [
                Ereignis(
                    "nachkauf_zurueck",
                    key,
                    wer,
                    f"Nachkauf-Hinweis zurückgenommen: Kurs wieder unter der Einstiegsmarke {_fmt(ein)}{z}. Kein Nachkauf.",
                    False,
                    marke=f"NK{k}-zurueck",
                )
            ]
        return []
    ktx = kontext or {}
    dagegen = "ab" if lang else "auf"
    konflikt = dagegen in (ktx.get("d1"), ktx.get("h4"))
    ok = None if close is None else ((close >= ein) if lang else (close <= ein))
    risiko_grund = nachkauf_risiko_grund(depot, key, menge)
    crv = upside / risiko
    lage = lage_zeile(lang, kurs, ziel, stp, ktx, z, aktion="nachkauf")
    menge_satz = (
        f" Obergrenze: ca. {menge / 3:.4g} Stück (ein Drittel der bestehenden Menge)." if menge and menge > 0 else ""
    )
    basis = (
        f"Das Setup gilt weiter (Alarm-Tor bestanden), Chance-Risiko vom jetzigen Kurs {crv:.1f} zu 1, "
        f"Stop/Invalidierung {_fmt(stp)}{z}, Ziel {_fmt(ziel)}{z}."
    )
    if ok is True and not konflikt and risiko_grund is None:
        if alt == "bestaetigt":
            return []
        zust[k] = "bestaetigt"
        st["nachkauf"] = zust
        return [
            Ereignis(
                "nachkauf",
                key,
                wer,
                f"NACHKAUF-HINWEIS bestätigt: Stundenschluss {_fmt(close)}{z} über der Marke {_fmt(ein)}{z}. "
                f"{basis}{menge_satz}{lage}",
                True,
                marke=f"NK{k}",
            )
        ]
    if alt in ("vorlaeufig", "bestaetigt"):
        return []
    zust[k] = "vorlaeufig"
    st["nachkauf"] = zust
    grund = (
        "Der Trend (D1/H4) steht dagegen — nicht bestätigt."
        if konflikt
        else risiko_grund
        if risiko_grund and ok is True
        else "Es liegen keine frischen, vollständigen Stundendaten vor — nicht bestätigt."
        if ok is None
        else "Der letzte abgeschlossene Stundenschluss liegt noch nicht über der Marke."
    )
    return [
        Ereignis(
            "nachkauf_vorlaeufig",
            key,
            wer,
            f"NACHKAUF VORLÄUFIG: Marke {_fmt(ein)}{z} berührt ({_fmt(kurs)}{z}). {grund} {basis}{lage}",
            True,
            marke=f"NK{k}-vorlaeufig",
        )
    ]


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
    pos: dict[str, Any],
    lg: Lage,
    alt: dict[str, Any] | None,
    schluss: dict[str, float] | None = None,
    kontext: dict[str, dict[str, Any]] | None = None,
    depot: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[Ereignis]]:
    """Neuer Stand fuer eine Position plus das, was zu melden ist.

    ``alt`` ist der bisherige Stand dieser Position (oder ``None``):
    ``{"stop", "ebene", "quelle", "gemeldet_stop", "ziele": {...}, "erreicht": [...],
    "ausgestoppt": iso|None, "vorlaeufig": [...]}``.

    ``schluss``: letzter ABGESCHLOSSENER Stundenschluss je Instrument, nur wenn er frisch und
    vollstaendig ist (siehe ``scripts/depot_wache.stunden_schluesse``). Fehlt er, gilt ein
    Ziel wie bisher beim Beruehren — mit dem Vermerk, dass keine Stundenbestaetigung da war.
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

    # 0b — ueberdehnt und deutlich im Plus: kein Grund zu verkaufen, aber ein Hinweis, dass
    # der naechste Rueckschlag jederzeit kommen kann (07.10., aus einem echten Fall: MET
    # +30 % nach starkem Lauf, H4-RSI ueberdehnt — selbe Lage, die die App im Nacht-Plan
    # zeigt, hier zusaetzlich per Mail, auch wenn Ozan die App nicht offen hat).
    einstieg_ud = _zahl(pos.get("einstieg"))
    gv_pct = (
        ((kurs / einstieg_ud - 1) if lang else (einstieg_ud / kurs - 1)) * 100
        if einstieg_ud and einstieg_ud > 0 and kurs > 0
        else None
    )
    ueberdehnt = next(
        (w for w in ((lg.row or {}).get("warnungen") or []) if "ueberdehnt" in str(w).lower()),
        None,
    )
    if ueberdehnt and gv_pct is not None and gv_pct > 15:
        ev.append(
            Ereignis(
                "ueberdehnt",
                lg.key,
                wer,
                f"{lg.name} steht mit {_pct(gv_pct)} im Plus, und die Analyse markiert die "
                f"Bewegung als überdehnt ({ueberdehnt}). Kein Grund zu verkaufen, aber ein "
                "guter Moment für einen Rückschlags-Alarm, statt in die Stärke zu verkaufen.",
                False,
                marke=f"{int(gv_pct // 5) * 5}",
            )
        )

    # 0c — Gewinn-Rueckgang vom Hoch: Ozan, 08.10. 17:40 — „wir haben noch nie richtig
    # was mitgenommen". Konkreter Anlass: MET lief weit ins Plus und faellt seitdem
    # zurueck, ohne dass je verkauft wurde — Ozan wartet live auf „eine Zahl, die er mag",
    # statt vorher zu entscheiden. ``hoch_gv_pct`` merkt sich ueber alle Laeufe den
    # bisher hoechsten Gewinn dieser Position (nicht nur den heutigen); faellt der
    # aktuelle Gewinn deutlich davon zurueck, ist das ein eigenstaendiger, dringender
    # Grund zu handeln — unabhaengig vom gesetzten Stop, der ja noch nicht gerissen sein
    # muss. Siehe docs/AUSSTIEG-TRAILING-STUDIE-2026-10.md: ein generelles engeres
    # Trailing hat im Test nicht mehr gebracht als der Plan — hier geht es nicht um eine
    # neue Ausstiegsregel, sondern nur darum, einen bereits eingetretenen Rueckgang
    # ueberhaupt zu melden, statt ihn schweigend zu dulden.
    if gv_pct is not None:
        hoch_vorher = _zahl(st.get("hoch_gv_pct"))
        hoch = max(hoch_vorher, gv_pct) if hoch_vorher is not None else gv_pct
        st["hoch_gv_pct"] = hoch
        rueckgang = hoch - gv_pct
        if hoch >= 10 and rueckgang >= max(8.0, hoch * 0.35):
            ev.append(
                Ereignis(
                    "gewinn_rueckgang",
                    lg.key,
                    wer,
                    f"{lg.name} stand zwischenzeitlich bei {_pct(hoch)} im Plus, jetzt nur "
                    f"noch {_pct(gv_pct)} — {_pct(rueckgang)} vom Spitzenwert schon wieder "
                    "weg, ohne dass etwas verkauft wurde. Teilverkauf jetzt sichert, was "
                    "da ist; Warten auf eine bestimmte Zahl hat beim letzten Mal mehr "
                    "gekostet als gebracht.",
                    True,
                    marke=f"{int(rueckgang // 10) * 10}",
                )
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
    # Teilverkauf-Rahmen (13.10.): Ozan setzt nur den Einstieg und den eigenen Stop. Dann
    # gelten die Ziele aus dem Abstand zum Stop — 1 R, 2 R, 3 R vom Einstieg aus. Nur wenn
    # es keine echten Ziele gibt; die Quelle steht im Alarm, damit man nicht raetselt.
    rahmen = False
    if lg.ebene == "position" and not ziele and plan and str(plan.get("quelle") or "") == "selbst":
        e_r = _zahl(pos.get("einstieg"))
        s_r = _zahl(plan.get("stop"))
        if e_r and s_r and e_r > 0 and e_r != s_r and ((lang and s_r < e_r) or (not lang and s_r > e_r)):
            risiko_r = abs(e_r - s_r)
            for i, m in enumerate(("tp1", "tp2", "tp3"), start=1):
                ziele[m] = e_r + i * risiko_r if lang else e_r - i * risiko_r
            rahmen = True
    erledigt = set(pos.get("erledigt") or []) | set(st.get("erreicht") or [])
    menge = _zahl(pos.get("menge"))
    offen = 3 - len({"TP1", "TP2", "TP3"} & erledigt)
    vor = set(st.get("vorlaeufig") or [])
    inst = str((lg.row or {}).get("instrument") or "")
    close = (schluss or {}).get(inst)
    for marke, teil in (("TP1", "ein Drittel"), ("TP2", "das zweite Drittel"), ("TP3", "den Rest")):
        ziel = _zahl(ziele.get(marke.lower()))
        if ziel is None or marke in erledigt:
            continue
        if menge and menge > 0 and offen > 0:
            stk = menge / offen
            teil += f" (ca. {stk:.4g} Stück von {menge:.6g})"
        beruehrt = kurs >= ziel if lang else kurs <= ziel
        # Stundenbestaetigung: nur wenn ein frischer, abgeschlossener Schluss vorliegt.
        # Ohne Daten keine Bestaetigung behaupten — dann gilt das Beruehren wie bisher.
        schluss_ok = None if close is None else ((close >= ziel) if lang else (close <= ziel))
        if beruehrt and schluss_ok is not True and marke not in vor:
            st["vorlaeufig"] = sorted(vor | {marke})
            grund = (
                "Es liegen keine frischen, vollständigen Stundendaten vor — nicht bestätigt."
                if schluss_ok is None
                else "Der letzte abgeschlossene Stundenschluss liegt noch nicht über dem Ziel."
            )
            ev.append(
                Ereignis(
                    "ziel_vorlaeufig",
                    lg.key,
                    wer,
                    f"VORLÄUFIG: {marke} berührt ({_fmt(kurs)}{z}, Ziel {_fmt(ziel)}{z}). "
                    f"{grund} Erst ein bestätigter Stundenschluss macht daraus einen "
                    "Verkaufshinweis. Bis dahin kein Verkauf.",
                    True,
                    marke=f"{marke}-vorlaeufig",
                )
            )
            break
        if not beruehrt and marke in vor:
            st["vorlaeufig"] = sorted(vor - {marke})
            ev.append(
                Ereignis(
                    "ziel_zurueck",
                    lg.key,
                    wer,
                    f"{marke}-Hinweis zurückgenommen: Kurs wieder unter Ziel ({_fmt(kurs)}{z}). Kein Verkauf.",
                    True,
                    marke=f"{marke}-zurueck",
                )
            )
            break
        if beruehrt and schluss_ok is not True:
            break  # schon vorläufig gemeldet; ohne bestätigten Schluss kein Verkaufshinweis
        if beruehrt and marke in vor:
            st["vorlaeufig"] = sorted(vor - {marke})
        if beruehrt:
            erledigt.add(marke)
            st["erreicht"] = sorted(set(st.get("erreicht") or []) | {marke})
            danach = (
                " Stop danach auf deinen Einstieg."
                if marke == "TP1"
                else " Stop danach auf Ziel 1."
                if marke == "TP2"
                else " Damit ist der Trade zu."
            )
            rahmen_satz = " (Ziel aus deinem Stop: je 1 R)" if rahmen else ""
            rahmen_satz += " (Stundenschluss bestätigt)"
            # Was der Verkauf bringt und wohin der Stop wandert — als feste Zahlen, nicht
            # als Hinweis auf eine Rechnung, die man selbst machen muss.
            ein_r = _zahl(pos.get("einstieg"))
            neu_stop = (ein_r if marke == "TP1" else ziele.get("tp1")) if marke in ("TP1", "TP2") else None
            extra = ""
            if neu_stop is not None and ein_r:
                extra += f" Neuer Stop: {_fmt(neu_stop)}{z}."
            if menge and menge > 0 and offen > 0:
                extra += f" Erlös ca. {_fmt((menge / offen) * kurs)}{z} brutto."
            naechstes = {"TP1": "tp2", "TP2": "tp3"}.get(marke)
            extra += lage_zeile(
                bool(lang),
                kurs,
                _zahl(ziele.get(naechstes)) if naechstes else None,
                _zahl(st.get("stop")),
                (kontext or {}).get(inst),
                z,
            )
            ev.append(
                Ereignis(
                    "ziel",
                    lg.key,
                    wer,
                    f"{marke} erreicht ({_fmt(kurs)}{z}, Ziel {_fmt(ziel)}{z}){rahmen_satz} — {teil} "
                    f"verkaufen{wo_satz}.{danach}{extra}",
                    True,
                    marke=marke,
                )
            )
        break  # immer nur das naechste Ziel pruefen

    # 2c — Nachkauf-Hinweis (nur fuer die Position selbst, nicht fuer einen Basiswert).
    if lg.ebene == "position" and lang is not None:
        ev.extend(_nachkauf(lg.row, bool(lang), kurs, close, (kontext or {}).get(inst), menge, st, z, wer, lg.key, depot))

    # 2d — Gewinnschutz (nur Position im Plus, nur Positions-Ebene).
    if lg.ebene == "position" and lang is not None and not st.get("ausgestoppt"):
        ev.extend(_gewinnschutz(lg, pos, st, kontext and kontext.get(inst), kurs, bool(lang), menge, _zahl(st.get("stop")), ziele, erledigt, z, wer))

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
    positionen: list[dict[str, Any]],
    scan: dict[str, Any],
    stand: dict[str, Any],
    schluss: dict[str, float] | None = None,
    kontext: dict[str, dict[str, Any]] | None = None,
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
    depot = depot_risiko(positionen, idx, alt_pos)
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
        st, ev = pflegen(pos, lg, alt, schluss, kontext, depot)
        st["menge"] = menge
        st["sym"] = str(pos.get("sym") or "")
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
