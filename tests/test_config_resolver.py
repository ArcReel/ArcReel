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

    async def get_all_settings(self) -> dict[str, str]:
        return dict(self._settings)

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

    async def test_default_image_backend_t2i_reads_dedicated_setting(self):
        """新 setting key default_image_backend_t2i 优先于旧 default_image_backend。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={
                "default_image_backend": "grok/grok-2-image",
                "default_image_backend_t2i": "ark/stable-diffusion-3",
            },
        )
        result = await resolver._resolve_default_image_backend(fake_svc, None, "t2i")
        assert result == ("ark", "stable-diffusion-3")

    async def test_default_image_backend_t2i_falls_back_to_legacy(self):
        """只设旧 default_image_backend，新 _t2i 未设时回退到旧值。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={"default_image_backend": "grok/grok-2-image"},
        )
        result = await resolver._resolve_default_image_backend(fake_svc, None, "t2i")
        assert result == ("grok", "grok-2-image")

    async def test_default_image_backend_i2i_reads_dedicated_setting(self):
        """对称测试 i2i：新 key default_image_backend_i2i 优先于旧 default_image_backend。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={
                "default_image_backend": "grok/grok-2-image",
                "default_image_backend_i2i": "ark/kolors-img2img",
            },
        )
        result = await resolver._resolve_default_image_backend(fake_svc, None, "i2i")
        assert result == ("ark", "kolors-img2img")

    async def test_default_image_backend_i2i_falls_back_to_legacy(self):
        """只设旧 default_image_backend，_i2i 未设时回退到旧值。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={"default_image_backend": "grok/grok-2-image"},
        )
        result = await resolver._resolve_default_image_backend(fake_svc, None, "i2i")
        assert result == ("grok", "grok-2-image")

    async def test_default_image_backend_t2i_explicit_empty_does_not_fall_back(self):
        """split key 显式置为空字符串时，不应回退到 legacy default_image_backend。

        语义锁：用户主动把 default_image_backend_t2i="" 表示「不设默认 / 自动选择」，
        必须走 _auto_resolve_backend；这里 ready_providers=[] 让 auto 路径抛错，
        以此区分"走了 auto 路径"（期望）和"被 legacy 静默回退"（被锁住的 bug）。
        """
        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver.__new__(ConfigResolver)
            fake_svc = _FakeConfigService(
                settings={
                    "default_image_backend": "grok/grok-2-image",
                    "default_image_backend_t2i": "",
                },
                ready_providers=[],
            )
            async with factory() as session:
                with pytest.raises(ValueError, match="未找到可用的 image 供应商"):
                    await resolver._resolve_default_image_backend(fake_svc, session, "t2i")
        finally:
            await engine.dispose()

    async def test_default_image_backend_i2i_explicit_empty_does_not_fall_back(self):
        """对称：default_image_backend_i2i="" 不应回退到 legacy。"""
        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver.__new__(ConfigResolver)
            fake_svc = _FakeConfigService(
                settings={
                    "default_image_backend": "grok/grok-2-image",
                    "default_image_backend_i2i": "",
                },
                ready_providers=[],
            )
            async with factory() as session:
                with pytest.raises(ValueError, match="未找到可用的 image 供应商"):
                    await resolver._resolve_default_image_backend(fake_svc, session, "i2i")
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
        # max_reference_images 来源：registry 中该 veo 视频模型的 ModelInfo.max_reference_images
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

    async def test_max_reference_images_reads_model_info_for_openai_sora(self):
        """openai sora 的 max_reference_images 来自 registry ModelInfo（=1），不再依赖 provider 级 fallback。"""
        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver(factory)
            with patch("lib.config.resolver.get_project_manager"):
                caps = await resolver.video_capabilities_for_project({"video_backend": "openai/sora-2"})
        finally:
            await engine.dispose()
        assert caps["max_reference_images"] == 1

    async def test_max_reference_images_reads_model_info_for_minimax_s2v(self):
        """minimax S2V-01 的 max_reference_images 来自 registry ModelInfo（=1）；

        编排层据此只取 1 张参考图，不会向只吃单脸的 S2V-01 拼多张。
        """
        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver(factory)
            with patch("lib.config.resolver.get_project_manager"):
                caps = await resolver.video_capabilities_for_project({"video_backend": "minimax/S2V-01"})
        finally:
            await engine.dispose()
        assert caps["max_reference_images"] == 1

    async def test_max_reference_images_reads_model_info_for_ark_seedance(self):
        """ark seedance 的 max_reference_images 来自 registry ModelInfo（=9）。"""
        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver(factory)
            with patch("lib.config.resolver.get_project_manager"):
                caps = await resolver.video_capabilities_for_project(
                    {"video_backend": "ark/doubao-seedance-2-0-260128"}
                )
        finally:
            await engine.dispose()
        assert caps["max_reference_images"] == 9

    async def test_max_reference_images_reads_model_info_for_kling_v3_omni(self):
        """kling-v3-omni（多图主体 R2V）的 max_reference_images 来自 registry ModelInfo（=4，保守值）；

        编排层据此裁剪参考图数量——内置 provider 经此值而非 backend caps 拿到上限。
        """
        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver(factory)
            with patch("lib.config.resolver.get_project_manager"):
                caps = await resolver.video_capabilities_for_project({"video_backend": "kling/kling-v3-omni"})
        finally:
            await engine.dispose()
        assert caps["max_reference_images"] == 4

    async def test_max_reference_images_reads_model_info_for_kling_video_o1(self):
        """kling-video-o1（多图主体 R2V）的 max_reference_images 来自 registry ModelInfo（=4，保守值）。"""
        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver(factory)
            with patch("lib.config.resolver.get_project_manager"):
                caps = await resolver.video_capabilities_for_project({"video_backend": "kling/kling-video-o1"})
        finally:
            await engine.dispose()
        assert caps["max_reference_images"] == 4

    async def test_kling_v3_non_reference_model_has_zero_max_refs(self):
        """kling-v3（声明 4K + 首尾帧但非多图主体）max_reference_images=0，不误报参考能力。"""
        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver(factory)
            with patch("lib.config.resolver.get_project_manager"):
                caps = await resolver.video_capabilities_for_project({"video_backend": "kling/kling-v3"})
        finally:
            await engine.dispose()
        assert caps["max_reference_images"] == 0

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
                    discovery_format="openai",
                    base_url="https://example.com",
                    api_key="xxx",
                )
                session.add(provider)
                await session.flush()
                model = CustomProviderModel(
                    provider_id=provider.id,
                    model_id="my-video-model",
                    display_name="My Video",
                    endpoint="newapi-video",
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
        # newapi-video endpoint 不接受参考图，max=0（来源：EndpointSpec.video_max_reference_images）
        assert caps["max_reference_images"] == 0

    async def test_custom_video_openai_endpoint_resolves_max_one(self):
        """custom-<id>/<model> 经 openai-video endpoint 解析出 max_reference_images=1（不再静默落 9）。"""
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        factory, engine = await _make_session()
        try:
            async with factory() as session:
                provider = CustomProvider(
                    display_name="Custom Sora",
                    discovery_format="openai",
                    base_url="https://example.com",
                    api_key="xxx",
                )
                session.add(provider)
                await session.flush()
                model = CustomProviderModel(
                    provider_id=provider.id,
                    model_id="sora-like",
                    display_name="Sora-like",
                    endpoint="openai-video",
                    supported_durations="[4, 8]",
                )
                session.add(model)
                await session.flush()

                project_backend = f"custom-{provider.id}/sora-like"
                with patch("lib.config.resolver.get_project_manager") as mock_pm:
                    mock_pm.return_value.load_project.return_value = {
                        "video_backend": project_backend,
                    }
                    caps = await resolver._resolve_video_capabilities(fake_svc, session, "demo")
        finally:
            await engine.dispose()
        assert caps["source"] == "custom"
        assert caps["max_reference_images"] == 1


class TestResolveImageBackend:
    """resolve_image_backend：payload > project > 全局默认，capability=t2i/i2i 各覆盖。"""

    async def test_payload_capability_slot_wins(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"image_provider_t2i": "ark/proj-t2i", "image_provider_i2i": "ark/proj-i2i"}
        payload = {"image_provider_t2i": "openai/pay-t2i", "image_provider_i2i": "openai/pay-i2i"}
        t2i = await resolver._resolve_image_provider_model(fake_svc, None, project, payload, "t2i")
        i2i = await resolver._resolve_image_provider_model(fake_svc, None, project, payload, "i2i")
        assert (t2i.provider_id, t2i.model_id) == ("openai", "pay-t2i")
        assert (i2i.provider_id, i2i.model_id) == ("openai", "pay-i2i")

    async def test_payload_legacy_fields_for_historical_tasks(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        payload = {"image_provider": "openai", "image_model": "legacy"}
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, {}, payload, "t2i")
        assert (resolved.provider_id, resolved.model_id) == ("openai", "legacy")

    async def test_project_capability_slot_when_no_payload(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"image_provider_t2i": "ark/proj-t2i", "image_provider_i2i": "ark/proj-i2i"}
        t2i = await resolver._resolve_image_provider_model(fake_svc, None, project, {}, "t2i")
        i2i = await resolver._resolve_image_provider_model(fake_svc, None, project, {}, "i2i")
        assert (t2i.provider_id, t2i.model_id) == ("ark", "proj-t2i")
        assert (i2i.provider_id, i2i.model_id) == ("ark", "proj-i2i")

    async def test_falls_through_to_global_default(self):
        """payload/project 都缺 → 落到全局默认（显式 default_image_backend_t2i）。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend_t2i": "grok/grok-2-image"})
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, None, None, "t2i")
        assert (resolved.provider_id, resolved.model_id) == ("grok", "grok-2-image")

    async def test_no_legacy_image_backend_fallback(self):
        """解析链不再认 legacy 单字段 image_backend（由迁移转规范字段），直接落全局默认。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend_t2i": "grok/grok-2-image"})
        project = {"image_backend": "openai/legacy"}
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, project, {}, "t2i")
        assert resolved.provider_id == "grok"

    async def test_project_bare_provider_pins_provider_with_default_model(self):
        """裸 provider 项目覆盖（写边界放行）→ pin 该 provider 并补全其默认 model，不静默回退全局默认。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend_t2i": "grok/grok-2-image"})
        project = {"image_provider_t2i": "openai"}  # 裸 provider，无 model
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, project, {}, "t2i")
        assert resolved.provider_id == "openai"
        assert resolved.model_id == "gpt-image-2"  # registry 中 openai 的默认 image model

    async def test_project_unknown_bare_provider_falls_through(self):
        """裸 provider 不在 registry（无默认 model 可补）→ 退回全局默认。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend_t2i": "grok/grok-2-image"})
        project = {"image_provider_t2i": "does-not-exist"}
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, project, {}, "t2i")
        assert resolved.provider_id == "grok"

    async def test_project_provider_with_trailing_slash_uses_provider_default(self):
        """脏值 "openai/"（缺 model，写校验器会放行）→ 取 openai 默认 model，不带空 model 下游。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend_t2i": "grok/grok-2-image"})
        project = {"image_provider_t2i": "openai/"}
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, project, {}, "t2i")
        assert (resolved.provider_id, resolved.model_id) == ("openai", "gpt-image-2")

    async def test_payload_legacy_provider_not_trusted_falls_through_to_project(self):
        """in-flight 历史任务 payload 携带 legacy 名（写边界拦不到）→ 不予信任，回退已迁移的 project。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"image_provider_t2i": "openai/gpt-image-2"}  # 启动期已迁移为规范名
        payload = {"image_provider": "vertex", "image_model": "legacy"}  # legacy，不可识别
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, project, payload, "t2i")
        assert (resolved.provider_id, resolved.model_id) == ("openai", "gpt-image-2")

    async def test_payload_known_provider_missing_model_uses_provider_default(self):
        """半截 payload（已知 provider 但缺 model）→ 补该 provider 默认 model，不带空 model 到执行层。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend_t2i": "grok/grok-2-image"})
        payload = {"image_provider": "openai"}  # 只有 provider，无 image_model
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, {}, payload, "t2i")
        assert (resolved.provider_id, resolved.model_id) == ("openai", "gpt-image-2")


class TestResolveVideoBackend:
    """resolve_video_backend：payload > project > 全局默认。"""

    async def test_payload_historical_provider_wins(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_backend": "grok/grok-imagine-video"}
        payload = {"video_provider": "ark", "video_provider_settings": {"model": "seedance"}}
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, project, payload)
        assert (resolved.provider_id, resolved.model_id) == ("ark", "seedance")

    async def test_project_video_backend_when_no_payload(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_backend": "ark/seedance-1-0-pro"}
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, project, {})
        assert (resolved.provider_id, resolved.model_id) == ("ark", "seedance-1-0-pro")

    async def test_falls_through_to_global_default(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_video_backend": "ark/doubao-seedance-1-5-pro"})
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, None, None)
        assert (resolved.provider_id, resolved.model_id) == ("ark", "doubao-seedance-1-5-pro")

    async def test_project_bare_provider_pins_provider_with_default_model(self):
        """裸 video_backend(如 "ark") → pin ark 并补全其默认 video model，不回退全局默认的另一供应商。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_video_backend": "grok/grok-imagine-video"})
        project = {"video_backend": "ark"}  # 裸 provider
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, project, {})
        assert resolved.provider_id == "ark"
        assert resolved.model_id == "doubao-seedance-1-5-pro-251215"  # registry 中 ark 的默认 video model

    async def test_payload_legacy_provider_not_trusted_falls_through_to_project(self):
        """in-flight 历史任务 payload 携带 legacy video_provider（如 seedance）→ 不予信任，回退已迁移的 project。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_backend": "ark/seedance-1-0-pro"}  # 启动期已迁移为规范名
        payload = {"video_provider": "seedance", "video_model": "legacy"}  # legacy，不可识别
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, project, payload)
        assert (resolved.provider_id, resolved.model_id) == ("ark", "seedance-1-0-pro")

    async def test_payload_non_dict_video_provider_settings_does_not_crash(self):
        """脏 payload：video_provider_settings 非 dict → 不抛异常，按缺 model 补该 provider 默认。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_video_backend": "grok/grok-imagine-video"})
        payload = {"video_provider": "ark", "video_provider_settings": "not-a-dict"}
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, {}, payload)
        assert resolved.provider_id == "ark"
        assert resolved.model_id == "doubao-seedance-1-5-pro-251215"  # 补 ark 默认 video model


