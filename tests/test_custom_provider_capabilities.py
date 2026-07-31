"""自定义供应商视频能力合成函数（系统判定 ⊕ 用户覆盖）。"""

from __future__ import annotations

import logging

import pytest

from lib.custom_provider.capabilities import (
    CAPABILITY_OVERRIDE_FIELDS,
    capability_value_matches,
    filter_valid_overrides,
    synthesize_video_capabilities,
    system_video_capabilities,
)
from lib.video_backends.base import ReferenceAudioMode, VideoCapabilities


class TestOverrideFieldSchema:
    @pytest.mark.unit
    def test_schema_covers_every_video_capabilities_field(self):
        """覆盖 schema 通用支持 VideoCapabilities 全部字段，键名严格对齐 dataclass。"""
        assert set(CAPABILITY_OVERRIDE_FIELDS) == {
            "first_frame",
            "last_frame",
            "max_reference_images",
            "reference_audio_mode",
            "max_reference_audio_count",
        }
        assert CAPABILITY_OVERRIDE_FIELDS["last_frame"] is bool
        assert CAPABILITY_OVERRIDE_FIELDS["max_reference_images"] is int
        assert CAPABILITY_OVERRIDE_FIELDS["reference_audio_mode"] is ReferenceAudioMode
        assert CAPABILITY_OVERRIDE_FIELDS["max_reference_audio_count"] is int


class TestSystemCapabilities:
    @pytest.mark.unit
    def test_endpoint_declaring_int_cap_rebuilds_capabilities(self):
        """endpoint 维度声明硬上限时原样落到 max_reference_images，其余维度取默认。"""
        caps = system_video_capabilities(endpoint="openai-video", model_id="sora-2")
        assert caps == VideoCapabilities(first_frame=True, last_frame=False, max_reference_images=1)

    @pytest.mark.unit
    def test_endpoint_declaring_zero_cap_yields_no_reference_images(self):
        caps = system_video_capabilities(endpoint="newapi-video", model_id="whatever")
        assert caps.max_reference_images == 0

    @pytest.mark.unit
    def test_endpoint_with_caps_fn_delegates_to_backend_declaration(self):
        """endpoint 未声明硬上限时走 backend 的 per-model 纯函数，四字段全量取其声明。"""
        from lib.video_backends.vidu import ViduVideoBackend

        caps = system_video_capabilities(endpoint="vidu-video", model_id="viduq3")
        assert caps == ViduVideoBackend.video_capabilities_for_model("viduq3")

    @pytest.mark.unit
    def test_non_video_endpoint_raises(self):
        with pytest.raises(ValueError, match="not video"):
            system_video_capabilities(endpoint="openai-chat", model_id="gpt-4o")

    @pytest.mark.unit
    def test_unknown_endpoint_raises(self):
        with pytest.raises(ValueError):
            system_video_capabilities(endpoint="no-such-endpoint", model_id="x")


class TestSynthesize:
    @pytest.mark.unit
    @pytest.mark.parametrize("overrides", [None, {}])
    def test_absent_overrides_follow_system(self, overrides: dict | None):
        """列 NULL 与空字典都等价于「全部跟随系统判定」。"""
        assert synthesize_video_capabilities(
            endpoint="openai-video", model_id="sora-2", overrides=overrides
        ) == system_video_capabilities(endpoint="openai-video", model_id="sora-2")

    @pytest.mark.unit
    def test_missing_key_follows_system(self):
        """字典存在但缺某键 → 该维度跟随系统判定，其余键照常覆盖。"""
        caps = synthesize_video_capabilities(
            endpoint="ark-seedance", model_id="doubao-seedance-1-0-pro-fast-251015", overrides={"first_frame": False}
        )
        assert caps.first_frame is False
        assert caps.last_frame is False
        assert caps.max_reference_images == 0

    @pytest.mark.unit
    def test_force_on(self):
        caps = synthesize_video_capabilities(
            endpoint="ark-seedance",
            model_id="doubao-seedance-1-0-pro-fast-251015",
            overrides={"last_frame": True, "max_reference_images": 4},
        )
        assert caps.last_frame is True
        assert caps.max_reference_images == 4

    @pytest.mark.unit
    def test_force_off(self):
        caps = synthesize_video_capabilities(
            endpoint="openai-video", model_id="sora-2", overrides={"first_frame": False}
        )
        assert caps.first_frame is False
        # 未覆盖的数值维度不受布尔覆盖牵连
        assert caps.max_reference_images == 1

    @pytest.mark.unit
    def test_force_off_on_last_frame(self):
        """last_frame 是首批开放的覆盖维度，单独验一遍「系统判定 True → 强制关」。

        用 viduq3-turbo 而非 viduq3：后者不在 /start-end2video、/img2video 白名单内
        （只支持 /reference2video），系统判定本身就是 False，验不出"强制关"的效果。
        """
        assert system_video_capabilities(endpoint="vidu-video", model_id="viduq3-turbo").last_frame is True
        caps = synthesize_video_capabilities(
            endpoint="vidu-video", model_id="viduq3-turbo", overrides={"last_frame": False}
        )
        assert caps.last_frame is False
        assert caps.first_frame is True

    @pytest.mark.unit
    def test_int_field_override(self):
        caps = synthesize_video_capabilities(
            endpoint="openai-video", model_id="sora-2", overrides={"max_reference_images": 3}
        )
        assert caps.max_reference_images == 3

    @pytest.mark.unit
    def test_system_capabilities_not_mutated_across_calls(self):
        """合成返回新实例，不得就地改写系统判定（caps_fn 可能返回共享对象）。"""
        first = synthesize_video_capabilities(
            endpoint="ark-seedance", model_id="doubao-seedance-1-0-pro-fast-251015", overrides={"last_frame": True}
        )
        second = system_video_capabilities(endpoint="ark-seedance", model_id="doubao-seedance-1-0-pro-fast-251015")
        assert first.last_frame is True
        assert second.last_frame is False


