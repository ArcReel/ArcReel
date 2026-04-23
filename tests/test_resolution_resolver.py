"""测试 resolve_resolution 按 project → legacy → custom_default → None 顺序解析。"""

from server.services.resolution_resolver import resolve_resolution


def test_returns_none_when_nothing_configured():
    assert resolve_resolution({}, "gemini-aistudio", "veo-3.1-lite-generate-preview") is None


def test_returns_custom_default_when_only_custom():
    assert resolve_resolution({}, "custom-1", "my-model", custom_default="720p") == "720p"


def test_returns_legacy_when_only_legacy():
    project = {"video_model_settings": {"veo-3.1": {"resolution": "1080p"}}}
    assert resolve_resolution(project, "gemini-aistudio", "veo-3.1") == "1080p"


def test_project_model_settings_overrides_legacy():
    project = {
        "model_settings": {"gemini-aistudio/veo-3.1": {"resolution": "720p"}},
        "video_model_settings": {"veo-3.1": {"resolution": "1080p"}},
    }
    assert resolve_resolution(project, "gemini-aistudio", "veo-3.1") == "720p"


def test_project_override_wins_over_custom_default():
    project = {"model_settings": {"custom-1/m": {"resolution": "2K"}}}
    assert resolve_resolution(project, "custom-1", "m", custom_default="1K") == "2K"


def test_legacy_wins_over_custom_default_when_no_project_model_settings():
    project = {"video_model_settings": {"m": {"resolution": "1080p"}}}
    assert resolve_resolution(project, "custom-1", "m", custom_default="720p") == "1080p"


def test_empty_string_project_override_treated_as_unset():
    """空字符串视为“未配置”，继续向下解析。"""
    project = {"model_settings": {"p/m": {"resolution": ""}}}
    assert resolve_resolution(project, "p", "m", custom_default="1K") == "1K"


def test_composite_key_format_uses_slash():
    """key 严格为 '<provider>/<model>'。"""
    project = {"model_settings": {"a/b": {"resolution": "4K"}}}
    assert resolve_resolution(project, "a", "b") == "4K"
    assert resolve_resolution(project, "a-b", "") is None  # 不应误匹配
