"""端点键的前缀分流：内置查表、``ce-`` 读库现构造，以及定义到 spec 的投影。"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from lib.custom_provider import is_custom_endpoint, make_endpoint_key
from lib.custom_provider.endpoint_resolution import endpoint_spec_from_row, resolve_endpoint_spec
from lib.custom_provider.endpoints import ENDPOINT_REGISTRY, get_endpoint_spec
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.video_backends.base import ReferenceAudioMode
from tests.factories import custom_endpoint_definition


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
