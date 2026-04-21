from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.config.resolver import ConfigResolver
from lib.config.service import ProviderStatus
from lib.db.base import Base


async def _make_session():
    """创建内存 SQLite 数据库并返回 (factory, engine)。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return factory, engine


def _make_ready_provider(name: str, media_types: list[str]) -> ProviderStatus:
    return ProviderStatus(
        name=name,
        display_name=name,
        description="",
        status="ready",
        media_types=media_types,
        capabilities=[],
        required_keys=[],
        configured_keys=[],
        missing_keys=[],
    )


class _FakeConfigService:
    """最小化的 ConfigService fake，只实现 resolver 需要的方法。"""

    def __init__(
        self,
        settings: dict[str, str] | None = None,
        *,
        ready_providers: list[ProviderStatus] | None = None,
    ):
        self._settings = settings or {}
        self._ready_providers = ready_providers

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

    async def get_all_providers_status(self) -> list[ProviderStatus]:
        if self._ready_providers is not None:
            return self._ready_providers
        return [_make_ready_provider("gemini-aistudio", ["text", "image", "video"])]


class TestVideoGenerateAudio:
    """验证 video_generate_audio 的默认值、全局配置、项目级覆盖优先级。"""

    async def test_default_is_true_when_db_empty(self, tmp_path):
        """DB 无值时应返回 True（PR7 §11 决策：与 Seedance/Grok 默认开启一致）。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
        assert result is True

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
        for val, expected in [("TRUE", True), ("1", True), ("yes", True), ("0", False), ("no", False), ("", True)]:
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
    """验证 video/image 后端解析：显式值 vs auto-resolve。"""

    async def test_video_backend_explicit(self):
        """DB 有显式值时直接返回。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={"default_video_backend": "ark/doubao-seedance-1-5-pro"},
        )
        result = await resolver._resolve_default_video_backend(fake_svc, None)
        assert result == ("ark", "doubao-seedance-1-5-pro")

    async def test_video_backend_auto_resolve(self):
        """DB 无值时走 auto-resolve，选第一个 ready 供应商的默认 video 模型。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        # auto-resolve 会在 PROVIDER_REGISTRY 中找到 ready 供应商，不会走到 custom provider 分支
        factory, engine = await _make_session()
        try:
            async with factory() as session:
                result = await resolver._resolve_default_video_backend(fake_svc, session)
            assert result[0] in ("gemini-aistudio", "gemini-vertex", "ark", "grok")
        finally:
            await engine.dispose()

    async def test_video_backend_auto_resolve_no_ready_provider(self):
        """无 ready 供应商且无自定义供应商时抛出 ValueError。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={}, ready_providers=[])
        factory, engine = await _make_session()
        try:
            async with factory() as session:
                with pytest.raises(ValueError, match="未找到可用的 video 供应商"):
                    await resolver._resolve_default_video_backend(fake_svc, session)
        finally:
            await engine.dispose()

    async def test_image_backend_explicit(self):
        """DB 有显式值时直接返回。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={"default_image_backend": "grok/grok-2-image"},
        )
        result = await resolver._resolve_default_image_backend(fake_svc, None)
        assert result == ("grok", "grok-2-image")

    async def test_image_backend_auto_resolve(self):
        """DB 无值时走 auto-resolve。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        factory, engine = await _make_session()
        try:
            async with factory() as session:
                result = await resolver._resolve_default_image_backend(fake_svc, session)
            assert result[0] in ("gemini-aistudio", "gemini-vertex", "ark", "grok")
        finally:
            await engine.dispose()

    async def test_image_backend_auto_resolve_no_ready_provider(self):
        """无 ready 供应商且无自定义供应商时抛出 ValueError。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={}, ready_providers=[])
        factory, engine = await _make_session()
        try:
            async with factory() as session:
                with pytest.raises(ValueError, match="未找到可用的 image 供应商"):
                    await resolver._resolve_default_image_backend(fake_svc, session)
        finally:
            await engine.dispose()


class TestProviderConfig:
    """验证供应商配置方法委托给 ConfigService。"""

    async def test_provider_config(self):
        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver.__new__(ConfigResolver)
            fake_svc = _FakeConfigService()
            async with factory() as session:
                result = await resolver._resolve_provider_config(fake_svc, session, "gemini-aistudio")
            assert result == {"api_key": "key-gemini-aistudio"}
        finally:
            await engine.dispose()

    async def test_all_provider_configs(self):
        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver.__new__(ConfigResolver)
            fake_svc = _FakeConfigService()
            async with factory() as session:
                result = await resolver._resolve_all_provider_configs(fake_svc, session)
            assert "gemini-aistudio" in result
        finally:
            await engine.dispose()


