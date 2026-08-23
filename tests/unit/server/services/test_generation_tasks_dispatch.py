from __future__ import annotations

import pytest

from lib.image_backends.base import ImageCapabilityError
from lib.reference_compression import ReferencePayloadFloorError
from lib.video_backends.base import VideoCapabilityError
from server.services.generation_tasks import _SKELETON_DRIVEN_TASK_ACTIONS, _TASK_EXECUTORS


def test_task_executors_registered_for_reference_video():
    assert "reference_video" in _TASK_EXECUTORS


def test_task_change_action_registered_for_reference_video():
    # entity_type 不再是静态 spec：按剧本骨架种类动态解析（见
    # test_generation_tasks_service.py 里对 emit_generation_success_batch 的骨架覆盖用例），
    # 与前端 ProjectChange["entity_type"] 联合类型的 "reference_unit" 对齐，不再是仅本侧
    # 认识的 "reference_video_unit"。
    assert _SKELETON_DRIVEN_TASK_ACTIONS.get("reference_video") == "reference_video_ready"


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
async def test_execute_generation_task_passes_claimed_provider_to_reference_proxy(monkeypatch):
    from server.services import generation_tasks

    captured: dict[str, object] = {}

    async def _fake_reference_proxy(
        project_name,
        resource_id,
        payload,
        *,
        script_file=None,
        user_id,
        task_id=None,
        claimed_provider_id=None,
    ):
        captured.update(
            project_name=project_name,
            resource_id=resource_id,
            payload=payload,
            script_file=script_file,
            user_id=user_id,
            task_id=task_id,
            claimed_provider_id=claimed_provider_id,
        )
        return {"ok": True}

    # 替身落在惰性代理的下游协作者上，代理本身的参数透传照跑。
    monkeypatch.setattr(
        "server.services.reference_video_tasks.execute_reference_video_task",
        _fake_reference_proxy,
    )

    result = await generation_tasks.execute_generation_task(
        {
            "task_id": "ref-1",
            "task_type": "reference_video",
            "project_name": "demo",
            "resource_id": "E1U1",
            "payload": {"script_file": "episode_1.json"},
            "script_file": "scripts/frozen.json",
            "user_id": "u1",
        },
        claimed_provider_id="ark",
    )

    assert result == {"ok": True}
    assert captured["claimed_provider_id"] == "ark"
    assert captured["script_file"] == "scripts/frozen.json"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        ImageCapabilityError("image_endpoint_mismatch_no_t2i", model="gpt-image-1"),
        ImageCapabilityError("image_capability_missing_i2i", provider="openai", model="gpt-image-1"),
        VideoCapabilityError("video_duration_not_supported", duration=7, supported="5, 10"),
        ReferencePayloadFloorError(),
    ],
)
async def test_execute_generation_task_propagates_capability_errors_unrendered(monkeypatch, error):
    """能力类异常原样上抛，dispatch 层不做本地化——渲染留到读侧 Translator。

    此前这里按 DEFAULT_LOCALE 烘焙成中文再包 RuntimeError，导致 en/vi 用户在任务
    列表看到中文失败原因。
    """
    from server.services.generation_tasks import execute_generation_task

    async def fake_executor(*_args, **_kwargs):
        raise error

    monkeypatch.setitem(_TASK_EXECUTORS, "storyboard", fake_executor)

    with pytest.raises(type(error)) as exc_info:
        await execute_generation_task(
            {
                "task_type": "storyboard",
                "project_name": "demo",
                "resource_id": "scene-1",
                "payload": {},
            }
        )

    assert exc_info.value is error
    assert exc_info.value.code == error.code


@pytest.mark.asyncio
async def test_execute_generation_task_propagates_other_exceptions(monkeypatch):
    """非能力类异常同样原样冒泡"""
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
