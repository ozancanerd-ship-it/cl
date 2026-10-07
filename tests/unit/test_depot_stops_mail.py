from trading_agent.ops.notify import EmailSink, Notification, Severity


def test_email_sink_nur_mit_zugang(monkeypatch):
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASS", raising=False)
    assert not EmailSink().available()
    monkeypatch.setenv("SMTP_USER", "a@b.de")
    monkeypatch.setenv("SMTP_PASS", "x")
    monkeypatch.setenv("MAIL_TO", "c@d.de")
    got = []
    s = EmailSink(transport=got.append)
    assert s.available()
    s.deliver(Notification(Severity.CRITICAL, "VERKAUFEN", "NVDA 5 Stück"))
    assert got[0]["To"] == "c@d.de" and got[0]["Subject"] == "VERKAUFEN"
    assert "5 Stück" in got[0].get_content()
