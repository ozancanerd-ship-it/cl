"""Secret-Auflösung: **ENV zuerst, dann OS-Keychain** (macOS), nie ins Repo, nie ins Log.

Ein ``Secret`` kapselt einen sensiblen String. ``repr``/``str`` sind **redigiert** — ein Secret
kann nicht versehentlich geloggt oder in einen Traceback geschrieben werden. Den Klartext gibt
nur ``.reveal()`` heraus (bewusster Aufruf an genau der Stelle, wo signiert/authentifiziert wird).

Auflösungsreihenfolge in ``get_secret``:

1. ``os.environ[<env_var>]`` — lokal via ``.env`` (in ``.gitignore``), in der Cloud via
   Secret-Manager der Plattform (als Env-Var injiziert).
2. macOS-Keychain: ``security find-generic-password -s <service> -a <env_var> -w`` —
   optional, nur auf ``darwin``, Fehler werden verschluckt.

Nichts wird erfunden: fehlt das Secret überall, ist ``Secret.present`` ``False`` und der
aufrufende Adapter meldet ``UNAVAILABLE`` (kein Fake, keine Order).

**Anlegen im macOS-Keychain (optional, Alternative zu ``.env``):**

    security add-generic-password -s trading-agent -a KRAKEN_API_KEY -w
    security add-generic-password -s trading-agent -a KRAKEN_API_SECRET -w
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterable

_DEFAULT_SERVICE = "trading-agent"
_REDACTED = "***redacted***"


class Secret:
    """Sensibler String. ``repr``/``str`` redigiert; Klartext nur über ``reveal()``."""

    __slots__ = ("_name", "_value")

    def __init__(self, value: str, *, name: str = "secret") -> None:
        self._value = value or ""
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def present(self) -> bool:
        return bool(self._value)

    def reveal(self) -> str:
        """Klartext. Nur an der Signier-/Auth-Stelle aufrufen — nie loggen, nie zurückgeben."""
        return self._value

    def __bool__(self) -> bool:
        return self.present

    def __repr__(self) -> str:
        state = "set" if self.present else "unset"
        return f"Secret({self._name}={_REDACTED!r}, {state})"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and other._value == self._value

    def __hash__(self) -> int:
        return hash(("Secret", self._value))


def _from_keychain(env_var: str, service: str) -> str | None:
    if not sys.platform.startswith("darwin"):
        return None
    try:
        proc = subprocess.run(
            ["/usr/bin/security", "find-generic-password", "-s", service, "-a", env_var, "-w"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value or None


def get_secret(
    env_var: str,
    *,
    service: str = _DEFAULT_SERVICE,
    allow_keychain: bool = True,
) -> Secret:
    """Löst ein Secret auf (ENV → macOS-Keychain). Immer ein ``Secret`` — ggf. ``present=False``."""
    raw = os.environ.get(env_var)
    if raw:
        return Secret(raw, name=env_var)
    if allow_keychain:
        kc = _from_keychain(env_var, service)
        if kc:
            return Secret(kc, name=env_var)
    return Secret("", name=env_var)


def missing_secrets(
    env_vars: Iterable[str],
    *,
    service: str = _DEFAULT_SERVICE,
    allow_keychain: bool = True,
) -> list[str]:
    """Die Namen der Variablen, die weder in ENV noch im Keychain gesetzt sind."""
    return [
        v
        for v in env_vars
        if not get_secret(v, service=service, allow_keychain=allow_keychain).present
    ]


def redact(text: str, *secrets: str | Secret) -> str:
    """Ersetzt jeden bekannten Secret-Klartext in ``text`` durch ``***redacted***``.
    Letzte Verteidigungslinie fürs Logging — primär gilt: Secrets gar nicht erst in Strings."""
    out = text
    for s in secrets:
        raw = s.reveal() if isinstance(s, Secret) else s
        if raw:
            out = out.replace(raw, _REDACTED)
    return out


__all__ = ["Secret", "get_secret", "missing_secrets", "redact"]
