"""Die ausgelieferte Freigabe-Registry muss ladbar sein und darf nichts freigeben.

Anlass: beim Setzen von ``status: "refuted"`` in ``config/setup_validation.json`` gab es
den Enum-Wert noch nicht — die Datei liess sich nicht mehr laden, und der Code fiel still
auf die eingebauten Defaults zurueck. Genau der Fall, in dem eine veraltete Default-Notiz
("OOS-Edge plausibel") ein widerlegtes Setup wieder harmlos aussehen laesst.
"""

from __future__ import annotations

import json
from pathlib import Path

from trading_agent.core.version import STRATEGY_VERSION
from trading_agent.governance.live_gate import LiveEligibility, evaluate_live_gate
from trading_agent.governance.validation import ValidationRegistry, ValidationStatus

CONFIG = Path("config/setup_validation.json")


def test_shipped_config_loads() -> None:
    reg = ValidationRegistry.from_file(CONFIG)
    assert reg.all(), "Registry ist leer"


def test_every_status_in_the_file_is_a_known_enum_value() -> None:
    doc = json.loads(CONFIG.read_text(encoding="utf-8"))
    known = {s.value for s in ValidationStatus}
    for entry in doc["setups"]:
        assert entry["status"] in known, f"{entry['setup_id']}: {entry['status']}"


def test_nothing_is_live_allowed() -> None:
    """Solange nichts validiert ist, darf auch nichts durchkommen."""
    reg = ValidationRegistry.from_file(CONFIG)
    for sv in reg.all():
        assert sv.live_allowed is False, sv.setup_id


def test_refuted_setups_are_blocked_not_merely_shadowed() -> None:
    reg = ValidationRegistry.from_file(CONFIG)
    for sv in reg.all():
        if sv.status is not ValidationStatus.REFUTED:
            continue
        rep = evaluate_live_gate(sv.setup_id, sv.strategy_version, registry=reg)
        assert rep.eligibility is LiveEligibility.BLOCKED, sv.setup_id


def test_in_validation_setups_are_shadow() -> None:
    reg = ValidationRegistry.from_file(CONFIG)
    seen = 0
    for sv in reg.all():
        if sv.status is not ValidationStatus.IN_VALIDATION:
            continue
        seen += 1
        rep = evaluate_live_gate(sv.setup_id, sv.strategy_version, registry=reg)
        assert rep.eligibility is LiveEligibility.SHADOW, sv.setup_id
    assert seen >= 1, "kein Setup in der Pruefkette — dann sammelt nichts Forward-Daten"


def test_builtin_defaults_do_not_contradict_the_file() -> None:
    """Faellt die Datei aus, greifen die Defaults. Sie duerfen nicht milder sein."""
    from_file = {s.setup_id: s.status for s in ValidationRegistry.from_file(CONFIG).all()}
    builtin = {s.setup_id: s.status for s in ValidationRegistry.default().all()}
    milder = {
        ValidationStatus.REFUTED: 0,
        ValidationStatus.RETIRED: 0,
        ValidationStatus.EDGE_DEGRADED: 0,
        ValidationStatus.UNVALIDATED: 1,
        ValidationStatus.IN_VALIDATION: 2,
        ValidationStatus.VALIDATED: 3,
    }
    for setup_id, status in from_file.items():
        if setup_id in builtin:
            assert milder[builtin[setup_id]] <= milder[status], (
                f"eingebauter Default fuer {setup_id} ist milder als die Datei"
            )


def test_versions_match_what_the_code_queries_with() -> None:
    """Ein abweichender strategy_version faellt still auf UNVALIDATED zurueck."""
    reg = ValidationRegistry.from_file(CONFIG)
    for sv in reg.all():
        assert sv.strategy_version == STRATEGY_VERSION, (
            f"{sv.setup_id} traegt {sv.strategy_version}, der Code fragt mit {STRATEGY_VERSION}"
        )


def test_tsmom_parameters_are_recorded_as_frozen() -> None:
    doc = json.loads(CONFIG.read_text(encoding="utf-8"))
    tsmom = [s for s in doc["setups"] if s["setup_id"] == "SETUP-TSMOM-ENSEMBLE-01"]
    assert tsmom, "TSMOM fehlt in der Registry"
    pre = tsmom[0].get("preregistered")
    assert pre, "keine Vorab-Registrierung hinterlegt"
    assert pre["lookbacks"] == [28, 56, 90, 120, 180]
    assert "frozen_at" in pre
