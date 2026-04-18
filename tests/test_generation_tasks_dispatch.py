from __future__ import annotations

import pytest

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
