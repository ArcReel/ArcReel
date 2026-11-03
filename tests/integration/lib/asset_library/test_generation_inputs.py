"""生成时为参考图选用供应商素材关联：按调用通道实际使用的推理凭证与绑定判断，结果与参考图等长同序。

真实数据库（关联、绑定、凭证）与真实版本记录；调用通道只用一个报出推理凭证与素材引用声明的替身。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.artifacts.version_manager import VersionManager
from lib.asset_library.bindings import account_scope, confirm_binding, get_bindable_credential
from lib.asset_library.generation_inputs import (
    ProviderAssetLinkUnavailableError,
    ReferenceImageSource,
    choose_reference_image_assets,
)
from lib.backends.provider_assets import REFERENCE_IMAGE_ROLE, InferenceCredentialRef
from lib.db.models.asset_library import ProviderAssetLink
from lib.db.repositories.asset_library_repo import AssetLibraryRepository
from lib.db.repositories.credential_repository import CredentialRepository
from lib.infra.content_digest import sha256_file
from lib.infra.image_utils import IMAGE_FILE_EXTENSIONS
from lib.material.storage import CONTENT_VERSION_ID

ARK_DEFAULT = "https://ark.cn-beijing.volces.com/api/v3"
API_KEY = "TEST-ark-inference-key-0001"


@dataclass
class _Backend:
    """调用通道替身：只报出实际使用的推理凭证与可接受素材引用的槽位。"""

    inference_credential: InferenceCredentialRef | None
    provider_asset_roles: frozenset[str] = frozenset({REFERENCE_IMAGE_ROLE})


@dataclass
class _World:
    sessions: async_sessionmaker[AsyncSession]
    project_path: Path
    credential_id: int
    account_id: int

    def backend(self, *, api_key: str = API_KEY, roles=frozenset({REFERENCE_IMAGE_ROLE})) -> _Backend:
        return _Backend(
            InferenceCredentialRef(
                credential_type="builtin",
                credential_id=self.credential_id,
                api_key=api_key,
                base_url=None,
                default_base_url=ARK_DEFAULT,
            ),
            roles,
        )

    def commit_image(self, resource_type: str, resource_id: str, data: bytes, *, suffix: str = ".png") -> str:
        """提交一张图片为资源的新当前版本，返回它的内容版本身份。"""
        subdir = {"characters": "characters", "scenes": "scenes"}[resource_type]
        working = self.project_path / subdir / f"{resource_id}{suffix}"
        working.parent.mkdir(parents=True, exist_ok=True)
        working.write_bytes(data)
        manager = VersionManager(self.project_path)
        version = manager.add_version(resource_type, resource_id, "p", source_file=working)
        info = manager.get_versions(resource_type, resource_id)
        record = next(v for v in info["versions"] if v["version"] == version)
        return record[CONTENT_VERSION_ID]

    def source(self, rel: str, name: str) -> ReferenceImageSource:
        return ReferenceImageSource(locator=rel, sha256=sha256_file(self.project_path / rel), name=name)

    async def link(self, content_version_id: str, *, state: str, origin: str = "upload", **extra) -> None:
        async with self.sessions() as session:
            account = await AssetLibraryRepository(session).get_account(self.account_id)
            assert account is not None
            session.add(
                ProviderAssetLink(
                    project_name="demo",
                    content_version_id=content_version_id,
                    content_digest="0" * 64,
                    resource_type="characters",
                    resource_id="Alice",
                    version=1,
                    locator_path="versions/x.png",
                    origin=origin,
                    account_id=account.id,
                    account_identity=account_scope(account).account_identity,
                    site=account.site,
                    region=account.region,
                    provider_project_name=account.project_name,
                    state=state,
                    submit_name="Alice-arc-0000000000000000",
                    selected=True,
                    check_attempts=0,
                    **extra,
                )
            )
            await session.commit()

    async def choose(self, backend: _Backend, sources: list[ReferenceImageSource]):
        return await choose_reference_image_assets(
            self.sessions, backend=backend, project_name="demo", project_path=self.project_path, sources=sources
        )


@pytest.fixture
async def world(db_engine, tmp_path: Path) -> _World:
    sessions = async_sessionmaker(db_engine, expire_on_commit=False)
    project_path = tmp_path / "demo"
    project_path.mkdir()
    async with sessions() as session:
        credential = await CredentialRepository(session).create(provider="ark", name="主 Key", api_key=API_KEY)
        account = await AssetLibraryRepository(session).create_account(
            site="volcengine",
            name="火山",
            region="cn-beijing",
            project_name="default",
            access_key_id="TEST-ACCESS-KEY-ID",
            secret_access_key="TEST-SECRET-ACCESS-KEY",
        )
        bindable = await get_bindable_credential(session, "builtin", credential.id)
        assert bindable is not None
        await confirm_binding(session, account, bindable)
        await session.commit()
        return _World(sessions, project_path, credential.id, account.id)


@pytest.mark.parametrize("origin", ["upload", "import"])
async def test_usable_link_replaces_only_its_image_and_keeps_order(world: _World, origin: str):
    """有可用关联的图片换成素材引用，其余图片照常发送，顺序与提示词里的图片编号一致；上传与导入建立的关联同样适用。"""
    scene = world.commit_image("scenes", "Hall", b"hall-v1")
    alice = world.commit_image("characters", "Alice", b"alice-v1")
    assert scene
    await world.link(alice, state="active", origin=origin, provider_asset_id="asset-alice-1")

    choices = await world.choose(
        world.backend(), [world.source("scenes/Hall.png", "Hall"), world.source("characters/Alice.png", "Alice")]
    )

    assert choices[0] is None
    assert choices[1] is not None
    assert choices[1].asset_uri == "asset://asset-alice-1"
    assert choices[1].content_version_id == alice


@pytest.mark.parametrize("suffix", [s for s in IMAGE_FILE_EXTENSIONS if s != ".png"])
async def test_non_png_asset_sheet_resolves_its_content_version(world: _World, suffix: str):
    """上传或导入保留原格式的资产图（JPEG、WebP）同样按内容版本找到关联，不退回发送普通图片。"""
    alice = world.commit_image("characters", "Alice", b"alice-jpeg", suffix=suffix)
    await world.link(alice, state="active", provider_asset_id="asset-alice-1")

    [choice] = await world.choose(world.backend(), [world.source(f"characters/Alice{suffix}", "Alice")])

    assert choice is not None
    assert choice.asset_uri == "asset://asset-alice-1"
    assert choice.content_version_id == alice


async def test_new_image_does_not_inherit_the_previous_version_link(world: _World):
    """换成新图后没有适用关联，照常发送图片；切回旧图（当前版本即旧内容版本）时旧关联继续有效。"""
    first = world.commit_image("characters", "Alice", b"alice-v1")
    await world.link(first, state="active", provider_asset_id="asset-alice-1")
    world.commit_image("characters", "Alice", b"alice-v2")

    choices = await world.choose(world.backend(), [world.source("characters/Alice.png", "Alice")])

    assert choices == (None,)
    VersionManager(world.project_path).restore_version(
        "characters", "Alice", 1, world.project_path / "characters" / "Alice.png"
    )
    restored = await world.choose(world.backend(), [world.source("characters/Alice.png", "Alice")])
    assert restored[0] is not None
    assert restored[0].asset_uri == "asset://asset-alice-1"


@pytest.mark.parametrize(
    ("state", "extra", "code"),
    [
        ("processing", {"provider_asset_id": "asset-alice-1"}, "provider_asset_link_processing"),
        ("uncertain", {}, "provider_asset_link_processing"),
        (
            "failed",
            {"error_code": "DownloadFailed", "error_message": "cannot fetch"},
            "provider_asset_link_failed",
        ),
    ],
)
async def test_applicable_link_that_is_not_usable_fails_instead_of_sending_the_image(
    world: _World, state: str, extra: dict, code: str
):
    """选定的入库任务处理中、结果待确认或失败时明确失败，按提示词里的图片编号点名，不改发普通图片。"""
    world.commit_image("scenes", "Hall", b"hall-v1")
    alice = world.commit_image("characters", "Alice", b"alice-v1")
    await world.link(alice, state=state, **extra)

    with pytest.raises(ProviderAssetLinkUnavailableError) as excinfo:
        await world.choose(
            world.backend(), [world.source("scenes/Hall.png", "Hall"), world.source("characters/Alice.png", "Alice")]
        )

    assert excinfo.value.code == code
    assert excinfo.value.params["index"] == 2
    assert excinfo.value.params["name"] == "Alice"
    if state == "failed":
        assert excinfo.value.params["error_code"] == "DownloadFailed"


async def test_link_is_not_applicable_after_credential_change_or_on_models_without_asset_support(world: _World):
    """推理凭证内容变了（绑定待确认）或模型不接受素材引用：关联不适用，照常发送图片，处理中的关联也不阻断。"""
    alice = world.commit_image("characters", "Alice", b"alice-v1")
    await world.link(alice, state="processing", provider_asset_id="asset-alice-1")
    sources = [world.source("characters/Alice.png", "Alice")]

    assert await world.choose(world.backend(api_key="TEST-another-account-key"), sources) == (None,)
    assert await world.choose(world.backend(roles=frozenset()), sources) == (None,)
    assert await world.choose(_Backend(inference_credential=None), sources) == (None,)
