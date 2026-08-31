"""自定义供应商视频能力合成函数（系统判定 ⊕ 用户覆盖）。"""

from __future__ import annotations

import logging

import pytest

from lib.custom_provider.capabilities import (
    CAPABILITY_OVERRIDE_FIELDS,
    capability_type_name,
    capability_value_matches,
    filter_valid_overrides,
    synthesize_video_capabilities,
    system_video_capabilities,
    video_capabilities_from_definition,
)
from lib.video_backends.base import ReferenceAudioMode, VideoAudioMode, VideoCapabilities


class TestOverrideFieldSchema:
    def test_schema_covers_every_video_capabilities_field(self):
        """覆盖 schema 通用支持 VideoCapabilities 全部字段，键名严格对齐 dataclass。"""
        assert set(CAPABILITY_OVERRIDE_FIELDS) == {
            "text_to_video",
            "first_frame",
            "last_frame",
            "max_reference_images",
            "reference_audio_mode",
            "max_reference_audio_count",
            "max_reference_audio_total_seconds",
            "reference_audio_per_image",
            "max_prompt_chars",
            "first_frame_ratio_adaptive_only",
            "audio_track",
            "reference_route_audio_track",
        }
        assert CAPABILITY_OVERRIDE_FIELDS["last_frame"] is bool
        assert CAPABILITY_OVERRIDE_FIELDS["text_to_video"] is bool
        assert CAPABILITY_OVERRIDE_FIELDS["max_reference_images"] is int
        assert CAPABILITY_OVERRIDE_FIELDS["reference_audio_mode"] is ReferenceAudioMode
        assert CAPABILITY_OVERRIDE_FIELDS["max_reference_audio_count"] is int
        assert CAPABILITY_OVERRIDE_FIELDS["max_reference_audio_total_seconds"] == (float | None)
        assert CAPABILITY_OVERRIDE_FIELDS["max_prompt_chars"] == (int | None)
        assert CAPABILITY_OVERRIDE_FIELDS["reference_audio_per_image"] is bool
        assert CAPABILITY_OVERRIDE_FIELDS["first_frame_ratio_adaptive_only"] is bool
        assert CAPABILITY_OVERRIDE_FIELDS["audio_track"] is VideoAudioMode
        assert CAPABILITY_OVERRIDE_FIELDS["reference_route_audio_track"] == (VideoAudioMode | None)

    def test_audio_track_dimensions_are_not_open_to_users(self):
        """音轨形态不对用户开放覆盖（``docs/adr/0054``）。

        自定义供应商的音轨口径按「无信号不收紧」处理：设置界面拿不到自定义模型的逐模型音轨
        目录，服务端单方面派生会让界面与入队预检各说一套；endpoint 背后的上游 model 又由用户
        填写、无从核实。开放覆盖等于把一份没有执行侧对应物的手写声明请回来——正是收编音轨真相源
        要消除的分裂。
        """
        from server.routers.custom_providers import CAPABILITY_OVERRIDE_ALLOWLIST

        assert not CAPABILITY_OVERRIDE_ALLOWLIST & {"audio_track", "reference_route_audio_track"}

    def test_text_to_video_is_not_open_to_users(self):
        from server.routers.custom_providers import CAPABILITY_OVERRIDE_ALLOWLIST

        assert "text_to_video" not in CAPABILITY_OVERRIDE_ALLOWLIST


