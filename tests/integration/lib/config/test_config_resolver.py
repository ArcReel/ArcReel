from typing import cast
from unittest.mock import patch

import pytest

from lib.backends.text_backends.base import TextTaskType
from lib.catalog import query as catalog
from lib.catalog.errors import ModelNotInCatalogError
from lib.config.recommended_models import FROZEN_SETTING_KEY
from lib.config.resolver import (
    ConfigResolver,
    VideoBucketCapabilityError,
    VideoGenerationType,
    _LayeredBackendKeys,
    _parse_int,
    caps_generation_mode,
    video_bucket_for_generation_mode,
)
from lib.config.service import (
    DEFAULT_REFERENCE_SINGLE_MAX_BYTES,
    DEFAULT_REFERENCE_TOTAL_MAX_BYTES,
    ConfigService,
    ProviderStatus,
)
from lib.generation.video_request_facts import project_video_capabilities_with_facts
from lib.video_runtime.auto_backends import auto_resolved_backends
from lib.video_runtime.image_resolution import ImageRuntime
from lib.video_runtime.resolution import VideoRuntime
from lib.video_runtime.text import TextRuntime
from tests.catalog_support import install_catalog, with_entry_changes


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

    async def get_provider_config(self, provider: str) -> dict[str, str]:
        return {"api_key": f"key-{provider}"}

    async def get_all_provider_configs(self) -> dict[str, dict[str, str]]:
        return {"gemini-aistudio": {"api_key": "key-aistudio"}}


async def _video_caps(db_factory, project: dict, *, generation_type: VideoGenerationType | None = None) -> dict:
    """通过 VideoRuntime 读侧快照 + project_video_capabilities_with_facts 解析项目视频能力。"""
    bucket = generation_type or video_bucket_for_generation_mode(caps_generation_mode(project))
    read = await VideoRuntime(ConfigResolver(db_factory)).read_bucket(project, bucket)
    caps, _ = project_video_capabilities_with_facts(
        read.snapshot,
        project,
        generation_type=bucket,
        requested_audio=read.requested_audio,
    )
    return caps


