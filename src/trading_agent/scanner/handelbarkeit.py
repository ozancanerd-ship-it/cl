"""Was ein Signal braucht, um tatsächlich handelbar zu sein: Name, Börse, Währung, Gebühr.

WARUM DAS EIN EIGENES MODUL IST

Ein Signal, das ``NVDA`` sagt und einen Dollarkurs nennt, ist für Ozan **nicht
ausführbar**. Er handelt Einzelaktien über Trade Republic, dort stehen sie in Euro,
und er muss erst nachschlagen, welche Firma sich hinter dem Kürzel verbirgt. Drei
kleine Hürden, die zusammen dafür sorgen, dass ein gutes Signal liegen bleibt.

Dieses Modul räumt alle drei weg:

* **Voller Name.** ``NVDA`` → „NVIDIA". ``BTCUSDT`` → „Bitcoin". Kein Nachschlagen.
* **Wo.** Je Anlageklasse die Börse, auf der er das tatsächlich kaufen kann.
* **Womit.** Bei Aktien zusätzlich der Euro-Preis, umgerechnet zum aktuellen Kurs.
* **Was es kostet.** Trade Republic nimmt 1 € je Ausführung. Auf eine Position von
  300 € sind Hin- und Rückweg zusammen 0,67 % — bei einem Trade, der 2 % bringen
  soll, ist das ein Drittel des Gewinns. Das gehört sichtbar neben das Signal, nicht
  in eine Fußnote.

ZUR WÄHRUNG — WARUM UMRECHNEN UND NICHT DEN DEUTSCHEN CHART ANALYSIEREN

Naheliegend wäre, gleich die Frankfurter Notierung zu analysieren. Das wäre schlechter:
dort ist der Umsatz ein Bruchteil, der Spread breiter, und die Kerzen zeigen Lücken,
die es an der Heimatbörse nicht gibt. Die Struktur — Hochs, Tiefs, Brüche — entsteht
dort, wo gehandelt wird, und das ist die US-Notierung.

Also: **analysiert wird in Dollar, ausgeführt wird in Euro.** Der Umrechnungskurs steht
dabei, damit die Zahl nachvollziehbar bleibt.

Ein Nebeneffekt, der genannt gehört und nicht verschwiegen wird: der Euro-Preis einer
US-Aktie bewegt sich auch dann, wenn die Aktie stillsteht — nämlich wenn der Eurokurs
sich bewegt. Ein Stop in Euro kann also von einer Währungsbewegung ausgelöst werden.
Bei Bewegungen von 1–2 % am Tag fällt das kaum ins Gewicht, bei einem engen Stop schon.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Gebühr je Ausführung bei Trade Republic, in Euro. Hin und zurück also das Doppelte.
TR_GEBUEHR_EUR = 1.0

#: Ab diesem Anteil am erwarteten Gewinn ist die Gebühr ein ernstes Argument.
GEBUEHR_WARNUNG_ANTEIL = 0.20

# Die echten Kryptogebühren — nachgeschlagen, nicht geschätzt (Stand 12.09.2026).
#
# Das ist kein Detail. Im Code stand vorher pauschal 0,1 % je Seite, weil Binance so
# viel nimmt. Bei Kraken ist es das Vier- bis Achtfache, und bei einem Swing-Trade mit
# 4 % erwartetem Gewinn ist der Unterschied zwischen 0,2 % und 1,6 % Rundlauf der
# Unterschied zwischen einem Trade, der sich lohnt, und einem, der es nicht tut.
#
#: Kraken Pro, unterste Stufe (< 10.000 $ Monatsumsatz): Limit-Order, die im Buch liegt.
KRAKEN_MAKER_PCT = 0.40
#: Kraken Pro, unterste Stufe: Market-Order, die sofort ausgeführt wird.
KRAKEN_TAKER_PCT = 0.80
#: Die einfache Kraken-App („Instant Buy") — 1 % auf Sofortkauf, 1,5 % auf eigene
#: Orders. Steht hier, damit der Hinweis auf der Karte nicht geraten ist.
KRAKEN_APP_PCT = 1.5
#: Bybit Spot, VIP 0: gleich für Maker und Taker.
BYBIT_PCT = 0.10


@dataclass(frozen=True, slots=True)
class Handelsort:
    """Wo und in welcher Währung ein Wert tatsächlich gekauft wird."""

    broker: str
    waehrung: str
    #: Gebühr je Ausführung. Bei prozentualen Gebühren steht hier 0 und
    #: ``gebuehr_pct`` trägt den Wert.
    gebuehr_fix: float = 0.0
    gebuehr_pct: float = 0.0
    hinweis: str = ""


#: Je Anlageklasse. Bewusst an Ozans tatsächlichen Konten ausgerichtet und nicht an
#: dem, was theoretisch ginge — ein Signal für eine Börse, bei der er kein Konto hat,
#: ist kein Signal.
ORTE: dict[str, Handelsort] = {
    "krypto": Handelsort(
        broker="Bybit (USDT) oder Kraken (USD)",
        waehrung="USD",
        gebuehr_pct=BYBIT_PCT,
        hinweis=(
            "Der Dollarmarkt ist der tiefe — dort wird analysiert und gekauft. Bybit "
            "kostet 0,10 % je Seite, Kraken Pro 0,40 % mit Limit-Order und 0,80 % mit "
            "Market-Order (die einfache Kraken-App 1 % bis 1,5 %). Der Euro-Preis steht "
            "zur Orientierung daneben."
        ),
    ),
    "aktien": Handelsort(
        broker="Trade Republic",
        waehrung="EUR",
        gebuehr_fix=TR_GEBUEHR_EUR,
        hinweis="1 € je Ausführung — bei kleinen Positionen der größte Einzelposten.",
    ),
    "gold": Handelsort(
        broker="Bybit oder Kraken (PAXG)",
        waehrung="USD",
        gebuehr_pct=BYBIT_PCT,
        hinweis="PAXG ist 1:1 mit physischem Gold hinterlegt und an beiden Börsen handelbar.",
    ),
}


def ort(klasse: str) -> Handelsort:
    return ORTE.get((klasse or "").lower(), ORTE["krypto"])


def gebuehr_anteil(
    klasse: str, positionswert: float, erwarteter_gewinn_pct: float | None
) -> tuple[float, str] | None:
    """Wie viel des erwarteten Gewinns die Gebühr auffrisst.

    Rückgabe: (Anteil 0..1, Satz im Klartext) — oder ``None``, wenn sich das nicht
    beziffern lässt. Der Satz ist bewusst als Warnung formuliert, wenn der Anteil
    spürbar wird: eine Gebühr, die ein Drittel des Gewinns kostet, entscheidet darüber,
    ob ein Trade überhaupt Sinn ergibt.
    """
    if positionswert <= 0 or not erwarteter_gewinn_pct or erwarteter_gewinn_pct <= 0:
        return None
    o = ort(klasse)
    kosten = 2 * o.gebuehr_fix + 2 * positionswert * o.gebuehr_pct / 100.0
    gewinn = positionswert * erwarteter_gewinn_pct / 100.0
    if gewinn <= 0:
        return None
    anteil = kosten / gewinn
    euro = f"{kosten:.2f} €".replace(".", ",")
    if anteil >= GEBUEHR_WARNUNG_ANTEIL:
        satz = (
            f"Gebühr hin und zurück {euro} — das sind {anteil:.0%} des erwarteten "
            "Gewinns. Entweder größer einsteigen oder den Trade lassen."
        )
    else:
        satz = f"Gebühr hin und zurück {euro} ({anteil:.0%} des erwarteten Gewinns)."

    # Bei Krypto gibt es zwei Wege mit sehr verschiedenem Preis. Das gehört auf die
    # Karte, nicht in eine Fußnote: der Unterschied zwischen Kraken-Market und Bybit
    # ist bei einer 200-€-Position das Achtfache — und er kostet keinen Gedanken mehr,
    # wenn er einmal dasteht.
    if (klasse or "").lower() in ("krypto", "gold"):
        taker = 2 * positionswert * KRAKEN_TAKER_PCT / 100.0
        bybit = 2 * positionswert * BYBIT_PCT / 100.0
        t_txt = f"{taker:.2f} €".replace(".", ",")
        b_txt = f"{bybit:.2f} €".replace(".", ",")
        satz += (
            f" Das gilt für eine Limit-Order bei Kraken Pro; eine Market-Order kostet "
            f"{t_txt}, die einfache Kraken-App mehr. Bei Bybit wären es {b_txt} — "
            "dafür in USDT statt Euro."
        )
    return anteil, satz


def in_euro(wert: float | None, eurusd: float | None) -> float | None:
    """Dollarbetrag in Euro. ``eurusd`` ist der Kurs, wie er üblich notiert wird
    (1,08 heißt: ein Euro kostet 1,08 Dollar)."""
    if wert is None or not eurusd or eurusd <= 0:
        return None
    return wert / eurusd


#: Volle Namen. Der Zweck ist banal und wichtig: Ozan soll nicht nachschlagen müssen,
#: welche Firma hinter einem Kürzel steckt, bevor er entscheidet.
#:
#: Die Liste deckt bewusst nicht jeden Kryptowert ab — das dynamische Universum bringt
#: laufend neue. Was fehlt, fällt auf den Basisnamen zurück (``ARBUSDT`` → „ARB"), und
#: das ist immer noch besser als das volle Handelspaar.
AKTIEN_NAMEN: dict[str, str] = {
    "NVDA": "NVIDIA",
    "AMD": "AMD",
    "MSFT": "Microsoft",
    "GOOGL": "Alphabet (Google)",
    "META": "Meta (Facebook)",
    "AAPL": "Apple",
    "AMZN": "Amazon",
    "TSLA": "Tesla",
    "PLTR": "Palantir",
    "AVGO": "Broadcom",
    "MU": "Micron",
    "SMCI": "Super Micro",
    "ARM": "ARM Holdings",
    "CRWD": "CrowdStrike",
    "NOW": "ServiceNow",
    "ANET": "Arista",
    "UBER": "Uber",
    "SHOP": "Shopify",
    "COIN": "Coinbase",
    "MSTR": "MicroStrategy",
    "JNJ": "Johnson & Johnson",
    "LLY": "Eli Lilly",
    "UNH": "UnitedHealth",
    "NVO": "Novo Nordisk",
    "XOM": "ExxonMobil",
    "CVX": "Chevron",
    "JPM": "JPMorgan",
    "V": "Visa",
    "MA": "Mastercard",
    "COST": "Costco",
    "WMT": "Walmart",
    "HD": "Home Depot",
    "PG": "Procter & Gamble",
    "KO": "Coca-Cola",
    "PEP": "PepsiCo",
    "DIS": "Disney",
    "NFLX": "Netflix",
    "BA": "Boeing",
    "CAT": "Caterpillar",
    "GE": "General Electric",
    "LMT": "Lockheed Martin",
    "HON": "Honeywell",
    "NEE": "NextEra Energy",
    "LIN": "Linde",
    "MCD": "McDonald's",
    "CRM": "Salesforce",
}

KRYPTO_NAMEN: dict[str, str] = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "BNB": "BNB",
    "SOL": "Solana",
    "XRP": "XRP (Ripple)",
    "ADA": "Cardano",
    "DOGE": "Dogecoin",
    "AVAX": "Avalanche",
    "DOT": "Polkadot",
    "LINK": "Chainlink",
    "MATIC": "Polygon",
    "POL": "Polygon",
    "LTC": "Litecoin",
    "BCH": "Bitcoin Cash",
    "TRX": "Tron",
    "ATOM": "Cosmos",
    "UNI": "Uniswap",
    "ETC": "Ethereum Classic",
    "XLM": "Stellar",
    "NEAR": "NEAR",
    "APT": "Aptos",
    "FIL": "Filecoin",
    "ICP": "Internet Computer",
    "HBAR": "Hedera",
    "ARB": "Arbitrum",
    "OP": "Optimism",
    "INJ": "Injective",
    "SUI": "Sui",
    "SEI": "Sei",
    "TIA": "Celestia",
    "AAVE": "Aave",
    "MKR": "Maker",
    "RUNE": "THORChain",
    "ALGO": "Algorand",
    "VET": "VeChain",
    "SAND": "The Sandbox",
    "MANA": "Decentraland",
    "AXS": "Axie Infinity",
    "GALA": "Gala",
    "IMX": "Immutable",
    "GRT": "The Graph",
    "LDO": "Lido",
    "CRV": "Curve",
    "SNX": "Synthetix",
    "COMP": "Compound",
    "ENS": "Ethereum Name Service",
    "PEPE": "Pepe",
    "SHIB": "Shiba Inu",
    "WIF": "dogwifhat",
    "BONK": "Bonk",
    "FLOKI": "Floki",
    "JUP": "Jupiter",
    "PYTH": "Pyth",
    "JTO": "Jito",
    "W": "Wormhole",
    "STRK": "Starknet",
    "ONDO": "Ondo",
    "ENA": "Ethena",
    "ETHFI": "ether.fi",
    "RENDER": "Render",
    "RNDR": "Render",
    "FET": "Artificial Superintelligence",
    "TAO": "Bittensor",
    "AR": "Arweave",
    "KAS": "Kaspa",
    "TON": "Toncoin",
    "XAU": "Gold",
    "PAXG": "Gold (PAX)",
    # Nachgetragen, nachdem der Scan auf Kraken/Euro umgestellt wurde: dort stehen
    # andere Coins oben als vorher bei Binance. Ozans Regel gilt fuer alle —
    # „ich muss jedes Mal suchen" ist bei einem Kaufsignal ein echtes Problem.
    "MINA": "Mina Protocol",
    "ZEC": "Zcash",
    "DASH": "Dash",
    "HYPE": "Hyperliquid",
    "WLD": "World (Worldcoin)",
    "PUMP": "Pump.fun",
    "USELESS": "Useless Coin",
    "VVV": "Venice Token",
    "TRUMP": "Official Trump",
    "PENGU": "Pudgy Penguins",
    "SPX": "SPX6900",
    "MOG": "Mog Coin",
    "POPCAT": "Popcat",
    "RAY": "Raydium",
    "MOVE": "Movement",
    "EIGEN": "EigenLayer",
    "ZRO": "LayerZero",
    "BLUR": "Blur",
    "SKY": "Sky (ehemals Maker)",
    "BAT": "Basic Attention Token",
    "FLOW": "Flow",
    "EGLD": "MultiversX",
    "XTZ": "Tezos",
    "EOS": "EOS",
    "IOTA": "IOTA",
    "QNT": "Quant",
    "CHZ": "Chiliz",
    "STX": "Stacks",
    "KSM": "Kusama",
    "ROSE": "Oasis",
    "CFX": "Conflux",
    "ZK": "ZKsync",
    "SAFE": "Safe",
    "MASK": "Mask Network",
    "API3": "API3",
    "BAND": "Band Protocol",
    "OCEAN": "Ocean Protocol",
    "AUDIO": "Audius",
    "1INCH": "1inch",
    "YFI": "yearn.finance",
    "SUSHI": "SushiSwap",
    "BAL": "Balancer",
    "KAVA": "Kava",
    "MOODENG": "Moo Deng",
    "TURBO": "Turbo",
    "BRETT": "Brett",
}


def voller_name(instrument: str, klasse: str = "") -> str:
    """„NVDA-YFD" → „NVIDIA". „BTCUSDT" → „Bitcoin". Unbekanntes → der Basisname.

    Nie das rohe Handelspaar, und nie eine leere Zeichenkette: im Zweifel steht dort
    das Kürzel, und das ist immer noch lesbar.
    """
    s = (instrument or "").strip().upper()
    if not s:
        return "?"
    kern = s.split("-")[0].split(".")[0]
    if kern in AKTIEN_NAMEN:
        return AKTIEN_NAMEN[kern]
    for endung in ("USDT", "USDC", "USD", "EUR", "BUSD", "FDUSD"):
        if kern.endswith(endung) and len(kern) > len(endung):
            basis = kern[: -len(endung)]
            return KRYPTO_NAMEN.get(basis, basis)
    return KRYPTO_NAMEN.get(kern, kern)


def waehrung_von(instrument: str, ersatz: str = "EUR") -> str:
    """Die Währung, in der ein Paar notiert — aus dem Namen, nicht aus einer Tabelle."""
    s = (instrument or "").strip().upper()
    for endung in ("USDT", "USDC", "EUR", "USD"):
        if s.endswith(endung) and len(s) > len(endung):
            return endung
    return ersatz


def beschrifte(zeile: dict[str, Any], *, eurusd: float | None = None) -> None:
    """Ergänzt eine Scan-Zeile um Name, Handelsort und — bei Aktien — Euro-Preise.

    Ändert die Zeile an Ort und Stelle. Fehlt der Umrechnungskurs, bleiben die
    Euro-Felder schlicht weg; ein geschätzter Wechselkurs wäre schlimmer als keiner.
    """
    name = str(zeile.get("instrument") or "")
    klasse = str(zeile.get("klasse") or "")
    o = ort(klasse)
    zeile["name"] = voller_name(name, klasse)
    zeile["broker"] = o.broker
    # Die Währung steht am Paar, nicht in einer Tabelle. Sobald der Scan auf Bybit
    # ausweicht, heißen die Paare BTCUSDT statt BTCEUR — und dann muss auf der Karte
    # USDT stehen und nicht Euro, sonst rechnet er mit dem falschen Geld.
    zeile["waehrung"] = waehrung_von(name, o.waehrung)
    if o.hinweis:
        zeile["broker_hinweis"] = o.hinweis

    # Umgerechnet wird ab jetzt fuer ALLES, was nicht schon in Euro notiert.
    #
    # Bei Aktien ist Euro der Ausfuehrungspreis (Trade Republic kann nichts anderes),
    # bei Krypto ist es die Orientierung: gekauft wird im Dollarmarkt, aber Ozan denkt
    # in Euro und zahlt am Ende in Euro. Beides nebeneinander zu zeigen kostet nichts
    # und erspart das Kopfrechnen.
    if not eurusd or zeile.get("waehrung") == "EUR":
        return
    plan = zeile.get("plan") or {}
    eur: dict[str, float] = {}
    for schluessel, wert in (
        ("kurs", zeile.get("kurs")),
        ("einstieg", zeile.get("einstieg")),
        ("stop", plan.get("stop", zeile.get("invalidierung"))),
        ("tp1", plan.get("tp1", zeile.get("ziel"))),
        ("tp2", plan.get("tp2", zeile.get("tp2"))),
        ("tp3", plan.get("tp3", zeile.get("tp3"))),
    ):
        u = in_euro(wert, eurusd)
        if u is not None:
            eur[schluessel] = round(u, 4)
    if eur:
        zeile["eur"] = eur
        zeile["eurusd"] = round(float(eurusd), 4)


__all__ = [
    "AKTIEN_NAMEN",
    "GEBUEHR_WARNUNG_ANTEIL",
    "KRYPTO_NAMEN",
    "ORTE",
    "TR_GEBUEHR_EUR",
    "Handelsort",
    "beschrifte",
    "gebuehr_anteil",
    "in_euro",
    "ort",
    "voller_name",
    "waehrung_von",
]
