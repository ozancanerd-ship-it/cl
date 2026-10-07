from pathlib import Path

from trading_agent.analysis import notenbanken as nb

SEITE = """<html><nav>Menu</nav><p>The Federal Open Market Committee approved the following
statement for release by a 12 – 0 vote: The Committee decided to raise the target range for the
federal funds rate by 1/4 percentage point to 3-3/4 to 4 percent, in support of the mandate.
Economic activity is expanding. While uncertainty remains elevated owing to geopolitics.
Inflation remains elevated. For media inquiries, call</p><footer>x</footer></html>"""


def test_anheben_wird_gelesen():
    e = nb.fed_entscheid(nb.fed_statement(SEITE))
    assert e["aktion"] == "anheben" and e["stimmen"] == "12:0"
    assert e["spanne"] == "3-3/4 to 4" and e["inflation_hoch"] and e["unsicherheit"]
    assert nb.ton_von(e["aktion"]) == "straff"
    assert "angehoben" in nb.fed_satz(e) and "Menu" not in nb.fed_statement(SEITE)


def test_halten_und_senken():
    h = nb.fed_entscheid("The Committee decided to maintain the target range for the federal funds rate at 3-3/4 percent.")
    assert h["aktion"] == "halten" and nb.ton_von("halten") == "neutral"
    s = nb.fed_entscheid("The Committee decided to lower the target range for the federal funds rate by 1/4 percentage point to 3 to 3-1/4 percent.")
    assert s["aktion"] == "senken" and nb.ton_von("senken") == "locker"


def test_unbekannt_bleibt_ehrlich():
    e = nb.fed_entscheid("Nothing about rates here.")
    assert e["aktion"] == "unbekannt" and "nichts Eindeutiges" in nb.fed_satz(e)


def test_relevante_filtert_rauschen():
    items = [
        {"titel": "Minutes of the London FXJSC Legal Sub Committee Meeting"},
        {"titel": "Federal Reserve issues FOMC statement"},
        {"titel": "Speech about gardening"},
    ]
    assert [i["titel"] for i in nb.relevante(items)] == ["Federal Reserve issues FOMC statement"]


def test_einordnung_hat_wirkung_je_klasse():
    o = nb.einordnung("straff", {"krypto": 0.4})
    assert set(o["wirkung"]) == {"krypto", "gold", "aktien"} and o["depot"]["krypto"] == 0.4


def test_app_hat_den_kasten():
    t = Path("site/template.html").read_text(encoding="utf-8")
    assert "function notenbankKasten" in t and "notenbanken.json" in t
