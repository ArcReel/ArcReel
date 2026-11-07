"""Postcommit global-library response failures never undo saved images; precommit failures still clean new contents."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.config.repository import SystemSettingRepository
from lib.db.models.asset import Asset
from lib.db.models.object_storage import FormalObject, FormalObjectRef
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.material.formal_objects import MATERIAL_STORAGE_TARGET_SETTING
from tests.fakes import bind_session_factory, use_assets_router_sessions, use_object_storage_sdk


@pytest.mark.parametrize("entry", ["create", "replace", "from-project"])
@pytest.mark.parametrize("failure", ["before_commit", "after_commit"])
async def test_asset_image_compensation_stops_at_commit(assets_env, db_engine, db_factory, monkeypatch, entry, failure):
    objects: dict[str, bytes] = {}
    sdk = SimpleNamespace(
        put_object=lambda *, key, content, **kw: objects.update({key: content}),
        head_object=lambda *, key, **kw: SimpleNamespace(content_length=len(objects[key]), etag="etag"),
        delete_object=lambda *, key, **kw: objects.pop(key, None),
    )
    use_object_storage_sdk(monkeypatch, lambda config: sdk)
    bind_session_factory(monkeypatch, db_factory)
    async with db_factory() as session:
        await ObjectStorageConnectionRepository(session).save(
            kind="tos",
            region="cn-beijing",
            endpoint="tos-cn-beijing.volces.com",
            bucket="test-library",
            access_key_id="TEST-TOS-AK-LIBRARY",
            secret_access_key="TEST-TOS-SECRET-LIBRARY",
        )
        await SystemSettingRepository(session).set(MATERIAL_STORAGE_TARGET_SETTING, "object_storage")
        await session.commit()

    content = b"library-image"
    pm = assets_env["pm"]
    if entry == "from-project":
        pm.create_project("demo")
        pm.create_project_metadata("demo", "demo", "Anime", "narration")
        pm.add_character("demo", "Alice", "character")
        project = pm.load_project("demo")
        project["characters"]["Alice"]["character_sheet"] = "characters/Alice.png"
        pm.save_project("demo", project)
        (pm.get_project_path("demo") / "characters" / "Alice.png").write_bytes(content)

    class FailingAssetSession(AsyncSession):
        async def commit(self):
            if failure == "before_commit" and any(isinstance(row, Asset) for row in self.identity_map.values()):
                raise ConnectionError("asset database unavailable")
            await super().commit()

        async def refresh(self, instance, *args, **kwargs):
            if failure == "after_commit" and isinstance(instance, Asset):
                raise ConnectionError("asset database unavailable")
            await super().refresh(instance, *args, **kwargs)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=assets_env["client"].app), base_url="http://test"
    ) as api:
        asset_id = None
        if entry == "replace":
            response = await api.post("/api/v1/assets", data={"type": "character", "name": "Alice"})
            assert response.status_code == 200, response.text
            asset_id = response.json()["asset"]["id"]
        use_assets_router_sessions(
            monkeypatch, async_sessionmaker(db_engine, class_=FailingAssetSession, expire_on_commit=False)
        )
        if entry == "from-project":
            request = api.post(
                "/api/v1/assets/from-project",
                json={"project_name": "demo", "resource_type": "character", "resource_id": "Alice"},
            )
        elif entry == "replace":
            request = api.post(f"/api/v1/assets/{asset_id}/image", files={"image": ("image.png", content, "image/png")})
        else:
            request = api.post(
                "/api/v1/assets",
                data={"type": "character", "name": "Alice"},
                files={"image": ("image.png", content, "image/png")},
            )
        with pytest.raises(ConnectionError, match="asset database unavailable"):
            await request

    async with db_factory() as session:
        asset = (await session.execute(select(Asset))).scalar_one_or_none()
        refs = list((await session.execute(select(FormalObjectRef))).scalars())
        obj = (await session.execute(select(FormalObject))).scalar_one()
    if failure == "before_commit":
        assert asset is None or asset.image_path is None
        assert not objects
        assert not refs
        assert obj.state == "deleted"
        assert not list(pm.get_global_assets_root().rglob("*.png"))
    else:
        assert asset is not None
        assert asset.image_storage is not None
        assert (pm.data_root / asset.image_path).read_bytes() == content
        assert objects[asset.image_storage["key"]] == content
        assert obj.state == "stored"
        assert [(ref.content_version_id, ref.state) for ref in refs] == [(asset.image_content_id, "active")]