def test_parse_int_variants():
    from lib.config.resolver import _parse_int

    assert _parse_int("100", 7) == 100
    assert _parse_int("", 7) == 7
    assert _parse_int("abc", 7) == 7
    assert _parse_int("0", 7) == 7  # 非正回 default
    assert _parse_int("-5", 7) == 7  # "-5".isdigit() == False
    assert _parse_int(None, 7) == 7
    assert _parse_int(50, 7) == 50
    assert _parse_int(True, 7) == 7  # bool 显式排除（避免 True→1）


class TestReferencePayloadLimits:
    """验证 reference_payload_limits 的默认、per-provider 覆盖、容错与 None 短路。"""

    async def test_none_provider_returns_default_without_db(self):
        # 无需 DB：provider_id=None 直接返回保守通用默认
        resolver = ConfigResolver.__new__(ConfigResolver)
        total, single = await resolver.reference_payload_limits(None)
        from lib.config.service import (
            _DEFAULT_REFERENCE_SINGLE_MAX_BYTES,
            _DEFAULT_REFERENCE_TOTAL_MAX_BYTES,
        )

        assert total == _DEFAULT_REFERENCE_TOTAL_MAX_BYTES
        assert single == _DEFAULT_REFERENCE_SINGLE_MAX_BYTES

    async def test_default_when_unset(self):
        from lib.config.service import (
            _DEFAULT_REFERENCE_SINGLE_MAX_BYTES,
            _DEFAULT_REFERENCE_TOTAL_MAX_BYTES,
        )

        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver(factory)
            total, single = await resolver.reference_payload_limits("gemini-aistudio")
            assert total == _DEFAULT_REFERENCE_TOTAL_MAX_BYTES
            assert single == _DEFAULT_REFERENCE_SINGLE_MAX_BYTES
        finally:
            await engine.dispose()

    async def test_provider_override_applies(self):
        from lib.config.service import ConfigService

        factory, engine = await _make_session()
        try:
            async with factory() as session:
                svc = ConfigService(session)
                await svc.set_provider_config("gemini-aistudio", "reference_total_max_bytes", "1000000")
                await svc.set_provider_config("gemini-aistudio", "reference_single_max_bytes", "500000")
                await session.commit()
            resolver = ConfigResolver(factory)
            total, single = await resolver.reference_payload_limits("gemini-aistudio")
            assert (total, single) == (1000000, 500000)
        finally:
            await engine.dispose()

    async def test_unknown_provider_falls_back_to_default(self):
        from lib.config.service import _DEFAULT_REFERENCE_TOTAL_MAX_BYTES

        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver(factory)
            # 未知 provider → get_provider_config 抛 ValueError → catch 回退默认
            total, single = await resolver.reference_payload_limits("totally-unknown-provider")
            assert total == _DEFAULT_REFERENCE_TOTAL_MAX_BYTES
        finally:
            await engine.dispose()

    async def test_non_numeric_override_falls_back(self):
        from lib.config.service import (
            _DEFAULT_REFERENCE_SINGLE_MAX_BYTES,
            _DEFAULT_REFERENCE_TOTAL_MAX_BYTES,
            ConfigService,
        )

        factory, engine = await _make_session()
        try:
            async with factory() as session:
                svc = ConfigService(session)
                await svc.set_provider_config("gemini-aistudio", "reference_total_max_bytes", "not-a-number")
                await session.commit()
            resolver = ConfigResolver(factory)
            total, single = await resolver.reference_payload_limits("gemini-aistudio")
            assert total == _DEFAULT_REFERENCE_TOTAL_MAX_BYTES  # 非数字回退
            assert single == _DEFAULT_REFERENCE_SINGLE_MAX_BYTES
        finally:
            await engine.dispose()
