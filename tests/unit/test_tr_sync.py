import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import tr_sync  # noqa: E402

CSV = (
    "Name;ISIN;quantity;price;avgCost;netValue\n"
    "NVIDIA;US67066G1040;1.094105;190.7200;154.6800;208.65\n"
    "Turbo Long X;DE000ABC1234;47.000000;2.6000;3.0600;122.20\n"
)


def test_parse_und_text():
    pos = tr_sync.parse_csv(CSV)
    assert len(pos) == 2 and str(pos[0]["menge"]) == "1.094105"
    d = json.loads(tr_sync.depot_text(pos, jetzt_ms=1))
    assert d["k"] == ["Trade Republic"] and d["v"] == 1
    assert d["p"][0]["sym"] == "NVDA" and d["p"][0]["isin"] == "US67066G1040"
    assert d["p"][1]["sym"] == "TR-DE000ABC1234" and d["p"][1]["einstieg"] == 3.06
    assert d["p"][1]["wert_manuell"] == 122.2


def test_komma_dezimal_wird_gelesen():
    pos = tr_sync.parse_csv("Name;ISIN;quantity;price;avgCost;netValue\nA;US67066G1040;1,5;2,5;2,0;3,75\n")
    assert str(pos[0]["menge"]) == "1.5"


@pytest.mark.parametrize("text", [
    "x;y\n1;2\n",
    "Name;ISIN;quantity;price;avgCost;netValue\nA;B;abc;1;1;1\n",
    "Name;ISIN;quantity;price;avgCost;netValue\nA;B;0;1;1;1\n",
    "Name;ISIN;quantity;price;avgCost;netValue\nA;B;1;1\n",
])
def test_fehlerhaftes_wird_abgelehnt_statt_erraten(text):
    with pytest.raises(ValueError):
        tr_sync.parse_csv(text)


def test_leeres_depot_ist_gueltig():
    assert tr_sync.parse_csv("Name;ISIN;quantity;price;avgCost;netValue\n") == []
