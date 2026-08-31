"""Tests: strukturiertes JSON-Logging + Secret-Redaction."""

from __future__ import annotations

import io
import json
import logging

from trading_agent.utils.logging import configure_logging, get_logger, redact


class TestRedact:
    def test_redacts_sensitive_keys(self) -> None:
        out = redact({"api_key": "abc123", "nested": {"secret": "s", "ok": 1}})
        assert out["api_key"] == "***REDACTED***"
        assert out["nested"]["secret"] == "***REDACTED***"
        assert out["nested"]["ok"] == 1

    def test_redacts_key_like_strings(self) -> None:
        s = redact("token is sk_live_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        assert "sk_live_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" not in s
        assert "***REDACTED***" in s

    def test_keeps_short_strings(self) -> None:
        assert redact("hello world") == "hello world"

    def test_lists_and_tuples(self) -> None:
        assert redact([{"password": "x"}, "ok"]) == [{"password": "***REDACTED***"}, "ok"]


class TestJsonLogging:
    def test_emits_one_json_line(self) -> None:
        stream = io.StringIO()
        configure_logging("INFO", stream=stream)
        logging.getLogger().handlers[0].stream = stream  # sicherstellen
        log = get_logger("test")
        log.info("hello", extra={"symbol": "BTCUSDT", "count": 3})
        line = stream.getvalue().strip().splitlines()[-1]
        payload = json.loads(line)
        assert payload["message"] == "hello"
        assert payload["level"] == "INFO"
        assert payload["ctx"]["symbol"] == "BTCUSDT"
        assert payload["ctx"]["count"] == 3
        assert payload["ts"].endswith("+00:00")

    def test_does_not_log_secrets(self) -> None:
        stream = io.StringIO()
        configure_logging("INFO", stream=stream)
        logging.getLogger().handlers[0].stream = stream
        get_logger("test").warning("connect", extra={"api_secret": "TOPSECRETVALUE"})
        text = stream.getvalue()
        assert "TOPSECRETVALUE" not in text
        assert "***REDACTED***" in text

    def test_exception_included(self) -> None:
        stream = io.StringIO()
        configure_logging("INFO", stream=stream)
        logging.getLogger().handlers[0].stream = stream
        try:
            raise ValueError("boom")
        except ValueError:
            get_logger("test").exception("failed")
        payload = json.loads(stream.getvalue().strip().splitlines()[-1])
        assert "boom" in payload["exc"]
