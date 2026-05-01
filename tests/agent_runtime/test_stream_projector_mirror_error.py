"""stream_projector should surface SystemMessage(subtype='mirror_error') to UI."""

from __future__ import annotations

from server.agent_runtime.stream_projector import is_mirror_error_event


def test_recognizes_mirror_error():
    event = {"type": "system", "subtype": "mirror_error", "message": "DB write failed"}
    assert is_mirror_error_event(event) is True


def test_ignores_other_system_events():
    assert is_mirror_error_event({"type": "system", "subtype": "init"}) is False


def test_ignores_non_system_events():
    assert is_mirror_error_event({"type": "assistant"}) is False
    assert is_mirror_error_event({"type": "user"}) is False
    assert is_mirror_error_event({}) is False
    assert is_mirror_error_event(None) is False