class TestSessionReuse:
    """验证 session() 上下文管理器的 session 复用行为。"""

    async def test_session_context_manager_reuses_single_session(self):
        """resolver.session() 下多次调用只创建 1 个 session。"""
        factory, engine = await _make_session()
        try:
            call_count = 0
            real_call = factory.__call__

            def counting_factory():
                nonlocal call_count
                call_count += 1
                return real_call()

            resolver = ConfigResolver(factory)
            fake_backend = ("gemini-aistudio", "test-model")

            # 不使用 session()：每次调用创建新 session
            call_count = 0
            with (
                patch.object(resolver, "_session_factory", side_effect=counting_factory),
                patch.object(resolver, "_resolve_default_video_backend", return_value=fake_backend),
                patch.object(resolver, "_resolve_default_image_backend", return_value=fake_backend),
            ):
                await resolver.default_video_backend()
                await resolver.default_image_backend()
            assert call_count == 2, f"不使用 session() 应创建 2 个 session，实际 {call_count}"

            # 使用 session()：只创建 1 个 session
            call_count = 0
            with patch.object(resolver, "_session_factory", side_effect=counting_factory):
                async with resolver.session() as r:
                    with (
                        patch.object(r, "_resolve_default_video_backend", return_value=fake_backend),
                        patch.object(r, "_resolve_default_image_backend", return_value=fake_backend),
                        patch.object(r, "_resolve_video_generate_audio", return_value=False),
                    ):
                        await r.default_video_backend()
                        await r.default_image_backend()
                        await r.video_generate_audio()
            # session() 自身创建 1 个，内部调用复用 bound session 不再创建
            assert call_count == 1, f"使用 session() 应只创建 1 个 session，实际 {call_count}"
        finally:
            await engine.dispose()

    async def test_bound_resolver_shares_session_object(self):
        """bound resolver 的 _open_session 返回同一个 session 对象。"""
        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver(factory)
            sessions_seen = []

            async with resolver.session() as r:
                async with r._open_session() as (s1, _):
                    sessions_seen.append(s1)
                async with r._open_session() as (s2, _):
                    sessions_seen.append(s2)

            assert sessions_seen[0] is sessions_seen[1]
        finally:
            await engine.dispose()

    async def test_unbound_resolver_creates_separate_sessions(self):
        """未绑定的 resolver 每次 _open_session 创建不同 session。"""
        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver(factory)
            sessions_seen = []

            async with resolver._open_session() as (s1, _):
                sessions_seen.append(s1)
            async with resolver._open_session() as (s2, _):
                sessions_seen.append(s2)

            assert sessions_seen[0] is not sessions_seen[1]
        finally:
            await engine.dispose()


class TestVideoBackendThreeLevelPriority:
    """验证 video_backend 三级优先级：项目设置 > 系统设置 > auto-resolve。"""

    async def test_project_override_wins_over_system_setting(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={"default_video_backend": "grok/grok-imagine-video"},
        )
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = {
                "video_backend": "gemini-aistudio/veo-3.1-generate-preview",
            }
            result = await resolver._resolve_video_backend(fake_svc, None, "demo")
        assert result == ("gemini-aistudio", "veo-3.1-generate-preview")

    async def test_project_empty_falls_back_to_system_setting(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={"default_video_backend": "grok/grok-imagine-video"},
        )
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = {}
            result = await resolver._resolve_video_backend(fake_svc, None, "demo")
        assert result == ("grok", "grok-imagine-video")

    async def test_no_project_name_uses_system_setting(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={"default_video_backend": "ark/doubao-seedance-2-0-260128"},
        )
        result = await resolver._resolve_video_backend(fake_svc, None, None)
        assert result == ("ark", "doubao-seedance-2-0-260128")


