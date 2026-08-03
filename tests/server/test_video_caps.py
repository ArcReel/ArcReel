"""项目级视频能力解析共享出口的单测。"""

import pytest

from server.services import video_caps


@pytest.mark.unit
async def test_resolve_project_voice_consistency_reads_caps_field(monkeypatch: pytest.MonkeyPatch):
    """正常解析：直接读 caps 的 voice_consistency 字段，不重新派生。"""

    async def fake_caps(_project, *, degraded_to, episode=None):
        return {"voice_consistency": "none"}

    monkeypatch.setattr(video_caps, "project_video_caps", fake_caps)
    assert await video_caps.resolve_project_voice_consistency({}) == "none"


@pytest.mark.unit
async def test_resolve_project_voice_consistency_degrades_to_soft(monkeypatch: pytest.MonkeyPatch):
    """caps 解析失败（空 dict）时按既有「无信号不判定为真无声」口径退化为 soft。"""

    async def fake_caps(_project, *, degraded_to, episode=None):
        return {}

    monkeypatch.setattr(video_caps, "project_video_caps", fake_caps)
    assert await video_caps.resolve_project_voice_consistency({}) == "soft"


@pytest.mark.unit
async def test_project_video_caps_preserves_silent_intent_on_capability_failure(monkeypatch: pytest.MonkeyPatch):
    """能力解析失败时，独立解析出的 requested_generate_audio 仍随项目覆盖走，不回退成 True。"""

    class _FakeResolver:
        def __init__(self, _session_factory):
            pass

        async def video_capabilities_for_project(self, _project, _episode):
            raise ValueError("cannot resolve video capabilities")

        async def video_generate_audio_for_project(self, _project):
            return False

    monkeypatch.setattr(video_caps, "ConfigResolver", _FakeResolver)
    caps = await video_caps.project_video_caps({"video_generate_audio": False}, degraded_to="test")
    assert caps == {"requested_generate_audio": False}


@pytest.mark.unit
async def test_project_video_caps_degrades_silent_on_double_failure(monkeypatch: pytest.MonkeyPatch):
    """独立解析也失败（双重故障）时收紧到 False，同 text_generation.py 的同款兜底口径。"""

    class _FakeResolver:
        def __init__(self, _session_factory):
            pass

        async def video_capabilities_for_project(self, _project, _episode):
            raise ValueError("cannot resolve video capabilities")

        async def video_generate_audio_for_project(self, _project):
            raise ValueError("db unavailable")

    monkeypatch.setattr(video_caps, "ConfigResolver", _FakeResolver)
    caps = await video_caps.project_video_caps({"video_generate_audio": False}, degraded_to="test")
    assert caps == {"requested_generate_audio": False}
