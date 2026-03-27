from unittest.mock import patch

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from lib.db.base import Base
from lib.config.resolver import ConfigResolver


class _FakeConfigService:
    """最小化的 ConfigService fake，只实现 resolver 需要的方法。"""

    def __init__(self, settings: dict[str, str] | None = None):
        self._settings = settings or {}

    async def get_setting(self, key: str, default: str = "") -> str:
        return self._settings.get(key, default)

    async def get_default_video_backend(self) -> tuple[str, str]:
        return ("gemini-aistudio", "veo-3.1-fast-generate-preview")

    async def get_default_image_backend(self) -> tuple[str, str]:
        return ("gemini-aistudio", "gemini-3.1-flash-image-preview")

    async def get_provider_config(self, provider: str) -> dict[str, str]:
        return {"api_key": f"key-{provider}"}

    async def get_all_provider_configs(self) -> dict[str, dict[str, str]]:
        return {"gemini-aistudio": {"api_key": "key-aistudio"}}


class TestVideoGenerateAudio:
    """验证 video_generate_audio 的默认值、全局配置、项目级覆盖优先级。"""

    async def test_default_is_false_when_db_empty(self, tmp_path):
        """DB 无值时应返回 False（不是 True）。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
        assert result is False

    async def test_global_true(self, tmp_path):
        """DB 中值为 "true" 时返回 True。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "true"})
        result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
        assert result is True

    async def test_global_false(self, tmp_path):
        """DB 中值为 "false" 时返回 False。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "false"})
        result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
        assert result is False

    async def test_bool_parsing_variants(self, tmp_path):
        """验证各种布尔字符串的解析。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        for val, expected in [("TRUE", True), ("1", True), ("yes", True), ("0", False), ("no", False), ("", False)]:
            fake_svc = _FakeConfigService(settings={"video_generate_audio": val} if val else {})
            result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
            assert result is expected, f"Failed for {val!r}: got {result}"

    async def test_project_override_true_over_global_false(self, tmp_path):
        """项目级覆盖 True 优先于全局 False。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "false"})
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = {"video_generate_audio": True}
            result = await resolver._resolve_video_generate_audio(fake_svc, project_name="demo")
        assert result is True

    async def test_project_override_false_over_global_true(self, tmp_path):
        """项目级覆盖 False 优先于全局 True。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "true"})
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = {"video_generate_audio": False}
            result = await resolver._resolve_video_generate_audio(fake_svc, project_name="demo")
        assert result is False

    async def test_project_none_skips_override(self, tmp_path):
        """project_name=None 时不读取项目配置。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "true"})
        result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
        assert result is True

    async def test_project_override_string_value(self, tmp_path):
        """项目级覆盖值为字符串时也能正确解析。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "true"})
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = {"video_generate_audio": "false"}
            result = await resolver._resolve_video_generate_audio(fake_svc, project_name="demo")
        assert result is False


class TestDefaultBackends:
    """验证后端配置方法委托给 ConfigService。"""

    async def test_default_video_backend(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService()
        result = await resolver._resolve_default_video_backend(fake_svc)
        assert result == ("gemini-aistudio", "veo-3.1-fast-generate-preview")

    async def test_default_image_backend(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService()
        result = await resolver._resolve_default_image_backend(fake_svc)
        assert result == ("gemini-aistudio", "gemini-3.1-flash-image-preview")


class TestProviderConfig:
    """验证供应商配置方法委托给 ConfigService。"""

    async def _make_session(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        return factory, engine

    async def test_provider_config(self):
        factory, engine = await self._make_session()
        try:
            resolver = ConfigResolver.__new__(ConfigResolver)
            fake_svc = _FakeConfigService()
            async with factory() as session:
                result = await resolver._resolve_provider_config(fake_svc, session, "gemini-aistudio")
            assert result == {"api_key": "key-gemini-aistudio"}
        finally:
            await engine.dispose()

    async def test_all_provider_configs(self):
        factory, engine = await self._make_session()
        try:
            resolver = ConfigResolver.__new__(ConfigResolver)
            fake_svc = _FakeConfigService()
            async with factory() as session:
                result = await resolver._resolve_all_provider_configs(fake_svc, session)
            assert "gemini-aistudio" in result
        finally:
            await engine.dispose()
