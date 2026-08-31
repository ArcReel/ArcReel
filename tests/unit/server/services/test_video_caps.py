"""项目级视频能力解析共享出口的单测。"""

import pytest

from server.services import video_caps


@pytest.mark.parametrize(
    ("caps", "expected"),
    [
        # 有音轨且本集开着音频：有声
        ({"voice_consistency": "soft", "requested_generate_audio": True}, False),
        ({"voice_consistency": "native", "requested_generate_audio": True}, False),
        # C 类模型不产音
        ({"voice_consistency": "none", "requested_generate_audio": True}, True),
        # 本集关闭音频：模型有音轨也听不到声音
        ({"voice_consistency": "soft", "requested_generate_audio": False}, True),
        # 能力解析失败：档位缺失退化为 soft，无声开关由 project_video_caps 独立解析后写入
        ({"requested_generate_audio": False}, True),
        ({"requested_generate_audio": True}, False),
    ],
)
async def test_resolve_project_is_silent_covers_both_paths(monkeypatch: pytest.MonkeyPatch, caps: dict, expected: bool):
    """无声判据合并模型档与本集开关两条路径，入队层据此决定是否注入 Voice_Profiles。"""

    async def fake_caps(_project, *, degraded_to, episode=None):
        return caps

    monkeypatch.setattr(video_caps, "project_video_caps", fake_caps)
    assert await video_caps.resolve_project_is_silent({}) is expected


async def test_project_video_caps_preserves_silent_intent_on_capability_failure(monkeypatch: pytest.MonkeyPatch):
    """能力解析失败时，独立解析出的 requested_generate_audio 仍随项目覆盖走，不回退成 True。"""

    class _FakeResolver:
        def __init__(self, _session_factory):
            pass

        async def video_capabilities_for_project(self, _project, *, capability=None):
            raise ValueError("cannot resolve video capabilities")

        async def video_generate_audio_for_project(self, _project):
            return False

    monkeypatch.setattr(video_caps, "ConfigResolver", _FakeResolver)
    caps = await video_caps.project_video_caps({"video_generate_audio": False}, degraded_to="test")
    assert caps == {"requested_generate_audio": False}


async def test_project_video_caps_degrades_silent_on_double_failure(monkeypatch: pytest.MonkeyPatch):
    """独立解析也失败（双重故障）时收紧到 False，同 text_generation.py 的同款兜底口径。"""

    class _FakeResolver:
        def __init__(self, _session_factory):
            pass

        async def video_capabilities_for_project(self, _project, *, capability=None):
            raise ValueError("cannot resolve video capabilities")

        async def video_generate_audio_for_project(self, _project):
            raise ValueError("db unavailable")

    monkeypatch.setattr(video_caps, "ConfigResolver", _FakeResolver)
    caps = await video_caps.project_video_caps({"video_generate_audio": False}, degraded_to="test")
    assert caps == {"requested_generate_audio": False}
