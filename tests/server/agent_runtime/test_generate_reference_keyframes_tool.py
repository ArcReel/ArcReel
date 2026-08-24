from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from server.agent_runtime.sdk_tools.enqueue_reference_keyframes import generate_reference_keyframes_tool

pytestmark = pytest.mark.unit


class _ProjectManager:
    def load_script(self, project_name: str, script_file: str) -> dict:
        assert project_name == "proj-test"
        assert script_file == "episode_1.json"
        return {
            "video_units": [
                {
                    "unit_id": "E1U01",
                    "keyframes": [
                        {
                            "keyframe_id": "E1U01K01",
                            "description": "@[鳄鱼爸爸] 在 @[乡村工坊庭院] 中工作",
                            "image_path": "keyframes/E1U01K01.png",
                        }
                    ],
                }
            ]
        }


def test_generate_reference_keyframes_uses_fresh_reference_task(monkeypatch, tmp_path):
    captured = {}

    async def _fake_batch(*, project_name, specs):
        captured["project_name"] = project_name
        captured["specs"] = specs
        return [SimpleNamespace(resource_id="E1U01K01")], []

    monkeypatch.setattr(
        "server.agent_runtime.sdk_tools.enqueue_reference_keyframes.batch_enqueue_and_wait",
        _fake_batch,
    )
    ctx = SimpleNamespace(project_name="proj-test", pm=_ProjectManager(), projects_root=tmp_path)
    result = asyncio.run(
        generate_reference_keyframes_tool(ctx).handler(
            {"episode": 1, "keyframe_ids": ["E1U01K01"]}
        )
    )

    assert result["is_error"] is False
    assert result["succeeded"] == ["E1U01K01"]
    spec = captured["specs"][0]
    assert spec.task_type == "reference_keyframe"
    assert spec.resource_id == "E1U01K01"
    assert spec.script_file == "episode_1.json"


def test_generate_reference_keyframes_rejects_unknown_id(tmp_path):
    ctx = SimpleNamespace(project_name="proj-test", pm=_ProjectManager(), projects_root=tmp_path)
    result = asyncio.run(
        generate_reference_keyframes_tool(ctx).handler(
            {"episode": 1, "keyframe_ids": ["E1U99K01"]}
        )
    )

    assert result["is_error"] is True
    assert "E1U99K01" in result["content"][0]["text"]
