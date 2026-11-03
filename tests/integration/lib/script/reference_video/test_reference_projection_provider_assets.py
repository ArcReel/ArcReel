"""请求投影里的供应商素材关联核对：读侧（接收任务、批量预览、报价共用的投影）与执行侧对同一份配置得出同一结论。

真实数据库（凭证、素材库账号与绑定、关联、自定义供应商）、真实版本记录与生产装配（``assemble_backend``）；
只有视频请求事实用构造好的结果对象。不发出任何网络请求。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.artifacts.version_manager import VersionManager
from lib.asset_library.bindings import CredentialType, account_scope, confirm_binding, get_bindable_credential
from lib.asset_library.generation_inputs import choose_reference_image_assets
from lib.backends.backend_assembly.assembler import assemble_backend
from lib.backends.backend_assembly.specs import effective_builtin_video_capabilities
from lib.backends.video_frame_slots import resolve_video_capabilities
from lib.catalog import query as catalog
from lib.config.resolver import ConfigResolver
from lib.custom_provider import make_provider_id
from lib.db.models.asset_library import ProviderAssetLink
from lib.db.repositories.asset_library_repo import AssetLibraryRepository
from lib.db.repositories.credential_repository import CredentialRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.material.storage import CONTENT_VERSION_ID
from lib.script.reference_video.provider_asset_links import lane_asset_link_check
from lib.script.reference_video.request_projection import (
    ReferenceUnitRequestProjection,
    project_reference_unit_request,
)
from tests.factories import activate_reference_project
from tests.fakes import fake_reference_request_facts

SEEDANCE_2 = "doubao-seedance-2-0-260128"
OPAQUE_MODEL = "ep-20261103-opaque"
FORGED_MODEL = "proxy-seedance-2-0-anything"

_UNIT: dict[str, Any] = {"unit_id": "E1U1", "text": "@[张三] 走进 @[酒馆]", "duration_seconds": 8}
_SCRIPT: dict[str, Any] = {"episode": 1, "generation_mode": "reference_video", "video_units": [_UNIT]}


@dataclass
class _World:
    sessions: async_sessionmaker[AsyncSession]
    project_path: Path
    project: dict[str, Any]
    zhang_version: str
    account_id: int

    @property
    def resolver(self) -> ConfigResolver:
        return ConfigResolver(self.sessions)

    async def link(self, *, state: str, **extra: Any) -> int:
        """为张三的当前内容版本在素材库账号里建一条选定的关联。"""
        async with self.sessions() as session:
            account = await AssetLibraryRepository(session).get_account(self.account_id)
            assert account is not None
            link = ProviderAssetLink(
                project_name=self.project_path.name,
                content_version_id=self.zhang_version,
                content_digest="0" * 64,
                resource_type="characters",
                resource_id="张三",
                version=1,
                locator_path="versions/x.png",
                origin="upload",
                account_id=account.id,
                account_identity=account_scope(account).account_identity,
                site=account.site,
                region=account.region,
                provider_project_name=account.project_name,
                state=state,
                submit_name="zhang-arc-0000000000000000",
                selected=True,
                check_attempts=0,
                **extra,
            )
            session.add(link)
            await session.commit()
            return link.id

    async def set_link(self, link_id: int, **values: Any) -> None:
        async with self.sessions() as session:
            await session.execute(update(ProviderAssetLink).where(ProviderAssetLink.id == link_id).values(**values))
            await session.commit()

    async def bind(self, credential_type: CredentialType, credential_id: int) -> None:
        async with self.sessions() as session:
            account = await AssetLibraryRepository(session).get_account(self.account_id)
            bindable = await get_bindable_credential(session, credential_type, credential_id)
            assert account is not None
            assert bindable is not None
            await confirm_binding(session, account, bindable)
            await session.commit()

    async def custom_provider(self, models: list[dict[str, Any]]) -> int:
        async with self.sessions() as session:
            provider = await CustomProviderRepository(session).create_provider(
                display_name="中转",
                discovery_format="openai",
                base_url="https://relay.example.test",
                api_key="TEST-relay-key-0001",
                models=[{"display_name": m["model_id"], "endpoint": "ark-seedance", **m} for m in models],
            )
            await session.commit()
            return provider.id

    async def read_side(self, provider_id: str, model_id: str) -> ReferenceUnitRequestProjection:
        """接收任务、批量预览与报价共用的投影：素材关联按当前配置装配的调用通道核对。"""
        return await project_reference_unit_request(
            project=self.project,
            script=_SCRIPT,
            unit=_UNIT,
            project_path=self.project_path,
            resolver=self.resolver,
            request_facts_lookup=fake_reference_request_facts(
                durations=(8,), provider_id=provider_id, model_id=model_id
            ),
        )

    async def execution_side(self, provider_id: str, model_id: str) -> ReferenceUnitRequestProjection:
        """执行侧的投影：素材关联按 lane 实际构造的调用通道核对（执行器交给投影的就是这一份）。"""
        backend = await assemble_backend(
            provider_id=provider_id, media_type="video", model_id=model_id, resolver=self.resolver
        )
        choose = partial(
            choose_reference_image_assets,
            self.sessions,
            backend=backend,
            project_name=self.project_path.name,
            project_path=self.project_path,
        )
        return await project_reference_unit_request(
            project=self.project,
            script=_SCRIPT,
            unit=_UNIT,
            project_path=self.project_path,
            resolver=self.resolver,
            request_facts_lookup=fake_reference_request_facts(
                durations=(8,), provider_id=provider_id, model_id=model_id
            ),
            asset_links=lane_asset_link_check(
                choose=lambda sources: choose(sources=sources), project_path=self.project_path
            ),
        )


def _commit_sheet(project_path: Path, resource_type: str, name: str, data: bytes) -> str:
    working = project_path / resource_type / f"{name}.png"
    working.parent.mkdir(parents=True, exist_ok=True)
    working.write_bytes(data)
    manager = VersionManager(project_path)
    version = manager.add_version(resource_type, name, "p", source_file=working)
    record = next(v for v in manager.get_versions(resource_type, name)["versions"] if v["version"] == version)
    return record[CONTENT_VERSION_ID]


@pytest.fixture
async def world(db_engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _World:
    sessions = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("lib.db.async_session_factory", sessions)
    project_path = tmp_path / "demo"
    project_path.mkdir()
    zhang = _commit_sheet(project_path, "characters", "张三", b"zhang-v1")
    _commit_sheet(project_path, "scenes", "酒馆", b"tavern-v1")
    project = activate_reference_project(
        project_path,
        {
            "characters": {"张三": {"description": "张三", "character_sheet": "characters/张三.png"}},
            "scenes": {"酒馆": {"description": "酒馆", "scene_sheet": "scenes/酒馆.png"}},
        },
    )
    async with sessions() as session:
        account = await AssetLibraryRepository(session).create_account(
            site="volcengine",
            name="火山",
            region="cn-beijing",
            project_name="default",
            access_key_id="TEST-ACCESS-KEY-ID",
            secret_access_key="TEST-SECRET-ACCESS-KEY",
        )
        await session.commit()
        account_id = account.id
    return _World(sessions, project_path, project, zhang, account_id)


def _link_problems(projection: ReferenceUnitRequestProjection) -> list[dict[str, object]]:
    return [p for p in projection.problem_payloads() if str(p["code"]).startswith("provider_asset_link_")]


async def test_precheck_and_execution_report_the_same_unusable_link_for_builtin_ark(world: _World):
    """内置方舟：适用关联处理中时，读侧投影与执行侧投影报同一个阻断问题，点名提示词里的图片编号；可用后都不阻断。"""
    async with world.sessions() as session:
        credential = await CredentialRepository(session).create(
            provider="ark", name="主 Key", api_key="TEST-ark-inference-key-0001"
        )
        await session.commit()
    await world.bind("builtin", credential.id)
    link_id = await world.link(state="processing", provider_asset_id="asset-zhang-1")

    read = await world.read_side("ark", SEEDANCE_2)
    execution = await world.execution_side("ark", SEEDANCE_2)

    problems = _link_problems(read)
    assert problems == _link_problems(execution)
    assert [(p["code"], p["blocking"], p["params"]) for p in problems] == [
        ("provider_asset_link_processing", True, {"index": 1, "name": "张三"})
    ]
    assert read.cost is not None, "关联阻断不改变投影的请求事实，报价仍按同一份请求坐标"

    await world.set_link(link_id, state="active")
    assert _link_problems(await world.read_side("ark", SEEDANCE_2)) == []
    assert _link_problems(await world.execution_side("ark", SEEDANCE_2)) == []


@pytest.mark.parametrize(
    ("model", "entry_binding", "applies"),
    [
        (OPAQUE_MODEL, "bytedance/doubao-seedance-2.0", True),
        (OPAQUE_MODEL, None, False),
        (FORGED_MODEL, None, False),
    ],
    ids=["opaque-id-bound-to-seedance", "opaque-id-unbound", "forged-seedance-name"],
)
async def test_custom_endpoint_asset_support_comes_from_the_model_entry(
    world: _World, model: str, entry_binding: str | None, applies: bool
):
    """自定义「火山方舟 (Seedance)」端点：是否发素材引用按模型对应的条目认定，绑定 Seedance 条目的不透明模型 ID
    适用，名字里带 Seedance 字样但对应不上条目的不适用；读侧与执行侧结论一致。"""
    db_id = await world.custom_provider([{"model_id": model, "is_default": True, "entry_binding": entry_binding}])
    await world.bind("custom", db_id)
    await world.link(state="failed", error_code="DownloadFailed", error_message="cannot fetch")
    provider_id = make_provider_id(db_id)

    read = await world.read_side(provider_id, model)
    execution = await world.execution_side(provider_id, model)

    assert _link_problems(read) == _link_problems(execution)
    codes = [p["code"] for p in _link_problems(read)]
    assert codes == (["provider_asset_link_failed"] if applies else [])


async def _configured_projection(world: _World, provider_id: str, model_id: str) -> ReferenceUnitRequestProjection:
    """生产读侧投影：请求事实按项目配置与真实能力解析，不替换。"""
    project = {**world.project, "video_provider_r2v": f"{provider_id}/{model_id}"}
    return await project_reference_unit_request(
        project=project, script=_SCRIPT, unit=_UNIT, project_path=world.project_path, resolver=world.resolver
    )


async def test_opaque_custom_model_bound_to_seedance_gets_the_entry_video_capabilities(world: _World):
    """不透明模型 ID 绑定 Seedance 2.0 条目：参考生视频可选，两张参考图都不被裁掉，素材关联核对随之生效；
    执行时调用通道按同一条目声明能力，请求里仍是原模型 ID。"""
    db_id = await world.custom_provider(
        [{"model_id": OPAQUE_MODEL, "is_default": True, "entry_binding": "bytedance/doubao-seedance-2.0"}]
    )
    await world.bind("custom", db_id)
    await world.link(state="failed", error_code="DownloadFailed", error_message="cannot fetch")
    provider_id = make_provider_id(db_id)

    selected = await world.resolver.resolve_video_backend(
        {"video_provider_r2v": f"{provider_id}/{OPAQUE_MODEL}"}, None, generation_type="r2v"
    )
    projection = await _configured_projection(world, provider_id, OPAQUE_MODEL)
    backend = await assemble_backend(
        provider_id=provider_id, media_type="video", model_id=OPAQUE_MODEL, resolver=world.resolver
    )

    assert (selected.provider_id, selected.model_id) == (provider_id, OPAQUE_MODEL)
    assert projection.request_facts is not None
    assert projection.request_facts.max_reference_images == 9
    assert len(projection.request_assets) == 2
    assert [p["code"] for p in projection.problem_payloads()] == ["provider_asset_link_failed"]
    assert backend.model == OPAQUE_MODEL
    assert backend.video_capabilities.max_reference_images == 9
    assert backend.video_capabilities_for_tier("default").max_reference_images == 9


async def test_opaque_custom_model_without_binding_keeps_the_conservative_capabilities(world: _World):
    """未绑定条目的不透明模型 ID 按保守回落：尾帧与参考音频不开；参考图上限只受通道约束（docs/adr/0104）。

    方舟通道实现了参考图且不设上限，参考生视频因而可选，上限为未收录：执行时不裁剪，超出部分由上游拒绝。
    """
    db_id = await world.custom_provider([{"model_id": OPAQUE_MODEL, "is_default": True}])
    provider_id = make_provider_id(db_id)

    selected = await world.resolver.resolve_video_backend(
        {"video_provider_r2v": f"{provider_id}/{OPAQUE_MODEL}"}, None, generation_type="r2v"
    )
    backend = await assemble_backend(
        provider_id=provider_id, media_type="video", model_id=OPAQUE_MODEL, resolver=world.resolver
    )
    caps = backend.video_capabilities_for_tier("default")
    assert (selected.provider_id, selected.model_id) == (provider_id, OPAQUE_MODEL)
    assert caps.max_reference_images is None
    assert caps.last_frame is False
    assert caps.max_reference_audio_count == 0


async def test_explicit_capability_override_still_wins_over_the_bound_entry(world: _World):
    """用户的能力覆盖优先于绑定条目给出的通道能力。"""
    db_id = await world.custom_provider(
        [
            {
                "model_id": OPAQUE_MODEL,
                "is_default": True,
                "entry_binding": "bytedance/doubao-seedance-2.0",
                "capability_overrides": {"max_reference_images": 3},
            }
        ]
    )
    provider_id = make_provider_id(db_id)

    caps = await world.resolver.video_capabilities_for_model(provider_id, OPAQUE_MODEL, generation_type="r2v")
    backend = await assemble_backend(
        provider_id=provider_id, media_type="video", model_id=OPAQUE_MODEL, resolver=world.resolver
    )
    assert caps["max_reference_images"] == 3
    assert backend.video_capabilities_for_tier("default").max_reference_images == 3


async def test_user_added_builtin_model_bound_to_seedance_gets_the_entry_video_capabilities(world: _World):
    """内置方舟下用户添加的不透明模型 ID 绑定 Seedance 2.0 条目：读侧与执行侧都按条目取得参考图能力。"""
    async with world.sessions() as session:
        await CredentialRepository(session).create(provider="ark", name="主 Key", api_key="TEST-ark-inference-key-0001")
        await session.commit()
    catalog.install_user_added_models(
        [catalog.UserAddedModel("ark", OPAQUE_MODEL, "video", entry_binding="bytedance/doubao-seedance-2.0")]
    )
    try:
        projection = await _configured_projection(world, "ark", OPAQUE_MODEL)
        backend = await assemble_backend(
            provider_id="ark", media_type="video", model_id=OPAQUE_MODEL, resolver=world.resolver
        )
        # 执行闸门同一口径：调用通道的声明与绑定条目两侧求值
        executed = effective_builtin_video_capabilities(resolve_video_capabilities(backend), "ark", OPAQUE_MODEL)
    finally:
        catalog.install_user_added_models(())

    assert projection.request_facts is not None
    assert projection.request_facts.max_reference_images == 9
    assert len(projection.request_assets) == 2
    assert backend.model == OPAQUE_MODEL
    assert executed.max_reference_images == 9
