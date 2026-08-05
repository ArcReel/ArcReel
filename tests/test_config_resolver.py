from typing import cast
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.config.resolver import (
    ConfigResolver,
    VideoBucketCapabilityError,
    caps_generation_mode,
    constrain_durations_for_project,
    resolve_raw_supported_durations,
    video_bucket_for_generation_mode,
)
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

    @pytest.mark.unit
    async def test_default_is_true_when_db_empty(self, tmp_path):
        """DB 无值时应返回 True（PR7 §11 决策：与 Seedance/Grok 默认开启一致）。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
        assert result is True

    @pytest.mark.unit
    async def test_global_true(self, tmp_path):
        """DB 中值为 "true" 时返回 True。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "true"})
        result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
        assert result is True

    @pytest.mark.unit
    async def test_global_false(self, tmp_path):
        """DB 中值为 "false" 时返回 False。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "false"})
        result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
        assert result is False

    @pytest.mark.unit
    async def test_bool_parsing_variants(self, tmp_path):
        """验证各种布尔字符串的解析。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        for val, expected in [("TRUE", True), ("1", True), ("yes", True), ("0", False), ("no", False), ("", True)]:
            fake_svc = _FakeConfigService(settings={"video_generate_audio": val} if val else {})
            result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
            assert result is expected, f"Failed for {val!r}: got {result}"

    @pytest.mark.unit
    async def test_project_override_true_over_global_false(self, tmp_path):
        """项目级覆盖 True 优先于全局 False。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "false"})
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = {"video_generate_audio": True}
            result = await resolver._resolve_video_generate_audio(fake_svc, project_name="demo")
        assert result is True

    @pytest.mark.unit
    async def test_project_override_false_over_global_true(self, tmp_path):
        """项目级覆盖 False 优先于全局 True。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "true"})
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = {"video_generate_audio": False}
            result = await resolver._resolve_video_generate_audio(fake_svc, project_name="demo")
        assert result is False

    @pytest.mark.unit
    async def test_project_none_skips_override(self, tmp_path):
        """project_name=None 时不读取项目配置。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "true"})
        result = await resolver._resolve_video_generate_audio(fake_svc, project_name=None)
        assert result is True

    @pytest.mark.unit
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

    @pytest.mark.unit
    async def test_video_backend_explicit(self):
        """DB 有显式值时直接返回。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={"default_video_backend": "ark/doubao-seedance-1-5-pro"},
        )
        result = await resolver._resolve_default_video_backend(fake_svc, None)
        assert result == ("ark", "doubao-seedance-1-5-pro")

    @pytest.mark.unit
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

    @pytest.mark.unit
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

    @pytest.mark.unit
    async def test_image_backend_explicit(self):
        """DB 有显式值时直接返回。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={"default_image_backend": "grok/grok-2-image"},
        )
        result = await resolver._resolve_default_image_backend(fake_svc, None)
        assert result == ("grok", "grok-2-image")

    @pytest.mark.unit
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

    @pytest.mark.unit
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

    @pytest.mark.unit
    async def test_default_image_backend_t2i_bucket_overrides_default_layer(self):
        """全局桶 default_image_backend_t2i 覆盖全局默认层 default_image_backend。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={
                "default_image_backend": "grok/grok-2-image",
                "default_image_backend_t2i": "ark/stable-diffusion-3",
            },
        )
        result = await resolver._resolve_default_image_backend(fake_svc, None, "t2i")
        assert result == ("ark", "stable-diffusion-3")

    @pytest.mark.unit
    async def test_default_image_backend_t2i_falls_back_to_default_layer(self):
        """只设默认层 default_image_backend、t2i 桶未配时回退到默认层。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={"default_image_backend": "grok/grok-2-image"},
        )
        result = await resolver._resolve_default_image_backend(fake_svc, None, "t2i")
        assert result == ("grok", "grok-2-image")

    @pytest.mark.unit
    async def test_default_image_backend_i2i_bucket_overrides_default_layer(self):
        """对称测试 i2i：全局桶覆盖全局默认层。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={
                "default_image_backend": "grok/grok-2-image",
                "default_image_backend_i2i": "ark/kolors-img2img",
            },
        )
        result = await resolver._resolve_default_image_backend(fake_svc, None, "i2i")
        assert result == ("ark", "kolors-img2img")

    @pytest.mark.unit
    async def test_default_image_backend_i2i_falls_back_to_default_layer(self):
        """只设默认层 default_image_backend、i2i 桶未配时回退到默认层。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={"default_image_backend": "grok/grok-2-image"},
        )
        result = await resolver._resolve_default_image_backend(fake_svc, None, "i2i")
        assert result == ("grok", "grok-2-image")

    @pytest.mark.unit
    @pytest.mark.parametrize("capability", ["t2i", "i2i"])
    async def test_default_image_backend_empty_bucket_falls_back_to_default_layer(self, capability: str):
        """桶键为空字符串时回退默认层（docs/adr/0054）。

        语义锁：桶是可选覆盖，空值不再表示「不设默认 / 自动选择」。ready_providers=[] 让
        自动推断路径抛错，以此区分「回退到默认层」（期望）与「跳到自动推断」。
        """
        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver.__new__(ConfigResolver)
            fake_svc = _FakeConfigService(
                settings={
                    "default_image_backend": "grok/grok-2-image",
                    f"default_image_backend_{capability}": "",
                },
                ready_providers=[],
            )
            async with factory() as session:
                result = await resolver._resolve_default_image_backend(fake_svc, session, capability)
            assert result == ("grok", "grok-2-image")
        finally:
            await engine.dispose()

    @pytest.mark.unit
    @pytest.mark.parametrize("capability", ["t2i", "i2i"])
    async def test_default_image_backend_only_default_layer_covers_all_buckets(self, capability: str):
        """只配 default_image_backend、两个桶都不配时，全部图片路径解析到该默认模型。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend": "grok/grok-2-image"})
        result = await resolver._resolve_default_image_backend(fake_svc, None, capability)
        assert result == ("grok", "grok-2-image")


class TestProviderConfig:
    """验证供应商配置方法委托给 ConfigService。"""

    @pytest.mark.unit
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

    @pytest.mark.unit
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

    @pytest.mark.unit
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

    @pytest.mark.unit
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

    @pytest.mark.unit
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

    @pytest.mark.unit
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

    @pytest.mark.unit
    async def test_project_empty_falls_back_to_system_setting(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={"default_video_backend": "grok/grok-imagine-video"},
        )
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = {}
            result = await resolver._resolve_video_backend(fake_svc, None, "demo")
        assert result == ("grok", "grok-imagine-video")

    @pytest.mark.unit
    async def test_no_project_name_uses_system_setting(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={"default_video_backend": "ark/doubao-seedance-2-0-260128"},
        )
        result = await resolver._resolve_video_backend(fake_svc, None, None)
        assert result == ("ark", "doubao-seedance-2-0-260128")


