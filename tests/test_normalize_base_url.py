"""base_url 归一化工具函数测试。"""

from lib.config.url_utils import ensure_google_base_url, ensure_openai_base_url, normalize_base_url


class TestNormalizeBaseUrl:
    def test_none_returns_none(self):
        assert normalize_base_url(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_base_url("") is None

    def test_whitespace_only_returns_none(self):
        assert normalize_base_url("   ") is None

    def test_adds_trailing_slash(self):
        assert normalize_base_url("https://proxy.example.com/v1") == "https://proxy.example.com/v1/"

    def test_preserves_existing_trailing_slash(self):
        assert normalize_base_url("https://proxy.example.com/v1/") == "https://proxy.example.com/v1/"

    def test_strips_whitespace(self):
        assert normalize_base_url("  https://proxy.example.com/v1  ") == "https://proxy.example.com/v1/"

    def test_plain_domain(self):
        assert normalize_base_url("https://example.com") == "https://example.com/"


class TestEnsureOpenaiBaseUrl:
    """ensure_openai_base_url 自动追加 /v1 后缀。"""

    def test_none_returns_none(self):
        assert ensure_openai_base_url(None) is None

    def test_empty_string_returns_empty(self):
        assert ensure_openai_base_url("") == ""

    def test_appends_v1_to_plain_domain(self):
        assert ensure_openai_base_url("https://api.example.com") == "https://api.example.com/v1"

    def test_appends_v1_to_domain_with_path(self):
        assert ensure_openai_base_url("https://proxy.example.com/api") == "https://proxy.example.com/api/v1"

    def test_preserves_existing_v1(self):
        assert ensure_openai_base_url("https://api.example.com/v1") == "https://api.example.com/v1"

    def test_preserves_existing_v2(self):
        assert ensure_openai_base_url("https://api.example.com/v2") == "https://api.example.com/v2"

    def test_strips_trailing_slash_before_check(self):
        assert ensure_openai_base_url("https://api.example.com/") == "https://api.example.com/v1"

    def test_strips_trailing_slash_with_v1(self):
        assert ensure_openai_base_url("https://api.example.com/v1/") == "https://api.example.com/v1"

    def test_strips_whitespace(self):
        assert ensure_openai_base_url("  https://api.example.com  ") == "https://api.example.com/v1"

    def test_real_world_newapi_url(self):
        assert ensure_openai_base_url("https://new.xiaoweiliang.cn") == "https://new.xiaoweiliang.cn/v1"

    def test_real_world_newapi_url_with_v1(self):
        assert ensure_openai_base_url("https://new.xiaoweiliang.cn/v1") == "https://new.xiaoweiliang.cn/v1"


class TestEnsureGoogleBaseUrl:
    """ensure_google_base_url 剥离版本路径，防止 SDK 重复拼接。"""

    def test_none_returns_none(self):
        assert ensure_google_base_url(None) is None

    def test_empty_string_returns_none(self):
        assert ensure_google_base_url("") is None

    def test_plain_domain_adds_trailing_slash(self):
        assert ensure_google_base_url("https://sub2api.pollochen.com") == "https://sub2api.pollochen.com/"

    def test_strips_v1beta_suffix(self):
        assert ensure_google_base_url("https://sub2api.pollochen.com/v1beta") == "https://sub2api.pollochen.com/"

    def test_strips_v1_suffix(self):
        assert ensure_google_base_url("https://sub2api.pollochen.com/v1") == "https://sub2api.pollochen.com/"

    def test_strips_v1alpha_suffix(self):
        assert ensure_google_base_url("https://sub2api.pollochen.com/v1alpha") == "https://sub2api.pollochen.com/"

    def test_strips_trailing_slash_then_version(self):
        assert ensure_google_base_url("https://sub2api.pollochen.com/v1beta/") == "https://sub2api.pollochen.com/"

    def test_preserves_path_without_version(self):
        assert ensure_google_base_url("https://proxy.example.com/api") == "https://proxy.example.com/api/"

    def test_strips_whitespace(self):
        assert ensure_google_base_url("  https://sub2api.pollochen.com  ") == "https://sub2api.pollochen.com/"

    def test_real_world_googleapis(self):
        assert (
            ensure_google_base_url("https://generativelanguage.googleapis.com")
            == "https://generativelanguage.googleapis.com/"
        )

    def test_real_world_googleapis_with_v1beta(self):
        assert (
            ensure_google_base_url("https://generativelanguage.googleapis.com/v1beta")
            == "https://generativelanguage.googleapis.com/"
        )