class TestVideoGenerateAudio:
    """验证 video_generate_audio 的默认值、全局配置、项目级覆盖优先级。"""

    async def test_default_is_true_when_db_empty(self):
        """DB 无值时应返回 True（与 Seedance/Grok 默认开启一致）。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={})
        result = await resolver._resolve_video_generate_audio_from_project(fake_svc, project=None)
        assert result is True

    async def test_global_true(self):
        """DB 中值为 "true" 时返回 True。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "true"})
        result = await resolver._resolve_video_generate_audio_from_project(fake_svc, project=None)
        assert result is True

    async def test_global_false(self):
        """DB 中值为 "false" 时返回 False。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "false"})
        result = await resolver._resolve_video_generate_audio_from_project(fake_svc, project=None)
        assert result is False

    async def test_bool_parsing_variants(self):
        """验证各种布尔字符串的解析。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        for val, expected in [("TRUE", True), ("1", True), ("yes", True), ("0", False), ("no", False), ("", True)]:
            fake_svc = _FakeConfigService(settings={"video_generate_audio": val} if val else {})
            result = await resolver._resolve_video_generate_audio_from_project(fake_svc, project=None)
            assert result is expected, f"Failed for {val!r}: got {result}"

    async def test_project_override_true_over_global_false(self):
        """项目级覆盖 True 优先于全局 False。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "false"})
        result = await resolver._resolve_video_generate_audio_from_project(
            fake_svc, project={"video_generate_audio": True}
        )
        assert result is True

    async def test_project_override_false_over_global_true(self):
        """项目级覆盖 False 优先于全局 True。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "true"})
        result = await resolver._resolve_video_generate_audio_from_project(
            fake_svc, project={"video_generate_audio": False}
        )
        assert result is False

    async def test_project_override_string_value(self):
        """项目级覆盖值为字符串时也能正确解析。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"video_generate_audio": "true"})
        result = await resolver._resolve_video_generate_audio_from_project(
            fake_svc, project={"video_generate_audio": "false"}
        )
        assert result is False


class TestProviderConfig:
    """验证供应商配置方法委托给 ConfigService。"""

    async def test_provider_config(self, db_factory):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService()
        async with db_factory() as session:
            result = await resolver._resolve_provider_config(fake_svc, session, "gemini-aistudio")
        assert result == {"api_key": "key-gemini-aistudio"}

    async def test_all_provider_configs(self, db_factory):
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService()
        async with db_factory() as session:
            result = await resolver._resolve_all_provider_configs(fake_svc, session)
        assert "gemini-aistudio" in result


class TestSessionReuse:
    """验证 session() 上下文管理器的 session 复用行为。"""

    async def test_session_context_manager_reuses_single_session(self, db_factory):
        """resolver.session() 下多次调用只创建 1 个 session。"""
        call_count = 0
        real_call = db_factory.__call__

        def counting_factory():
            nonlocal call_count
            call_count += 1
            return real_call()

        resolver = ConfigResolver(db_factory)

        call_count = 0
        with patch.object(resolver, "_session_factory", side_effect=counting_factory):
            await resolver.video_generate_audio()
            await resolver.narration_defaults()
        assert call_count == 2, f"不使用 session() 应创建 2 个 session，实际 {call_count}"

        call_count = 0
        with patch.object(resolver, "_session_factory", side_effect=counting_factory):
            async with resolver.session() as r:
                await r.video_generate_audio()
                await r.narration_defaults()
                await r.provider_config("gemini-aistudio")
        assert call_count == 1, f"使用 session() 应只创建 1 个 session，实际 {call_count}"

    async def test_bound_resolver_shares_session_object(self, db_factory):
        """bound resolver 的 _open_session 返回同一个 session 对象。"""
        resolver = ConfigResolver(db_factory)
        sessions_seen = []

        async with resolver.session() as r:
            async with r._open_session() as (s1, _):
                sessions_seen.append(s1)
            async with r._open_session() as (s2, _):
                sessions_seen.append(s2)

        assert sessions_seen[0] is sessions_seen[1]

    async def test_unbound_resolver_creates_separate_sessions(self, db_factory):
        """未绑定的 resolver 每次 _open_session 创建不同 session。"""
        resolver = ConfigResolver(db_factory)
        sessions_seen = []

        async with resolver._open_session() as (s1, _):
            sessions_seen.append(s1)
        async with resolver._open_session() as (s2, _):
            sessions_seen.append(s2)

        assert sessions_seen[0] is not sessions_seen[1]


def test_generation_mode_helpers():
    assert video_bucket_for_generation_mode("storyboard") == "i2v"
    assert video_bucket_for_generation_mode("reference_video") == "r2v"
    assert video_bucket_for_generation_mode(None) == "i2v"
    assert video_bucket_for_generation_mode("bogus") == "i2v"
    assert video_bucket_for_generation_mode("grid") == "i2v"
    assert video_bucket_for_generation_mode(cast(str, ["reference_video"])) == "i2v"

    assert caps_generation_mode({"generation_mode": "reference_video"}) == "reference_video"
    assert caps_generation_mode({"generation_mode": "storyboard"}) == "storyboard"
    assert caps_generation_mode(None) is None
    assert caps_generation_mode({}) is None
    assert caps_generation_mode({"generation_mode": ""}) is None
    assert caps_generation_mode({"generation_mode": {"nested": "dict"}}) is None


@pytest.fixture
def active_aistudio_veo(monkeypatch):
    """AI Studio Veo 的计价与音轨口径仍由 gemini-video 端点实现；出厂目录已将其下线，这里在目录副本中设为可用。"""
    install_catalog(
        monkeypatch,
        with_entry_changes(
            catalog.factory_snapshot(),
            "gemini-aistudio",
            "veo-3.1-generate-preview",
            discontinued=False,
            shutdown_date=None,
        ),
    )


class TestVoiceConsistency:
    """voice_consistency 三维派生（模型能力 × generation_mode × 角色声音绑定方式）。全员经
    `db_factory` 落真实 in-memory DB，按 docs/standards/testing.md 的分档规则归 integration。"""

    async def test_seedance_2_reference_video_is_native(self, db_factory):
        """reference_audio_mode=direct、generation_mode=reference_video 且项目选了参考音频绑定 → native。"""
        caps = await _video_caps(
            db_factory,
            {
                "video_backend": "ark/doubao-seedance-2-0-260128",
                "generation_mode": "reference_video",
                "character_voice_binding": "reference_audio",
            },
        )
        assert caps["voice_consistency"] == "native"

    async def test_prompt_binding_downgrades_native_to_soft(self, db_factory):
        """项目选提示词软约束时，原生音频参考通道的模型同样降格 soft：声音只靠 voice_style 约束。"""
        caps = await _video_caps(
            db_factory,
            {
                "video_backend": "ark/doubao-seedance-2-0-260128",
                "generation_mode": "reference_video",
                "character_voice_binding": "prompt",
            },
        )
        assert caps["voice_consistency"] == "soft"

    async def test_missing_binding_defaults_to_prompt_and_downgrades(self, db_factory):
        """字段缺省即默认档（提示词软约束）：新项目不写该字段时不得解出 native。"""
        caps = await _video_caps(
            db_factory,
            {
                "video_backend": "ark/doubao-seedance-2-0-260128",
                "generation_mode": "reference_video",
            },
        )
        assert caps["voice_consistency"] == "soft"

    async def test_unreadable_binding_falls_back_to_prompt(self, db_factory):
        """project.json 被手编成非法值时按默认档解读，不得升格成参考音频直传。"""
        caps = await _video_caps(
            db_factory,
            {
                "video_backend": "ark/doubao-seedance-2-0-260128",
                "generation_mode": "reference_video",
                "character_voice_binding": ["reference_audio"],
            },
        )
        assert caps["voice_consistency"] == "soft"

    async def test_requested_generate_audio_is_exposed_separately_from_pricing_value(
        self, db_factory, active_aistudio_veo
    ):
        """caps 并列透出用户无声意图与计价口径：AI Studio Veo 恒按含音出账，但项目关掉音频时
        编排层必须读到 False（否则无声视频照旧上传参考音频）。"""
        caps = await _video_caps(
            db_factory,
            {
                "video_backend": "gemini-aistudio/veo-3.1-generate-preview",
                "generation_mode": "reference_video",
                "video_generate_audio": False,
            },
        )
        assert caps["requested_generate_audio"] is False
        assert caps["generate_audio"] is True

    async def test_requested_generate_audio_defaults_to_true(self, db_factory):
        caps = await _video_caps(db_factory, {"video_backend": "ark/doubao-seedance-2-0-260128"})
        assert caps["requested_generate_audio"] is True

    async def test_seedance_2_non_reference_mode_downgrades_to_soft(self, db_factory):
        """同一模型非参考生视频路径：native 蕴含有音轨，降格恒落 soft，不落 none。"""
        caps = await _video_caps(
            db_factory,
            {
                "video_backend": "ark/doubao-seedance-2-0-260128",
                "generation_mode": "storyboard",
            },
        )
        assert caps["voice_consistency"] == "soft"

    async def test_seedance_2_missing_generation_mode_downgrades_to_soft(self, db_factory):
        """generation_mode 缺省（非 reference_video）同样降格 soft。"""
        caps = await _video_caps(db_factory, {"video_backend": "ark/doubao-seedance-2-0-260128"})
        assert caps["voice_consistency"] == "soft"

    async def test_aistudio_veo_always_soft_regardless_of_generation_mode(self, db_factory, active_aistudio_veo):
        """AI Studio Veo 无参考音频通道，即便走参考生视频路径也只能 soft（恒有声不推 none）。"""
        caps = await _video_caps(
            db_factory,
            {
                "video_backend": "gemini-aistudio/veo-3.1-generate-preview",
                "generation_mode": "reference_video",
            },
        )
        assert caps["voice_consistency"] == "soft"

    async def test_grok_imagine_soft(self, db_factory):
        """Grok Imagine：音轨可开关、无参考音频通道 → soft。"""
        caps = await _video_caps(db_factory, {"video_backend": "grok/grok-imagine-video"})
        assert caps["voice_consistency"] == "soft"

    async def test_kling_v3_audio_models_are_soft(self, db_factory):
        """可灵 v3 系声明音频能力 → soft（注入 Voice_Profiles）。"""
        for model_id in ("kling-v3", "kling-v3-omni"):
            caps = await _video_caps(db_factory, {"video_backend": f"kling/{model_id}"})
            assert caps["voice_consistency"] == "soft"

    async def test_kling_turbo_true_silent_is_none(self, db_factory):
        """可灵 v2-5-turbo 无音频开关 → none。"""
        caps = await _video_caps(db_factory, {"video_backend": "kling/kling-v2-5-turbo"})
        assert caps["voice_consistency"] == "none"

    async def test_minimax_true_silent_is_none(self, db_factory):
        """MiniMax 真无声模型 → none。"""
        caps = await _video_caps(db_factory, {"video_backend": "minimax/MiniMax-Hailuo-2.3"})
        assert caps["voice_consistency"] == "none"

    async def test_discontinued_model_is_rejected_instead_of_read(self, db_factory):
        """项目选的是已下线的模型（Agnes v2.0）：能力读取同调用一样报下线，不再给出能力。"""
        with pytest.raises(ModelNotInCatalogError) as excinfo:
            await _video_caps(db_factory, {"video_backend": "agnes/agnes-video-v2.0"})
        assert excinfo.value.code == "model_discontinued"

    async def test_custom_provider_without_overrides_defaults_to_soft(self, db_factory):
        """自定义供应商无 generate_audio 目录声明：与 default_tier_generates_audio 同口径，
        无信号时假定有声，不凭空判定为真无声模型。"""
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        async with db_factory() as session:
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
            )
            session.add(model)
            await session.commit()
            project_backend = f"custom-{provider.id}/sora-like"

        caps = await _video_caps(db_factory, {"video_backend": project_backend, "generation_mode": "reference_video"})
        assert caps["voice_consistency"] == "soft"

    async def test_custom_provider_with_direct_override_and_reference_video_is_native(self, db_factory):
        """自定义供应商覆盖 reference_audio_mode=direct + 上限 > 0，参考生视频路径 + 参考音频绑定 → native。"""
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        async with db_factory() as session:
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
                endpoint="ark-seedance-2",
                capability_overrides={
                    "reference_audio_mode": "direct",
                    "max_reference_audio_count": 2,
                    "max_reference_images": 4,
                },
            )
            session.add(model)
            await session.commit()
            project_backend = f"custom-{provider.id}/seedance-like"

        caps = await _video_caps(
            db_factory,
            {
                "video_backend": project_backend,
                "generation_mode": "reference_video",
                "character_voice_binding": "reference_audio",
            },
        )
        assert caps["voice_consistency"] == "native"


class TestLayeredBackendSkeleton:
    """「默认 + 任务类型桶」四级解析骨架：项目桶 > 项目默认 > 全局桶 > 全局默认 > None。"""

    @staticmethod
    def _keys(**overrides):
        params = {
            "media_type": "image",
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
        result = await resolver._configured_backend_pair(fake_svc, project, self._keys())

        if p_bucket:
            assert result == ("p-bucket", "m")
        elif p_def:
            assert result == ("p-def", "m")
        elif g_bucket:
            assert result == ("g-bucket", "m")
        elif g_def:
            assert result == ("g-def", "m")
        else:
            assert result is None

    async def test_none_keys_skip_that_level(self):
        """键位为 None 的层直接跳过——项目默认层未声明时项目里的同名字段不生效。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"global_default": "g-def/m"})
        project = {"img_default": "p-def/m"}
        keys = self._keys(project_bucket_key=None, project_default_key=None)
        result = await resolver._configured_backend_pair(fake_svc, project, keys)
        assert result == ("g-def", "m")

    async def test_empty_global_bucket_follows_default(self):
        """全局桶键存在但无有效值 → 回退全局默认层（docs/adr/0054）。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"global_bucket": "", "global_default": "g-def/m"})
        result = await resolver._configured_backend_pair(fake_svc, None, self._keys())
        assert result == ("g-def", "m")

    async def test_project_bare_provider_is_rejected(self):
        """项目层的裸内置供应商报错，不按目录推荐补模型，也不回退全局默认（docs/adr/0099）。"""
        resolver = ConfigResolver.__new__(ConfigResolver)
        fake_svc = _FakeConfigService(settings={"global_default": "g-def/m"})
        with pytest.raises(ModelNotInCatalogError):
            await resolver._configured_backend_pair(fake_svc, {"img_bucket": "openai"}, self._keys())


def test_parse_int_variants():
    assert _parse_int("100", 7) == 100
    assert _parse_int("", 7) == 7
    assert _parse_int("abc", 7) == 7
    assert _parse_int("0", 7) == 7
    assert _parse_int("-5", 7) == 7
    assert _parse_int(None, 7) == 7
    assert _parse_int(50, 7) == 50
    assert _parse_int(True, 7) == 7


class TestReferencePayloadLimits:
    """验证 reference_payload_limits 的默认、per-provider 覆盖、容错与 None 短路。"""

    async def test_none_provider_returns_default_without_db(self):
        resolver = ConfigResolver.__new__(ConfigResolver)
        total, single = await resolver.reference_payload_limits(None)
        assert total == DEFAULT_REFERENCE_TOTAL_MAX_BYTES
        assert single == DEFAULT_REFERENCE_SINGLE_MAX_BYTES

    async def test_default_when_unset(self, db_factory):
        resolver = ConfigResolver(db_factory)
        total, single = await resolver.reference_payload_limits("gemini-aistudio")
        assert total == DEFAULT_REFERENCE_TOTAL_MAX_BYTES
        assert single == DEFAULT_REFERENCE_SINGLE_MAX_BYTES

    async def test_provider_override_applies(self, db_factory):
        async with db_factory() as session:
            svc = ConfigService(session)
            await svc.set_provider_config("gemini-aistudio", "reference_total_max_bytes", "1000000")
            await svc.set_provider_config("gemini-aistudio", "reference_single_max_bytes", "500000")
            await session.commit()
        resolver = ConfigResolver(db_factory)
        total, single = await resolver.reference_payload_limits("gemini-aistudio")
        assert (total, single) == (1000000, 500000)

    async def test_unknown_provider_falls_back_to_default(self, db_factory):
        resolver = ConfigResolver(db_factory)
        total, _single = await resolver.reference_payload_limits("totally-unknown-provider")
        assert total == DEFAULT_REFERENCE_TOTAL_MAX_BYTES

    async def test_non_numeric_override_falls_back(self, db_factory):
        async with db_factory() as session:
            svc = ConfigService(session)
            await svc.set_provider_config("gemini-aistudio", "reference_total_max_bytes", "not-a-number")
            await session.commit()
        resolver = ConfigResolver(db_factory)
        total, single = await resolver.reference_payload_limits("gemini-aistudio")
        assert total == DEFAULT_REFERENCE_TOTAL_MAX_BYTES
        assert single == DEFAULT_REFERENCE_SINGLE_MAX_BYTES


class TestPayloadPinnedVideoModel:
    """入队锁进 payload 任务类型桶键的执行身份：优先级最高，且不承诺桶的调用方（resume）也读得到。"""

    async def test_pinned_bucket_key_wins_over_project(self, db_factory):
        project = {"video_provider_i2v": "vidu/viduq3-pro", "video_backend": "grok/grok-imagine-video"}
        payload = {"video_provider_i2v": "ark/doubao-seedance-2-0-260128"}
        read = await VideoRuntime(ConfigResolver(db_factory)).read_bucket(project, "i2v", payload=payload)
        assert (read.snapshot.provider_id, read.snapshot.model_id) == ("ark", "doubao-seedance-2-0-260128")

    async def test_pinned_bucket_key_skips_capability_gate(self, db_factory):
        """锁定身份只过身份可用性、不过能力闸：已入队任务按 payload 照常执行，不回头补校验。"""
        payload = {"video_provider_r2v": "vidu/viduq3-pro"}
        read = await VideoRuntime(ConfigResolver(db_factory)).read_bucket(None, "r2v", payload=payload)
        assert (read.snapshot.provider_id, read.snapshot.model_id) == ("vidu", "viduq3-pro")

    async def test_pinned_bucket_key_hit_without_capability(self, db_factory):
        """resume 口径（generation_type=None）：入队只写一个桶键，按固定桶序取到即命中。"""
        project = {"video_backend": "grok/grok-imagine-video"}
        payload = {"video_provider_r2v": "ark/doubao-seedance-2-0-260128"}
        read = await VideoRuntime(ConfigResolver(db_factory)).read_bucket(project, None, payload=payload)
        assert (read.snapshot.provider_id, read.snapshot.model_id) == ("ark", "doubao-seedance-2-0-260128")

    async def test_pin_of_other_bucket_ignored_when_capability_given(self, db_factory):
        """generation_type 明确时只认该桶的键，另一个桶的锁不越桶生效。"""
        project = {"video_backend": "grok/grok-imagine-video"}
        payload = {"video_provider_r2v": "ark/doubao-seedance-2-0-260128"}
        read = await VideoRuntime(ConfigResolver(db_factory)).read_bucket(project, "i2v", payload=payload)
        assert (read.snapshot.provider_id, read.snapshot.model_id) == ("grok", "grok-imagine-video")

    async def test_pin_of_unavailable_provider_raises_instead_of_falling_back(self, db_factory):
        """锁定的供应商已下线：报错，不回退配置层。"""
        project = {"video_backend": "grok/grok-imagine-video"}
        payload = {"video_provider_i2v": "seedance/legacy"}
        with pytest.raises(ModelNotInCatalogError) as excinfo:
            await VideoRuntime(ConfigResolver(db_factory)).read_bucket(project, "i2v", payload=payload)
        assert excinfo.value.provider_id == "seedance"

    async def test_pin_of_deleted_builtin_model_raises(self, db_factory):
        """锁定的内置 model 被注册表升级删除：报错，不带着悬空身份继续执行。"""
        project = {"video_backend": "grok/grok-imagine-video"}
        payload = {"video_provider_i2v": "ark/retired-model"}
        with pytest.raises(ModelNotInCatalogError) as excinfo:
            await VideoRuntime(ConfigResolver(db_factory)).read_bucket(project, "i2v", payload=payload)
        assert excinfo.value.model_id == "retired-model"

    async def test_pin_of_non_video_builtin_model_raises(self, db_factory):
        """锁定的内置 model 不是视频模型：同样报错，不静默改用配置层的视频模型。"""
        project = {"video_backend": "grok/grok-imagine-video"}
        payload = {"video_provider_i2v": "ark/doubao-seedream-4-0-250828"}
        with pytest.raises(ModelNotInCatalogError):
            await VideoRuntime(ConfigResolver(db_factory)).read_bucket(project, "i2v", payload=payload)

    async def test_malformed_pin_falls_through_to_config(self, db_factory):
        """非复合形态（缺 model）的桶键按未锁定处理，回退配置层。"""
        project = {"video_backend": "grok/grok-imagine-video"}
        read = await VideoRuntime(ConfigResolver(db_factory)).read_bucket(
            project, None, payload={"video_provider_i2v": "ark"}
        )
        assert (read.snapshot.provider_id, read.snapshot.model_id) == ("grok", "grok-imagine-video")

    async def test_custom_provider_pin_survives_resume_resolution(self, db_factory):
        """自定义供应商的视频任务中断续跑：沿用 checkpoint 回放的 model，不回落项目配置换模型。"""
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        async with db_factory() as session:
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

        read = await VideoRuntime(ConfigResolver(db_factory)).read_bucket(
            {"video_backend": "grok/grok-imagine-video"},
            None,
            payload={"video_provider_i2v": f"{provider_id}/pinned-model"},
        )
        assert (read.snapshot.provider_id, read.snapshot.model_id) == (provider_id, "pinned-model")

    async def test_disabled_pinned_custom_model_raises_instead_of_switching(self, db_factory):
        """checkpoint 锁定的自定义 model 在 resume 前被禁用：报错，不收敛到默认 model。"""
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        async with db_factory() as session:
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

        with pytest.raises(VideoBucketCapabilityError) as excinfo:
            await VideoRuntime(ConfigResolver(db_factory)).read_bucket(
                {"video_backend": "grok/grok-imagine-video"},
                None,
                payload={"video_provider_i2v": f"{provider_id}/pinned-model"},
            )
        assert excinfo.value.model_id == "pinned-model"
        assert excinfo.value.generation_type == "i2v"


class TestAutoResolvedBackendsPreview:
    """全层留空时自动选中模型的只读预览，与统一运行时层解析同一口径。"""

    @staticmethod
    async def _execution_pairs(resolver: ConfigResolver) -> dict[str, tuple[str, str] | None]:
        """各用途在全部层留空时执行路径解析出的模型；选不出即 None。"""

        async def _or_none(coro):
            try:
                return await coro
            except ValueError:
                return None

        async def _image_pair(bucket):
            snap = await ImageRuntime(resolver).read_bucket(None, bucket)
            return (snap.provider_id, snap.model_id)

        async def _video_pair(bucket):
            read = await VideoRuntime(resolver).read_bucket(None, bucket)
            return (read.snapshot.provider_id, read.snapshot.model_id)

        async def _text_pair():
            target = await TextRuntime(resolver).for_task(TextTaskType.SCRIPT, None)
            return (target.snapshot.provider_id, target.snapshot.model_id)

        async def _audio_pair():
            from lib.video_runtime.audio import AudioRuntime

            identity = await AudioRuntime(resolver).read_identity(None)
            return identity.provider_id, identity.model_id

        return {
            "t2i": await _or_none(_image_pair("t2i")),
            "i2i": await _or_none(_image_pair("i2i")),
            "i2v": await _or_none(_video_pair("i2v")),
            "r2v": await _or_none(_video_pair("r2v")),
            "text": await _or_none(_text_pair()),
            "audio": await _or_none(_audio_pair()),
        }

    async def test_ready_builtin_providers_are_not_previewed(self, db_factory):
        """就绪的内置供应商不按目录推荐进入预览，与执行期一致（docs/adr/0099）。"""
        async with db_factory() as session:
            svc = ConfigService(session)
            await svc.set_setting(FROZEN_SETTING_KEY, "[]")
            await svc.set_provider_config("gemini-aistudio", "api_key", "k")
            await session.commit()
        resolver = ConfigResolver(db_factory)
        preview = await auto_resolved_backends(resolver)
        executed = await self._execution_pairs(resolver)
        assert preview == executed
        assert preview == {"t2i": None, "i2i": None, "i2v": None, "r2v": None, "text": None, "audio": None}

    async def test_custom_fallback_filters_by_bucket_and_reports_missing_media(self, db_factory):
        """无就绪内置供应商时按桶取自定义默认；该媒体一个候选都没有时为 None，不报错。"""
        from lib.custom_provider import make_provider_id
        from lib.db.models.custom_provider import CustomProvider, CustomProviderModel

        async with db_factory() as session:
            await ConfigService(session).set_setting(FROZEN_SETTING_KEY, "[]")
            provider = CustomProvider(
                display_name="Prov", discovery_format="openai", base_url="https://api.example.com", api_key="k"
            )
            session.add(provider)
            await session.flush()
            session.add_all(
                [
                    CustomProviderModel(
                        provider_id=provider.id,
                        model_id="t2i-m",
                        display_name="T2I",
                        endpoint="openai-images-generations",
                        is_default=True,
                        is_enabled=True,
                    ),
                    CustomProviderModel(
                        provider_id=provider.id,
                        model_id="i2i-m",
                        display_name="I2I",
                        endpoint="openai-images-edits",
                        is_default=True,
                        is_enabled=True,
                    ),
                ]
            )
            await session.commit()
            pid = make_provider_id(provider.id)

        resolver = ConfigResolver(db_factory)
        preview = await auto_resolved_backends(resolver)
        executed = await self._execution_pairs(resolver)
        assert preview == {
            "t2i": (pid, "t2i-m"),
            "i2i": (pid, "i2i-m"),
            "i2v": None,
            "r2v": None,
            "text": None,
            "audio": None,
        }
        assert preview == executed
