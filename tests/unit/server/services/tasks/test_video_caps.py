"""Unit tests for the shared project-level video-capability resolver."""

import pytest

from server.services.tasks import video_caps


@pytest.mark.parametrize(
    ("caps", "expected"),
    [
        # Model has an audio track and episode audio is enabled: audible.
        ({"voice_consistency": "soft", "requested_generate_audio": True}, False),
        ({"voice_consistency": "native", "requested_generate_audio": True}, False),
        # Class C models produce no audio.
        ({"voice_consistency": "none", "requested_generate_audio": True}, True),
        # Episode audio disabled: even a model audio track is inaudible.
        ({"voice_consistency": "soft", "requested_generate_audio": False}, True),
        # Capability lookup fails: missing tier degrades to soft; project_video_caps independently resolves and records the mute switch.
        ({"requested_generate_audio": False}, True),
        ({"requested_generate_audio": True}, False),
    ],
)
async def test_resolve_project_is_silent_covers_both_paths(monkeypatch: pytest.MonkeyPatch, caps: dict, expected: bool):
    """Mute criteria combine model tiers and episode switches; enqueueing uses them to decide Voice_Profiles injection."""

    async def fake_caps(_project, *, degraded_to, episode=None):
        return caps

    monkeypatch.setattr(video_caps, "project_video_caps", fake_caps)
    assert await video_caps.resolve_project_is_silent({}) is expected


async def test_project_video_caps_preserves_silent_intent_on_capability_failure(monkeypatch: pytest.MonkeyPatch):
    """If capability lookup fails, independently resolved requested_generate_audio still follows project overrides, never defaulting back to True."""

    class _FakeResolver:
        def __init__(self, _session_factory):
            pass

        async def video_capabilities_for_project(self, _project, *, generation_type=None):
            raise ValueError("cannot resolve video capabilities")

        async def video_generate_audio_for_project(self, _project):
            return False

    monkeypatch.setattr(video_caps, "ConfigResolver", _FakeResolver)
    caps = await video_caps.project_video_caps({"video_generate_audio": False}, degraded_to="test")
    assert caps == {"requested_generate_audio": False}


async def test_project_video_caps_degrades_silent_on_double_failure(monkeypatch: pytest.MonkeyPatch):
    """If independent lookup also fails (double failure), tighten to False, matching text_generation.py fallback."""

    class _FakeResolver:
        def __init__(self, _session_factory):
            pass

        async def video_capabilities_for_project(self, _project, *, generation_type=None):
            raise ValueError("cannot resolve video capabilities")

        async def video_generate_audio_for_project(self, _project):
            raise ValueError("db unavailable")

    monkeypatch.setattr(video_caps, "ConfigResolver", _FakeResolver)
    caps = await video_caps.project_video_caps({"video_generate_audio": False}, degraded_to="test")
    assert caps == {"requested_generate_audio": False}
