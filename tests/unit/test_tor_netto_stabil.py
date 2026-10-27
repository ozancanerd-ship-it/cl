from trading_agent.scanner import alarm_tor as a


def _w(i, r_stop, risiko=1.0, klasse="krypto"):
    e = 100.0
    return {
        "instrument": f"X{i}", "setup": "S", "klasse": klasse, "richtung": "long",
        "zustand": "stop" if r_stop else "ziel_erreicht", "einstiegskurs": e,
        "einstieg": e, "stop": e * (1 - risiko / 100),
        "erreicht": [] if r_stop else ["TP1", "TP2", "TP3"],
        "aufgenommen": f"2026-10-{10 + i:02d}T10:00:00", "zuletzt": f"2026-10-{10 + i:02d}T12:00:00",
        "bestes_r": 0.5,
    }


def test_nach_kosten_nicht_mehr_bewaehrt():
    # 2 grosse Gewinne (+3,5 R), 10 Stops (-1 R): brutto +... aber enger Stop -> Kosten fressen es
    ws = [_w(i, False, 0.2) for i in range(4)] + [_w(i + 4, True, 0.2) for i in range(4)]
    st = a.bilanz(ws)["klasse_setup:krypto|S"]
    assert st.kosten_r > 0 and st.netto_r < st.summe_r


def test_zweite_haelfte_negativ_verhindert_bewaehrt():
    gut = [_w(i, False) for i in range(8)]
    schlecht = [_w(i + 8, True) for i in range(5)] + [_w(i + 13, False) for i in range(1)]
    st = a.bilanz(gut + schlecht)["klasse_setup:krypto|S"]
    assert st.neu_netto_r < 0
    assert st.urteil != "bewaehrt"