class TestVideoCapabilities:
    """验证 video_capabilities：第一步模型选择 + 第二步 model 能力查询。"""

    async def test_registry_grok(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={"default_video_backend": "grok/grok-imagine-video"},
        )
        factory, engine = await _make_session()
        try:
            async with factory() as session:
                with patch("lib.config.resolver.get_project_manager") as mock_pm:
                    mock_pm.return_value.load_project.return_value = {}
                    caps = await resolver._resolve_video_capabilities(fake_svc, session, "demo")
        finally:
            await engine.dispose()
        assert caps["provider_id"] == "grok"
        assert caps["model"] == "grok-imagine-video"
        assert caps["source"] == "registry"
        assert caps["supported_durations"] == list(range(1, 16))
        assert caps["max_duration"] == 15
        assert caps["max_reference_images"] == 7

    async def test_registry_veo(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        factory, engine = await _make_session()
        try:
            async with factory() as session:
                with patch("lib.config.resolver.get_project_manager") as mock_pm:
                    mock_pm.return_value.load_project.return_value = {
                        "video_backend": "gemini-aistudio/veo-3.1-generate-preview",
                    }
                    caps = await resolver._resolve_video_capabilities(fake_svc, session, "demo")
        finally:
            await engine.dispose()
        assert caps["provider_id"] == "gemini-aistudio"
        assert caps["model"] == "veo-3.1-generate-preview"
        assert caps["source"] == "registry"
        assert caps["supported_durations"] == [4, 6, 8]
        assert caps["max_duration"] == 8
        # normalize("gemini-aistudio") -> "gemini"，查 PROVIDER_MAX_REFS["gemini"]
        assert caps["max_reference_images"] == 3

    async def test_reads_project_default_duration_and_modes(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        factory, engine = await _make_session()
        try:
            async with factory() as session:
                with patch("lib.config.resolver.get_project_manager") as mock_pm:
                    mock_pm.return_value.load_project.return_value = {
                        "video_backend": "grok/grok-imagine-video",
                        "default_duration": 6,
                        "content_mode": "narration",
                        "generation_mode": "reference_video",
                    }
                    caps = await resolver._resolve_video_capabilities(fake_svc, session, "demo")
        finally:
            await engine.dispose()
        assert caps["default_duration"] == 6
        assert caps["content_mode"] == "narration"
        assert caps["generation_mode"] == "reference_video"

    async def test_missing_default_duration_is_null(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        factory, engine = await _make_session()
        try:
            async with factory() as session:
                with patch("lib.config.resolver.get_project_manager") as mock_pm:
                    mock_pm.return_value.load_project.return_value = {
                        "video_backend": "grok/grok-imagine-video",
                    }
                    caps = await resolver._resolve_video_capabilities(fake_svc, session, "demo")
        finally:
            await engine.dispose()
        assert caps["default_duration"] is None

    async def test_unknown_model_raises(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        factory, engine = await _make_session()
        try:
            async with factory() as session:
                with patch("lib.config.resolver.get_project_manager") as mock_pm:
                    mock_pm.return_value.load_project.return_value = {
                        "video_backend": "grok/nonexistent-model",
                    }
                    with pytest.raises(ValueError, match="model not found"):
                        await resolver._resolve_video_capabilities(fake_svc, session, "demo")
        finally:
            await engine.dispose()

    async def test_unknown_provider_raises(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        factory, engine = await _make_session()
        try:
            async with factory() as session:
                with patch("lib.config.resolver.get_project_manager") as mock_pm:
                    mock_pm.return_value.load_project.return_value = {
                        "video_backend": "bogus-provider/some-model",
                    }
                    with pytest.raises(ValueError, match="provider not in PROVIDER_REGISTRY"):
                        await resolver._resolve_video_capabilities(fake_svc, session, "demo")
        finally:
            await engine.dispose()

    async def test_video_capabilities_for_project_uses_passed_dict(self):
        """video_capabilities_for_project(dict) 不调用 load_project；直接消费传入 dict。

        防御 codex review 指出的"按目录名二次 load 可能读到同名错项目"风险。
        """
        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver(factory)
            with patch("lib.config.resolver.get_project_manager") as mock_pm:
                caps = await resolver.video_capabilities_for_project(
                    {
                        "video_backend": "grok/grok-imagine-video",
                        "default_duration": 9,
                    }
                )
                # 关键断言：load_project 一次都不能被调到
                mock_pm.return_value.load_project.assert_not_called()
        finally:
            await engine.dispose()
        assert caps["provider_id"] == "grok"
        assert caps["max_duration"] == 15
        assert caps["default_duration"] == 9
        assert caps["max_reference_images"] == 7

    async def test_max_reference_images_falls_back_to_default_for_unlisted_provider(self):
        """PROVIDER_MAX_REFS 未覆盖的 provider → resolver 返 DEFAULT_MAX_REFS，不返 None。

        gemini 建议：下游消费者（subagent / 前端）不用处理 None 特例。
        """
        from lib.reference_video.limits import DEFAULT_MAX_REFS

        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver(factory)
            with patch("lib.config.resolver.get_project_manager"):
                # ark 在 PROVIDER_MAX_REFS 里登记（=9），这里借道 normalize_provider_id 不会剥离的串验证
                # 使用一个 PROVIDER_MAX_REFS 明确未登记的 provider：不过所有注册 provider 都有入口，
                # 本测试改为 patch normalize_provider_id 让它返回未登记字符串以触发 fallback
                with patch(
                    "lib.config.resolver.normalize_provider_id",
                    return_value="___never_registered___",
                ):
                    caps = await resolver.video_capabilities_for_project({"video_backend": "grok/grok-imagine-video"})
        finally:
            await engine.dispose()
        assert caps["max_reference_images"] == DEFAULT_MAX_REFS

    async def test_custom_provider_reads_db_supported_durations(self):
        """custom-<id>/<model> 走 DB 分支，返回 source='custom'。"""
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        factory, engine = await _make_session()
        try:
            async with factory() as session:
                provider = CustomProvider(
                    display_name="Custom X",
                    api_format="openai",
                    base_url="https://example.com",
                    api_key="xxx",
                )
                session.add(provider)
                await session.flush()
                model = CustomProviderModel(
                    provider_id=provider.id,
                    model_id="my-video-model",
                    display_name="My Video",
                    media_type="video",
                    supported_durations="[5, 10]",
                )
                session.add(model)
                await session.flush()

                project_backend = f"custom-{provider.id}/my-video-model"
                with patch("lib.config.resolver.get_project_manager") as mock_pm:
                    mock_pm.return_value.load_project.return_value = {
                        "video_backend": project_backend,
                    }
                    caps = await resolver._resolve_video_capabilities(fake_svc, session, "demo")
        finally:
            await engine.dispose()
        assert caps["source"] == "custom"
        assert caps["supported_durations"] == [5, 10]
        assert caps["max_duration"] == 10
