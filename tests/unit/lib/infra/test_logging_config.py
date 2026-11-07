import logging

import httpx

from lib.infra.logging_config import _HANDLER_ATTR, setup_logging


class TestSetupLogging:
    def test_sets_root_level(self):
        setup_logging(level="DEBUG")
        assert logging.getLogger().level == logging.DEBUG

    def test_default_level_is_info(self):
        setup_logging()
        assert logging.getLogger().level == logging.INFO

    def test_adds_handler_to_root(self):
        setup_logging()
        root = logging.getLogger()
        assert any(getattr(h, _HANDLER_ATTR, False) for h in root.handlers)

    def test_log_format_contains_level_and_name(self):
        setup_logging(level="INFO")
        root = logging.getLogger()
        our_handler = next(h for h in root.handlers if getattr(h, _HANDLER_ATTR, False))
        formatter = our_handler.formatter
        record = logging.LogRecord(
            name="test.module",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello world",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        assert "INFO" in formatted
        assert "test.module" in formatted
        assert "hello world" in formatted

    def test_env_variable_override(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "WARNING")
        setup_logging()
        assert logging.getLogger().level == logging.WARNING

    def test_idempotent_no_duplicate_handlers(self):
        setup_logging()
        setup_logging()
        root = logging.getLogger()
        our_handlers = [h for h in root.handlers if getattr(h, _HANDLER_ATTR, False)]
        assert len(our_handlers) == 1

    def test_httpx_request_log_drops_signed_query(self, caplog):
        setup_logging()
        setup_logging()
        httpx_logger = logging.getLogger("httpx")
        # Repeated calls never duplicate filters.
        assert len([f for f in httpx_logger.filters if type(f).__name__ == "_StripUrlQueryFilter"]) == 1
        url = httpx.URL("https://assets.example/a.png?X-Tos-Signature=TEST-SIGNATURE")
        with caplog.at_level(logging.INFO, logger="httpx"):
            httpx_logger.info('HTTP Request: %s %s "%s %d %s"', "GET", url, "HTTP/1.1", 200, "OK")
        assert "https://assets.example/a.png" in caplog.text
        assert "TEST-SIGNATURE" not in caplog.text

    def teardown_method(self):
        """Clear root-logger handlers after each test."""
        root = logging.getLogger()
        root.handlers = [h for h in root.handlers if not getattr(h, _HANDLER_ATTR, False)]
        root.setLevel(logging.WARNING)