class TestVideoCapabilitiesBucketing:
    """读侧按 generation_mode 定桶：能力查询回答的是当前配置真正会执行的那个模型。"""

    async def _caps(self, project: dict) -> dict:
        factory, engine = await _make_session()
        try:
            with patch("lib.config.resolver.get_project_manager"):
                return await ConfigResolver(factory).video_capabilities_for_project(project)
        finally:
            await engine.dispose()

    @pytest.mark.unit
    def test_generation_mode_maps_to_bucket(self):
        assert video_bucket_for_generation_mode("storyboard") == "i2v"
        assert video_bucket_for_generation_mode("reference_video") == "r2v"
        # 缺省、未知值（含已退役的三值 grid）与非字符串脏数据一律落默认桶
        assert video_bucket_for_generation_mode(None) == "i2v"
        assert video_bucket_for_generation_mode("bogus") == "i2v"
        assert video_bucket_for_generation_mode("grid") == "i2v"
        assert video_bucket_for_generation_mode(cast(str, ["reference_video"])) == "i2v"

    @pytest.mark.integration
    async def test_i2v_bucket_shadows_project_default(self):
        """图生视频项目读 i2v 桶，遮蔽项目默认层。"""
        caps = await self._caps(
            {
                "video_backend": "grok/grok-imagine-video",
                "video_provider_i2v": "kling/kling-v3",
                "generation_mode": "storyboard",
            }
        )
        assert (caps["provider_id"], caps["model"]) == ("kling", "kling-v3")

    @pytest.mark.integration
    async def test_r2v_bucket_shadows_project_default(self):
        """参考生视频项目读 r2v 桶，遮蔽项目默认层。"""
        caps = await self._caps(
            {
                "video_backend": "grok/grok-imagine-video",
                "video_provider_r2v": "minimax/S2V-01",
                "generation_mode": "reference_video",
            }
        )
        assert (caps["provider_id"], caps["model"]) == ("minimax", "S2V-01")

    @pytest.mark.integration
    async def test_same_config_follows_generation_mode(self):
        """同一份配置下切 generation_mode，能力查询随桶换到另一个模型。"""
        project = {
            "video_provider_i2v": "kling/kling-v3",
            "video_provider_r2v": "minimax/S2V-01",
        }
        i2v_caps = await self._caps({**project, "generation_mode": "storyboard"})
        r2v_caps = await self._caps({**project, "generation_mode": "reference_video"})
        assert i2v_caps["model"] == "kling-v3"
        assert r2v_caps["model"] == "S2V-01"
        # 能力字典本身随之变：i2v 桶的型号不接受参考图
        assert i2v_caps["max_reference_images"] == 0
        assert r2v_caps["max_reference_images"] == 1

    @pytest.mark.integration
    async def test_reference_video_project_errors_when_model_lacks_reference_support(self):
        """参考生视频项目解析到无参考图能力的模型时报结构化错误，不静默换模型。"""
        with pytest.raises(VideoBucketCapabilityError) as excinfo:
            await self._caps({"video_backend": "kling/kling-v3", "generation_mode": "reference_video"})
        assert excinfo.value.code == "video_capability_missing_r2v"
        assert excinfo.value.params == {"provider": "kling", "model": "kling-v3"}

    @pytest.mark.integration
    async def test_storyboard_project_errors_when_model_lacks_first_frame(self):
        """图生视频项目解析到无首帧能力的模型时同样报错（桶换成 i2v）。"""
        with pytest.raises(VideoBucketCapabilityError) as excinfo:
            await self._caps({"video_backend": "minimax/S2V-01", "generation_mode": "storyboard"})
        assert excinfo.value.code == "video_capability_missing_i2v"

    @pytest.mark.integration
    async def test_duration_constraints_evaluate_on_bucket_model(self):
        """时长收窄按桶生效模型求值：参考生视频项目落 r2v 桶模型声明的「参考图↔时长」约束。"""
        project = {
            "video_provider_i2v": "kling/kling-v3",
            "video_provider_r2v": "gemini-aistudio/veo-3.1-generate-preview",
            "generation_mode": "reference_video",
        }
        caps = await self._caps(project)
        assert caps["model"] == "veo-3.1-generate-preview"
        assert caps["supported_durations"] == [4, 6, 8]
        constrained = constrain_durations_for_project(
            project,
            list(caps["supported_durations"]),
            provider_id=caps["provider_id"],
            model_id=caps["model"],
            generation_mode="reference_video",
        )
        assert constrained == [8]

    @pytest.mark.integration
    async def test_max_reference_images_follows_backend_declaration(self):
        """viduq3-pro 不在 /reference2video 白名单：能力查询报 0，不报 registry 的并行声明。"""
        caps = await self._caps({"video_backend": "vidu/viduq3-pro", "generation_mode": "storyboard"})
        assert caps["max_reference_images"] == 0


