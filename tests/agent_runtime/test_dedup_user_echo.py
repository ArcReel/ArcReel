"""Reconnect dedup regression tests for echo / sdk UserMessage collisions.

Covers R1 (双显) and "user 消失" 现象的根因：_is_buffer_duplicate 之前
local_echo dedup 只查 DB transcript；eager flush + 此修复让 dedup 在
DB 滞后 buffer 时仍鲁棒。
"""

from __future__ import annotations

from server.agent_runtime.service import AssistantService


def test_collect_buffer_real_user_texts_excludes_local_echo(tmp_path):
    service = AssistantService(project_root=tmp_path)
    buffer = [
        {"type": "user", "content": "hello", "local_echo": True},
        {"type": "user", "content": "hello", "uuid": "u-real"},
        {"type": "assistant", "content": [{"type": "text", "text": "hi"}]},
        {"type": "user", "content": "world", "uuid": "u-real-2"},
    ]
    texts = service._collect_buffer_real_user_texts(buffer)
    assert texts == {"hello", "world"}


def test_collect_buffer_real_user_texts_handles_image_only_user(tmp_path):
    """Image-only user (no plain text) should not poison the set."""
    service = AssistantService(project_root=tmp_path)
    buffer = [
        {
            "type": "user",
            "content": [{"type": "image", "source": {"data": "..."}}],
            "uuid": "u-img",
        },
    ]
    texts = service._collect_buffer_real_user_texts(buffer)
    assert texts == set()


def test_collect_buffer_real_user_texts_handles_non_user_types(tmp_path):
    service = AssistantService(project_root=tmp_path)
    buffer = [
        {"type": "assistant", "content": [{"type": "text", "text": "hi"}]},
        {"type": "result", "subtype": "success"},
        {"type": "stream_event"},
    ]
    texts = service._collect_buffer_real_user_texts(buffer)
    assert texts == set()


def test_collect_buffer_real_user_texts_skips_invalid_entries(tmp_path):
    service = AssistantService(project_root=tmp_path)
    buffer = [
        None,
        "not a dict",
        {"type": "user", "uuid": "u-real", "content": "ok"},
    ]
    texts = service._collect_buffer_real_user_texts(buffer)
    assert texts == {"ok"}
