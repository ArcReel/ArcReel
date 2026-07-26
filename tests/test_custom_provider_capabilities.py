"""自定义供应商视频能力合成函数（系统判定 ⊕ 用户覆盖）。"""

from __future__ import annotations

import logging

import pytest

from lib.custom_provider.capabilities import (
    CAPABILITY_OVERRIDE_FIELDS,
    filter_valid_overrides,
    synthesize_video_capabilities,
    system_video_capabilities,
)
from lib.video_backends.base import VideoCapabilities


class TestOverrideFieldSchema:
    @pytest.mark.unit
    def test_schema_covers_every_video_capabilities_field(self):
        """覆盖 schema 通用支持 VideoCapabilities 全部字段，键名严格对齐 dataclass。"""
        assert set(CAPABILITY_OVERRIDE_FIELDS) == {
            "first_frame",
            "last_frame",
            "reference_images",
            "max_reference_images",
        }
        assert CAPABILITY_OVERRIDE_FIELDS["last_frame"] is bool
        assert CAPABILITY_OVERRIDE_FIELDS["max_reference_images"] is int


class TestSystemCapabilities:
    @pytest.mark.unit
    def test_endpoint_declaring_int_cap_rebuilds_capabilities(self):
        """endpoint 维度声明硬上限时，参考图布尔位由上限推出（>0 即支持）。"""
        caps = system_video_capabilities(endpoint="openai-video", model_id="sora-2")
        assert caps == VideoCapabilities(
            first_frame=True, last_frame=False, reference_images=True, max_reference_images=1
        )

    @pytest.mark.unit
    def test_endpoint_declaring_zero_cap_yields_no_reference_images(self):
        caps = system_video_capabilities(endpoint="newapi-video", model_id="whatever")
        assert caps.reference_images is False
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
            endpoint="ark-seedance", model_id="doubao-seedance-1-0-pro-fast-251015", overrides={"last_frame": True}
        )
        assert caps.last_frame is True
        assert caps.first_frame is True
        assert caps.max_reference_images == 0

    @pytest.mark.unit
    def test_force_on(self):
        caps = synthesize_video_capabilities(
            endpoint="ark-seedance",
            model_id="doubao-seedance-1-0-pro-fast-251015",
            overrides={"last_frame": True, "reference_images": True},
        )
        assert caps.last_frame is True
        assert caps.reference_images is True

    @pytest.mark.unit
    def test_force_off(self):
        caps = synthesize_video_capabilities(
            endpoint="openai-video", model_id="sora-2", overrides={"first_frame": False, "reference_images": False}
        )
        assert caps.first_frame is False
        assert caps.reference_images is False
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
        assert caps == VideoCapabilities(
            first_frame=True, last_frame=True, reference_images=False, max_reference_images=0
        )
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