class TestVideoCapabilities:
    """验证 video_capabilities：第一步模型选择 + 第二步 model 能力查询。"""

    @pytest.mark.unit
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

    @pytest.mark.unit
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
        # max_reference_images 来源：backend 的 VideoCapabilities 声明（与执行层同源）
        assert caps["max_reference_images"] == 3

    @pytest.mark.unit
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

    @pytest.mark.unit
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

    @pytest.mark.unit
    async def test_unknown_model_raises(self):
        """悬空模型引用在能力桶解析闸即报错，携带可本地化的 code。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        factory, engine = await _make_session()
        try:
            async with factory() as session:
                with patch("lib.config.resolver.get_project_manager") as mock_pm:
                    mock_pm.return_value.load_project.return_value = {
                        "video_backend": "grok/nonexistent-model",
                    }
                    with pytest.raises(VideoBucketCapabilityError) as excinfo:
                        await resolver._resolve_video_capabilities(fake_svc, session, "demo")
        finally:
            await engine.dispose()
        assert excinfo.value.code == "video_capability_reference_unavailable"
        assert excinfo.value.capability == "i2v"

    @pytest.mark.unit
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
                    with pytest.raises(VideoBucketCapabilityError):
                        await resolver._resolve_video_capabilities(fake_svc, session, "demo")
        finally:
            await engine.dispose()

    @pytest.mark.unit
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

    @pytest.mark.unit
    async def test_max_reference_images_reads_backend_caps_for_openai_sora(self):
        """openai sora 的 max_reference_images 来自 backend 声明（=1），不依赖 provider 级 fallback。"""
        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver(factory)
            with patch("lib.config.resolver.get_project_manager"):
                caps = await resolver.video_capabilities_for_project({"video_backend": "openai/sora-2"})
        finally:
            await engine.dispose()
        assert caps["max_reference_images"] == 1

    @pytest.mark.unit
    async def test_max_reference_images_reads_backend_caps_for_minimax_s2v(self):
        """minimax S2V-01 的 max_reference_images 来自 backend 声明（=1）；

        编排层据此只取 1 张参考图，不会向只吃单脸的 S2V-01 拼多张。S2V-01 不支持首帧，
        项目须是参考生视频模式才落进它所属的 r2v 桶。
        """
        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver(factory)
            with patch("lib.config.resolver.get_project_manager"):
                caps = await resolver.video_capabilities_for_project(
                    {"video_backend": "minimax/S2V-01", "generation_mode": "reference_video"}
                )
        finally:
            await engine.dispose()
        assert caps["max_reference_images"] == 1

    @pytest.mark.unit
    async def test_max_reference_images_reads_backend_caps_for_ark_seedance(self):
        """ark seedance 的 max_reference_images 来自 backend 声明（=9）。"""
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

    @pytest.mark.unit
    async def test_max_reference_images_reads_backend_caps_for_kling_v3_omni(self):
        """kling-v3-omni（多图主体 R2V）的 max_reference_images 来自 backend 声明（=4，保守值）；

        编排层据此裁剪参考图数量，与执行期 gate_video_request 依据的是同一个数。
        """
        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver(factory)
            with patch("lib.config.resolver.get_project_manager"):
                caps = await resolver.video_capabilities_for_project({"video_backend": "kling/kling-v3-omni"})
        finally:
            await engine.dispose()
        assert caps["max_reference_images"] == 4

    @pytest.mark.unit
    async def test_max_reference_images_reads_backend_caps_for_kling_video_o1(self):
        """kling-video-o1（多图主体 R2V）的 max_reference_images 来自 backend 声明（=4，保守值）。"""
        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver(factory)
            with patch("lib.config.resolver.get_project_manager"):
                caps = await resolver.video_capabilities_for_project({"video_backend": "kling/kling-video-o1"})
        finally:
            await engine.dispose()
        assert caps["max_reference_images"] == 4

    @pytest.mark.unit
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

    @pytest.mark.unit
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

    @pytest.mark.unit
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

    @pytest.mark.integration
    async def test_custom_disabled_model_errors_like_execution_layer(self):
        """project 仍指向已禁用的 model 时，能力解析与执行路径同样在能力桶解析闸报悬空引用。

        不静默换成该供应商的默认启用 model（``docs/adr/0054``）：宣称一个用户没选过的模型的能力，
        与执行期直接报错的行为对不上。"""
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        factory, engine = await _make_session()
        try:
            async with factory() as session:
                provider = CustomProvider(
                    display_name="Custom Fallback",
                    discovery_format="openai",
                    base_url="https://example.com",
                    api_key="xxx",
                )
                session.add(provider)
                await session.flush()
                disabled = CustomProviderModel(
                    provider_id=provider.id,
                    model_id="disabled-model",
                    display_name="Disabled",
                    endpoint="ark-seedance",
                    is_enabled=False,
                    supported_durations="[5]",
                    capability_overrides={"last_frame": True},
                )
                default = CustomProviderModel(
                    provider_id=provider.id,
                    model_id="default-model",
                    display_name="Default",
                    endpoint="newapi-video",
                    is_enabled=True,
                    is_default=True,
                    supported_durations="[5, 10]",
                )
                session.add_all([disabled, default])
                await session.flush()

                project_backend = f"custom-{provider.id}/disabled-model"
                with patch("lib.config.resolver.get_project_manager") as mock_pm:
                    mock_pm.return_value.load_project.return_value = {
                        "video_backend": project_backend,
                    }
                    with pytest.raises(VideoBucketCapabilityError) as excinfo:
                        await resolver._resolve_video_capabilities(fake_svc, session, "demo")
        finally:
            await engine.dispose()
        assert excinfo.value.code == "video_capability_reference_unavailable"


@pytest.mark.integration
class TestVoiceConsistency:
    """voice_consistency 二维派生（模型能力 × generation_mode）。全员经 `_make_session()` 落真实
    in-memory DB，按 CONTRIBUTING.md 的 pytest markers 纪律归 integration。"""

    async def _caps(self, project: dict) -> dict:
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        factory, engine = await _make_session()
        try:
            async with factory() as session:
                with patch("lib.config.resolver.get_project_manager") as mock_pm:
                    mock_pm.return_value.load_project.return_value = project
                    return await resolver._resolve_video_capabilities(fake_svc, session, "demo")
        finally:
            await engine.dispose()

    async def test_seedance_2_reference_video_is_native(self):
        """reference_audio_mode=direct 且 generation_mode=reference_video → native。"""
        caps = await self._caps(
            {
                "video_backend": "ark/doubao-seedance-2-0-260128",
                "generation_mode": "reference_video",
            }
        )
        assert caps["voice_consistency"] == "native"

    async def test_requested_generate_audio_is_exposed_separately_from_pricing_value(self):
        """caps 并列透出用户无声意图与计价口径：AI Studio Veo 恒按含音出账，但项目关掉音频时
        编排层必须读到 False（否则无声视频照旧上传参考音频）。"""
        caps = await self._caps(
            {
                "video_backend": "gemini-aistudio/veo-3.1-generate-preview",
                "generation_mode": "reference_video",
                "video_generate_audio": False,
            }
        )
        assert caps["requested_generate_audio"] is False
        assert caps["generate_audio"] is True

    async def test_requested_generate_audio_defaults_to_true(self):
        caps = await self._caps({"video_backend": "ark/doubao-seedance-2-0-260128"})
        assert caps["requested_generate_audio"] is True

    async def test_seedance_2_non_reference_mode_downgrades_to_soft(self):
        """同一模型非参考生视频路径：native 蕴含有音轨，降格恒落 soft，不落 none。"""
        caps = await self._caps(
            {
                "video_backend": "ark/doubao-seedance-2-0-260128",
                "generation_mode": "storyboard",
            }
        )
        assert caps["voice_consistency"] == "soft"

    async def test_seedance_2_missing_generation_mode_downgrades_to_soft(self):
        """generation_mode 缺省（非 reference_video）同样降格 soft。"""
        caps = await self._caps({"video_backend": "ark/doubao-seedance-2-0-260128"})
        assert caps["voice_consistency"] == "soft"

    async def test_aistudio_veo_always_soft_regardless_of_generation_mode(self):
        """AI Studio Veo 无参考音频通道，即便走参考生视频路径也只能 soft（恒有声不推 none）。"""
        caps = await self._caps(
            {
                "video_backend": "gemini-aistudio/veo-3.1-generate-preview",
                "generation_mode": "reference_video",
            }
        )
        assert caps["voice_consistency"] == "soft"

    async def test_grok_imagine_soft(self):
        """Grok Imagine：恒有声、无参考音频通道 → soft。"""
        caps = await self._caps({"video_backend": "grok/grok-imagine-video"})
        assert caps["voice_consistency"] == "soft"

    async def test_sora_2_soft_after_token_correction(self):
        """Sora 2 目录补 generate_audio 后派生 soft（不再因缺 token 误判 none）。"""
        caps = await self._caps({"video_backend": "openai/sora-2"})
        assert caps["voice_consistency"] == "soft"

    async def test_kling_v3_audio_models_are_soft(self):
        """可灵 v3 系声明音频能力 → soft（注入 Voice_Profiles）。"""
        for model_id in ("kling-v3", "kling-v3-omni"):
            caps = await self._caps({"video_backend": f"kling/{model_id}"})
            assert caps["voice_consistency"] == "soft"

    async def test_kling_turbo_true_silent_is_none(self):
        """可灵 v2-5-turbo 无音频开关 → none。"""
        caps = await self._caps({"video_backend": "kling/kling-v2-5-turbo"})
        assert caps["voice_consistency"] == "none"

    async def test_minimax_true_silent_is_none(self):
        """MiniMax 真无声模型 → none。"""
        caps = await self._caps({"video_backend": "minimax/MiniMax-Hailuo-2.3"})
        assert caps["voice_consistency"] == "none"

    async def test_agnes_true_silent_is_none(self):
        """Agnes 真无声模型 → none。"""
        caps = await self._caps({"video_backend": "agnes/agnes-video-v2.0"})
        assert caps["voice_consistency"] == "none"

    async def test_custom_provider_without_overrides_defaults_to_soft(self):
        """自定义供应商无 generate_audio 目录声明：与 default_tier_generates_audio 同口径，
        无信号时假定有声，不凭空判定为真无声模型。"""
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        factory, engine = await _make_session()
        try:
            async with factory() as session:
                provider = CustomProvider(
                    display_name="Custom Voice",
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
                        "generation_mode": "reference_video",
                    }
                    caps = await resolver._resolve_video_capabilities(fake_svc, session, "demo")
        finally:
            await engine.dispose()
        # openai-video endpoint 不带 reference_audio_capable，覆盖无法宣称 direct，故非 native；
        # 无音轨目录声明时假定有声 → soft，不落 none。
        assert caps["voice_consistency"] == "soft"

    async def test_custom_provider_with_direct_override_and_reference_video_is_native(self):
        """自定义供应商覆盖 reference_audio_mode=direct + 上限 > 0，且走参考生视频路径 → native。"""
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        factory, engine = await _make_session()
        try:
            async with factory() as session:
                provider = CustomProvider(
                    display_name="Custom Seedance",
                    discovery_format="openai",
                    base_url="https://example.com",
                    api_key="xxx",
                )
                session.add(provider)
                await session.flush()
                model = CustomProviderModel(
                    provider_id=provider.id,
                    model_id="seedance-like",
                    display_name="Seedance-like",
                    endpoint="ark-seedance",
                    supported_durations="[4, 8]",
                    # max_reference_images 覆盖是让该 model 落进 r2v 桶的前提：参考生视频项目按
                    # r2v 定桶解析，ark-seedance 对未上表型号保守判 0，不覆盖会被解析闸挡在能力查询前
                    capability_overrides={
                        "reference_audio_mode": "direct",
                        "max_reference_audio_count": 2,
                        "max_reference_images": 4,
                    },
                )
                session.add(model)
                await session.flush()

                project_backend = f"custom-{provider.id}/seedance-like"
                with patch("lib.config.resolver.get_project_manager") as mock_pm:
                    mock_pm.return_value.load_project.return_value = {
                        "video_backend": project_backend,
                        "generation_mode": "reference_video",
                    }
                    caps = await resolver._resolve_video_capabilities(fake_svc, session, "demo")
        finally:
            await engine.dispose()
        assert caps["voice_consistency"] == "native"


class TestVideoPricingGenerateAudio:
    """video_pricing_generate_audio：能力接口解析不出时的计价降级口径。"""

    @pytest.mark.integration
    async def test_falls_back_to_provider_rule_for_registry_unknown_model(self):
        """注册表已下线的 veo model id 仍按含音档出价，不因能力解析失败被低估为静音档。"""
        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver(factory)
            # veo-3.1-generate-001 只留在 gemini-vertex 注册表：能力查询抛错，而价目查询
            # 仍会回落到 Gemini 家族费率出价。
            result = await resolver.video_pricing_generate_audio("gemini-aistudio", "veo-3.1-generate-001")
        finally:
            await engine.dispose()
        assert result is True

    @pytest.mark.integration
    async def test_falls_back_to_requested_value_for_unknown_provider(self):
        """非恒含音 provider 解析不出能力时保留请求值——价目仍回落 Gemini 家族的含音费率。"""
        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver(factory)
            result = await resolver.video_pricing_generate_audio("unknown", "unknown")
        finally:
            await engine.dispose()
        assert result is True

    @pytest.mark.integration
    async def test_keeps_requested_audio_for_vertex_unknown_model(self):
        """gemini-vertex 上注册表没有的 model：backend 照请求值下发并结算，估算不得降为静音档。"""
        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver(factory)
            # lite 只登记在 gemini-aistudio：把 backend 切到 vertex 却留着旧 model id 时，
            # 能力查询抛 model not found，而价目查询仍按 Gemini 家族含音费率出价。
            result = await resolver.video_pricing_generate_audio("gemini-vertex", "veo-3.1-lite-generate-preview")
        finally:
            await engine.dispose()
        assert result is True

    @pytest.mark.integration
    async def test_project_audio_off_survives_capability_failure(self):
        """项目关掉音频时降级路径同样按静音档出价，不凭空补成含音。"""
        factory, engine = await _make_session()
        try:
            resolver = ConfigResolver(factory)
            result = await resolver.video_pricing_generate_audio(
                "gemini-vertex",
                "veo-3.1-lite-generate-preview",
                {"video_generate_audio": False},
            )
        finally:
            await engine.dispose()
        assert result is False


class TestResolveImageBackend:
    """resolve_image_backend：payload > 项目桶 > 项目默认 > 全局桶 > 全局默认 > 自动推断。"""

    @pytest.mark.unit
    async def test_payload_capability_slot_is_not_a_payload_layer_key(self):
        """图片任务不钉住执行身份：``image_provider_<cap>`` 只是项目层键，payload 里同名键不参与解析。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"image_provider_t2i": "ark/proj-t2i"}
        payload = {"image_provider_t2i": "openai/pay-t2i"}
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, project, payload, "t2i")
        assert (resolved.provider_id, resolved.model_id) == ("ark", "proj-t2i")

    @pytest.mark.unit
    async def test_payload_legacy_fields_for_historical_tasks(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        payload = {"image_provider": "openai", "image_model": "legacy"}
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, {}, payload, "t2i")
        assert (resolved.provider_id, resolved.model_id) == ("openai", "legacy")

    @pytest.mark.unit
    async def test_project_capability_slot_when_no_payload(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"image_provider_t2i": "ark/proj-t2i", "image_provider_i2i": "ark/proj-i2i"}
        t2i = await resolver._resolve_image_provider_model(fake_svc, None, project, {}, "t2i")
        i2i = await resolver._resolve_image_provider_model(fake_svc, None, project, {}, "i2i")
        assert (t2i.provider_id, t2i.model_id) == ("ark", "proj-t2i")
        assert (i2i.provider_id, i2i.model_id) == ("ark", "proj-i2i")

    @pytest.mark.unit
    async def test_project_bucket_wins_over_project_default(self):
        """项目桶优先于项目默认（default_image_backend）。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"image_provider_t2i": "ark/proj-t2i", "default_image_backend": "openai/proj-default"}
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, project, {}, "t2i")
        assert (resolved.provider_id, resolved.model_id) == ("ark", "proj-t2i")

    @pytest.mark.unit
    async def test_project_default_wins_over_global_layers(self):
        """项目桶未配时落项目默认，遮蔽全局桶与全局默认。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={
                "default_image_backend_t2i": "grok/global-t2i",
                "default_image_backend": "grok/global-default",
            }
        )
        project = {"default_image_backend": "openai/proj-default"}
        for capability in ("t2i", "i2i"):
            resolved = await resolver._resolve_image_provider_model(fake_svc, None, project, {}, capability)
            assert (resolved.provider_id, resolved.model_id) == ("openai", "proj-default")

    @pytest.mark.unit
    async def test_empty_project_bucket_falls_through_to_project_default(self):
        """项目桶为空字符串 → 回退项目默认，不跳过默认层。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"image_provider_i2i": "", "default_image_backend": "openai/proj-default"}
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, project, {}, "i2i")
        assert (resolved.provider_id, resolved.model_id) == ("openai", "proj-default")

    @pytest.mark.unit
    async def test_only_global_default_covers_both_capabilities(self):
        """只配全局默认层、两桶皆空 → t2i / i2i 都解析到该模型。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend": "grok/grok-2-image"})
        for capability in ("t2i", "i2i"):
            resolved = await resolver._resolve_image_provider_model(fake_svc, None, None, None, capability)
            assert (resolved.provider_id, resolved.model_id) == ("grok", "grok-2-image")

    @pytest.mark.unit
    async def test_falls_through_to_global_default(self):
        """payload/project 都缺 → 落到全局桶（显式 default_image_backend_t2i）。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend_t2i": "grok/grok-2-image"})
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, None, None, "t2i")
        assert (resolved.provider_id, resolved.model_id) == ("grok", "grok-2-image")

    @pytest.mark.unit
    async def test_no_legacy_image_backend_fallback(self):
        """解析链不再认 legacy 单字段 image_backend（由迁移转规范字段），直接落全局默认。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend_t2i": "grok/grok-2-image"})
        project = {"image_backend": "openai/legacy"}
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, project, {}, "t2i")
        assert resolved.provider_id == "grok"

    @pytest.mark.unit
    async def test_project_bare_provider_pins_provider_with_default_model(self):
        """裸 provider 项目覆盖（写边界放行）→ pin 该 provider 并补全其默认 model，不静默回退全局默认。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend_t2i": "grok/grok-2-image"})
        project = {"image_provider_t2i": "openai"}  # 裸 provider，无 model
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, project, {}, "t2i")
        assert resolved.provider_id == "openai"
        assert resolved.model_id == "gpt-image-2"  # registry 中 openai 的默认 image model

    @pytest.mark.unit
    async def test_project_unknown_bare_provider_falls_through(self):
        """裸 provider 不在 registry（无默认 model 可补）→ 退回全局默认。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend_t2i": "grok/grok-2-image"})
        project = {"image_provider_t2i": "does-not-exist"}
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, project, {}, "t2i")
        assert resolved.provider_id == "grok"

    @pytest.mark.unit
    async def test_project_provider_with_trailing_slash_uses_provider_default(self):
        """脏值 "openai/"（缺 model，写校验器会放行）→ 取 openai 默认 model，不带空 model 下游。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend_t2i": "grok/grok-2-image"})
        project = {"image_provider_t2i": "openai/"}
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, project, {}, "t2i")
        assert (resolved.provider_id, resolved.model_id) == ("openai", "gpt-image-2")

    @pytest.mark.unit
    async def test_payload_legacy_provider_not_trusted_falls_through_to_project(self):
        """in-flight 历史任务 payload 携带 legacy 名（写边界拦不到）→ 不予信任，回退已迁移的 project。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"image_provider_t2i": "openai/gpt-image-2"}  # 启动期已迁移为规范名
        payload = {"image_provider": "vertex", "image_model": "legacy"}  # legacy，不可识别
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, project, payload, "t2i")
        assert (resolved.provider_id, resolved.model_id) == ("openai", "gpt-image-2")

    @pytest.mark.unit
    async def test_payload_known_provider_missing_model_uses_provider_default(self):
        """半截 payload（已知 provider 但缺 model）→ 补该 provider 默认 model，不带空 model 到执行层。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_image_backend_t2i": "grok/grok-2-image"})
        payload = {"image_provider": "openai"}  # 只有 provider，无 image_model
        resolved = await resolver._resolve_image_provider_model(fake_svc, None, {}, payload, "t2i")
        assert (resolved.provider_id, resolved.model_id) == ("openai", "gpt-image-2")


@pytest.mark.unit
class TestLayeredBackendSkeleton:
    """「默认 + 能力桶」四级解析骨架：项目桶 > 项目默认 > 全局桶 > 全局默认 > 自动推断。

    用带全部四层键位的合成声明直测骨架契约，与各媒体的具体键位无关；媒体桶接入只补
    键位声明、不改骨架本身。
    """

    @staticmethod
    def _keys(**overrides):
        from lib.config.resolver import _LayeredBackendKeys

        params = {
            "media_type": "image",
            "parse_fallback": "fallback/m",
            "project_bucket_key": "img_bucket",
            "project_default_key": "img_default",
            "global_bucket_key": "global_bucket",
            "global_default_key": "global_default",
        }
        params.update(overrides)
        return _LayeredBackendKeys(**params)

    @pytest.mark.parametrize("p_bucket", [False, True])
    @pytest.mark.parametrize("p_def", [False, True])
    @pytest.mark.parametrize("g_bucket", [False, True])
    @pytest.mark.parametrize("g_def", [False, True])
    async def test_four_level_priority_all_combinations(self, p_bucket, p_def, g_bucket, g_def):
        settings = {}
        if g_bucket:
            settings["global_bucket"] = "g-bucket/m"
        if g_def:
            settings["global_default"] = "g-def/m"
        project = {}
        if p_bucket:
            project["img_bucket"] = "p-bucket/m"
        if p_def:
            project["img_default"] = "p-def/m"

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings=settings)
        result = await resolver._resolve_layered_backend(fake_svc, None, project, self._keys())

        if p_bucket:
            assert result == ("p-bucket", "m")
        elif p_def:
            assert result == ("p-def", "m")
        elif g_bucket:
            assert result == ("g-bucket", "m")
        elif g_def:
            assert result == ("g-def", "m")
        else:
            assert result[0] == "gemini-aistudio"  # 全层缺失 → 自动推断到 ready provider

    async def test_none_keys_skip_that_level(self):
        """键位为 None 的层直接跳过——项目默认层未声明时项目里的同名字段不生效。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"global_default": "g-def/m"})
        project = {"img_default": "p-def/m"}
        keys = self._keys(project_bucket_key=None, project_default_key=None)
        result = await resolver._resolve_layered_backend(fake_svc, None, project, keys)
        assert result == ("g-def", "m")

    @pytest.mark.unit
    async def test_empty_global_bucket_follows_default(self):
        """全局桶键存在但无有效值 → 回退全局默认层（docs/adr/0054）。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"global_bucket": "", "global_default": "g-def/m"})
        result = await resolver._resolve_layered_backend(fake_svc, None, None, self._keys())
        assert result == ("g-def", "m")

    async def test_project_bare_provider_supported(self):
        """项目层兼容裸 provider 覆盖（补该 provider 默认 model），与既有图片/视频项目字段语义一致。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"global_default": "g-def/m"})
        result = await resolver._resolve_layered_backend(fake_svc, None, {"img_bucket": "openai"}, self._keys())
        assert result == ("openai", "gpt-image-2")