class TestCapabilitiesFromDefinition:
    """声明式定义的协议能力：显式声明照收，``text_to_video`` 由必需图输入机械推导。"""

    @staticmethod
    def _definition(*, source: str = "start_image", required: bool | None = None, **capabilities: object) -> dict:
        declaration: dict[str, object] = {"source": source, "encoding": "data_uri"}
        if required is not None:
            declaration["required"] = required
        return {"inputs": {"asset": declaration}, "capabilities": capabilities}

    def test_required_image_input_yields_no_text_to_video(self):
        caps = video_capabilities_from_definition(
            self._definition(required=True, first_frame=True, text_to_video=False)
        )
        assert caps.text_to_video is False
        assert caps.first_frame is True

    def test_optional_image_input_keeps_text_to_video(self):
        caps = video_capabilities_from_definition(
            self._definition(required=False, first_frame=True, text_to_video=True)
        )
        assert caps.text_to_video is True

    def test_undeclared_required_defaults_to_optional(self):
        """``required`` 缺省即可选：缺席的键不该被当成「必需」而误关文生能力。"""
        caps = video_capabilities_from_definition(self._definition(first_frame=True))
        assert caps.text_to_video is True

    def test_required_audio_input_does_not_close_text_to_video(self):
        """只有图输入参与推导：必需的参考音频不改变纯文生请求的可行性。"""
        caps = video_capabilities_from_definition(
            self._definition(
                source="reference_audio_files",
                required=True,
                reference_audio_mode="direct",
                max_reference_audio_count=2,
            ),
        )
        assert caps.text_to_video is True

    def test_definition_without_first_frame_declaration_does_not_inherit_dataclass_default(self):
        """``VideoCapabilities`` 的 first_frame 默认真值不适用于声明式：未声明即不支持。"""
        caps = video_capabilities_from_definition({"inputs": {}, "capabilities": {}})
        assert caps.first_frame is False
        assert caps.text_to_video is True

    @pytest.mark.parametrize(
        "capabilities",
        [{"not_a_capability": True}, {"max_reference_images": -1}, {"first_frame": "yes"}],
        ids=["unknown-key", "negative-count", "wrong-type"],
    )
    def test_invalid_capability_declaration_raises(self, capabilities: dict[str, object]):
        """定义里的能力节不走覆盖那套「丢弃脏键继续跑」：定义是唯一真相源，脏声明必须炸。"""
        with pytest.raises(ValueError, match="invalid capability"):
            video_capabilities_from_definition({"inputs": {}, "capabilities": capabilities})


class TestOptionalDimensionSchema:
    """可选维度（``T | None``）的注解不是 ``type``，消费 schema 的各处都不能假设它是。"""

    def test_type_name_renders_optional_annotation_without_crashing(self):
        """展示名对 types.UnionType 也要给得出——校验失败分支不该比被校验的值更脆。"""
        assert capability_type_name(CAPABILITY_OVERRIDE_FIELDS["max_reference_audio_count"]) == "int"
        assert capability_type_name(CAPABILITY_OVERRIDE_FIELDS["max_reference_audio_total_seconds"]) == "float | None"

    @pytest.mark.parametrize("value", [15.0, 15, 0, None])
    def test_optional_float_accepts_none_and_non_negative_numbers(self, value):
        """None 表示「该后端不声明这项约束」；JSON 的 15 与 15.0 是同一个数，都收。"""
        assert capability_value_matches(value, CAPABILITY_OVERRIDE_FIELDS["max_reference_audio_total_seconds"])

    @pytest.mark.parametrize("value", [-1.0, True, "15", [15]])
    def test_optional_float_rejects_negative_bool_and_non_numeric(self, value):
        assert not capability_value_matches(value, CAPABILITY_OVERRIDE_FIELDS["max_reference_audio_total_seconds"])

    def test_none_rejected_on_non_optional_dimension(self):
        assert not capability_value_matches(None, CAPABILITY_OVERRIDE_FIELDS["max_reference_audio_count"])

    def test_invalid_optional_float_override_is_dropped_with_warning(self, caplog):
        """存量脏行 / 非 API 写入路径的坏值走降级日志，不抛 AttributeError。"""
        with caplog.at_level(logging.WARNING):
            applied = filter_valid_overrides(
                endpoint="vidu-video",
                model_id="viduq3",
                overrides={"max_reference_audio_total_seconds": "15"},
            )
        assert applied == {}
        assert "float | None" in caplog.text

    def test_integer_override_is_coerced_to_float_on_merge(self):
        """校验放行整数字面量后，合并须还原成 float，字段不留 int。"""
        caps = synthesize_video_capabilities(
            endpoint="vidu-video",
            model_id="viduq3",
            overrides={"max_reference_audio_total_seconds": 12},
        )
        assert caps.max_reference_audio_total_seconds == 12.0
        assert isinstance(caps.max_reference_audio_total_seconds, float)


