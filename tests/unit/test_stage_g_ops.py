"""Stufe G — Audit-Log (Hash-Chain), Notifier (Dedup/Rate-Limit), Report-Generator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading_agent.ops.notify import (
    ConsoleSink,
    FileSink,
    Notification,
    Notifier,
    Severity,
    TelegramSink,
)
from trading_agent.ops.reports import ReportInputs, ReportPeriod, build_report
from trading_agent.safety.audit_log import AuditLog

_T0 = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)


class _Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def now(self) -> datetime:
        return self.t


# --------------------------------------------------------------------------- audit log


def test_audit_log_chains_and_verifies(tmp_path) -> None:
    log = AuditLog(tmp_path / "audit.jsonl")
    log.record("supervisor", "startup", {"pid": 1})
    log.record("strategy", "signal_emitted", {"instrument": "BTCUSDT", "action": "BUY"})
    e3 = log.record("safety", "kill_switch", {"engaged": True})

    assert log.count == 3
    assert e3.prev_hash != "0" * 64
    res = log.verify()
    assert res.ok and res.entries == 3


def test_audit_log_detects_tampering(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.record("a", "one", {"x": 1})
    log.record("a", "two", {"x": 2})
    log.record("a", "three", {"x": 3})

    lines = path.read_text().splitlines()
    lines[1] = lines[1].replace('"x":2', '"x":99')
    path.write_text("\n".join(lines) + "\n")

    res = AuditLog(path).verify()
    assert not res.ok and res.broken_at == 2


def test_audit_log_reopens_and_continues_chain(tmp_path) -> None:
    path = tmp_path / "audit.jsonl"
    AuditLog(path).record("a", "one")
    log2 = AuditLog(path)
    assert log2.count == 1
    log2.record("a", "two")
    assert log2.verify().ok and log2.count == 2


# --------------------------------------------------------------------------- notifier


def test_notifier_dedup_within_window() -> None:
    clk = _Clock(_T0)
    sink = ConsoleSink(min_severity=Severity.DEBUG)
    n = Notifier([sink], clock=clk, dedup_window_s=300.0)
    assert n.notify(Notification(Severity.WARNING, "stale feed", dedup_key="stale:BTC"))
    assert not n.notify(Notification(Severity.WARNING, "stale feed", dedup_key="stale:BTC"))
    clk.t = _T0 + timedelta(seconds=301)
    assert n.notify(Notification(Severity.WARNING, "stale feed", dedup_key="stale:BTC"))
    assert n.deduped == 1
    assert len(sink.delivered) == 2


def test_notifier_rate_limit_then_summary() -> None:
    clk = _Clock(_T0)
    sink = ConsoleSink(min_severity=Severity.DEBUG)
    n = Notifier([sink], clock=clk, rate_window_s=60.0, max_per_window=3)
    for i in range(5):
        n.notify(Notification(Severity.INFO, f"msg {i}"))
    assert n.emitted == 3
    assert n.rate_limited == 2
    # nächste durchgelassene Nachricht trägt zuerst die Sammelmeldung nach
    clk.t = _T0 + timedelta(seconds=61)
    n.notify(Notification(Severity.INFO, "later"))
    titles = [d.title for d in sink.delivered]
    assert any("unterdrückt" in t for t in titles)


def test_notifier_critical_bypasses_rate_limit() -> None:
    clk = _Clock(_T0)
    sink = ConsoleSink(min_severity=Severity.DEBUG)
    n = Notifier([sink], clock=clk, max_per_window=1)
    n.notify(Notification(Severity.INFO, "one"))
    n.notify(Notification(Severity.INFO, "two"))  # rate-limited
    assert n.notify(Notification(Severity.CRITICAL, "KILL SWITCH"))


def test_file_sink_writes_jsonl(tmp_path) -> None:
    path = tmp_path / "notes.jsonl"
    n = Notifier([FileSink(path)], clock=_Clock(_T0))
    n.notify(Notification(Severity.WARNING, "x"))
    assert path.read_text().count("\n") == 1


def test_telegram_sink_unavailable_without_token(monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    sink = TelegramSink(transport=lambda url, payload: None)
    assert not sink.available()
    sink.deliver(Notification(Severity.CRITICAL, "test"))
    assert sink.sent == 0


def test_telegram_sink_sends_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    seen: list[tuple[str, dict]] = []
    sink = TelegramSink(transport=lambda url, payload: seen.append((url, payload)))
    assert sink.available()
    sink.deliver(Notification(Severity.CRITICAL, "boom"))
    assert sink.sent == 1 and "sendMessage" in seen[0][0] and seen[0][1]["chat_id"] == "42"


# --------------------------------------------------------------------------- reports


def test_build_report_no_trade_day() -> None:
    inp = ReportInputs(
        period=ReportPeriod.DAILY,
        generated_at=_T0,
        window_start=_T0 - timedelta(days=1),
        window_end=_T0,
        top_opportunities=[
            {
                "rank": 1,
                "instrument": "BTCUSDT",
                "score": 26.8,
                "tier": None,
                "setup_state": "scanning",
            }
        ],
        signals_emitted=[],
        breadth={"regime": "neutral", "breadth_score": 0.1, "advancers": 3, "decliners": 3},
    )
    rep = build_report(inp)
    txt = rep.as_text()
    assert "DAILY REPORT" in rep.headline
    assert "NO-TRADE-Zeitraum" in txt
    assert "BTCUSDT" in txt and "neutral" in txt
    assert set(rep.sections) >= {"Top Opportunities", "Signale", "Market Breadth"}


def test_build_report_with_signal_and_portfolio() -> None:
    inp = ReportInputs(
        period=ReportPeriod.WEEKLY,
        generated_at=_T0,
        window_start=_T0 - timedelta(days=7),
        window_end=_T0,
        signals_emitted=[
            {
                "action": "BUY",
                "instrument": "XAUUSDT",
                "direction": "LONG",
                "entry": 4480,
                "stop_loss": 4460,
                "tp2": 4560,
                "rr_to_tp2": 4.0,
                "opportunity_score": 88,
            }
        ],
        portfolio={
            "equity": 12000,
            "cash_pct": 0.25,
            "health": {"score": 72.0, "grade": "GREEN", "flags": ["nur 2 Positionen"]},
            "ranking": [
                {
                    "rank": 1,
                    "instrument": "BTCUSDT",
                    "score": 80,
                    "verdict": "hold",
                    "weight_pct": 20,
                }
            ],
        },
    )
    rep = build_report(inp)
    txt = rep.as_text()
    assert "BUY XAUUSDT" in txt
    assert "Health 72.0/100 (GREEN)" in txt
    assert "nur 2 Positionen" in txt