class TestResolveVideoBackend:
    """resolve_video_backend：payload 钉住键 > project > 全局默认。"""

    @pytest.mark.unit
    async def test_project_video_backend_when_no_payload(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_backend": "ark/doubao-seedance-2-0-260128"}
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, project, {})
        assert (resolved.provider_id, resolved.model_id) == ("ark", "doubao-seedance-2-0-260128")

    @pytest.mark.integration
    async def test_disabled_custom_model_resolves_to_runtime_default(self):
        """身份解析直接交付执行层会实际调用的 model，不把禁用身份留给后续构造层修正。"""
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        factory, engine = await _make_session()
        try:
            async with factory() as session:
                provider = CustomProvider(
                    display_name="Custom Fallback",
                    discovery_format="openai",
                    base_url="https://example.com",
                    api_key="xxx",
                )
                session.add(provider)
                await session.flush()
                session.add_all(
                    [
                        CustomProviderModel(
                            provider_id=provider.id,
                            model_id="disabled-model",
                            display_name="Disabled",
                            endpoint="openai-video",
                            is_enabled=False,
                        ),
                        CustomProviderModel(
                            provider_id=provider.id,
                            model_id="runtime-model",
                            display_name="Runtime",
                            endpoint="openai-video",
                            is_enabled=True,
                            is_default=True,
                        ),
                    ]
                )
                await session.commit()

                resolver = ConfigResolver(factory)
                resolved = await resolver.resolve_video_backend(
                    {"video_backend": f"custom-{provider.id}/disabled-model"}, None
                )
        finally:
            await engine.dispose()

        assert (resolved.provider_id, resolved.model_id) == (f"custom-{provider.id}", "runtime-model")

    @pytest.mark.unit
    async def test_falls_through_to_global_default(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_video_backend": "ark/doubao-seedance-1-5-pro"})
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, None, None)
        assert (resolved.provider_id, resolved.model_id) == ("ark", "doubao-seedance-1-5-pro")

    @pytest.mark.unit
    async def test_project_bare_provider_pins_provider_with_default_model(self):
        """裸 video_backend(如 "ark") → pin ark 并补全其默认 video model，不回退全局默认的另一供应商。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_video_backend": "grok/grok-imagine-video"})
        project = {"video_backend": "ark"}  # 裸 provider
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, project, {})
        assert resolved.provider_id == "ark"
        assert resolved.model_id == "doubao-seedance-2-0-mini-260615"  # registry 中 ark 的默认 video model

    @pytest.mark.unit
    async def test_payload_without_pinned_bucket_key_falls_through_to_project(self):
        """payload 层只认钉住的能力桶键：非桶键的 provider 字段不参与解析，一律回退配置层。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_backend": "ark/doubao-seedance-2-0-260128"}
        payload = {"video_provider": "grok", "video_provider_settings": {"model": "grok-imagine-video"}}
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, project, payload)
        assert (resolved.provider_id, resolved.model_id) == ("ark", "doubao-seedance-2-0-260128")


