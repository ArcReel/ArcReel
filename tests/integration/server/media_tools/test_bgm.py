"""list_bgm 工具：列出项目里已上传的 BGM 供 edit_timeline 引用。"""

from __future__ import annotations

from pathlib import Path

from lib.bgm.service import BgmLibraryService
from lib.db.base import DEFAULT_USER_ID
from lib.project.project_manager import ProjectManager
from server.agent_toolset.edit_timelines import LIST_BGM
from server.media_tools.bgm import BgmListItem
from server.tool_runtime import CallerContext
from tests.factories import wav_bytes
from tests.integration.server.agent_tool_support import ToolHarness, run_declared_tool


def _harness(tmp_path: Path) -> ToolHarness:
    pm = ProjectManager(tmp_path)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    return ToolHarness("demo", tmp_path, pm, caller=CallerContext(user_id=DEFAULT_USER_ID, source="mcp"))


async def test_list_bgm_reports_id_name_and_duration_in_upload_order(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    service = BgmLibraryService(harness.pm)
    first = await service.upload("demo", filename="雨夜.wav", content=wav_bytes(1.0, tone_hz=330))
    second = await service.upload("demo", filename="追逐.mp3.wav", content=wav_bytes(2.0, tone_hz=440))

    listed = await run_declared_tool(LIST_BGM, harness, {})

    assert listed.problem is None
    assert listed.value == (
        BgmListItem(id=first.id, name="雨夜", duration=1.0),
        BgmListItem(id=second.id, name="追逐.mp3", duration=2.0),
    )
    assert LIST_BGM.summary is not None
    assert first.id in LIST_BGM.summary(listed.value)


async def test_empty_library_says_where_bgm_comes_from(tmp_path: Path) -> None:
    listed = await run_declared_tool(LIST_BGM, _harness(tmp_path), {})

    assert listed.value == ()
    assert LIST_BGM.summary is not None
    assert "BGM 轨" in LIST_BGM.summary(())
