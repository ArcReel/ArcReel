"""server.agent_runtime.sdk_tools 测试的目录级 fixture。

替身与断言 helper 在 `sdk_tools_support.py`；两个 stub fixture 只对显式
`pytest.mark.usefixtures` 的模块生效，不按目录自动铺开。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.agent_runtime.sdk_tools._context import ToolContext
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import (
    _fake_reference_projection,
    _FakePM,
)


@pytest.fixture
def fake_ctx(tmp_path: Path) -> ToolContext:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    # Build a storyboard image so video tools can find it.
    (project_dir / "storyboards").mkdir()
    (project_dir / "storyboards" / "scene_E1S01.png").write_bytes(b"")
    # 旁白夹具登记的音频同样落盘：旧 schema 的复用判定要核实文件确实还在。
    (project_dir / "audio").mkdir()
    (project_dir / "audio" / "segment_E1S01.wav").write_bytes(b"")
    (project_dir / "audio" / "segment_E1S02.wav").write_bytes(b"")

    return ToolContext(
        project_name="demo",
        projects_root=tmp_path,
        pm=_FakePM("demo", project_dir),  # type: ignore[arg-type]
    )


@pytest.fixture
def _stub_audio_switch_guard(monkeypatch):
    """视频入队前的音频开关预检要读真实配置库，声明本 fixture 的模块不覆盖它的行为，一律放行。

    行为覆盖在 tests/integration/server/agent_runtime/sdk_tools/test_enqueue_videos_audio_switch.py。
    """
    from server.services import video_batch_admission as _mod

    async def _noop(_project, _capability):
        return None

    monkeypatch.setattr(_mod, "assert_audio_switch_supported", _noop)


@pytest.fixture
def _stub_reference_request_projection(monkeypatch):
    """Agent 工具接线测试不访问真实供应商配置、项目资产文件与任务库。"""
    from server.services import video_batch_admission as _admission

    async def _no_active_tasks(**_kwargs):
        return []

    async def _no_active_tts(**_kwargs):
        return frozenset()

    monkeypatch.setattr(_admission, "project_reference_unit_request", _fake_reference_projection())
    monkeypatch.setattr(_admission, "get_active_tasks_for_resources", _no_active_tasks)
    monkeypatch.setattr(_admission, "active_tts_resource_ids", _no_active_tts)
