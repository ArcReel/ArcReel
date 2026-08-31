"""ARCREEL_SDK_SESSION_STORE_FLUSH env parser."""

from __future__ import annotations

import logging

from lib.agent_session_store import session_store_flush_mode


def test_default_is_eager(monkeypatch):
    monkeypatch.delenv("ARCREEL_SDK_SESSION_STORE_FLUSH", raising=False)
    assert session_store_flush_mode() == "eager"


def test_explicit_batched(monkeypatch):
    monkeypatch.setenv("ARCREEL_SDK_SESSION_STORE_FLUSH", "batched")
    assert session_store_flush_mode() == "batched"


def test_case_insensitive(monkeypatch):
    monkeypatch.setenv("ARCREEL_SDK_SESSION_STORE_FLUSH", "Batched")
    assert session_store_flush_mode() == "batched"


def test_unknown_falls_back_to_eager_with_warning(monkeypatch, caplog):
    monkeypatch.setenv("ARCREEL_SDK_SESSION_STORE_FLUSH", "weird")
    with caplog.at_level(logging.WARNING, logger="arcreel.session_store"):
        assert session_store_flush_mode() == "eager"
    assert any("ARCREEL_SDK_SESSION_STORE_FLUSH" in rec.message for rec in caplog.records)


def test_empty_treated_as_eager(monkeypatch):
    monkeypatch.setenv("ARCREEL_SDK_SESSION_STORE_FLUSH", "")
    assert session_store_flush_mode() == "eager"


def test_eager_explicit(monkeypatch):
    monkeypatch.setenv("ARCREEL_SDK_SESSION_STORE_FLUSH", "eager")
    assert session_store_flush_mode() == "eager"
