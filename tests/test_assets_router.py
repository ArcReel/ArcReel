"""assets 路由基础 CRUD 测试。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from lib.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.routers import assets


@pytest.fixture
async def _assets_env(tmp_path, monkeypatch):
    # 1) per-test in-memory DB
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # 2) per-test ProjectManager pointed at tmp_path/projects
    pm = ProjectManager(tmp_path / "projects")

    # 3) monkeypatch symbols used inside assets router
    monkeypatch.setattr(assets, "async_session_factory", factory)
    monkeypatch.setattr(assets, "pm", pm)

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(assets.router, prefix="/api/v1")

    yield {"client": TestClient(app), "pm": pm}
    await engine.dispose()


class TestAssetsCRUD:
    def test_create_and_list(self, _assets_env):
        client = _assets_env["client"]
        r = client.post(
            "/api/v1/assets",
            data={"type": "character", "name": "王小明", "description": "白衣少年"},
        )
        assert r.status_code == 200, r.text
        asset_id = r.json()["asset"]["id"]
        assert asset_id

        r2 = client.get("/api/v1/assets?type=character")
        assert r2.status_code == 200
        assert len(r2.json()["items"]) == 1
        assert r2.json()["items"][0]["id"] == asset_id

    def test_duplicate_type_name_returns_409(self, _assets_env):
        client = _assets_env["client"]
        r1 = client.post("/api/v1/assets", data={"type": "prop", "name": "玉佩"})
        assert r1.status_code == 200, r1.text
        r = client.post("/api/v1/assets", data={"type": "prop", "name": "玉佩"})
        assert r.status_code == 409

    def test_patch_and_delete(self, _assets_env):
        client = _assets_env["client"]
        r = client.post("/api/v1/assets", data={"type": "scene", "name": "A"})
        assert r.status_code == 200, r.text
        aid = r.json()["asset"]["id"]

        r2 = client.patch(f"/api/v1/assets/{aid}", json={"description": "new"})
        assert r2.status_code == 200
        assert r2.json()["asset"]["description"] == "new"

        r3 = client.delete(f"/api/v1/assets/{aid}")
        assert r3.status_code == 204

        r4 = client.get(f"/api/v1/assets/{aid}")
        assert r4.status_code == 404

    def test_invalid_type_returns_400(self, _assets_env):
        client = _assets_env["client"]
        r = client.post("/api/v1/assets", data={"type": "invalid", "name": "X"})
        assert r.status_code == 400

    def test_list_filters_by_q(self, _assets_env):
        client = _assets_env["client"]
        client.post("/api/v1/assets", data={"type": "character", "name": "王小明"})
        client.post("/api/v1/assets", data={"type": "character", "name": "李小红"})
        r = client.get("/api/v1/assets?type=character&q=小明")
        assert r.status_code == 200
        assert len(r.json()["items"]) == 1

    def test_create_conflict_does_not_leave_orphan_file(self, _assets_env):
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        img_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

        # First create: succeeds
        r1 = client.post(
            "/api/v1/assets",
            data={"type": "prop", "name": "玉佩"},
            files={"image": ("a.png", img_bytes, "image/png")},
        )
        assert r1.status_code == 200

        global_dir = pm.get_global_assets_root() / "prop"
        files_after_first = list(global_dir.iterdir())

        # Duplicate create with image: must 409 AND not increase file count
        r2 = client.post(
            "/api/v1/assets",
            data={"type": "prop", "name": "玉佩"},
            files={"image": ("b.png", img_bytes, "image/png")},
        )
        assert r2.status_code == 409
        files_after_dup = list(global_dir.iterdir())
        assert len(files_after_dup) == len(files_after_first), "duplicate upload must not leave orphan files"

    def test_replace_image(self, _assets_env):
        client = _assets_env["client"]
        r = client.post("/api/v1/assets", data={"type": "scene", "name": "A"})
        aid = r.json()["asset"]["id"]

        img = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128
        r2 = client.post(
            f"/api/v1/assets/{aid}/image",
            files={"image": ("pic.png", img, "image/png")},
        )
        assert r2.status_code == 200
        assert r2.json()["asset"]["image_path"] is not None

    def test_replace_image_invalid_format_preserves_old_image(self, _assets_env):
        """If new upload fails validation, old image must NOT be deleted."""
        client = _assets_env["client"]
        pm = _assets_env["pm"]

        # create asset with a valid image
        img = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        r = client.post(
            "/api/v1/assets",
            data={"type": "scene", "name": "X"},
            files={"image": ("a.png", img, "image/png")},
        )
        assert r.status_code == 200
        old_rel = r.json()["asset"]["image_path"]
        assert old_rel
        assert (pm.projects_root / old_rel).exists()

        aid = r.json()["asset"]["id"]

        # try replacing with unsupported format → 415, old file must still exist
        bad = b"garbage"
        r2 = client.post(
            f"/api/v1/assets/{aid}/image",
            files={"image": ("bad.exe", bad, "application/octet-stream")},
        )
        assert r2.status_code == 415
        assert (pm.projects_root / old_rel).exists(), "old image deleted on failed replace"


class TestFromProject:
    def test_from_project_copies_image(self, _assets_env):
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        # 造 project + character + sheet 文件
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo")
        pm.add_project_character("demo", "王", "d", "")
        sheet_rel = "characters/王.png"
        (pm.projects_root / "demo" / "characters").mkdir(parents=True, exist_ok=True)
        (pm.projects_root / "demo" / sheet_rel).write_bytes(b"img")

        def _set_sheet(project):
            project["characters"]["王"]["character_sheet"] = sheet_rel

        pm.update_project("demo", _set_sheet)

        r = client.post(
            "/api/v1/assets/from-project",
            json={
                "project_name": "demo",
                "resource_type": "character",
                "resource_id": "王",
            },
        )
        assert r.status_code == 200, r.text
        ip = r.json()["asset"]["image_path"]
        assert ip and ip.startswith("_global_assets/character/")
        # 落盘文件与源文件相同字节
        assert (pm.projects_root / ip).read_bytes() == b"img"

    def test_from_project_conflict_409_and_overwrite(self, _assets_env):
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo")
        pm.add_project_character("demo", "王", "d", "")

        r1 = client.post(
            "/api/v1/assets/from-project",
            json={
                "project_name": "demo",
                "resource_type": "character",
                "resource_id": "王",
            },
        )
        assert r1.status_code == 200, r1.text

        r2 = client.post(
            "/api/v1/assets/from-project",
            json={
                "project_name": "demo",
                "resource_type": "character",
                "resource_id": "王",
            },
        )
        assert r2.status_code == 409

        r3 = client.post(
            "/api/v1/assets/from-project",
            json={
                "project_name": "demo",
                "resource_type": "character",
                "resource_id": "王",
                "overwrite": True,
            },
        )
        assert r3.status_code == 200

    def test_from_project_invalid_type_returns_400(self, _assets_env):
        client = _assets_env["client"]
        r = client.post(
            "/api/v1/assets/from-project",
            json={
                "project_name": "demo",
                "resource_type": "invalid",
                "resource_id": "X",
            },
        )
        assert r.status_code == 400

    def test_from_project_missing_project_returns_404(self, _assets_env):
        client = _assets_env["client"]
        r = client.post(
            "/api/v1/assets/from-project",
            json={
                "project_name": "nonexistent",
                "resource_type": "character",
                "resource_id": "X",
            },
        )
        assert r.status_code == 404

    def test_from_project_missing_resource_returns_404(self, _assets_env):
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo")

        r = client.post(
            "/api/v1/assets/from-project",
            json={
                "project_name": "demo",
                "resource_type": "character",
                "resource_id": "ghost",
            },
        )
        assert r.status_code == 404

    def test_from_project_without_sheet_has_null_image_path(self, _assets_env):
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo")
        pm.add_project_character("demo", "王", "d", "")
        # No character_sheet set

        r = client.post(
            "/api/v1/assets/from-project",
            json={
                "project_name": "demo",
                "resource_type": "character",
                "resource_id": "王",
            },
        )
        assert r.status_code == 200
        assert r.json()["asset"]["image_path"] is None


class TestApplyToProject:
    def test_apply_with_skip_policy(self, _assets_env):
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("target")
        pm.create_project_metadata("target", "Target")

        # Create 2 scene assets in library
        for n in ("A", "B"):
            client.post("/api/v1/assets", data={"type": "scene", "name": n})
        ids = [a["id"] for a in client.get("/api/v1/assets?type=scene").json()["items"]]

        r = client.post(
            "/api/v1/assets/apply-to-project",
            json={
                "asset_ids": ids,
                "target_project": "target",
                "conflict_policy": "skip",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["succeeded"]) == 2
        data = pm.load_project("target")
        assert set(data["scenes"].keys()) == {"A", "B"}

        # Second round: duplicates, skip all
        r2 = client.post(
            "/api/v1/assets/apply-to-project",
            json={
                "asset_ids": ids,
                "target_project": "target",
                "conflict_policy": "skip",
            },
        )
        body2 = r2.json()
        assert len(body2["succeeded"]) == 0
        assert len(body2["skipped"]) == 2

    def test_rename_policy_adds_numeric_suffix(self, _assets_env):
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("target")
        pm.create_project_metadata("target", "Target")

        client.post("/api/v1/assets", data={"type": "prop", "name": "玉佩"})
        aid = client.get("/api/v1/assets?type=prop").json()["items"][0]["id"]

        # Apply first — creates "玉佩"
        client.post(
            "/api/v1/assets/apply-to-project",
            json={
                "asset_ids": [aid],
                "target_project": "target",
                "conflict_policy": "rename",
            },
        )
        # Apply again with rename — creates "玉佩 (2)"
        r = client.post(
            "/api/v1/assets/apply-to-project",
            json={
                "asset_ids": [aid],
                "target_project": "target",
                "conflict_policy": "rename",
            },
        )
        assert r.status_code == 200
        assert r.json()["succeeded"][0]["name"] == "玉佩 (2)"
        data = pm.load_project("target")
        assert "玉佩" in data["props"] and "玉佩 (2)" in data["props"]

    def test_overwrite_policy_replaces_existing(self, _assets_env):
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("target")
        pm.create_project_metadata("target", "Target")

        r0 = client.post(
            "/api/v1/assets",
            data={"type": "character", "name": "王", "description": "library desc"},
        )
        aid = r0.json()["asset"]["id"]

        # Pre-populate target with a different "王"
        pm.add_project_character("target", "王", "old desc", "")

        r = client.post(
            "/api/v1/assets/apply-to-project",
            json={
                "asset_ids": [aid],
                "target_project": "target",
                "conflict_policy": "overwrite",
            },
        )
        assert r.status_code == 200
        assert len(r.json()["succeeded"]) == 1
        data = pm.load_project("target")
        assert data["characters"]["王"]["description"] == "library desc"

    def test_invalid_policy_returns_400(self, _assets_env):
        client = _assets_env["client"]
        r = client.post(
            "/api/v1/assets/apply-to-project",
            json={
                "asset_ids": [],
                "target_project": "target",
                "conflict_policy": "nope",
            },
        )
        assert r.status_code == 400

    def test_missing_project_returns_404(self, _assets_env):
        client = _assets_env["client"]
        r = client.post(
            "/api/v1/assets/apply-to-project",
            json={
                "asset_ids": [],
                "target_project": "nonexistent",
                "conflict_policy": "skip",
            },
        )
        assert r.status_code == 404

    def test_unknown_asset_id_listed_in_failed(self, _assets_env):
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("target")
        pm.create_project_metadata("target", "Target")

        r = client.post(
            "/api/v1/assets/apply-to-project",
            json={
                "asset_ids": ["00000000-0000-0000-0000-000000000000"],
                "target_project": "target",
                "conflict_policy": "skip",
            },
        )
        assert r.status_code == 200
        assert len(r.json()["failed"]) == 1
        assert r.json()["failed"][0]["reason"] == "not_found"

    def test_image_missing_adds_to_failed(self, _assets_env):
        """If asset.image_path is set but the file on disk is gone, record as failed."""
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("target")
        pm.create_project_metadata("target", "Target")

        # Create asset with image
        img = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        r0 = client.post(
            "/api/v1/assets",
            data={"type": "scene", "name": "A"},
            files={"image": ("a.png", img, "image/png")},
        )
        aid = r0.json()["asset"]["id"]
        rel = r0.json()["asset"]["image_path"]

        # Simulate external deletion of the global file
        (pm.projects_root / rel).unlink()

        r = client.post(
            "/api/v1/assets/apply-to-project",
            json={
                "asset_ids": [aid],
                "target_project": "target",
                "conflict_policy": "skip",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["failed"]) == 1
        assert body["failed"][0]["id"] == aid
        assert body["failed"][0]["reason"] == "image_missing"
        # project.json should NOT contain the entry
        data = pm.load_project("target")
        assert "A" not in (data.get("scenes") or {})

    def test_image_copied_to_target_project(self, _assets_env):
        """End-to-end: from-project → asset library → apply-to-project copies the image too."""
        client = _assets_env["client"]
        pm = _assets_env["pm"]

        # 1) Create asset with image directly
        img = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        r0 = client.post(
            "/api/v1/assets",
            data={"type": "scene", "name": "A"},
            files={"image": ("a.png", img, "image/png")},
        )
        aid = r0.json()["asset"]["id"]

        # 2) Prepare target project
        pm.create_project("target")
        pm.create_project_metadata("target", "Target")

        # 3) Apply
        r = client.post(
            "/api/v1/assets/apply-to-project",
            json={
                "asset_ids": [aid],
                "target_project": "target",
                "conflict_policy": "skip",
            },
        )
        assert r.status_code == 200
        # File copied to target
        assert (pm.projects_root / "target" / "scenes" / "A.png").exists()
        data = pm.load_project("target")
        assert data["scenes"]["A"]["scene_sheet"] == "scenes/A.png"
