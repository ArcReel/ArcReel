"""项目级视频能力解析共享出口的单测。"""

import pytest

from server.services import video_caps


@pytest.mark.unit
async def test_resolve_project_voice_consistency_reads_caps_field(monkeypatch: pytest.MonkeyPatch):
    """正常解析：直接读 caps 的 voice_consistency 字段，不重新派生。"""

    async def fake_caps(_project, *, degraded_to):
        return {"voice_consistency": "none"}

    monkeypatch.setattr(video_caps, "project_video_caps", fake_caps)
    assert await video_caps.resolve_project_voice_consistency({}) == "none"


@pytest.mark.unit
async def test_resolve_project_voice_consistency_degrades_to_soft(monkeypatch: pytest.MonkeyPatch):
    """caps 解析失败（空 dict）时按既有「无信号不判定为真无声」口径退化为 soft。"""

    async def fake_caps(_project, *, degraded_to):
        return {}

    monkeypatch.setattr(video_caps, "project_video_caps", fake_caps)
    assert await video_caps.resolve_project_voice_consistency({}) == "soft"
