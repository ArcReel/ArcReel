"""assets 路由基础 CRUD 测试。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.artifact_activation import ArtifactCurrencyResolver
from lib.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ArtifactStatus, ProjectArtifactManifestAdapter
from lib.db.base import Base
from lib.i18n import _ as translate_message
from lib.project_manager import ProjectManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import assets
from tests.auth_deps import AUTH_DEPENDENCIES


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
    monkeypatch.setattr(assets, "get_project_manager", lambda: pm)

    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(assets.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)

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

    def test_product_type_excluded_from_global_library(self, _assets_env):
        """product 是多图列表型资产，单图列模型的全局库不收：create 与 from-project 均 400。"""
        client = _assets_env["client"]
        r = client.post("/api/v1/assets", data={"type": "product", "name": "保温杯"})
        assert r.status_code == 400

        r2 = client.post(
            "/api/v1/assets/from-project",
            json={
                "project_name": "demo",
                "resource_type": "product",
                "resource_id": "保温杯",
            },
        )
        assert r2.status_code == 400

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

    def test_from_project_missing_resource_error_localizes_kind(self, _assets_env):
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo")

        zh = client.post(
            "/api/v1/assets/from-project",
            json={"project_name": "demo", "resource_type": "character", "resource_id": "ghost"},
            headers={"Accept-Language": "zh"},
        )
        en = client.post(
            "/api/v1/assets/from-project",
            json={"project_name": "demo", "resource_type": "character", "resource_id": "ghost"},
            headers={"Accept-Language": "en"},
        )
        vi = client.post(
            "/api/v1/assets/from-project",
            json={"project_name": "demo", "resource_type": "character", "resource_id": "ghost"},
            headers={"Accept-Language": "vi"},
        )

        assert "角色" in zh.json()["detail"] and "character" not in zh.json()["detail"]
        # en 显示名与内部标识同形，区分不了裸透传，只能断言整句按 asset_type_* 渲染
        assert en.json()["detail"] == translate_message(
            "asset_source_resource_not_found",
            locale="en",
            project="demo",
            kind=translate_message("asset_type_character", locale="en"),
            name="ghost",
        )
        assert "nhân vật" in vi.json()["detail"] and "character" not in vi.json()["detail"]

    def test_from_project_copies_audio(self, _assets_env):
        """character 的 reference_audio 随 character_sheet 一起复制到全局资产库。"""
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo")
        pm.add_project_character("demo", "王", "d", "")
        audio_rel = "characters/refs_audio/王.wav"
        (pm.projects_root / "demo" / "characters" / "refs_audio").mkdir(parents=True, exist_ok=True)
        (pm.projects_root / "demo" / audio_rel).write_bytes(b"audio-bytes")
        pm.update_character_reference_audio("demo", "王", audio_rel)

        r = client.post(
            "/api/v1/assets/from-project",
            json={
                "project_name": "demo",
                "resource_type": "character",
                "resource_id": "王",
            },
        )
        assert r.status_code == 200, r.text
        ap = r.json()["asset"]["audio_path"]
        assert ap and ap.startswith("_global_assets/character/")
        assert (pm.projects_root / ap).read_bytes() == b"audio-bytes"

    def test_from_project_audio_copy_failure_cleans_up_image(self, _assets_env, monkeypatch):
        """图片拷贝成功后音频拷贝失败：不留孤儿图片文件，异常正常传播。"""
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo")
        pm.add_project_character("demo", "王", "d", "")
        sheet_rel = "characters/王.png"
        (pm.projects_root / "demo" / "characters").mkdir(parents=True, exist_ok=True)
        (pm.projects_root / "demo" / sheet_rel).write_bytes(b"img")
        audio_rel = "characters/refs_audio/王.wav"
        (pm.projects_root / "demo" / "characters" / "refs_audio").mkdir(parents=True, exist_ok=True)
        (pm.projects_root / "demo" / audio_rel).write_bytes(b"audio-bytes")

        def _set_fields(project):
            project["characters"]["王"]["character_sheet"] = sheet_rel
            project["characters"]["王"]["reference_audio"] = audio_rel

        pm.update_project("demo", _set_fields)

        real_copyfile = assets.shutil.copyfile
        calls = {"n": 0}

        def flaky_copyfile(src, dst):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("disk full")
            return real_copyfile(src, dst)

        monkeypatch.setattr(assets.shutil, "copyfile", flaky_copyfile)

        with pytest.raises(OSError):
            client.post(
                "/api/v1/assets/from-project",
                json={"project_name": "demo", "resource_type": "character", "resource_id": "王"},
            )

        global_character_assets = pm.get_global_assets_root() / "character"
        leftover = list(global_character_assets.glob("*")) if global_character_assets.exists() else []
        assert leftover == []

    def test_from_project_ignores_reference_audio_outside_refs_audio_dir(self, _assets_env):
        """reference_audio 可经通用角色 PATCH 被写成项目内任意字符串；仅路径不越界
        不足以防止把 project.json 等其它项目文件当作音频复制进全局库，须额外确认
        父目录命中 characters/refs_audio（与 files.py::_resolve_audio_ref_path 同口径）。"""
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo")
        pm.add_project_character("demo", "王", "d", "")

        def _set_fields(project):
            # 项目内真实存在、但不在 characters/refs_audio 下的文件——模拟经通用 PATCH
            # 写入的越权路径。
            project["characters"]["王"]["reference_audio"] = "project.json"

        pm.update_project("demo", _set_fields)

        r = client.post(
            "/api/v1/assets/from-project",
            json={"project_name": "demo", "resource_type": "character", "resource_id": "王"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["asset"]["audio_path"] is None

    def test_from_project_without_audio_has_null_audio_path(self, _assets_env):
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo")
        pm.add_project_character("demo", "王", "d", "")
        # No reference_audio set

        r = client.post(
            "/api/v1/assets/from-project",
            json={
                "project_name": "demo",
                "resource_type": "character",
                "resource_id": "王",
            },
        )
        assert r.status_code == 200
        assert r.json()["asset"]["audio_path"] is None

    def test_from_project_missing_audio_file_degrades_quietly(self, _assets_env):
        """reference_audio 字段指向不存在的文件时静默降级为无音频，不中断入库。"""
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo")
        pm.add_project_character("demo", "王", "d", "")
        pm.update_character_reference_audio("demo", "王", "characters/refs_audio/ghost.wav")

        r = client.post(
            "/api/v1/assets/from-project",
            json={
                "project_name": "demo",
                "resource_type": "character",
                "resource_id": "王",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["asset"]["audio_path"] is None

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

    def test_overwrite_policy_registers_the_imported_formal_sheet_claim(self, _assets_env):
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("target")
        pm.create_project_metadata("target", "Target")
        pm.add_project_scene("target", "A", "same description")
        pm.install_asset_sheet_bytes("scene", "target", "A", "scenes/A.png", b"old-sheet")
        project_dir = pm.get_project_path("target")
        key = ArtifactKey.asset_sheet("scene", "A")
        adapter = ProjectArtifactManifestAdapter(project_dir)
        adapter.put_entry(
            key,
            ArtifactManifestEntry("scenes/A.png", "sha256-v1:" + "a" * 64),
        )
        created = client.post(
            "/api/v1/assets",
            data={"type": "scene", "name": "A", "description": "same description"},
            files={"image": ("A.png", b"library-sheet", "image/png")},
        )

        response = client.post(
            "/api/v1/assets/apply-to-project",
            json={
                "asset_ids": [created.json()["asset"]["id"]],
                "target_project": "target",
                "conflict_policy": "overwrite",
            },
        )

        assert response.status_code == 200
        assert (project_dir / "scenes" / "A.png").read_bytes() == b"library-sheet"
        assert adapter.get_entry(key) is not None
        assert (
            ArtifactCurrencyResolver(project_dir).compare(key, artifact_path="scenes/A.png").status
            is ArtifactStatus.CURRENT
        )

    def test_rename_policy_registers_the_imported_formal_sheet_claim(self, _assets_env):
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("target")
        pm.create_project_metadata("target", "Target")
        pm.add_project_scene("target", "A", "existing description")
        created = client.post(
            "/api/v1/assets",
            data={"type": "scene", "name": "A", "description": "library description"},
            files={"image": ("A.png", b"library-sheet", "image/png")},
        )

        response = client.post(
            "/api/v1/assets/apply-to-project",
            json={
                "asset_ids": [created.json()["asset"]["id"]],
                "target_project": "target",
                "conflict_policy": "rename",
            },
        )

        assert response.status_code == 200
        assert response.json()["succeeded"] == [{"id": created.json()["asset"]["id"], "name": "A (2)"}]
        project_dir = pm.get_project_path("target")
        key = ArtifactKey.asset_sheet("scene", "A (2)")
        assert (project_dir / "scenes" / "A (2).png").read_bytes() == b"library-sheet"
        assert (
            ArtifactCurrencyResolver(project_dir).compare(key, artifact_path="scenes/A (2).png").status
            is ArtifactStatus.CURRENT
        )

    def test_overwrite_policy_rolls_back_when_the_sheet_claim_commit_fails(self, _assets_env, monkeypatch):
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("target")
        pm.create_project_metadata("target", "Target")
        pm.add_project_scene("target", "A", "old description")
        pm.install_asset_sheet_bytes("scene", "target", "A", "scenes/A.png", b"old-sheet")
        project_dir = pm.get_project_path("target")
        key = ArtifactKey.asset_sheet("scene", "A")
        frozen = ArtifactManifestEntry("scenes/A.png", "sha256-v1:" + "a" * 64)
        adapter = ProjectArtifactManifestAdapter(project_dir)
        adapter.put_entry(key, frozen)
        created = client.post(
            "/api/v1/assets",
            data={"type": "scene", "name": "A", "description": "library description"},
            files={"image": ("A.png", b"library-sheet", "image/png")},
        )

        def _fail_claim_commit(*_args, **_kwargs):
            raise RuntimeError("injected claim failure")

        monkeypatch.setattr(assets, "register_artifact_entries_atomically", _fail_claim_commit)
        with pytest.raises(RuntimeError, match="injected claim failure"):
            client.post(
                "/api/v1/assets/apply-to-project",
                json={
                    "asset_ids": [created.json()["asset"]["id"]],
                    "target_project": "target",
                    "conflict_policy": "overwrite",
                },
            )

        assert (project_dir / "scenes" / "A.png").read_bytes() == b"old-sheet"
        assert pm.load_project("target")["scenes"]["A"]["description"] == "old description"
        assert ProjectArtifactManifestAdapter(project_dir).get_entry(key) == frozen

    def test_duplicate_overwrite_asset_id_is_idempotent(self, _assets_env):
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("target")
        pm.create_project_metadata("target", "Target")
        created = client.post(
            "/api/v1/assets",
            data={"type": "scene", "name": "A"},
            files={"image": ("A.png", b"image", "image/png")},
        )
        asset_id = created.json()["asset"]["id"]

        response = client.post(
            "/api/v1/assets/apply-to-project",
            json={
                "asset_ids": [asset_id, asset_id],
                "target_project": "target",
                "conflict_policy": "overwrite",
            },
        )

        assert response.status_code == 200
        assert response.json()["succeeded"] == [{"id": asset_id, "name": "A"}]
        assert (pm.projects_root / "target" / "scenes" / "A.png").read_bytes() == b"image"

    def test_overwrite_policy_rejects_cross_type_name(self, _assets_env):
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("target")
        pm.create_project_metadata("target", "Target")
        pm.add_project_character("target", "Shared", "character", "")
        created = client.post("/api/v1/assets", data={"type": "scene", "name": "Shared"})

        response = client.post(
            "/api/v1/assets/apply-to-project",
            json={
                "asset_ids": [created.json()["asset"]["id"]],
                "target_project": "target",
                "conflict_policy": "overwrite",
            },
        )

        assert response.status_code == 200
        assert response.json()["failed"] == [{"id": created.json()["asset"]["id"], "reason": "project_name_conflict"}]
        assert pm.load_project("target")["scenes"] == {}

    def test_rename_policy_uses_project_wide_occupancy(self, _assets_env):
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("target")
        pm.create_project_metadata("target", "Target")
        pm.add_project_character("target", "Shared", "character", "")
        pm.add_prop("target", "Shared (2)", "prop")
        created = client.post("/api/v1/assets", data={"type": "scene", "name": "Shared"})

        response = client.post(
            "/api/v1/assets/apply-to-project",
            json={
                "asset_ids": [created.json()["asset"]["id"]],
                "target_project": "target",
                "conflict_policy": "rename",
            },
        )

        assert response.status_code == 200
        assert response.json()["succeeded"][0]["name"] == "Shared (3)"
        assert "Shared (3)" in pm.load_project("target")["scenes"]

    @pytest.mark.parametrize(
        ("locale", "localized_fragment"),
        [("zh", "不能同名"), ("en", "unique names"), ("vi", "tên duy nhất")],
    )
    def test_concurrent_cross_type_conflict_returns_localized_409(
        self, _assets_env, monkeypatch, locale, localized_fragment
    ):
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("target")
        pm.create_project_metadata("target", "Target")
        created = client.post("/api/v1/assets", data={"type": "scene", "name": "Shared"})
        original_update = pm.update_project
        original_transaction = pm.update_project_with_file_copies
        injected = False

        def racing_transaction(project_name, mutate, copies, *, on_commit=None):
            nonlocal injected
            if not injected:
                injected = True
                original_update(
                    project_name,
                    lambda project: project["characters"].update({"Shared": {"description": "character"}}),
                )
            return original_transaction(project_name, mutate, copies, on_commit=on_commit)

        monkeypatch.setattr(pm, "update_project_with_file_copies", racing_transaction)

        response = client.post(
            "/api/v1/assets/apply-to-project",
            json={
                "asset_ids": [created.json()["asset"]["id"]],
                "target_project": "target",
                "conflict_policy": "overwrite",
            },
            headers={"Accept-Language": locale},
        )

        assert response.status_code == 409
        assert "Shared" in response.json()["detail"]
        assert localized_fragment in response.json()["detail"]
        assert pm.load_project("target")["scenes"] == {}

    def test_concurrent_same_type_conflict_reapplies_skip_inside_lock(self, _assets_env, monkeypatch):
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("target")
        pm.create_project_metadata("target", "Target")
        created = client.post("/api/v1/assets", data={"type": "scene", "name": "Shared", "description": "library"})
        original_update = pm.update_project
        original_transaction = pm.update_project_with_file_copies

        def racing_transaction(project_name, mutate, copies, *, on_commit=None):
            original_update(
                project_name,
                lambda project: project["scenes"].update({"Shared": {"description": "concurrent"}}),
            )
            return original_transaction(project_name, mutate, copies, on_commit=on_commit)

        monkeypatch.setattr(pm, "update_project_with_file_copies", racing_transaction)

        response = client.post(
            "/api/v1/assets/apply-to-project",
            json={
                "asset_ids": [created.json()["asset"]["id"]],
                "target_project": "target",
                "conflict_policy": "skip",
            },
        )

        assert response.status_code == 200
        assert response.json()["succeeded"] == []
        assert response.json()["skipped"] == [{"id": created.json()["asset"]["id"], "name": "Shared"}]
        assert pm.load_project("target")["scenes"]["Shared"]["description"] == "concurrent"

    def test_concurrent_same_type_conflict_reapplies_rename_inside_lock(self, _assets_env, monkeypatch):
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("target")
        pm.create_project_metadata("target", "Target")
        created = client.post(
            "/api/v1/assets",
            data={"type": "scene", "name": "Shared", "description": "library"},
            files={"image": ("Shared.png", b"library-image", "image/png")},
        )
        original_update = pm.update_project
        original_transaction = pm.update_project_with_file_copies

        def racing_transaction(project_name, mutate, copies, *, on_commit=None):
            original_update(
                project_name,
                lambda project: project["scenes"].update({"Shared": {"description": "concurrent"}}),
            )
            return original_transaction(project_name, mutate, copies, on_commit=on_commit)

        monkeypatch.setattr(pm, "update_project_with_file_copies", racing_transaction)

        response = client.post(
            "/api/v1/assets/apply-to-project",
            json={
                "asset_ids": [created.json()["asset"]["id"]],
                "target_project": "target",
                "conflict_policy": "rename",
            },
        )

        assert response.status_code == 200
        assert response.json()["succeeded"] == [{"id": created.json()["asset"]["id"], "name": "Shared (2)"}]
        project = pm.load_project("target")
        assert project["scenes"]["Shared"]["description"] == "concurrent"
        assert project["scenes"]["Shared (2)"]["description"] == "library"
        assert (pm.projects_root / "target" / "scenes" / "Shared (2).png").read_bytes() == b"library-image"

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

    def test_audio_missing_adds_to_failed(self, _assets_env):
        """asset.audio_path 有值但磁盘文件缺失时记 failed，不中断整批（与 image_path 同口径）。"""
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("source")
        pm.create_project_metadata("source", "Source")
        pm.add_project_character("source", "王", "d", "")
        audio_rel = "characters/refs_audio/王.wav"
        (pm.projects_root / "source" / "characters" / "refs_audio").mkdir(parents=True, exist_ok=True)
        (pm.projects_root / "source" / audio_rel).write_bytes(b"audio-bytes")
        pm.update_character_reference_audio("source", "王", audio_rel)

        r0 = client.post(
            "/api/v1/assets/from-project",
            json={"project_name": "source", "resource_type": "character", "resource_id": "王"},
        )
        aid = r0.json()["asset"]["id"]
        ap = r0.json()["asset"]["audio_path"]

        # Simulate external deletion of the global audio file
        (pm.projects_root / ap).unlink()

        pm.create_project("target")
        pm.create_project_metadata("target", "Target")

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
        assert body["failed"][0]["reason"] == "audio_missing"
        data = pm.load_project("target")
        assert "王" not in (data.get("characters") or {})

    def test_audio_copied_to_target_project(self, _assets_env):
        """端到端：from-project → 资产库 → apply-to-project 把音频也随图一起复制回项目。"""
        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("source")
        pm.create_project_metadata("source", "Source")
        pm.add_project_character("source", "王", "d", "")
        audio_rel = "characters/refs_audio/王.wav"
        (pm.projects_root / "source" / "characters" / "refs_audio").mkdir(parents=True, exist_ok=True)
        (pm.projects_root / "source" / audio_rel).write_bytes(b"audio-bytes")
        pm.update_character_reference_audio("source", "王", audio_rel)

        r0 = client.post(
            "/api/v1/assets/from-project",
            json={"project_name": "source", "resource_type": "character", "resource_id": "王"},
        )
        aid = r0.json()["asset"]["id"]

        pm.create_project("target")
        pm.create_project_metadata("target", "Target")

        r = client.post(
            "/api/v1/assets/apply-to-project",
            json={
                "asset_ids": [aid],
                "target_project": "target",
                "conflict_policy": "skip",
            },
        )
        assert r.status_code == 200, r.text
        target_audio = pm.projects_root / "target" / "characters" / "refs_audio" / "王.wav"
        assert target_audio.exists()
        assert target_audio.read_bytes() == b"audio-bytes"
        data = pm.load_project("target")
        assert data["characters"]["王"]["reference_audio"] == "characters/refs_audio/王.wav"
        # 「资产即开关」：导入即视为该项目新设置了这个声音，存量过渡横幅计数须能感知到
        assert data["characters"]["王"]["voice_updated_at"]

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

    def test_multi_file_copy_failure_rolls_back_batch(self, _assets_env, monkeypatch):
        from lib import project_manager as project_manager_module

        client = _assets_env["client"]
        pm = _assets_env["pm"]
        pm.create_project("target")
        pm.create_project_metadata("target", "Target")
        asset_ids = []
        for name, content in (("A", b"image-a"), ("B", b"image-b")):
            response = client.post(
                "/api/v1/assets",
                data={"type": "scene", "name": name},
                files={"image": (f"{name}.png", content, "image/png")},
            )
            asset_ids.append(response.json()["asset"]["id"])

        real_copyfile = project_manager_module.shutil.copyfile
        calls = 0

        def fail_second_copy(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected second copy failure")
            return real_copyfile(source, destination)

        monkeypatch.setattr(project_manager_module.shutil, "copyfile", fail_second_copy)

        with pytest.raises(OSError, match="injected second copy failure"):
            client.post(
                "/api/v1/assets/apply-to-project",
                json={"asset_ids": asset_ids, "target_project": "target", "conflict_policy": "skip"},
            )

        assert pm.load_project("target")["scenes"] == {}
        target_dir = pm.projects_root / "target" / "scenes"
        assert not (target_dir / "A.png").exists()
        assert not (target_dir / "B.png").exists()
        assert not list(target_dir.glob(".*.tmp"))
        assert not list(target_dir.glob(".*.bak"))

    def test_second_file_install_failure_restores_all_overwritten_media(self, _assets_env, monkeypatch):
        from lib import project_manager as project_manager_module

        client = _assets_env["client"]
        pm = _assets_env["pm"]
        asset_ids = []
        for name, content in (("A", b"new-a"), ("B", b"new-b")):
            response = client.post(
                "/api/v1/assets",
                data={"type": "scene", "name": name, "description": f"new-{name}"},
                files={"image": (f"{name}.png", content, "image/png")},
            )
            asset_ids.append(response.json()["asset"]["id"])

        pm.create_project("target")
        pm.create_project_metadata("target", "Target")
        target_dir = pm.projects_root / "target" / "scenes"
        target_dir.mkdir(parents=True, exist_ok=True)
        for name, content in (("A", b"old-a"), ("B", b"old-b")):
            (target_dir / f"{name}.png").write_bytes(content)
        pm.update_project(
            "target",
            lambda project: project["scenes"].update(
                {name: {"description": f"old-{name}", "scene_sheet": f"scenes/{name}.png"} for name in ("A", "B")}
            ),
        )

        real_replace = project_manager_module.os.replace

        def fail_second_install(source, destination):
            source_path = project_manager_module.Path(source)
            destination_path = project_manager_module.Path(destination)
            if source_path.suffix == ".tmp" and destination_path.name == "B.png":
                raise OSError("injected second install failure")
            return real_replace(source, destination)

        monkeypatch.setattr(project_manager_module.os, "replace", fail_second_install)

        with pytest.raises(OSError, match="injected second install failure"):
            client.post(
                "/api/v1/assets/apply-to-project",
                json={"asset_ids": asset_ids, "target_project": "target", "conflict_policy": "overwrite"},
            )

        project = pm.load_project("target")
        assert {name: project["scenes"][name]["description"] for name in ("A", "B")} == {
            "A": "old-A",
            "B": "old-B",
        }
        assert (target_dir / "A.png").read_bytes() == b"old-a"
        assert (target_dir / "B.png").read_bytes() == b"old-b"
        assert not list(target_dir.glob(".*.tmp"))
        assert not list(target_dir.glob(".*.bak"))

    def test_project_json_failure_restores_overwritten_media(self, _assets_env, monkeypatch):
        from lib import project_manager as project_manager_module

        client = _assets_env["client"]
        pm = _assets_env["pm"]
        created = client.post(
            "/api/v1/assets",
            data={"type": "scene", "name": "A", "description": "new"},
            files={"image": ("A.png", b"new-image", "image/png")},
        )
        asset_id = created.json()["asset"]["id"]

        pm.create_project("target")
        pm.create_project_metadata("target", "Target")
        target_image = pm.projects_root / "target" / "scenes" / "A.png"
        target_image.parent.mkdir(parents=True, exist_ok=True)
        target_image.write_bytes(b"old-image")
        pm.update_project(
            "target",
            lambda project: project["scenes"].update({"A": {"description": "old", "scene_sheet": "scenes/A.png"}}),
        )
        project_file = pm.projects_root / "target" / "project.json"
        real_atomic_write = project_manager_module.atomic_write_json

        def fail_project_write(path, data):
            if path == project_file:
                raise OSError("injected project write failure")
            return real_atomic_write(path, data)

        monkeypatch.setattr(project_manager_module, "atomic_write_json", fail_project_write)

        with pytest.raises(OSError, match="injected project write failure"):
            client.post(
                "/api/v1/assets/apply-to-project",
                json={"asset_ids": [asset_id], "target_project": "target", "conflict_policy": "overwrite"},
            )

        project = pm.load_project("target")
        assert project["scenes"]["A"]["description"] == "old"
        assert project["scenes"]["A"]["scene_sheet"] == "scenes/A.png"
        assert target_image.read_bytes() == b"old-image"
        assert not list(target_image.parent.glob(".*.tmp"))
        assert not list(target_image.parent.glob(".*.bak"))
