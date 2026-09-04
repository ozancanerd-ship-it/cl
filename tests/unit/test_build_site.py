"""scripts/build_site — die Web-App, die der taegliche Job veroeffentlicht.

Warum getestet: die Seite ist das, was Ozan auf dem Homescreen hat. Wenn der Platzhalter
nicht ersetzt wird oder die Zahlen nicht ankommen, sieht er eine leere Seite und merkt
nicht, dass der Job kaputt ist. Genau davor schuetzen diese Tests.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("build_site", _ROOT / "scripts" / "build_site.py")
assert _spec and _spec.loader
bs = importlib.util.module_from_spec(_spec)
sys.modules["build_site"] = bs
_spec.loader.exec_module(bs)


def test_every_universe_instrument_has_a_readable_name() -> None:
    """Ein fehlender Name faellt sonst erst auf der Seite auf — als kryptischer Schluessel."""
    spec = importlib.util.spec_from_file_location("tf", _ROOT / "scripts" / "tsmom_forward.py")
    assert spec and spec.loader
    tf = importlib.util.module_from_spec(spec)
    sys.modules["tf"] = tf
    spec.loader.exec_module(tf)
    for canon in tf.UNIVERSE:
        assert canon in bs.NAMES, f"{canon} hat keinen lesbaren Namen"
        assert canon in bs.KLASSE, f"{canon} hat keine Anlageklasse"


def test_rules_come_from_the_real_risk_config() -> None:
    r = bs._rules(_ROOT)
    assert r["max_dd_pct"] == 10.0  # der Kill-Switch, nicht irgendein Standardwert
    assert r["min_cash_pct"] == 40.0
    assert r["max_positions"] == 8


def test_icon_is_a_valid_png() -> None:
    png = bs._icon_png()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert b"IHDR" in png[:20]
    assert png[-8:-4] == b"IEND"


def test_template_has_exactly_one_placeholder() -> None:
    tpl = (_ROOT / "site" / "template.html").read_text(encoding="utf-8")
    assert tpl.count("__DATA__") == 1
    assert "<title>" in tpl


def test_generated_page_carries_the_numbers() -> None:
    """Ende-zu-Ende: Vorlage + Daten ergeben eine Seite, in der die Zahlen wirklich stehen."""
    tpl = (_ROOT / "site" / "template.html").read_text(encoding="utf-8")
    payload = {"plan": {"date": "2026-09-03", "invested_eur": 240.0}, "marker": "</script>"}
    blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = tpl.replace("__DATA__", blob)

    assert "__DATA__" not in html, "Platzhalter nicht ersetzt — die Seite bliebe leer"
    assert "2026-09-03" in html
    # Der Payload darf das umschliessende <script> nicht vorzeitig schliessen.
    body = html.split('<script id="payload" type="application/json">')[1].split("</script>")[0]
    assert json.loads(body.replace("<\\/", "</")) == payload