class TestTolerance:
    """存量数据容错：合成函数是执行层的最后一道，坏数据降级为「跟随系统判定」而非抛错。

    合法性把关属于写入侧；存量行、手工 SQL、以及字段集收窄都可能让这里读到不认识的键，
    抛错会让整条生成链路因一条脏配置而不可用。
    """

    @pytest.mark.unit
    def test_unknown_key_ignored_with_warning(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            caps = synthesize_video_capabilities(
                endpoint="ark-seedance",
                model_id="doubao-seedance-1-0-pro-fast-251015",
                overrides={"last_frame": True, "audio_track": True},
            )
        assert caps.last_frame is True
        assert caps == VideoCapabilities(first_frame=True, last_frame=True, max_reference_images=0)
        assert "audio_track" in caplog.text

    @pytest.mark.unit
    def test_last_frame_override_ignored_when_endpoint_lacks_end_image_support(self, caplog: pytest.LogCaptureFixture):
        """openai-video 的 delegate 不下传 end_image：覆盖只会让合成层宣称支持、执行层仍静默丢帧，须降级跟随系统判定。"""
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            caps = synthesize_video_capabilities(
                endpoint="openai-video", model_id="sora-2", overrides={"last_frame": True}
            )
        assert caps.last_frame is False
        assert "last_frame" in caplog.text

    @pytest.mark.unit
    @pytest.mark.parametrize("bad", ["true", 1, None, [], {"x": 1}])
    def test_non_bool_value_on_bool_field_ignored(self, bad: object, caplog: pytest.LogCaptureFixture):
        """布尔维度只接受真 bool；1/"true" 等宽松真值不做语义映射，一律降级跟随。"""
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            caps = synthesize_video_capabilities(
                endpoint="openai-video", model_id="sora-2", overrides={"last_frame": bad}
            )
        assert caps.last_frame is False
        assert "last_frame" in caplog.text

    @pytest.mark.unit
    @pytest.mark.parametrize("bad", [True, -1, "2", 1.5, None])
    def test_bad_value_on_int_field_ignored(self, bad: object, caplog: pytest.LogCaptureFixture):
        """数值维度只接受非负 int；bool 是 int 子类，须被显式排除。"""
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            caps = synthesize_video_capabilities(
                endpoint="openai-video", model_id="sora-2", overrides={"max_reference_images": bad}
            )
        assert caps.max_reference_images == 1
        assert "max_reference_images" in caplog.text

    @pytest.mark.unit
    @pytest.mark.parametrize("bad", ["last_frame", ["last_frame"], 42])
    def test_non_dict_overrides_ignored(self, bad: object, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            caps = synthesize_video_capabilities(endpoint="openai-video", model_id="sora-2", overrides=bad)
        assert caps == system_video_capabilities(endpoint="openai-video", model_id="sora-2")
        assert caplog.records

    @pytest.mark.unit
    def test_last_frame_override_on_retired_endpoint_does_not_raise(self, caplog: pytest.LogCaptureFixture):
        """endpoint 已从注册表下线（升级移除）时，get_endpoint_spec 抛 ValueError；filter_valid_overrides
        是 API 响应过滤边界的最后一道，不得让存量脏配置把这里也炸穿——须降级丢弃并告警，而非抛错。"""
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            applied = filter_valid_overrides(
                endpoint="no-such-retired-endpoint", model_id="whatever", overrides={"last_frame": True}
            )
        assert applied == {}
        assert "last_frame" in caplog.text


class TestReferenceAudioOverrideGuards:
    """音频维度的三守卫：EndpointSpec 运输声明、合并后不变式、枚举值两侧同判定。"""

    @pytest.mark.unit
    def test_enum_value_matches_accepts_declared_literal(self):
        assert capability_value_matches("direct", ReferenceAudioMode)
        assert capability_value_matches("none", ReferenceAudioMode)

    @pytest.mark.unit
    @pytest.mark.parametrize("value", ["DIRECT", "Direct", "native", "", True, 1, None])
    def test_enum_value_matches_rejects_everything_else(self, value):
        """词表外的值一律判否——不做大小写归一，也不猜近义词。"""
        assert not capability_value_matches(value, ReferenceAudioMode)

    @pytest.mark.unit
    def test_direct_override_applies_on_audio_capable_endpoint(self):
        caps = synthesize_video_capabilities(
            endpoint="ark-seedance",
            model_id="doubao-seedance-1-0-pro-fast-251015",
            overrides={"reference_audio_mode": "direct", "max_reference_audio_count": 2},
        )
        assert caps.reference_audio_mode == ReferenceAudioMode.DIRECT
        assert caps.max_reference_audio_count == 2

    @pytest.mark.unit
    def test_direct_override_ignored_when_endpoint_cannot_send_audio(self, caplog: pytest.LogCaptureFixture):
        """endpoint 的 delegate 不下传参考音频时，覆盖只会让声明失真，须降级跟随系统判定。"""
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            caps = synthesize_video_capabilities(
                endpoint="openai-video", model_id="sora-2", overrides={"reference_audio_mode": "direct"}
            )

        assert caps.reference_audio_mode is ReferenceAudioMode.NONE
        assert "reference_audio_mode=direct" in caplog.text

    @pytest.mark.unit
    def test_none_override_needs_no_endpoint_support(self):
        """关掉音色能力不需要运输能力背书：守卫只拦「宣称支持」的方向。"""
        caps = synthesize_video_capabilities(
            endpoint="openai-video", model_id="sora-2", overrides={"reference_audio_mode": "none"}
        )
        assert caps.reference_audio_mode == ReferenceAudioMode.NONE

    @pytest.mark.unit
    def test_invalid_enum_value_falls_back_to_system_judgement(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            caps = synthesize_video_capabilities(
                endpoint="ark-seedance",
                model_id="doubao-seedance-2-0-260128",
                overrides={"reference_audio_mode": "DIRECT"},
            )

        assert caps.reference_audio_mode is ReferenceAudioMode.DIRECT  # 系统判定本身即 direct
        assert "reference_audio_mode" in caplog.text

    @pytest.mark.unit
    def test_direct_without_positive_count_falls_back_to_none(self, caplog: pytest.LogCaptureFixture):
        """稀疏覆盖只写模式：合并后是 direct ⊕ 上限 0，两维各自合法而合起来无意义。

        不修正就会让 gate 先过模式判定再撞上限 0，把「不支持参考音频」报成「最多支持 0 段」，
        用户按提示减角色数量减到零段也过不了。
        """
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            caps = synthesize_video_capabilities(
                endpoint="ark-seedance",
                model_id="doubao-seedance-1-0-pro-fast-251015",
                overrides={"reference_audio_mode": "direct"},
            )

        assert caps.reference_audio_mode is ReferenceAudioMode.NONE
        assert caps.max_reference_audio_count == 0
        assert "max_reference_audio_count" in caplog.text

    @pytest.mark.unit
    def test_zero_count_override_disables_audio_on_capable_model(self):
        """反向凑法同样收敛：系统判定 direct，用户把上限压成 0，模式随之降到 none。"""
        caps = synthesize_video_capabilities(
            endpoint="ark-seedance",
            model_id="doubao-seedance-2-0-260128",
            overrides={"max_reference_audio_count": 0},
        )
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE

    @pytest.mark.unit
    def test_none_mode_with_positive_count_is_not_a_violation(self):
        """反向组合不算违约：模式为 none 时上限不参与判定，判违约反会把用户关掉的能力顶回开启。"""
        caps = synthesize_video_capabilities(
            endpoint="ark-seedance",
            model_id="doubao-seedance-2-0-260128",
            overrides={"reference_audio_mode": "none"},
        )
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE
        assert caps.max_reference_audio_count == 3  # 系统判定原样保留，不被连坐清零