@pytest.mark.unit
class TestResolveVideoBackendBuckets:
    """capability 给定时的视频四级解析（项目桶 > 项目默认 > 全局桶 > 全局默认 > 自动推断）与能力闸。

    能力闸样本取 backend 声明的真实能力位：vidu/viduq3-pro 仅 i2v、dashscope/happyhorse-1.0-r2v
    仅 r2v、ark 全系两桶齐备（见 lib/capability_buckets.py 的判定口径）。
    """

    async def test_project_bucket_wins_over_project_default(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_provider_i2v": "vidu/viduq3-pro", "video_backend": "grok/grok-imagine-video"}
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, project, None, "i2v")
        assert (resolved.provider_id, resolved.model_id) == ("vidu", "viduq3-pro")

    async def test_project_default_wins_over_global_bucket(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_video_backend_i2v": "grok/grok-imagine-video"})
        project = {"video_backend": "ark/doubao-seedance-2-0-mini-260615"}
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, project, None, "i2v")
        assert (resolved.provider_id, resolved.model_id) == ("ark", "doubao-seedance-2-0-mini-260615")

    async def test_global_bucket_wins_over_global_default(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={
                "default_video_backend_r2v": "minimax/S2V-01",
                "default_video_backend": "grok/grok-imagine-video",
            }
        )
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, None, None, "r2v")
        assert (resolved.provider_id, resolved.model_id) == ("minimax", "S2V-01")

    async def test_empty_global_bucket_falls_back_to_global_default(self):
        """视频空桶语义（docs/adr/0054）：桶键存在但为空 → 回退全局默认层，不直达自动推断。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={
                "default_video_backend_r2v": "",
                "default_video_backend": "grok/grok-imagine-video",
            }
        )
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, None, None, "r2v")
        assert (resolved.provider_id, resolved.model_id) == ("grok", "grok-imagine-video")

    async def test_auto_resolve_when_nothing_configured(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, None, None, "i2v")
        assert resolved.provider_id == "gemini-aistudio"

    async def test_missing_i2v_capability_raises_structured_error(self):
        from lib.config.resolver import VideoBucketCapabilityError

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_video_backend": "dashscope/happyhorse-1.0-r2v"})
        with pytest.raises(VideoBucketCapabilityError) as exc_info:
            await resolver._resolve_video_provider_model(fake_svc, None, None, None, "i2v")
        exc = exc_info.value
        assert exc.code == "video_capability_missing_i2v"
        assert exc.capability == "i2v"
        assert exc.params == {"provider": "dashscope", "model": "happyhorse-1.0-r2v"}

    async def test_missing_r2v_capability_raises_structured_error(self):
        from lib.config.resolver import VideoBucketCapabilityError

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_video_backend": "minimax/MiniMax-Hailuo-2.3"})
        with pytest.raises(VideoBucketCapabilityError) as exc_info:
            await resolver._resolve_video_provider_model(fake_svc, None, None, None, "r2v")
        assert exc_info.value.code == "video_capability_missing_r2v"

    async def test_bucket_reference_to_unknown_model_raises_unavailable(self):
        """悬空引用（注册表已无该 model）由同一解析闸报错兜底，不静默换模型。"""
        from lib.config.resolver import VideoBucketCapabilityError

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_video_backend_i2v": "ark/removed-model"})
        with pytest.raises(VideoBucketCapabilityError) as exc_info:
            await resolver._resolve_video_provider_model(fake_svc, None, None, None, "i2v")
        assert exc_info.value.code == "video_capability_reference_unavailable"
        assert exc_info.value.params == {"provider": "ark", "model": "removed-model"}

    async def test_bucket_reference_to_non_video_model_raises_unavailable(self):
        """所引模型不是视频模型同属悬空引用：backend caps 函数对同 provider 的图片模型也返回
        静态能力（``gemini-aistudio/gemini-3.1-flash-image-preview`` 会拿到 first_frame=True），
        只判成员资格会放行，落到执行层才炸。"""
        from lib.config.resolver import VideoBucketCapabilityError

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={"default_video_backend_i2v": "gemini-aistudio/gemini-3.1-flash-image-preview"}
        )
        with pytest.raises(VideoBucketCapabilityError) as exc_info:
            await resolver._resolve_video_provider_model(fake_svc, None, None, None, "i2v")
        assert exc_info.value.code == "video_capability_reference_unavailable"

    async def test_capability_none_keeps_legacy_resolution_without_gate(self):
        """不定桶调用（费用估算、限流路由兜底）保持旧三级解析，不读桶键、不过能力闸。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={
                "default_video_backend_i2v": "ark/doubao-seedance-2-0-mini-260615",
                "default_video_backend": "dashscope/happyhorse-1.0-r2v",
            }
        )
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, None, None, None)
        assert (resolved.provider_id, resolved.model_id) == ("dashscope", "happyhorse-1.0-r2v")