class TestSystemCapabilities:
    def test_endpoint_declaring_int_cap_rebuilds_capabilities(self):
        """endpoint 维度声明硬上限时原样落到 max_reference_images，其余维度取默认。"""
        caps = system_video_capabilities(endpoint="openai-video", model_id="sora-2")
        assert caps == VideoCapabilities(first_frame=True, last_frame=False, max_reference_images=1)

    def test_endpoint_declaring_zero_cap_yields_no_reference_images(self):
        caps = system_video_capabilities(endpoint="newapi-video", model_id="whatever")
        assert caps.max_reference_images == 0

    def test_endpoint_with_caps_fn_delegates_to_backend_declaration(self):
        """endpoint 未声明硬上限时走 backend 的 per-model 纯函数，四字段全量取其声明。"""
        from lib.video_backends.vidu import ViduVideoBackend

        caps = system_video_capabilities(endpoint="vidu-video", model_id="viduq3")
        assert caps == ViduVideoBackend.video_capabilities_for_model("viduq3")

    def test_non_video_endpoint_raises(self):
        with pytest.raises(ValueError, match="not video"):
            system_video_capabilities(endpoint="openai-chat", model_id="gpt-4o")

    def test_unknown_endpoint_raises(self):
        with pytest.raises(ValueError, match=r"unknown endpoint"):
            system_video_capabilities(endpoint="no-such-endpoint", model_id="x")


