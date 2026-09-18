"""端点键的前缀分流：内置查表、``ce-`` 读库现构造，以及定义到 spec 的投影。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from lib.custom_provider import is_custom_endpoint, make_endpoint_key
from lib.custom_provider.backends import CustomVideoBackend
from lib.custom_provider.comfyui.failures import ComfyuiError
from lib.custom_provider.endpoint_resolution import (
    definition_media_type,
    derive_mirror_columns,
    endpoint_spec_from_row,
    resolve_endpoint_spec,
)
from lib.custom_provider.endpoints import ENDPOINT_REGISTRY, get_endpoint_spec
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.image_backends.base import ImageCapability
from lib.task_failure import FAILURE_CODE_KEYS
from lib.video_backends.base import ReferenceAudioMode, VideoAudioMode
from tests.factories import comfyui_endpoint_definition, custom_endpoint_definition

if TYPE_CHECKING:
    from lib.db.models.custom_endpoint import CustomEndpoint


async def _store(session: AsyncSession, definition: dict) -> int:
    row = await CustomEndpointRepository(session).create(
        definition=definition,
        kind="declarative",
        schema_version="1.0.0",
        media_type="video",
        display_name=definition["meta"]["name"],
    )
    await session.commit()
    return row.id


class TestBuiltinRegistryInvariant:
    def test_no_builtin_key_uses_custom_prefix(self):
        """内置键占用 ce- 前缀会让前缀分流失去唯一性，import 期不变式守住这一条。"""
        assert not [key for key in ENDPOINT_REGISTRY if is_custom_endpoint(key)]


class TestSpecFromRow:
    """用户定义与随版定义共用 declarative_endpoint_spec 那一份投影（定义→spec 的能力缺省、家族、
    路径剥离等由 test_builtin_endpoint_definitions 覆盖），此处只守住 ce- 行独有的那几位。"""

    async def test_projects_identity_and_source(self, db_session: AsyncSession):
        endpoint_id = await _store(db_session, custom_endpoint_definition())
        row = await CustomEndpointRepository(db_session).get(endpoint_id)
        assert row is not None

        spec = endpoint_spec_from_row(row)

        assert spec.key == f"ce-{endpoint_id}"
        assert spec.media_type == "video"
        # 来源决定 catalog 分组与「可否编辑删除」；家族不取键首段（那会得到 "ce"），
        # 用户定义的协议由定义自身描述、没有可归属的外部家族。
        assert spec.source == "custom"
        assert spec.family == "custom"
        assert spec.kind == "declarative"
        assert spec.display_name == "示例端点"
        assert spec.display_name_key == ""
        assert spec.request_method == "POST"
        # base_url 由 provider 提供，目录展示的是接口路径
        assert spec.request_path_template == "/v1/video/create"

    async def test_capabilities_reach_the_spec(self, db_session: AsyncSession):
        definition = custom_endpoint_definition()
        definition["inputs"]["voice"] = {"source": "reference_audio_files", "encoding": "base64"}
        definition["submit"]["body"]["voices"] = [{"$each": {"in": "inputs.voice", "as": "clip", "item": "{{ clip }}"}}]
        definition["capabilities"] = {
            "first_frame": True,
            "reference_audio_mode": "direct",
            "max_reference_audio_count": 2,
        }
        endpoint_id = await _store(db_session, definition)
        row = await CustomEndpointRepository(db_session).get(endpoint_id)
        assert row is not None

        spec = endpoint_spec_from_row(row)

        assert spec.reference_audio_capable is True
        assert spec.video_caps_for_model is not None
        assert spec.video_caps_for_model("m").reference_audio_mode is ReferenceAudioMode.DIRECT


class TestKindDispatch:
    """定义的 ``kind`` 决定投影走谁；名录外的 kind 在投影层就拒，不靠某一种 kind 的规则兜底。"""

    def test_media_type_comes_from_the_definition_kind(self):
        assert definition_media_type(custom_endpoint_definition()) == "video"

    def test_mirror_columns_take_kind_and_media_type_from_the_definition(self):
        mirror = derive_mirror_columns(custom_endpoint_definition())

        assert mirror.kind == "declarative"
        assert mirror.media_type == "video"

    @pytest.mark.parametrize("media_type", ["image", "video"])
    def test_a_comfyui_definition_declares_its_own_media_type(self, media_type: str):
        mirror = derive_mirror_columns(comfyui_endpoint_definition(media_type=media_type))

        assert mirror.kind == "comfyui"
        assert mirror.media_type == media_type

    def test_spec_from_a_comfyui_row_reads_the_definition(self):
        """ComfyUI 行投影成 spec：键由行 id 派生，媒体类型与 kind 读定义，来源标为 custom。"""
        row = SimpleNamespace(id=7, definition=comfyui_endpoint_definition())

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))

        assert spec.key == "ce-7"
        assert spec.kind == "comfyui"
        assert spec.media_type == "video"
        assert spec.source == "custom"
        assert spec.display_name == "示例 ComfyUI 端点"

    def test_a_comfyui_spec_reads_its_capabilities_off_the_bindings(self):
        """夹具没有任何图绑定：纯文生视频，首尾帧与参考图都不支持。"""
        row = SimpleNamespace(id=7, definition=comfyui_endpoint_definition())

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))
        caps = spec.video_caps_for_model("wan-t2v") if spec.video_caps_for_model else None

        assert caps is not None
        assert caps.text_to_video is True
        assert caps.first_frame is False
        assert caps.max_reference_images == 0
        assert spec.end_image_capable is False
        assert spec.reference_audio_capable is False

    def test_the_dimensions_the_bindings_say_nothing_about_stay_unclaimed(self):
        """绑定表里没有对应语义键的维度保持 ``VideoCapabilities`` 的默认值，不凭空声明。"""
        row = SimpleNamespace(id=7, definition=comfyui_endpoint_definition())

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))
        caps = spec.video_caps_for_model("m") if spec.video_caps_for_model else None

        assert caps is not None
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE
        assert caps.max_reference_audio_count == 0
        assert caps.max_prompt_chars is None
        assert caps.first_frame_ratio_adaptive_only is False

    def test_the_audio_track_is_read_off_the_output_chain_and_is_never_controllable(self):
        definition = comfyui_endpoint_definition()
        definition["workflow"]["20"] = {"class_type": "VHS_VideoCombine", "inputs": {"audio": ["21", 0]}}
        definition["bindings"]["output"] = [{"node": "20", "class_type": "VHS_VideoCombine"}]
        row = SimpleNamespace(id=7, definition=definition)

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))
        caps = spec.video_caps_for_model("m") if spec.video_caps_for_model else None

        assert caps is not None
        assert caps.audio_track is VideoAudioMode.ALWAYS_ON
        assert caps.audio_track_for_route("r2v") is VideoAudioMode.ALWAYS_ON

    def test_the_size_dimension_is_fixed_unless_both_sides_are_bound(self):
        drivable = SimpleNamespace(id=7, definition=comfyui_endpoint_definition())
        one_sided = comfyui_endpoint_definition()
        one_sided["bindings"].pop("height")

        assert endpoint_spec_from_row(cast("CustomEndpoint", drivable)).size_fixed is False
        assert endpoint_spec_from_row(cast("CustomEndpoint", SimpleNamespace(id=7, definition=one_sided))).size_fixed

    def test_the_duration_dimension_is_fixed_when_frames_are_unbound(self):
        """``duration_fixed`` 与 ``duration_tier_optional`` 是两件事：前者说用户编不编得动档位。"""
        fixed = SimpleNamespace(id=7, definition=comfyui_endpoint_definition())
        drivable = comfyui_endpoint_definition()
        drivable["bindings"]["frames"] = [{"node": "5", "input": "length", "class_type": "EmptyLatentImage"}]

        assert endpoint_spec_from_row(cast("CustomEndpoint", fixed)).duration_fixed is True
        spec = endpoint_spec_from_row(cast("CustomEndpoint", SimpleNamespace(id=7, definition=drivable)))
        assert spec.duration_fixed is False
        assert spec.duration_tier_optional is True

    def test_both_ways_of_having_no_tier_report_the_same_empty_bit(self):
        """界面的只读 / 禁用判据是「档位为空」，两支都要为真；``duration_fixed`` 只挑文案。"""
        unbound = SimpleNamespace(id=7, definition=comfyui_endpoint_definition())
        no_fps = comfyui_endpoint_definition()
        no_fps["workflow"]["5"]["inputs"]["length"] = 81
        no_fps["bindings"].pop("fps")
        no_fps["bindings"]["frames"] = [{"node": "5", "input": "length", "class_type": "EmptyLatentImage"}]
        derivable = comfyui_endpoint_definition()
        derivable["workflow"]["5"]["inputs"]["length"] = 81
        derivable["bindings"]["frames"] = [{"node": "5", "input": "length", "class_type": "EmptyLatentImage"}]

        def spec_of(definition: dict):
            return endpoint_spec_from_row(cast("CustomEndpoint", SimpleNamespace(id=7, definition=definition)))

        assert (spec_of(unbound.definition).duration_tier_empty, spec_of(unbound.definition).duration_fixed) == (
            True,
            True,
        )
        assert (spec_of(no_fps).duration_tier_empty, spec_of(no_fps).duration_fixed) == (True, False)
        assert (spec_of(derivable).duration_tier_empty, spec_of(derivable).duration_fixed) == (False, False)
        assert get_endpoint_spec("openai-video").duration_tier_empty is False

    def test_a_native_duration_that_cannot_round_trip_is_no_tier_at_all(self):
        """81 帧 @ 24fps 折成 3 秒，而选中 3 秒会让构造层把帧数改写成 73。

        一个名为「原生」、选中却改掉原图的档位比没有档位更坏，故这一维照「不由 ArcReel 驱动」
        对待。同一份帧数配 16fps 恰好回得去，仍报 5 秒。
        """
        off = comfyui_endpoint_definition()
        off["workflow"]["5"]["inputs"]["length"] = 81
        off["workflow"]["9"]["inputs"]["fps"] = 24
        off["bindings"]["frames"] = [{"node": "5", "input": "length", "class_type": "EmptyLatentImage"}]
        exact = json.loads(json.dumps(off))
        exact["workflow"]["9"]["inputs"]["fps"] = 16

        def spec_of(definition: dict):
            return endpoint_spec_from_row(cast("CustomEndpoint", SimpleNamespace(id=7, definition=definition)))

        assert spec_of(off).duration_tier_empty is True
        assert spec_of(exact).duration_tier_empty is False
        assert spec_of(exact).endpoint_durations == [5]

    def test_the_native_resolution_borrows_a_tier_word_only_on_an_exact_hit(self):
        """夹具的字面宽高是 832 × 480，短边 480 恰好是视频档位表里的 480p。"""
        row = SimpleNamespace(id=7, definition=comfyui_endpoint_definition())

        assert endpoint_spec_from_row(cast("CustomEndpoint", row)).native_resolution == "480p"

    def test_a_short_edge_off_the_tiers_is_reported_as_pixels(self):
        """848 既不是 720p 也不是 1080p：报最近的档位会让人以为选中它得到的是同一份画面。"""
        definition = comfyui_endpoint_definition()
        definition["workflow"]["5"]["inputs"]["width"] = 848
        definition["workflow"]["5"]["inputs"]["height"] = 1504
        row = SimpleNamespace(id=7, definition=definition)

        assert endpoint_spec_from_row(cast("CustomEndpoint", row)).native_resolution == "848px"

    def test_an_unreadable_literal_size_leaves_the_native_resolution_unknown(self):
        definition = comfyui_endpoint_definition()
        definition["workflow"]["5"]["inputs"]["width"] = "832"
        definition["workflow"]["5"]["inputs"]["height"] = "480"
        row = SimpleNamespace(id=7, definition=definition)

        assert endpoint_spec_from_row(cast("CustomEndpoint", row)).native_resolution is None

    def test_a_one_sided_size_binding_reports_no_native_resolution(self):
        """只绑一侧时尺寸整维判为固定，构造层压根走不到「字面短边」那一步。

        读得到的那个字面值也未必是短边：把 832 的长边说成「原生 832px」，用户会以为不选档位就
        得到一张短边 832 的画面。这一支的占位文案改由 ``size_fixed`` 那一位给出。
        """
        definition = comfyui_endpoint_definition()
        definition["bindings"].pop("height")
        row = SimpleNamespace(id=7, definition=definition)

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))

        assert (spec.size_fixed, spec.native_resolution) == (True, None)

    def test_a_declarative_endpoint_declares_no_fixed_dimension(self):
        """尺寸与时长「被端点固定」只是 ComfyUI 的形态，其余端点由请求参数决定。"""
        spec = get_endpoint_spec("openai-video")

        assert (spec.size_fixed, spec.duration_fixed, spec.native_resolution) == (False, False, None)

    def test_only_a_comfyui_spec_lets_its_duration_tier_be_empty(self):
        """ADR 0018 的空集 fail loud 对其余端点不变，判据挂在 spec 上供能力解析层直读。"""
        row = SimpleNamespace(id=7, definition=comfyui_endpoint_definition())

        assert endpoint_spec_from_row(cast("CustomEndpoint", row)).duration_tier_optional is True
        assert get_endpoint_spec("openai-video").duration_tier_optional is False

    def test_the_model_name_does_not_enter_the_derivation(self):
        """一份 workflow 恰是一个型号：模型行换名字不改它能做什么。"""
        definition = comfyui_endpoint_definition()
        definition["bindings"]["start_image"] = [{"node": "11", "input": "image", "class_type": "LoadImage"}]
        row = SimpleNamespace(id=7, definition=definition)

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))
        caps_fn = spec.video_caps_for_model

        assert caps_fn is not None
        assert caps_fn("wan-t2v") == caps_fn("完全不相干的名字")
        assert caps_fn("wan-t2v").first_frame is True
        assert caps_fn("wan-t2v").text_to_video is False

    def test_the_end_frame_binding_reaches_the_end_image_transport_bit(self):
        """``end_image_capable`` 与声明式端点同处理：从 caps 反推，不另写一份判据。"""
        definition = comfyui_endpoint_definition()
        definition["bindings"]["end_image"] = [{"node": "12", "input": "image", "class_type": "LoadImage"}]
        row = SimpleNamespace(id=7, definition=definition)

        assert endpoint_spec_from_row(cast("CustomEndpoint", row)).end_image_capable is True

    def test_an_image_comfyui_spec_derives_its_image_capabilities(self):
        definition = comfyui_endpoint_definition(media_type="image")
        definition["bindings"].pop("fps")
        row = SimpleNamespace(id=7, definition=definition)

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))

        assert spec.image_capabilities == frozenset({ImageCapability.TEXT_TO_IMAGE})
        assert spec.video_caps_for_model is None

    def test_a_comfyui_spec_builds_the_comfyui_video_backend(self):
        """投影按 kind 分叉产出 Python 实现的 ComfyUI backend，仍走自定义供应商的包装层。"""
        row = SimpleNamespace(id=7, definition=comfyui_endpoint_definition())
        provider = SimpleNamespace(provider_id="custom-1", base_url="https://comfy.test", api_key="")

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))
        backend = spec.build_backend(cast("Any", provider), "wan-t2v")

        assert isinstance(backend, CustomVideoBackend)
        assert backend.name == "custom-1"
        assert backend.model == "wan-t2v"

    def test_building_a_comfyui_backend_without_a_base_url_is_refused(self):
        """没有地址就无处提交：与「端点不存在」同一出口，让上游按既有分流吞掉这一条。"""
        row = SimpleNamespace(id=7, definition=comfyui_endpoint_definition())
        provider = SimpleNamespace(provider_id="custom-1", base_url="", api_key="")

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))

        with pytest.raises(ValueError, match="base_url"):
            spec.build_backend(cast("Any", provider), "wan-t2v")

    def test_building_a_backend_for_a_comfyui_image_spec_says_the_runtime_is_missing(self):
        """图像通道还没有 backend：抛带失败码的 ComfyuiError，不与「端点不认识」混同。

        码进 ``FAILURE_CODE_KEYS``，worker 据此落结构化失败、读侧按语言渲染——落一段裸文本的话，
        非中文用户在任务列表里看到的是一句中文。
        """
        row = SimpleNamespace(id=7, definition=comfyui_endpoint_definition(media_type="image"))
        provider = SimpleNamespace(provider_id="custom-1", base_url="https://comfy.test", api_key="")

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))

        with pytest.raises(ComfyuiError) as caught:
            spec.build_backend(cast("Any", provider), "flux")

        assert caught.value.code == "provider_unsupported_media"
        assert caught.value.params == {"provider_id": "custom-1", "media_type": "image"}
        assert caught.value.code in FAILURE_CODE_KEYS

    def test_media_type_of_an_unsupported_kind_is_refused(self):
        definition = custom_endpoint_definition(kind="unregistered")

        with pytest.raises(ValueError, match="unsupported endpoint definition kind"):
            definition_media_type(definition)

    def test_spec_from_a_row_of_an_unsupported_kind_is_refused(self):
        """库里的 kind 是本层没有投影实现的那种：抛 ValueError，与「端点不存在」同一出口。"""
        row = SimpleNamespace(id=7, definition=custom_endpoint_definition(kind="unregistered"))

        with pytest.raises(ValueError, match="unsupported endpoint definition kind"):
            endpoint_spec_from_row(cast("CustomEndpoint", row))


class TestResolveEndpointSpec:
    async def test_builtin_key_delegates_to_registry(self, db_session: AsyncSession):
        repo = CustomEndpointRepository(db_session)
        assert await resolve_endpoint_spec("openai-video", repo.get) is get_endpoint_spec("openai-video")

    async def test_custom_key_reads_definition_from_db(self, db_session: AsyncSession):
        endpoint_id = await _store(db_session, custom_endpoint_definition())

        spec = await resolve_endpoint_spec(make_endpoint_key(endpoint_id), CustomEndpointRepository(db_session).get)

        assert spec.key == f"ce-{endpoint_id}"
        assert spec.display_name == "示例端点"

    async def test_updated_definition_resolves_to_new_spec(self, db_session: AsyncSession):
        """原地更新立即对新解析生效：不做启动时全量装载，也没有进程内注册表缓存。"""
        endpoint_id = await _store(db_session, custom_endpoint_definition())
        repo = CustomEndpointRepository(db_session)
        renamed = custom_endpoint_definition(meta={"name": "改名后", "author": "ArcReel", "version": "0.2.0"})
        await repo.update(
            endpoint_id,
            definition=renamed,
            kind="declarative",
            schema_version="1.0.0",
            media_type="video",
            display_name="改名后",
        )
        await db_session.commit()

        spec = await resolve_endpoint_spec(make_endpoint_key(endpoint_id), repo.get)

        assert spec.display_name == "改名后"

    async def test_deleted_custom_endpoint_is_unknown(self, db_session: AsyncSession):
        with pytest.raises(ValueError, match="unknown endpoint"):
            await resolve_endpoint_spec("ce-404", CustomEndpointRepository(db_session).get)

    @pytest.mark.parametrize(
        "endpoint",
        [
            "ce-not-a-number",
            "ce-",
            "ce-0",
            "ce-03",
            "ce- 3",
            "ce-+3",
            "ce--3",
            "ce-٣",
            "ce-3_0",
            "ce-3\n",
        ],
    )
    async def test_malformed_custom_key_is_unknown(self, db_session: AsyncSession, endpoint: str):
        with pytest.raises(ValueError, match="unknown endpoint"):
            await resolve_endpoint_spec(endpoint, CustomEndpointRepository(db_session).get)

    async def test_unknown_builtin_key_is_unknown(self, db_session: AsyncSession):
        with pytest.raises(ValueError, match="unknown endpoint"):
            await resolve_endpoint_spec("no-such-endpoint", CustomEndpointRepository(db_session).get)
