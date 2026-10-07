import importlib.util
from pathlib import Path

from trading_agent.security.siegel import schluessel_aus, versiegeln

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("depot_wache", ROOT / "scripts" / "depot_wache.py")
dw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dw)


def test_sync_wird_mit_dem_richtigen_schluessel_gelesen(tmp_path):
    f = tmp_path / "depot_sync.siegel"
    f.write_text(versiegeln(b'{"v":2,"p":[{"sym":"NVDA","menge":3}]}', schluessel_aus("k1", dw.SYNC_ZWECK)))
    roh = dw.sync_lesen(f, "k1")
    assert [p["sym"] for p in dw.depot_lesen(roh)] == ["NVDA"]


def test_falscher_oder_fehlender_schluessel_gibt_nichts(tmp_path):
    f = tmp_path / "depot_sync.siegel"
    f.write_text(versiegeln(b'{"p":[]}', schluessel_aus("k1", dw.SYNC_ZWECK)))
    assert dw.sync_lesen(f, "falsch") == ""
    assert dw.sync_lesen(f, "") == ""
    assert dw.sync_lesen(tmp_path / "fehlt", "k1") == ""


def test_app_hat_den_abgleich():
    t = (ROOT / "site" / "template.html").read_text(encoding="utf-8")
    assert "async function siegelJS" in t and "depot-sync-v1|" in t and "planeAutoSync()" in t
    assert "DEPOT_SCHLUESSEL" in (ROOT / ".github/workflows/daily.yml").read_text()
