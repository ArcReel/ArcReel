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


def test_echo_dedup_when_buffer_has_same_text_real_user(tmp_path):
    """eager 慢 store 兜底：history 还没本轮 user，buffer 已有 echo + sdk user。"""
    service = AssistantService(project_root=tmp_path)
    echo = {"type": "user", "content": "hi", "local_echo": True}
    is_dup = service._is_buffer_duplicate(
        echo,
        "user",
        transcript_uuids=set(),
        tail_fps=set(),
        history_messages=[],
        buffer_real_user_texts={"hi"},
    )
    assert is_dup is True


def test_echo_preserved_when_no_real_user_anywhere(tmp_path):
    """正向兜底：history 空 + buffer 不含真实 user → echo 必须保留。"""
    service = AssistantService(project_root=tmp_path)
    echo = {"type": "user", "content": "hi", "local_echo": True}
    is_dup = service._is_buffer_duplicate(
        echo,
        "user",
        transcript_uuids=set(),
        tail_fps=set(),
        history_messages=[],
        buffer_real_user_texts=set(),
    )
    assert is_dup is False


def test_existing_signature_backward_compat(tmp_path):
    """旧调用（5 个位置参数）保持工作 — 不破坏 test_assistant_service_more 回归。"""
    service = AssistantService(project_root=tmp_path)
    # uuid dedup 路径：transcript 已有 uuid → True
    assert service._is_buffer_duplicate({"uuid": "u1", "type": "user"}, "user", {"u1"}, set(), []) is True


def test_build_projector_dedups_echo_when_buffer_has_real_user(tmp_path):
    """集成：history 空 + buffer = [echo, sdk_user_msg] → projector 单条 user。"""
    import asyncio

    from server.agent_runtime.models import SessionMeta

    service = AssistantService(project_root=tmp_path)

    class _StubAdapter:
        async def read_raw_messages(self, sid, project_cwd):
            return []  # transcript 空，模拟 batched 模式 turn 进行中

    service.transcript_adapter = _StubAdapter()  # type: ignore[assignment]

    buffer = [
        {"type": "user", "content": "你好", "local_echo": True},
        {"type": "user", "content": "你好", "uuid": "user-uuid-1"},
    ]

    class _SmStub:
        sessions: dict = {}

        def get_buffered_messages(self, sid):
            return buffer

    service.session_manager = _SmStub()  # type: ignore[assignment]

    meta = SessionMeta(
        id="sid-1",
        project_name="proj",
        title="",
        status="running",
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )

    async def _go():
        return await service._build_projector(meta, "sid-1")

    projector = asyncio.run(_go())
    user_turns = [t for t in projector.turns if t.get("type") == "user"]
    assert len(user_turns) == 1, f"expected 1 user turn, got {len(user_turns)}: {user_turns}"
