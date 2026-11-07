"""Provider-asset link verification in request projection: shared read projection for task acceptance/batch preview/quotes agrees with execution on identical configuration.

Use real DB credentials, library accounts/bindings/links/custom providers, real version records and production ``assemble_backend``. Only video-request facts use constructed value objects. No network requests."""

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
        """Create a selected library-account association for 张三's current content version."""
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
                models=[{"display_name": m["model_id"], "endpoint": "ark-seedance-2", **m} for m in models],
            )
            await session.commit()
            return provider.id

    async def read_side(self, provider_id: str, model_id: str) -> ReferenceUnitRequestProjection:
        """Acceptance/batch-preview/quote projection verifies links using current-config-assembled backends."""
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
        """Execution projection verifies links using actual lane-constructed backends supplied by executors."""
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
    """Built-in Ark: applicable ingesting links produce identical read/execution blockers naming prompt-image indices; available links block neither."""
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
    ("model", "endpoint", "applies"),
    [
        (OPAQUE_MODEL, "ark-seedance-2", True),
        (OPAQUE_MODEL, "ark-seedance-2-5", True),
        (FORGED_MODEL, "ark-seedance-1", False),
    ],
    ids=["seedance-2", "seedance-2-5", "seedance-1-with-2-0-name"],
)
async def test_custom_endpoint_asset_support_comes_from_the_endpoint(
    world: _World, model: str, endpoint: str, applies: bool
):
    """Custom-provider Seedance asset references depend on bound endpoints (2.0/2.5 support,1.x does not), never model names; read/execution conclusions agree."""
    db_id = await world.custom_provider([{"model_id": model, "is_default": True, "endpoint": endpoint}])
    await world.bind("custom", db_id)
    await world.link(state="failed", error_code="DownloadFailed", error_message="cannot fetch")
    provider_id = make_provider_id(db_id)

    read = await world.read_side(provider_id, model)
    execution = await world.execution_side(provider_id, model)

    assert _link_problems(read) == _link_problems(execution)
    codes = [p["code"] for p in _link_problems(read)]
    assert codes == (["provider_asset_link_failed"] if applies else [])


async def _configured_projection(world: _World, provider_id: str, model_id: str) -> ReferenceUnitRequestProjection:
    """Production read projection resolves project-config request facts through real capabilities without substitutes."""
    project = {**world.project, "video_provider_r2v": f"{provider_id}/{model_id}"}
    return await project_reference_unit_request(
        project=project, script=_SCRIPT, unit=_UNIT, project_path=world.project_path, resolver=world.resolver
    )


async def test_opaque_custom_model_bound_to_seedance_gets_the_entry_video_capabilities(world: _World):
    """Opaque IDs bound to Seedance2.0 permit reference-video, retain both images and verify asset links. Execution declares capabilities from the same entry, preserving original API IDs."""
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
    """Unbound opaque IDs conservatively omit end frames/reference audio; image limits depend only on backend constraints (docs/adr/0104). Ark supports unrestricted reference images, so reference-video remains selectable with unlisted limits: execution does not clip and upstream rejects excess."""
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
    """User capability overrides outrank backend capabilities derived from bound entries."""
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
    """User-added opaque IDs under built-in Ark bound to Seedance2.0 use entry image capabilities on reads and execution."""
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
        # Match execution gates: evaluate both backend declarations and bound entries.
        executed = effective_builtin_video_capabilities(resolve_video_capabilities(backend), "ark", OPAQUE_MODEL)
    finally:
        catalog.install_user_added_models(())

    assert projection.request_facts is not None
    assert projection.request_facts.max_reference_images == 9
    assert len(projection.request_assets) == 2
    assert backend.model == OPAQUE_MODEL
    assert executed.max_reference_images == 9