@pytest.mark.integration
class TestVideoBucketCapabilityGateCustomProvider:
    """自定义供应商的能力闸：悬空引用报错而非收敛到默认模型；有效模型正常放行。"""

    async def _seed_provider(self, factory, *, models):
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        async with factory() as session:
            provider = CustomProvider(
                display_name="Custom Video",
                discovery_format="openai",
                base_url="https://example.com",
                api_key="xxx",
            )
            session.add(provider)
            await session.flush()
            for m in models:
                session.add(CustomProviderModel(provider_id=provider.id, **m))
            await session.commit()
            return provider.id

    async def test_bucket_reference_to_missing_custom_model_raises(self):
        from lib.config.resolver import VideoBucketCapabilityError

        factory, engine = await _make_session()
        try:
            provider_id = await self._seed_provider(factory, models=[])
            resolver = ConfigResolver(factory)
            with pytest.raises(VideoBucketCapabilityError) as exc_info:
                await resolver.resolve_video_backend(
                    {"video_provider_r2v": f"custom-{provider_id}/ghost-model"}, None, capability="r2v"
                )
        finally:
            await engine.dispose()
        assert exc_info.value.code == "video_capability_reference_unavailable"

    async def test_disabled_custom_model_raises_instead_of_converging(self):
        """桶解析路径不做有效身份收敛：禁用模型直接报悬空，而非静默换成该供应商默认模型。"""
        from lib.config.resolver import VideoBucketCapabilityError

        factory, engine = await _make_session()
        try:
            provider_id = await self._seed_provider(
                factory,
                models=[
                    {
                        "model_id": "disabled-model",
                        "display_name": "Disabled",
                        "endpoint": "openai-video",
                        "is_enabled": False,
                    },
                    {
                        "model_id": "runtime-model",
                        "display_name": "Runtime",
                        "endpoint": "openai-video",
                        "is_enabled": True,
                        "is_default": True,
                    },
                ],
            )
            resolver = ConfigResolver(factory)
            with pytest.raises(VideoBucketCapabilityError) as exc_info:
                await resolver.resolve_video_backend(
                    {"video_backend": f"custom-{provider_id}/disabled-model"}, None, capability="i2v"
                )
        finally:
            await engine.dispose()
        assert exc_info.value.code == "video_capability_reference_unavailable"

    async def test_enabled_custom_video_model_passes_gate(self):
        factory, engine = await _make_session()
        try:
            provider_id = await self._seed_provider(
                factory,
                models=[
                    {
                        "model_id": "live-model",
                        "display_name": "Live",
                        "endpoint": "openai-video",
                        "is_enabled": True,
                        "is_default": True,
                    }
                ],
            )
            resolver = ConfigResolver(factory)
            resolved = await resolver.resolve_video_backend(
                {"video_provider_i2v": f"custom-{provider_id}/live-model"}, None, capability="i2v"
            )
        finally:
            await engine.dispose()
        assert (resolved.provider_id, resolved.model_id) == (f"custom-{provider_id}", "live-model")


@pytest.mark.unit
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

    @pytest.mark.unit
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

    @pytest.mark.unit
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

    @pytest.mark.unit
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

    @pytest.mark.unit
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

    @pytest.mark.unit
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


