"""测试 resolve_resolution 按 project → legacy → custom default → None 顺序解析。"""

from unittest.mock import patch

import pytest

from server.services.resolution_resolver import _from_project, resolve_resolution

# --- 纯项目字典路径（同步即可） ---


def test_from_project_returns_none_when_nothing_configured():
    assert _from_project({}, "gemini-aistudio", "veo-3.1-lite-generate-preview") is None


def test_from_project_legacy_only():
    project = {"video_model_settings": {"veo-3.1": {"resolution": "1080p"}}}
    assert _from_project(project, "gemini-aistudio", "veo-3.1") == "1080p"


def test_from_project_model_settings_overrides_legacy():
    project = {
        "model_settings": {"gemini-aistudio/veo-3.1": {"resolution": "720p"}},
        "video_model_settings": {"veo-3.1": {"resolution": "1080p"}},
    }
    assert _from_project(project, "gemini-aistudio", "veo-3.1") == "720p"


def test_from_project_empty_string_override_treated_as_unset():
    project = {"model_settings": {"p/m": {"resolution": ""}}}
    assert _from_project(project, "p", "m") is None


def test_from_project_composite_key_format_uses_slash():
    project = {"model_settings": {"a/b": {"resolution": "4K"}}}
    assert _from_project(project, "a", "b") == "4K"
    assert _from_project(project, "a-b", "") is None


# --- 包含 custom default 的 async 集成路径 ---


@pytest.mark.asyncio
async def test_resolve_returns_none_when_nothing_configured():
    assert await resolve_resolution({}, "gemini-aistudio", "veo-3.1") is None


@pytest.mark.asyncio
async def test_resolve_returns_custom_default_when_only_custom():
    with patch(
        "server.services.resolution_resolver.get_custom_resolution_default",
        return_value="720p",
    ):
        assert await resolve_resolution({}, "custom-1", "my-model") == "720p"


@pytest.mark.asyncio
async def test_resolve_project_override_wins_over_custom_default():
    project = {"model_settings": {"custom-1/m": {"resolution": "2K"}}}
    with patch(
        "server.services.resolution_resolver.get_custom_resolution_default",
        return_value="1K",
    ) as mock_custom:
        assert await resolve_resolution(project, "custom-1", "m") == "2K"
        mock_custom.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_legacy_wins_over_custom_default():
    project = {"video_model_settings": {"m": {"resolution": "1080p"}}}
    with patch(
        "server.services.resolution_resolver.get_custom_resolution_default",
        return_value="720p",
    ) as mock_custom:
        assert await resolve_resolution(project, "custom-1", "m") == "1080p"
        mock_custom.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_falls_through_to_custom_when_project_empty_string():
    project = {"model_settings": {"custom-1/m": {"resolution": ""}}}
    with patch(
        "server.services.resolution_resolver.get_custom_resolution_default",
        return_value="1K",
    ):
        assert await resolve_resolution(project, "custom-1", "m") == "1K"
