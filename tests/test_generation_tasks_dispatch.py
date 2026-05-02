from __future__ import annotations

import pytest

from lib.image_backends.base import ImageCapabilityError
from server.services.generation_tasks import _TASK_CHANGE_SPECS, _TASK_EXECUTORS


def test_task_executors_registered_for_reference_video():
    assert "reference_video" in _TASK_EXECUTORS


def test_task_change_specs_registered_for_reference_video():
    spec = _TASK_CHANGE_SPECS.get("reference_video")
    assert spec is not None
    entity_type, action, _label_tpl, include_script_episode = spec
    assert entity_type == "reference_video_unit"
    assert action == "reference_video_ready"
    assert include_script_episode is True


@pytest.mark.asyncio
async def test_execute_generation_task_rejects_unknown_type():
    from server.services.generation_tasks import execute_generation_task

    with pytest.raises(ValueError, match="unsupported task_type"):
        await execute_generation_task(
            {
                "task_type": "unknown_xyz",
                "project_name": "demo",
                "resource_id": "x",
                "payload": {},
            }
        )


@pytest.mark.asyncio
async def test_execute_generation_task_translates_image_endpoint_mismatch(monkeypatch):
    from server.services.generation_tasks import execute_generation_task

    async def fake_executor(*_args, **_kwargs):
        raise ImageCapabilityError("image_endpoint_mismatch_no_t2i", model="gpt-image-1")

    monkeypatch.setitem(_TASK_EXECUTORS, "storyboard", fake_executor)

    with pytest.raises(RuntimeError) as exc_info:
        await execute_generation_task(
            {
                "task_type": "storyboard",
                "project_name": "demo",
                "resource_id": "scene-1",
                "payload": {},
            }
        )

    message = str(exc_info.value)
    # 必须是已翻译的 zh 文案，而不是裸 code
    assert "image_endpoint_mismatch_no_t2i" not in message
    assert "gpt-image-1" in message
    assert "图生图" in message  # zh 文案关键字


@pytest.mark.asyncio
async def test_execute_generation_task_translates_capability_missing_i2i(monkeypatch):
    from server.services.generation_tasks import execute_generation_task

    async def fake_executor(*_args, **_kwargs):
        raise ImageCapabilityError("image_capability_missing_i2i", provider="openai", model="gpt-image-1")

    monkeypatch.setitem(_TASK_EXECUTORS, "storyboard", fake_executor)

    with pytest.raises(RuntimeError) as exc_info:
        await execute_generation_task(
            {
                "task_type": "storyboard",
                "project_name": "demo",
                "resource_id": "scene-1",
                "payload": {},
            }
        )

    message = str(exc_info.value)
    assert "image_capability_missing_i2i" not in message
    assert "openai" in message
    assert "gpt-image-1" in message


@pytest.mark.asyncio
async def test_execute_generation_task_propagates_other_exceptions(monkeypatch):
    """非 ImageCapabilityError 的异常应原样冒泡，不被 i18n 分支吞掉"""
    from server.services.generation_tasks import execute_generation_task

    async def fake_executor(*_args, **_kwargs):
        raise ValueError("unrelated business error")

    monkeypatch.setitem(_TASK_EXECUTORS, "storyboard", fake_executor)

    with pytest.raises(ValueError, match="unrelated business error"):
        await execute_generation_task(
            {
                "task_type": "storyboard",
                "project_name": "demo",
                "resource_id": "scene-1",
                "payload": {},
            }
        )
