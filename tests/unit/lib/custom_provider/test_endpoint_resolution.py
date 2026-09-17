"""端点键的前缀分流：内置查表、``ce-`` 读库现构造，以及定义到 spec 的投影。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from lib.custom_provider import is_custom_endpoint, make_endpoint_key
from lib.custom_provider.endpoint_resolution import (
    definition_media_type,
    derive_mirror_columns,
    endpoint_spec_from_row,
    resolve_endpoint_spec,
)
from lib.custom_provider.endpoints import ENDPOINT_REGISTRY, get_endpoint_spec
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.video_backends.base import ReferenceAudioMode
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

    def test_a_comfyui_spec_declares_no_capabilities_yet(self):
        """能力由节点绑定推导，推导未落地时一位都不宣称——宽松默认会让设置页展示执行层兑现不了的声明。"""
        row = SimpleNamespace(id=7, definition=comfyui_endpoint_definition())

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))
        caps = spec.video_caps_for_model("wan-t2v") if spec.video_caps_for_model else None

        assert caps is not None
        assert caps.text_to_video is False
        assert caps.first_frame is False
        assert caps.max_reference_images == 0
        assert spec.end_image_capable is False
        assert spec.reference_audio_capable is False

    def test_building_a_backend_for_a_comfyui_spec_says_the_runtime_is_missing(self):
        """端点合法、只是还没有能执行它的 backend：抛 NotImplementedError，不与「端点不认识」混同。"""
        row = SimpleNamespace(id=7, definition=comfyui_endpoint_definition())

        spec = endpoint_spec_from_row(cast("CustomEndpoint", row))

        with pytest.raises(NotImplementedError, match="ComfyUI"):
            spec.build_backend(cast("Any", SimpleNamespace(provider_id="custom-1")), "wan-t2v")

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