class TestTextBackendTierResolution:
    """文本档位五级解析链：项目档位 > 项目默认 > 全局档位 > 全局默认 > 自动推断。"""

    _AUTO = ("gemini-aistudio", "gemini-3-flash-preview")  # ready gemini 的 registry 默认 text model

    @pytest.mark.unit
    @pytest.mark.parametrize("p_tier", [False, True])
    @pytest.mark.parametrize("p_def", [False, True])
    @pytest.mark.parametrize("g_tier", [False, True])
    @pytest.mark.parametrize("g_def", [False, True])
    async def test_five_level_priority_all_combinations(self, p_tier, p_def, g_tier, g_def):
        from unittest.mock import MagicMock

        from lib.text_backends.base import TextTaskType

        settings = {}
        if g_tier:
            settings["text_backend_complex"] = "g-tier/m"
        if g_def:
            settings["default_text_backend"] = "g-def/m"
        project = {}
        if p_tier:
            project["text_backend_complex"] = "p-tier/m"
        if p_def:
            project["default_text_backend"] = "p-def/m"

        if p_tier:
            expected = ("p-tier", "m")
        elif p_def:
            expected = ("p-def", "m")
        elif g_tier:
            expected = ("g-tier", "m")
        elif g_def:
            expected = ("g-def", "m")
        else:
            expected = self._AUTO

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings=settings)
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = project
            result = await resolver._resolve_text_backend(fake_svc, MagicMock(), TextTaskType.SCRIPT, "demo")
        assert result == expected

    @pytest.mark.unit
    async def test_no_project_name_skips_project_levels(self):
        from unittest.mock import MagicMock

        from lib.text_backends.base import TextTaskType

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"text_backend_complex": "g-tier/m", "default_text_backend": "g-def/m"})
        result = await resolver._resolve_text_backend(fake_svc, MagicMock(), TextTaskType.SCRIPT, None)
        assert result == ("g-tier", "m")

    @pytest.mark.unit
    async def test_simple_tier_tasks_read_simple_key(self):
        """OVERVIEW / STYLE_ANALYSIS 归简单档，读 text_backend_simple 而非复杂档键。"""
        from unittest.mock import MagicMock

        from lib.text_backends.base import TextTaskType

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"text_backend_simple": "simple/m", "text_backend_complex": "complex/m"})
        for task in (TextTaskType.OVERVIEW, TextTaskType.STYLE_ANALYSIS):
            result = await resolver._resolve_text_backend(fake_svc, MagicMock(), task, None)
            assert result == ("simple", "m")

    @pytest.mark.unit
    async def test_script_task_reads_complex_key(self):
        from unittest.mock import MagicMock

        from lib.text_backends.base import TextTaskType

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"text_backend_simple": "simple/m", "text_backend_complex": "complex/m"})
        result = await resolver._resolve_text_backend(fake_svc, MagicMock(), TextTaskType.SCRIPT, None)
        assert result == ("complex", "m")

    @pytest.mark.unit
    async def test_malformed_value_without_slash_falls_through(self):
        """无 "/" 的脏值视为未设置，落到下一级。"""
        from unittest.mock import MagicMock

        from lib.text_backends.base import TextTaskType

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"text_backend_complex": "no-slash", "default_text_backend": "g-def/m"})
        result = await resolver._resolve_text_backend(fake_svc, MagicMock(), TextTaskType.SCRIPT, None)
        assert result == ("g-def", "m")

    @pytest.mark.unit
    async def test_project_bare_provider_pins_its_default_model(self):
        """项目档位写裸 provider（写边界放行的合法值）→ pin 住该 provider 补默认 model，
        不静默回退到全局默认的另一供应商。与图片 / 视频的项目层同构。"""
        from unittest.mock import MagicMock

        from lib.text_backends.base import TextTaskType

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"default_text_backend": "g-def/m"})
        with patch("lib.config.resolver.get_project_manager") as mock_pm:
            mock_pm.return_value.load_project.return_value = {"text_backend_complex": "openai"}
            result = await resolver._resolve_text_backend(fake_svc, MagicMock(), TextTaskType.SCRIPT, "demo")
        assert result == ("openai", "gpt-5.4-mini")


class TestStyleAnalysisVisionGuard:
    """简单档模型不支持图像输入时，风格分析解析直接报错，不静默换模型。"""

    @pytest.mark.unit
    async def test_rejects_registry_model_without_vision(self):
        from unittest.mock import MagicMock

        from lib.text_backends.base import TextTaskType

        resolver = ConfigResolver.__new__(ConfigResolver)
        # gemini-3.1-flash-lite-preview 在 registry 中未声明 vision
        fake_svc = _FakeConfigService(settings={"text_backend_simple": "gemini-aistudio/gemini-3.1-flash-lite-preview"})
        with pytest.raises(ValueError, match="vision"):
            await resolver._resolve_text_backend(fake_svc, MagicMock(), TextTaskType.STYLE_ANALYSIS, None)

    @pytest.mark.unit
    async def test_accepts_registry_model_with_vision(self):
        from unittest.mock import MagicMock

        from lib.text_backends.base import TextTaskType

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"text_backend_simple": "gemini-aistudio/gemini-3-flash-preview"})
        result = await resolver._resolve_text_backend(fake_svc, MagicMock(), TextTaskType.STYLE_ANALYSIS, None)
        assert result == ("gemini-aistudio", "gemini-3-flash-preview")

    @pytest.mark.unit
    async def test_unknown_model_passes_without_guess(self):
        """registry 之外（自定义供应商等）无逐模型能力事实，放行不猜测。"""
        from unittest.mock import MagicMock

        from lib.text_backends.base import TextTaskType

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"text_backend_simple": "custom-abc/some-model"})
        result = await resolver._resolve_text_backend(fake_svc, MagicMock(), TextTaskType.STYLE_ANALYSIS, None)
        assert result == ("custom-abc", "some-model")

    @pytest.mark.unit
    async def test_complex_tier_task_not_vision_checked(self):
        """vision 校验只针对需要图像输入的任务，SCRIPT 不受限。"""
        from unittest.mock import MagicMock

        from lib.text_backends.base import TextTaskType

        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(
            settings={"text_backend_complex": "gemini-aistudio/gemini-3.1-flash-lite-preview"}
        )
        result = await resolver._resolve_text_backend(fake_svc, MagicMock(), TextTaskType.SCRIPT, None)
        assert result == ("gemini-aistudio", "gemini-3.1-flash-lite-preview")


class TestProjectGenerationModeCaps:
    """能力解析按项目生成路线定轴：路线创建即定、全项目一条，能力不需要剧集上下文。"""

    async def _caps(self, project: dict) -> dict:
        factory, engine = await _make_session()
        try:
            with patch("lib.config.resolver.get_project_manager"):
                return await ConfigResolver(factory).video_capabilities_for_project(project)
        finally:
            await engine.dispose()

    @pytest.mark.unit
    def test_caps_generation_mode_reads_project_field(self):
        assert caps_generation_mode({"generation_mode": "reference_video"}) == "reference_video"
        assert caps_generation_mode({"generation_mode": "storyboard"}) == "storyboard"

    @pytest.mark.unit
    def test_caps_generation_mode_none_without_project_context(self):
        """无项目上下文时为 None（未声明 ≠ 显式选了某条路线）。"""
        assert caps_generation_mode(None) is None
        assert caps_generation_mode({}) is None
        assert caps_generation_mode({"generation_mode": ""}) is None
        assert caps_generation_mode({"generation_mode": {"nested": "dict"}}) is None

    @pytest.mark.integration
    async def test_bucket_follows_project_route(self):
        """定桶按项目路线取对应桶键的模型。"""
        storyboard_project = {
            "generation_mode": "storyboard",
            "video_provider_i2v": "kling/kling-v3",
            "video_provider_r2v": "minimax/S2V-01",
        }
        reference_project = {**storyboard_project, "generation_mode": "reference_video"}
        assert (await self._caps(storyboard_project))["model"] == "kling-v3"
        assert (await self._caps(reference_project))["model"] == "S2V-01"

    @pytest.mark.integration
    async def test_voice_consistency_follows_project_route(self):
        """参考路线按 native 解析，分镜路线降格 soft。"""
        project = {
            "generation_mode": "reference_video",
            "video_provider_r2v": "ark/doubao-seedance-2-0-260128",
            "video_backend": "ark/doubao-seedance-2-0-260128",
        }
        caps = await self._caps(project)
        assert caps["voice_consistency"] == "native"
        assert caps["generation_mode"] == "reference_video"
        assert (await self._caps({**project, "generation_mode": "storyboard"}))["voice_consistency"] == "soft"

    @pytest.mark.integration
    async def test_uses_reference_images_constraint_follows_project_route(self):
        """caps 的 generation_mode 是下游时长约束的入参，参考路线据此施加「参考图↔时长」约束。"""
        caps = await self._caps({"generation_mode": "reference_video", "video_provider_r2v": "minimax/S2V-01"})
        assert caps["generation_mode"] == "reference_video"
        assert caps["max_reference_images"] == 1


