"""Versiegelte Datei im oeffentlichen Repository — lesbar nur mit dem Geheimnis.

WARUM

Der Depot-Waechter braucht ein Gedaechtnis zwischen zwei Laeufen: welcher Stop gerade
gilt (er wird nur nachgezogen, nie zurueckgenommen) und was schon gemeldet wurde. Das
einzige, was zwischen zwei GitHub-Laeufen ueberlebt, ist das Repository — und das ist
oeffentlich. Eine Datei „SOL: Stop 128,40 $, Einstand 80,72 $" verriete jedem, was Ozan
haelt und wo er aussteigt.

Also liegt der Stand versiegelt im Repo. Der Schluessel wird aus dem Geheimnis abgeleitet,
das den Lauf ohnehin schon erreicht (``DEPOT_CODE``); ohne ihn ist die Datei Rauschen.

WIE (NUR STANDARDBIBLIOTHEK)

Verschluesseln-dann-Authentisieren mit HMAC-SHA256 als Pseudozufallsfunktion:

* ``k_enc = HMAC(k, "enc")``, ``k_mac = HMAC(k, "mac")``
* Schluesselstrom: ``HMAC(k_enc, nonce || zaehler)``, blockweise mit dem Klartext XOR-verknuepft
* Pruefsumme: ``HMAC(k_mac, "v1" || nonce || geheimtext)``, beim Oeffnen in konstanter Zeit
  verglichen — eine veraenderte oder mit einem anderen Schluessel geschriebene Datei wird
  abgelehnt, nicht als Unsinn gelesen.

HMAC-SHA256 im Zaehlermodus ist eine anerkannte Stromchiffre-Konstruktion; mit frischer
Zufallszahl (16 Byte) je Versiegelung wiederholt sich kein Schluesselstrom. Keine neue
Abhaengigkeit, die auf dem GitHub-Laeufer fehlen koennte.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

_KENNUNG = b"v1"
_NONCE = 16
_TAG = 32


def schluessel_aus(geheimnis: str, zweck: str) -> bytes:
    """Einen Schluessel aus einem Geheimnis ableiten — je Zweck ein anderer."""
    return hashlib.sha256(f"{zweck}|{geheimnis}".encode()).digest()


def _strom(k_enc: bytes, nonce: bytes, laenge: int) -> bytes:
    bloecke = []
    for zaehler in range((laenge + 31) // 32):
        bloecke.append(hmac.new(k_enc, nonce + zaehler.to_bytes(8, "big"), hashlib.sha256).digest())
    return b"".join(bloecke)[:laenge]


def versiegeln(daten: bytes, schluessel: bytes) -> str:
    k_enc = hmac.new(schluessel, b"enc", hashlib.sha256).digest()
    k_mac = hmac.new(schluessel, b"mac", hashlib.sha256).digest()
    nonce = os.urandom(_NONCE)
    geheim = bytes(a ^ b for a, b in zip(daten, _strom(k_enc, nonce, len(daten)), strict=True))
    tag = hmac.new(k_mac, _KENNUNG + nonce + geheim, hashlib.sha256).digest()
    return base64.b64encode(_KENNUNG + nonce + tag + geheim).decode("ascii")


def oeffnen(text: str, schluessel: bytes) -> bytes | None:
    """Den Klartext zurueck — oder ``None``, wenn Schluessel oder Datei nicht passen."""
    try:
        roh = base64.b64decode(text.strip().encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError):
        return None
    if len(roh) < len(_KENNUNG) + _NONCE + _TAG or not roh.startswith(_KENNUNG):
        return None
    nonce = roh[len(_KENNUNG) : len(_KENNUNG) + _NONCE]
    tag = roh[len(_KENNUNG) + _NONCE : len(_KENNUNG) + _NONCE + _TAG]
    geheim = roh[len(_KENNUNG) + _NONCE + _TAG :]
    k_enc = hmac.new(schluessel, b"enc", hashlib.sha256).digest()
    k_mac = hmac.new(schluessel, b"mac", hashlib.sha256).digest()
    soll = hmac.new(k_mac, _KENNUNG + nonce + geheim, hashlib.sha256).digest()
    if not hmac.compare_digest(soll, tag):
        return None
    return bytes(a ^ b for a, b in zip(geheim, _strom(k_enc, nonce, len(geheim)), strict=True))


__all__ = ["oeffnen", "schluessel_aus", "versiegeln"]