class TestSynthesize:
    @pytest.mark.parametrize("overrides", [None, {}])
    def test_absent_overrides_follow_system(self, overrides: dict | None):
        """列 NULL 与空字典都等价于「全部跟随系统判定」。"""
        assert synthesize_video_capabilities(
            endpoint="openai-video", model_id="sora-2", overrides=overrides
        ) == system_video_capabilities(endpoint="openai-video", model_id="sora-2")

    def test_missing_key_follows_system(self):
        """字典存在但缺某键 → 该维度跟随系统判定，其余键照常覆盖。"""
        caps = synthesize_video_capabilities(
            endpoint="ark-seedance", model_id="doubao-seedance-1-0-pro-fast-251015", overrides={"first_frame": False}
        )
        assert caps.first_frame is False
        assert caps.last_frame is False
        assert caps.max_reference_images == 0

    def test_force_on(self):
        caps = synthesize_video_capabilities(
            endpoint="ark-seedance",
            model_id="doubao-seedance-1-0-pro-fast-251015",
            overrides={"last_frame": True, "max_reference_images": 4},
        )
        assert caps.last_frame is True
        assert caps.max_reference_images == 4

    def test_force_off(self):
        caps = synthesize_video_capabilities(
            endpoint="openai-video", model_id="sora-2", overrides={"first_frame": False}
        )
        assert caps.first_frame is False
        # 未覆盖的数值维度不受布尔覆盖牵连
        assert caps.max_reference_images == 1

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

    def test_int_field_override(self):
        caps = synthesize_video_capabilities(
            endpoint="openai-video", model_id="sora-2", overrides={"max_reference_images": 3}
        )
        assert caps.max_reference_images == 3

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

    def test_last_frame_override_ignored_when_endpoint_lacks_end_image_support(self, caplog: pytest.LogCaptureFixture):
        """openai-video 的 delegate 不下传 end_image：覆盖只会让合成层宣称支持、执行层仍静默丢帧，须降级跟随系统判定。"""
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            caps = synthesize_video_capabilities(
                endpoint="openai-video", model_id="sora-2", overrides={"last_frame": True}
            )
        assert caps.last_frame is False
        assert "last_frame" in caplog.text

    @pytest.mark.parametrize("bad", ["true", 1, None, [], {"x": 1}])
    def test_non_bool_value_on_bool_field_ignored(self, bad: object, caplog: pytest.LogCaptureFixture):
        """布尔维度只接受真 bool；1/"true" 等宽松真值不做语义映射，一律降级跟随。"""
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            caps = synthesize_video_capabilities(
                endpoint="openai-video", model_id="sora-2", overrides={"last_frame": bad}
            )
        assert caps.last_frame is False
        assert "last_frame" in caplog.text

    @pytest.mark.parametrize("bad", [True, -1, "2", 1.5, None])
    def test_bad_value_on_int_field_ignored(self, bad: object, caplog: pytest.LogCaptureFixture):
        """数值维度只接受非负 int；bool 是 int 子类，须被显式排除。"""
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            caps = synthesize_video_capabilities(
                endpoint="openai-video", model_id="sora-2", overrides={"max_reference_images": bad}
            )
        assert caps.max_reference_images == 1
        assert "max_reference_images" in caplog.text

    @pytest.mark.parametrize("bad", ["last_frame", ["last_frame"], 42])
    def test_non_dict_overrides_ignored(self, bad: object, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            caps = synthesize_video_capabilities(endpoint="openai-video", model_id="sora-2", overrides=bad)
        assert caps == system_video_capabilities(endpoint="openai-video", model_id="sora-2")
        assert caplog.records

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

    def test_enum_value_matches_accepts_declared_literal(self):
        assert capability_value_matches("direct", ReferenceAudioMode)
        assert capability_value_matches("none", ReferenceAudioMode)

    @pytest.mark.parametrize("value", ["DIRECT", "Direct", "native", "", True, 1, None])
    def test_enum_value_matches_rejects_everything_else(self, value):
        """词表外的值一律判否——不做大小写归一，也不猜近义词。"""
        assert not capability_value_matches(value, ReferenceAudioMode)

    def test_direct_override_applies_on_audio_capable_endpoint(self):
        caps = synthesize_video_capabilities(
            endpoint="ark-seedance",
            model_id="doubao-seedance-1-0-pro-fast-251015",
            overrides={"reference_audio_mode": "direct", "max_reference_audio_count": 2},
        )
        assert caps.reference_audio_mode == ReferenceAudioMode.DIRECT
        assert caps.max_reference_audio_count == 2

    def test_direct_override_ignored_when_endpoint_cannot_send_audio(self, caplog: pytest.LogCaptureFixture):
        """endpoint 的 delegate 不下传参考音频时，覆盖只会让声明失真，须降级跟随系统判定。"""
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            caps = synthesize_video_capabilities(
                endpoint="openai-video", model_id="sora-2", overrides={"reference_audio_mode": "direct"}
            )

        assert caps.reference_audio_mode is ReferenceAudioMode.NONE
        assert "reference_audio_mode=direct" in caplog.text

    def test_none_override_needs_no_endpoint_support(self):
        """关掉音色能力不需要运输能力背书：守卫只拦「宣称支持」的方向。"""
        caps = synthesize_video_capabilities(
            endpoint="openai-video", model_id="sora-2", overrides={"reference_audio_mode": "none"}
        )
        assert caps.reference_audio_mode == ReferenceAudioMode.NONE

    def test_invalid_enum_value_falls_back_to_system_judgement(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            caps = synthesize_video_capabilities(
                endpoint="ark-seedance",
                model_id="doubao-seedance-2-0-260128",
                overrides={"reference_audio_mode": "DIRECT"},
            )

        assert caps.reference_audio_mode is ReferenceAudioMode.DIRECT  # 系统判定本身即 direct
        assert "reference_audio_mode" in caplog.text

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

    def test_zero_count_override_disables_audio_on_capable_model(self):
        """反向凑法同样收敛：系统判定 direct，用户把上限压成 0，模式随之降到 none。"""
        caps = synthesize_video_capabilities(
            endpoint="ark-seedance",
            model_id="doubao-seedance-2-0-260128",
            overrides={"max_reference_audio_count": 0},
        )
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE

    def test_none_mode_with_positive_count_is_not_a_violation(self):
        """反向组合不算违约：模式为 none 时上限不参与判定，判违约反会把用户关掉的能力顶回开启。"""
        caps = synthesize_video_capabilities(
            endpoint="ark-seedance",
            model_id="doubao-seedance-2-0-260128",
            overrides={"reference_audio_mode": "none"},
        )
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE
        assert caps.max_reference_audio_count == 3  # 系统判定原样保留，不被连坐清零