class TestResolveRawSupportedDurations:
    """收窄前的时长全集：caps → registry 两级解析。"""

    _VEO_PROJECT = {"video_backend": "gemini-aistudio/veo-3.1-generate-preview"}

    @pytest.mark.unit
    def test_caps_take_precedence_over_registry(self):
        """caps 是 DB 驱动的当下真相，压过 project.json 自报身份查到的静态声明。"""
        caps = {"supported_durations": [5, 10]}
        assert resolve_raw_supported_durations(dict(self._VEO_PROJECT), caps) == [5, 10]

    @pytest.mark.unit
    def test_falls_back_to_registry_identity_without_caps(self):
        assert resolve_raw_supported_durations(dict(self._VEO_PROJECT)) == [4, 6, 8]

    @pytest.mark.unit
    def test_custom_provider_resolves_only_through_caps(self):
        """``custom-`` 前缀不在 registry：不带 caps 时无从解析，带 caps 时取 caps 的档位表。

        这条是审阅门必须先解析 caps 的原因——同步两级链对自定义供应商恒为 None。
        """
        project = {"video_backend": "custom-7/acme-video"}
        assert resolve_raw_supported_durations(project) is None
        assert resolve_raw_supported_durations(project, {"supported_durations": [5, 10]}) == [5, 10]

    @pytest.mark.unit
    def test_project_json_duration_field_is_not_a_source(self):
        """project.json 不是档位来源：无生产写入者的字段不得再被当作一级回退读取，
        否则伪造 / 陈旧的项目字段会盖过 registry 的真实声明。"""
        project = dict(self._VEO_PROJECT) | {"_supported_durations": [99]}
        assert resolve_raw_supported_durations(project) == [4, 6, 8]

    @pytest.mark.unit
    def test_none_when_no_resolvable_model(self):
        assert resolve_raw_supported_durations({}) is None


class TestPayloadPinnedVideoModel:
    """入队钉进 payload 能力桶键的执行身份：优先级最高，且不承诺桶的调用方（resume）也读得到。"""

    @pytest.mark.unit
    async def test_pinned_bucket_key_wins_over_project(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_provider_i2v": "vidu/viduq3-pro", "video_backend": "grok/grok-imagine-video"}
        payload = {"video_provider_i2v": "ark/doubao-seedance-2-0-260128"}
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, project, payload, "i2v")
        assert (resolved.provider_id, resolved.model_id) == ("ark", "doubao-seedance-2-0-260128")

    @pytest.mark.unit
    async def test_pinned_bucket_key_skips_capability_gate(self):
        """钉住身份只过身份可用性、不过能力闸：已入队任务按 payload 照常执行，不回头补校验。

        钉的是 i2v-only 的 viduq3-pro 而按 r2v 解析——过能力闸就会报错。
        """
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        payload = {"video_provider_r2v": "vidu/viduq3-pro"}
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, None, payload, "r2v")
        assert (resolved.provider_id, resolved.model_id) == ("vidu", "viduq3-pro")

    @pytest.mark.unit
    async def test_pinned_bucket_key_hit_without_capability(self):
        """resume 口径（capability=None）：入队只写一个桶键，按固定桶序取到即命中。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_backend": "grok/grok-imagine-video"}
        payload = {"video_provider_r2v": "ark/doubao-seedance-2-0-260128"}
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, project, payload)
        assert (resolved.provider_id, resolved.model_id) == ("ark", "doubao-seedance-2-0-260128")

    @pytest.mark.unit
    async def test_pin_of_other_bucket_ignored_when_capability_given(self):
        """capability 明确时只认该桶的键，另一个桶的钉不越桶生效。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_backend": "grok/grok-imagine-video"}
        payload = {"video_provider_r2v": "ark/doubao-seedance-2-0-260128"}
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, project, payload, "i2v")
        assert (resolved.provider_id, resolved.model_id) == ("grok", "grok-imagine-video")

    @pytest.mark.unit
    async def test_pin_of_unavailable_provider_raises_instead_of_falling_back(self):
        """钉住的供应商已下线：报错，不回退配置层。

        回退等于换供应商执行，续跑更会拿另一个 backend 去轮原供应商的 provider_job_id。
        """
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_backend": "grok/grok-imagine-video"}
        payload = {"video_provider_i2v": "seedance/legacy"}
        with pytest.raises(VideoBucketCapabilityError) as excinfo:
            await resolver._resolve_video_provider_model(fake_svc, None, project, payload, "i2v")
        assert excinfo.value.provider_id == "seedance"

    @pytest.mark.unit
    async def test_pin_of_deleted_builtin_model_raises(self):
        """钉住的内置 model 被注册表升级删除：报错，不带着悬空身份继续执行。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_backend": "grok/grok-imagine-video"}
        payload = {"video_provider_i2v": "ark/retired-model"}
        with pytest.raises(VideoBucketCapabilityError) as excinfo:
            await resolver._resolve_video_provider_model(fake_svc, None, project, payload, "i2v")
        assert excinfo.value.model_id == "retired-model"

    @pytest.mark.unit
    async def test_pin_of_non_video_builtin_model_raises(self):
        """钉住的内置 model 不是视频模型：同样报错，不静默改用配置层的视频模型。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_backend": "grok/grok-imagine-video"}
        payload = {"video_provider_i2v": "ark/doubao-seedream-4-0-250828"}
        with pytest.raises(VideoBucketCapabilityError):
            await resolver._resolve_video_provider_model(fake_svc, None, project, payload, "i2v")

    @pytest.mark.unit
    async def test_malformed_pin_falls_through_to_config(self):
        """非复合形态（缺 model）的桶键按未钉住处理，回退配置层。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        project = {"video_backend": "grok/grok-imagine-video"}
        resolved = await resolver._resolve_video_provider_model(fake_svc, None, project, {"video_provider_i2v": "ark"})
        assert (resolved.provider_id, resolved.model_id) == ("grok", "grok-imagine-video")

    @pytest.mark.integration
    async def test_custom_provider_pin_survives_resume_resolution(self):
        """自定义供应商的视频任务中断续跑：沿用入队钉住的 model，不回落项目配置换模型。"""
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        factory, engine = await _make_session()
        try:
            async with factory() as session:
                provider = CustomProvider(
                    display_name="Custom Pinned",
                    discovery_format="openai",
                    base_url="https://example.com",
                    api_key="xxx",
                )
                session.add(provider)
                await session.flush()
                session.add(
                    CustomProviderModel(
                        provider_id=provider.id,
                        model_id="pinned-model",
                        display_name="Pinned",
                        endpoint="openai-video",
                        is_enabled=True,
                        is_default=True,
                    )
                )
                await session.commit()
                provider_id = f"custom-{provider.id}"

                resolver = ConfigResolver(factory)
                resolved = await resolver.resolve_video_backend(
                    {"video_backend": "grok/grok-imagine-video"},
                    {"video_provider_i2v": f"{provider_id}/pinned-model"},
                )
        finally:
            await engine.dispose()

        assert (resolved.provider_id, resolved.model_id) == (provider_id, "pinned-model")

    @pytest.mark.integration
    async def test_disabled_pinned_custom_model_raises_instead_of_switching(self):
        """钉住的自定义 model 入队后被禁用：报错，不收敛到该供应商的默认 model。

        换 model 执行等于静默换模型，续跑更会拿另一个 backend 去轮原 model 的 provider_job_id。
        """
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        factory, engine = await _make_session()
        try:
            async with factory() as session:
                provider = CustomProvider(
                    display_name="Custom Pinned",
                    discovery_format="openai",
                    base_url="https://example.com",
                    api_key="xxx",
                )
                session.add(provider)
                await session.flush()
                session.add_all(
                    [
                        CustomProviderModel(
                            provider_id=provider.id,
                            model_id="pinned-model",
                            display_name="Pinned",
                            endpoint="openai-video",
                            is_enabled=False,
                            is_default=False,
                        ),
                        CustomProviderModel(
                            provider_id=provider.id,
                            model_id="other-model",
                            display_name="Other",
                            endpoint="openai-video",
                            is_enabled=True,
                            is_default=True,
                        ),
                    ]
                )
                await session.commit()
                provider_id = f"custom-{provider.id}"

                resolver = ConfigResolver(factory)
                with pytest.raises(VideoBucketCapabilityError) as excinfo:
                    await resolver.resolve_video_backend(
                        {"video_backend": "grok/grok-imagine-video"},
                        {"video_provider_i2v": f"{provider_id}/pinned-model"},
                    )
        finally:
            await engine.dispose()

        assert excinfo.value.model_id == "pinned-model"
        assert excinfo.value.capability == "i2v"
