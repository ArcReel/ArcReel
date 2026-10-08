import dataclasses
import json
import os
import shutil
import sys
from io import BytesIO
from pathlib import Path
from typing import ClassVar

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

import lib.project.project_manager as project_manager_module
from lib.artifacts.artifact_activation import ArtifactCurrencyResolver, register_current_resource_artifact
from lib.artifacts.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactKey,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
)
from lib.backends.providers import CallPurpose
from lib.i18n.en import assets as en_assets
from lib.i18n.vi import assets as vi_assets
from lib.i18n.zh import assets as zh_assets
from lib.i18n.zh import errors as zh_errors
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.prompts.prompt_templates.builtin import builtin_templates
from lib.script import script_review
from lib.workflow.workflow_state import WorkflowStateService
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import files
from server.services.currency import upload_finalize
from tests.factories import wav_bytes


class _FakeTextBackend:
    requests: ClassVar[list] = []

    @property
    def name(self):
        return "fake"

    @property
    def model(self):
        return "fake-model"

    @property
    def capabilities(self):
        return set()

    async def generate(self, request):
        from lib.backends.text_backends.base import TextGenerationResult

        _FakeTextBackend.requests.append(request)
        return TextGenerationResult(text="cinematic, high contrast", provider="fake", model="fake-model")


async def _fake_create_backend(*args, **kwargs):
    return _FakeTextBackend(), "fake"


def _img_bytes(fmt="JPEG", color=(255, 0, 0)):
    image = Image.new("RGB", (8, 8), color)
    buf = BytesIO()
    image.save(buf, format=fmt)
    return buf.getvalue()


def _client(monkeypatch, tmp_path):
    pm = project_manager_module.ProjectManager(tmp_path)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.add_character("demo", "Alice", "desc")
    pm.add_prop("demo", "玉佩", "古玉")
    pm.add_product("demo", "保温杯", "不锈钢保温杯")

    monkeypatch.setattr(files, "get_project_manager", lambda: pm)
    monkeypatch.setattr("lib.backends.text_generator.create_text_backend_for_task", _fake_create_backend)

    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(files.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.include_router(files.public_router, prefix="/api/v1")
    return TestClient(app), pm


class TestFilesRouter:
    def test_source_and_file_endpoints(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)

        with client:
            upload = client.post(
                "/api/v1/projects/demo/upload/source",
                files={"file": ("chapter.txt", "hello", "text/plain")},
            )
            assert upload.status_code == 200
            path = upload.json()["path"]
            assert path == "source/chapter.txt"

            listed = client.get("/api/v1/projects/demo/files")
            assert listed.status_code == 200
            assert any(item["name"] == "chapter.txt" for item in listed.json()["files"]["source"])

            served = client.get("/api/v1/files/demo/source/chapter.txt")
            assert served.status_code == 404

            get_source = client.get("/api/v1/projects/demo/source/chapter.txt")
            assert get_source.status_code == 200
            assert get_source.text == "hello"

            update_source = client.put(
                "/api/v1/projects/demo/source/chapter.txt",
                content="updated",
                headers={"content-type": "text/plain"},
            )
            assert update_source.status_code == 200

            delete_source = client.delete("/api/v1/projects/demo/source/chapter.txt")
            assert delete_source.status_code == 200

            missing = client.get("/api/v1/projects/demo/source/missing.txt")
            assert missing.status_code == 404

    def test_episode_role_upload_registers_an_own_source_episode_at_the_end(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        pm.update_project(
            "demo",
            lambda project: project.update(
                episodes=[
                    {"episode": 4, "title": "已有", "script_file": "scripts/episode_4.json", "source_origin": "none"}
                ],
                episode_id_high_water=4,
            ),
        )

        with client:
            upload = client.post(
                "/api/v1/projects/demo/upload/source?role=episode",
                files={"file": ("第五集.txt", "第五集的原文\r\n第二行".encode(), "text/plain")},
            )

        assert upload.status_code == 200
        assert upload.json()["episode"] == 5
        assert upload.json()["path"] == "source/episode_5.txt"
        project = pm.load_project("demo")
        assert [(e["episode"], e["source_origin"]) for e in project["episodes"]] == [(4, "none"), (5, "own")]
        assert project["whole_source_files"] == []
        episode_file = pm.get_project_path("demo") / "source" / "episode_5.txt"
        assert episode_file.read_text(encoding="utf-8") == "第五集的原文\n第二行"
        summary = WorkflowStateService(pm).get_project_summary("demo")
        assert [episode.episode for episode in summary.episodes] == [4, 5]

    def test_drama_upload_records_the_chosen_source_kind_on_each_source(self, tmp_path, monkeypatch):
        """剧情演绎上传时逐个记源文件类型，缺省为小说；其他创作类型不记。"""
        client, pm = _client(monkeypatch, tmp_path)
        pm.update_project("demo", lambda project: project.update(content_mode="drama"))

        with client:
            for query, name in (("?source_kind=screenplay", "剧本.txt"), ("", "小说.txt")):
                resp = client.post(
                    f"/api/v1/projects/demo/upload/source{query}", files={"file": (name, "正文", "text/plain")}
                )
                assert resp.status_code == 200
            resp = client.post(
                "/api/v1/projects/demo/upload/source?role=episode&source_kind=screenplay",
                files={"file": ("番外.txt", "番外原文", "text/plain")},
            )
            assert resp.status_code == 200

        project = pm.load_project("demo")
        assert project["whole_source_files"] == [
            {"source_file": "source/剧本.txt", "source_kind": "screenplay"},
            {"source_file": "source/小说.txt", "source_kind": "novel"},
        ]
        assert project["episodes"][-1]["source_kind"] == "screenplay"

    def test_non_drama_upload_records_no_source_kind(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)

        with client:
            whole = client.post(
                "/api/v1/projects/demo/upload/source?source_kind=screenplay",
                files={"file": ("剧本.txt", "正文", "text/plain")},
            )
            episode = client.post(
                "/api/v1/projects/demo/upload/source?role=episode&source_kind=screenplay",
                files={"file": ("番外.txt", "番外原文", "text/plain")},
            )

        assert (whole.status_code, episode.status_code) == (200, 200)
        project = pm.load_project("demo")
        assert project["whole_source_files"] == [{"source_file": "source/剧本.txt"}]
        assert "source_kind" not in project["episodes"][-1]

    def test_episode_role_upload_keeps_an_unregistered_file_with_the_new_id(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        pm.update_project("demo", lambda project: project.update(episode_id_high_water=4))
        source_dir = pm.get_project_path("demo") / "source"
        (source_dir / "episode_5.txt").write_text("没有登记的旧文件", encoding="utf-8")

        with client:
            upload = client.post(
                "/api/v1/projects/demo/upload/source?role=episode",
                files={"file": ("第五集.txt", "第五集的原文".encode(), "text/plain")},
            )

        assert upload.status_code == 200
        assert upload.json()["episode"] == 5
        assert (source_dir / "episode_5.txt").read_text(encoding="utf-8") == "第五集的原文"
        assert (source_dir / "_episode_5.txt.bak").read_text(encoding="utf-8") == "没有登记的旧文件"

    def test_source_writes_refuse_while_the_project_migration_is_blocked(self, tmp_path, monkeypatch):
        import lib.project.project_migration_guard as guard
        from lib.project.project_migration_failure import MIGRATION_FAILURE_CODE, record_migration_failure

        client, pm = _client(monkeypatch, tmp_path)
        monkeypatch.setattr(guard, "get_project_manager", lambda: pm)
        source_dir = pm.get_project_path("demo") / "source"
        (source_dir / "novel.txt").write_text("旧整本源文", encoding="utf-8")
        record_migration_failure(
            pm.get_project_path("demo"), RuntimeError("step failed"), schema_version=CURRENT_PROJECT_SCHEMA_VERSION
        )
        before = pm.load_project("demo")
        refusal = zh_errors.MESSAGES[MIGRATION_FAILURE_CODE].split("{")[0]

        with client:
            responses = [
                client.post("/api/v1/projects/demo/upload/source", files={"file": ("new.txt", "新文件", "text/plain")}),
                client.post(
                    "/api/v1/projects/demo/upload/source?role=episode",
                    files={"file": ("ep.txt", "一集原文", "text/plain")},
                ),
                client.put(
                    "/api/v1/projects/demo/source/new.txt", content="新文件", headers={"content-type": "text/plain"}
                ),
                client.delete("/api/v1/projects/demo/source/novel.txt"),
            ]

        for resp in responses:
            assert resp.status_code == 409
            assert resp.json()["detail"].startswith(refusal)
        assert pm.load_project("demo") == before
        assert sorted(path.name for path in source_dir.iterdir() if path.is_file()) == ["novel.txt"]

    def test_put_source_refuses_episode_file_names(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        pm.update_project(
            "demo",
            lambda project: project.update(
                episodes=[
                    {"episode": 3, "title": "", "script_file": "scripts/episode_3.json", "source_origin": "none"}
                ],
                episode_id_high_water=3,
            ),
        )

        with client:
            resp = client.put(
                "/api/v1/projects/demo/source/episode_3.txt", content="绕过集页", headers={"content-type": "text/plain"}
            )

        assert resp.status_code == 400
        assert resp.json()["detail"] == zh_errors.MESSAGES["source_name_reserved"]
        assert not (pm.get_project_path("demo") / "source" / "episode_3.txt").exists()
        assert pm.load_project("demo")["episodes"][0]["source_origin"] == "none"

    def test_delete_source_refuses_a_cut_episode_file(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        source_dir = pm.get_project_path("demo") / "source"
        (source_dir / "novel.txt").write_text("甲乙丙丁", encoding="utf-8")
        (source_dir / "episode_1.txt").write_text("甲乙", encoding="utf-8")
        pm.update_project(
            "demo",
            lambda project: project.update(
                whole_source_files=[{"source_file": "source/novel.txt"}],
                episodes=[
                    {
                        "episode": 1,
                        "title": "",
                        "script_file": "scripts/episode_1.json",
                        "source_origin": "whole_source",
                        "source_range": {"source_file": "source/novel.txt", "start": 0, "end": 2},
                    }
                ],
                episode_id_high_water=1,
            ),
        )
        before = pm.load_project("demo")

        with client:
            resp = client.delete("/api/v1/projects/demo/source/episode_1.txt")

        assert resp.status_code == 409
        assert resp.json()["detail"] == zh_errors.MESSAGES["episode_source_derived"]
        assert (source_dir / "episode_1.txt").read_text(encoding="utf-8") == "甲乙"
        assert pm.load_project("demo") == before

    def test_delete_source_keeps_the_file_when_the_ledger_cannot_be_written(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        source_dir = pm.get_project_path("demo") / "source"
        (source_dir / "novel.txt").write_bytes("整本源文\r\n第二行".encode())
        pm.update_project(
            "demo", lambda project: project.update(whole_source_files=[{"source_file": "source/novel.txt"}])
        )
        project_file = pm.get_project_path("demo") / "project.json"
        real_atomic_write = project_manager_module.atomic_write_json

        def _fail_project_write(path, data):
            if path == project_file:
                raise OSError("injected project write failure")
            return real_atomic_write(path, data)

        monkeypatch.setattr(project_manager_module, "atomic_write_json", _fail_project_write)

        with client:
            resp = client.delete("/api/v1/projects/demo/source/novel.txt")

        assert resp.status_code == 500
        assert (source_dir / "novel.txt").read_bytes() == "整本源文\r\n第二行".encode()
        assert pm.load_project("demo")["whole_source_files"] == [{"source_file": "source/novel.txt"}]

    def test_episode_role_upload_rejects_blank_text(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)

        with client:
            upload = client.post(
                "/api/v1/projects/demo/upload/source?role=episode",
                files={"file": ("空.txt", b"  \n ", "text/plain")},
            )

        assert upload.status_code == 422
        assert upload.json()["detail"] == zh_errors.MESSAGES["episode_source_empty"]
        assert pm.load_project("demo")["episodes"] == []

    def test_whole_source_uploads_are_appended_to_the_file_list(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)

        with client:
            for name in ("第2章.txt", "第1章.txt"):
                upload = client.post(
                    "/api/v1/projects/demo/upload/source", files={"file": (name, "正文", "text/plain")}
                )
                assert upload.status_code == 200
            reserved = client.post(
                "/api/v1/projects/demo/upload/source", files={"file": ("episode_3.txt", "正文", "text/plain")}
            )
            assert client.delete("/api/v1/projects/demo/source/第2章.txt").status_code == 200

        assert reserved.status_code == 400
        assert reserved.json()["detail"] == zh_errors.MESSAGES["source_name_reserved"]
        assert not (pm.get_project_path("demo") / "source" / "episode_3.txt").exists()
        assert pm.load_project("demo")["whole_source_files"] == [{"source_file": "source/第1章.txt"}]

    def test_whole_source_upload_can_be_inserted_at_a_position(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)

        with client:
            for name, insert_at in (("卷二.txt", None), ("卷一.txt", 0), ("卷三.txt", 5), ("序.txt", 0)):
                query = "" if insert_at is None else f"?insert_at={insert_at}"
                upload = client.post(
                    f"/api/v1/projects/demo/upload/source{query}", files={"file": (name, "正文", "text/plain")}
                )
                assert upload.status_code == 200
            negative = client.post(
                "/api/v1/projects/demo/upload/source?insert_at=-1", files={"file": ("附录.txt", "正文", "text/plain")}
            )

        assert negative.status_code == 422
        assert [item["source_file"] for item in pm.load_project("demo")["whole_source_files"]] == [
            "source/序.txt",
            "source/卷一.txt",
            "source/卷二.txt",
            "source/卷三.txt",
        ]

    def test_registered_whole_source_writes_go_through_the_remap(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        source_dir = pm.get_project_path("demo") / "source"
        (source_dir / "a.txt").write_text("甲乙丙丁", encoding="utf-8")
        (source_dir / "b.txt").write_text("戊己庚辛", encoding="utf-8")
        (source_dir / "episode_1.txt").write_text("丙丁戊己", encoding="utf-8")
        crossing = {
            "episode": 1,
            "title": "",
            "script_file": "scripts/episode_1.json",
            "source_origin": "whole_source",
            "source_range": {"source_file": "source/a.txt", "start": 2, "end_file": "source/b.txt", "end": 2},
            "ledger_status": "planned",
        }
        pm.update_project(
            "demo",
            lambda project: project.update(
                whole_source_files=[{"source_file": "source/a.txt"}, {"source_file": "source/b.txt"}],
                episodes=[crossing],
                episode_id_high_water=1,
            ),
        )

        with client:
            inserted = client.post(
                "/api/v1/projects/demo/upload/source?insert_at=1", files={"file": ("插.txt", "插叙", "text/plain")}
            )
            written_before_confirm = (source_dir / "插.txt").exists()
            confirmed = client.post(
                f"/api/v1/projects/demo/upload/source?insert_at=1&revision={inserted.json()['revision']}",
                files={"file": ("插.txt", "插叙", "text/plain")},
            )
            put = client.put(
                "/api/v1/projects/demo/source/b.txt", content="戊己庚辛壬", headers={"content-type": "text/plain"}
            )
            deleted = client.delete("/api/v1/projects/demo/source/a.txt")

        assert (inserted.json()["status"], inserted.json()["success"]) == ("confirmation_required", False)
        assert inserted.json()["impact"]["changed_without_products"] == [1]
        assert written_before_confirm is False
        assert (confirmed.json()["status"], confirmed.json()["filename"]) == ("applied", "插.txt")
        assert (source_dir / "episode_1.txt").read_text(encoding="utf-8") == "丙丁插叙戊己"
        assert put.json()["status"] == "applied"
        assert (deleted.status_code, deleted.json()["status"]) == (200, "confirmation_required")
        assert (source_dir / "a.txt").exists()

    def test_deleting_a_whole_source_file_is_refused_while_a_removed_episode_has_active_tasks(
        self,
        tmp_path,
        monkeypatch,
        active_episode_tasks,
    ):
        client, pm = _client(monkeypatch, tmp_path)
        source_dir = pm.get_project_path("demo") / "source"
        (source_dir / "a.txt").write_text("甲乙丙丁", encoding="utf-8")
        pm.update_project(
            "demo",
            lambda project: project.update(
                whole_source_files=[{"source_file": "source/a.txt"}],
                episodes=[
                    {
                        "episode": 1,
                        "title": "",
                        "script_file": "scripts/episode_1.json",
                        "source_origin": "whole_source",
                        "source_range": {"source_file": "source/a.txt", "start": 0, "end": 4},
                        "ledger_status": "planned",
                    }
                ],
                episode_id_high_water=1,
            ),
        )

        active_episode_tasks["queued"] = [
            {"resource_id": "script_plan", "script_file": None, "payload": {"episode": 1}}
        ]

        with client:
            preview = client.delete("/api/v1/projects/demo/source/a.txt")
            refused = client.delete(f"/api/v1/projects/demo/source/a.txt?revision={preview.json()['revision']}")

        assert preview.json()["impact"]["removed"] == [1]
        assert refused.status_code == 409
        assert (source_dir / "a.txt").exists()
        assert [entry["episode"] for entry in pm.load_project("demo")["episodes"]] == [1]

    def test_source_upload_race_project_deleted_reports_project_not_found(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)

        class _RaceLoader:
            @staticmethod
            def load(*args, **kwargs):
                shutil.rmtree(tmp_path / "projects" / "demo")
                raise FileNotFoundError("/server/projects/demo/source gone")

        monkeypatch.setattr(files, "SourceLoader", _RaceLoader)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/source",
                files={"file": ("chapter.txt", BytesIO(b"hello"), "text/plain")},
            )
            assert resp.status_code == 404
            assert resp.json()["detail"] == zh_errors.MESSAGES["project_not_found"].format(name="demo")

    def test_source_upload_loader_file_missing_with_project_intact_stays_generic(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)

        class _BrokenLoader:
            @staticmethod
            def load(*args, **kwargs):
                raise FileNotFoundError("upload-tmp gone")

        monkeypatch.setattr(files, "SourceLoader", _BrokenLoader)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/source",
                files={"file": ("chapter.txt", BytesIO(b"hello"), "text/plain")},
            )
            assert resp.status_code == 404
            assert resp.json()["detail"] == zh_errors.MESSAGES["resource_not_found"]

    def test_upload_assets_and_drafts(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)

        with client:
            character = client.post(
                "/api/v1/projects/demo/upload/character?name=Alice",
                files={"file": ("alice.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert character.status_code == 200
            assert character.json()["path"] == "characters/Alice.jpg"

            character_ref = client.post(
                "/api/v1/projects/demo/upload/character_ref?name=Alice",
                files={"file": ("alice_ref.webp", _img_bytes("WEBP"), "image/webp")},
            )
            assert character_ref.status_code == 200
            assert character_ref.json()["path"] == "characters/refs/Alice.webp"

            clue = client.post(
                "/api/v1/projects/demo/upload/prop?name=玉佩",
                files={"file": ("prop.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert clue.status_code == 200
            assert clue.json()["path"] == "props/玉佩.jpg"

            # 分镜/视频上传走 shot_uploads 路由，通用上传不再支持 storyboard 类型
            legacy_storyboard = client.post(
                "/api/v1/projects/demo/upload/storyboard?name=E1S01",
                files={"file": ("storyboard.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert legacy_storyboard.status_code == 400

            invalid_ext = client.post(
                "/api/v1/projects/demo/upload/source",
                files={"file": ("bad.exe", b"x", "application/octet-stream")},
            )
            assert invalid_ext.status_code == 400

            bad_type = client.post(
                "/api/v1/projects/demo/upload/unknown",
                files={"file": ("x.txt", b"x", "text/plain")},
            )
            assert bad_type.status_code == 400

            # 无效图片格式仍应被拒绝（即使小于 2MB）
            bad_image = client.post(
                "/api/v1/projects/demo/upload/character?name=Alice",
                files={"file": ("bad.png", b"not-image", "image/png")},
            )
            assert bad_image.status_code == 400

            # drafts API
            update_draft = client.put(
                "/api/v1/projects/demo/drafts/1/script_plan",
                content="draft content",
                headers={"content-type": "text/plain"},
            )
            assert update_draft.status_code == 200

            get_draft = client.get("/api/v1/projects/demo/drafts/1/script_plan")
            assert get_draft.status_code == 200
            assert "draft content" in get_draft.text

            bad_stage = client.get("/api/v1/projects/demo/drafts/1/prompt_authoring")
            assert bad_stage.status_code == 400

            delete_draft = client.delete("/api/v1/projects/demo/drafts/1/script_plan")
            assert delete_draft.status_code == 200

            missing_draft = client.get("/api/v1/projects/demo/drafts/1/script_plan")
            assert missing_draft.status_code == 404

            # confirm metadata updated for character/prop
            project = pm.load_project("demo")
            assert project["characters"]["Alice"]["character_sheet"] == "characters/Alice.jpg"
            assert project["characters"]["Alice"]["reference_image"] == "characters/refs/Alice.webp"
            assert project["props"]["玉佩"]["prop_sheet"] == "props/玉佩.jpg"

    def test_formal_sheet_upload_registration_failure_restores_file_and_metadata(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            first = client.post(
                "/api/v1/projects/demo/upload/character?name=Alice",
                files={"file": ("alice.png", _img_bytes("PNG"), "image/png")},
            )
            assert first.status_code == 200, first.text

            project_dir = pm.get_project_path("demo")
            target = project_dir / first.json()["path"]
            manifest = project_dir / ".arcreel_artifacts.json"
            versions_file = project_dir / "versions" / "versions.json"

            def _durable_state():
                snapshots = sorted(path.name for path in (project_dir / "versions" / "characters").iterdir())
                return (
                    target.read_bytes(),
                    (project_dir / "project.json").read_bytes(),
                    manifest.read_bytes(),
                    versions_file.read_bytes(),
                    snapshots,
                )

            before = _durable_state()

            def _fail(*_args, **_kwargs):
                raise RuntimeError("injected manifest failure")

            monkeypatch.setattr(upload_finalize, "register_current_resource_artifact", _fail)
            failed = client.post(
                "/api/v1/projects/demo/upload/character?name=Alice",
                files={"file": ("replacement.png", _img_bytes("PNG", (0, 0, 255)), "image/png")},
            )

            assert failed.status_code == 500
            assert _durable_state() == before

    def test_formal_sheet_upload_rechecks_a_definition_created_before_install(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        name = "Late Arrival"
        project_dir = pm.get_project_path("demo")
        relative_path = f"characters/{name}.png"
        generated = _img_bytes("PNG", (10, 20, 30))
        uploaded = _img_bytes("PNG", (40, 50, 60))
        real_install = pm.install_asset_sheet_bytes
        delegated = False

        def _create_then_install(asset_type, project_name, asset_name, sheet_path, content, *, on_commit=None):
            nonlocal delegated
            assert delegated is False
            delegated = True
            pm.add_character("demo", name, "created while upload was waiting")
            real_install(
                "character",
                "demo",
                name,
                relative_path,
                generated,
                on_commit=lambda _target: register_current_resource_artifact(
                    project_dir,
                    resource_type="characters",
                    resource_id=name,
                ),
            )
            return real_install(
                asset_type,
                project_name,
                asset_name,
                sheet_path,
                content,
                on_commit=on_commit,
            )

        monkeypatch.setattr(pm, "install_asset_sheet_bytes", _create_then_install)

        with client:
            response = client.post(
                f"/api/v1/projects/demo/upload/character?name={name}",
                files={"file": ("late.png", uploaded, "image/png")},
            )

        assert response.status_code == 200, response.text
        assert delegated is True
        assert (project_dir / relative_path).read_bytes() == uploaded
        assert pm.load_project("demo")["characters"][name]["character_sheet"] == relative_path
        key = ArtifactKey.asset_sheet("character", name)
        entry = ProjectArtifactManifestAdapter(project_dir).get_entry(key)
        assert entry is not None
        assert (
            ArtifactCurrencyResolver(project_dir).compare(key, artifact_path=entry.artifact_path).status
            is ArtifactStatus.CURRENT
        )

    def test_bgm_upload_registers_the_track_and_lists_it_for_playback(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            upload = client.post(
                "/api/v1/projects/demo/upload/bgm",
                files={"file": ("雨夜.wav", wav_bytes(1.0, tone_hz=330), "audio/wav")},
            )
            assert upload.status_code == 200
            bgm = upload.json()["bgm"]
            assert (bgm["name"], bgm["duration"]) == ("雨夜", 1.0)
            assert bgm["path"] == f"bgm/{bgm['id']}.wav"
            assert 0 < bgm["gain"] < 1

            listed = client.get("/api/v1/projects/demo/bgm")
            assert listed.status_code == 200
            assert listed.json() == {"bgm": [bgm]}

            played = client.get(bgm["url"])
            assert played.status_code == 200
            assert played.content == wav_bytes(1.0, tone_hz=330)
        assert set(pm.load_project("demo")["bgm"]) == {bgm["id"]}

    @pytest.mark.parametrize(
        ("filename", "content", "message_key", "params"),
        [
            ("silence.wav", wav_bytes(1.0), "bgm_silent", {}),
            ("broken.mp3", b"not audio", "invalid_audio_file", {}),
            ("cover.jpg", b"jpeg", "unsupported_audio_type", {"ext": ".jpg", "allowed": ".mp3, .wav, .m4a"}),
        ],
    )
    def test_bgm_upload_refuses_unusable_audio_with_a_localized_reason(
        self, tmp_path, monkeypatch, filename, content, message_key, params
    ):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            resp = client.post("/api/v1/projects/demo/upload/bgm", files={"file": (filename, content, "audio/wav")})
            assert resp.status_code == 400
            assert resp.json()["detail"] == zh_errors.MESSAGES[message_key].format(**params)
        assert "bgm" not in pm.load_project("demo")

    def test_bgm_list_of_unknown_project_is_404(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            assert client.get("/api/v1/projects/absent/bgm").status_code == 404

    def test_character_audio_ref_upload_success(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("alice_voice.wav", wav_bytes(3), "audio/wav")},
            )
            assert resp.status_code == 200
            assert resp.json()["path"] == "characters/refs_audio/Alice.wav"
            project = pm.load_project("demo")
            assert project["characters"]["Alice"]["reference_audio"] == "characters/refs_audio/Alice.wav"

    def test_character_audio_ref_without_name_is_rejected_without_writing_an_orphan(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref",
                files={"file": ("orphan.wav", wav_bytes(3), "audio/wav")},
            )

            assert resp.status_code == 404
            refs_dir = pm.get_project_path("demo") / "characters" / "refs_audio"
            assert not refs_dir.exists() or not any(refs_dir.iterdir())

    def test_character_audio_ref_rejects_bad_extension(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("alice_voice.m4a", b"noop", "audio/mp4")},
            )
            assert resp.status_code == 400
            assert "音频类型" in resp.json()["detail"] or "audio type" in resp.json()["detail"].lower()

    def test_character_audio_ref_rejects_oversized(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        oversized = b"\x00" * (files.AUDIO_REFERENCE_MAX_BYTES + 1)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("alice_voice.wav", oversized, "audio/wav")},
            )
            assert resp.status_code == 400
            assert resp.json()["detail"] == zh_errors.MESSAGES["upload_too_large"].format(max_mb=15)

    def test_character_audio_ref_rejects_duration_out_of_range(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("alice_voice.wav", wav_bytes(1), "audio/wav")},
            )
            assert resp.status_code == 400
            assert resp.json()["detail"] == zh_errors.MESSAGES["audio_duration_out_of_range"].format(
                min_seconds=2, max_seconds=10
            )
            assert pm.load_project("demo")["characters"]["Alice"].get("reference_audio", "") == ""

    def test_character_audio_ref_replace_removes_old_extension(self, tmp_path, monkeypatch):
        """替换参考音频且新旧扩展名不同（wav -> mp3）时旧文件应被清理，不留孤儿。"""
        client, pm = _client(monkeypatch, tmp_path)

        async def _fake_duration(content, suffix):
            return 3.0

        monkeypatch.setattr(files, "probe_audio_duration_seconds", _fake_duration)

        with client:
            first = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("v1.wav", b"fake-wav-bytes", "audio/wav")},
            )
            assert first.status_code == 200
            project_dir = pm.get_project_path("demo")
            old_path = project_dir / "characters" / "refs_audio" / "Alice.wav"
            assert old_path.exists()

            second = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("v2.mp3", b"fake-mp3-bytes", "audio/mpeg")},
            )
            assert second.status_code == 200
            assert second.json()["path"] == "characters/refs_audio/Alice.mp3"
            assert not old_path.exists()
            new_path = project_dir / "characters" / "refs_audio" / "Alice.mp3"
            assert new_path.exists()

    def test_character_audio_ref_replace_succeeds_when_stale_cleanup_fails(self, tmp_path, monkeypatch):
        """替换成功但旧文件物理删除失败（权限/IO 错误）时，请求仍应成功——新文件与字段已提交，
        不应因清理失败误报整次替换失败并诱导重试（重试时旧文件已找不到指针，成为孤儿）。"""
        client, pm = _client(monkeypatch, tmp_path)

        async def _fake_duration(content, suffix):
            return 3.0

        monkeypatch.setattr(files, "probe_audio_duration_seconds", _fake_duration)

        with client:
            first = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("v1.wav", b"fake-wav-bytes", "audio/wav")},
            )
            assert first.status_code == 200
            project_dir = pm.get_project_path("demo")
            old_path = project_dir / "characters" / "refs_audio" / "Alice.wav"
            assert old_path.exists()

            original_unlink = Path.unlink

            def _boom(self, *args, **kwargs):
                if self == old_path:
                    raise PermissionError("simulated unlink failure")
                return original_unlink(self, *args, **kwargs)

            monkeypatch.setattr(Path, "unlink", _boom)

            second = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("v2.mp3", b"fake-mp3-bytes", "audio/mpeg")},
            )
            assert second.status_code == 200
            assert second.json()["path"] == "characters/refs_audio/Alice.mp3"
            new_path = project_dir / "characters" / "refs_audio" / "Alice.mp3"
            assert new_path.exists()
            assert old_path.exists()  # 清理失败，旧文件残留（已记告警日志），但不影响本次请求成功
            assert (
                pm.load_project("demo")["characters"]["Alice"]["reference_audio"] == "characters/refs_audio/Alice.mp3"
            )

    def test_delete_character_reference_audio(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            upload = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("alice_voice.wav", wav_bytes(3), "audio/wav")},
            )
            assert upload.status_code == 200
            project_dir = pm.get_project_path("demo")
            audio_path = project_dir / "characters" / "refs_audio" / "Alice.wav"
            assert audio_path.exists()

            delete = client.delete("/api/v1/projects/demo/characters/Alice/reference-audio")
            assert delete.status_code == 200
            assert not audio_path.exists()
            assert pm.load_project("demo")["characters"]["Alice"].get("reference_audio") == ""

    def test_delete_character_reference_audio_succeeds_when_cleanup_fails(self, tmp_path, monkeypatch):
        """字段已提交清空后，物理删除失败只残留无引用文件，不应让请求误报失败。"""
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            upload = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("alice_voice.wav", wav_bytes(3), "audio/wav")},
            )
            assert upload.status_code == 200
            project_dir = pm.get_project_path("demo")
            audio_path = project_dir / "characters" / "refs_audio" / "Alice.wav"
            assert audio_path.exists()

            original_unlink = Path.unlink

            def _boom(self, *args, **kwargs):
                if self == audio_path:
                    raise PermissionError("simulated unlink failure")
                return original_unlink(self, *args, **kwargs)

            monkeypatch.setattr(Path, "unlink", _boom)

            resp = client.delete("/api/v1/projects/demo/characters/Alice/reference-audio")
            assert resp.status_code == 200
            assert pm.load_project("demo")["characters"]["Alice"]["reference_audio"] == ""
            assert audio_path.exists()

    def test_delete_character_reference_audio_preserves_file_when_project_commit_fails(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            upload = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("alice_voice.wav", wav_bytes(3), "audio/wav")},
            )
            assert upload.status_code == 200
            project_dir = pm.get_project_path("demo")
            project_file = project_dir / "project.json"
            audio_path = project_dir / "characters" / "refs_audio" / "Alice.wav"
            real_atomic_write = project_manager_module.atomic_write_json

            def _fail_project_write(path, data):
                if path == project_file:
                    raise OSError("injected project write failure")
                return real_atomic_write(path, data)

            monkeypatch.setattr(project_manager_module, "atomic_write_json", _fail_project_write)

            resp = client.delete("/api/v1/projects/demo/characters/Alice/reference-audio")
            assert resp.status_code == 500
            assert (
                pm.load_project("demo")["characters"]["Alice"]["reference_audio"] == "characters/refs_audio/Alice.wav"
            )
            assert audio_path.exists()

    def test_delete_character_reference_audio_unknown_character_404(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            resp = client.delete("/api/v1/projects/demo/characters/Ghost/reference-audio")
            assert resp.status_code == 404

    def test_delete_character_reference_audio_noop_when_no_audio(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            resp = client.delete("/api/v1/projects/demo/characters/Alice/reference-audio")
            assert resp.status_code == 200
            assert pm.load_project("demo")["characters"]["Alice"].get("reference_audio", "") == ""

    def test_delete_character_reference_audio_ignores_out_of_project_path(self, tmp_path, monkeypatch):
        """reference_audio 可经资产 PATCH 写成任意字符串；越界路径只清字段，不得删项目外文件。"""
        client, pm = _client(monkeypatch, tmp_path)
        outsider = tmp_path / "outsider.wav"
        outsider.write_bytes(b"do-not-delete")
        pm.update_character_reference_audio("demo", "Alice", f"../../{outsider.name}")

        with client:
            resp = client.delete("/api/v1/projects/demo/characters/Alice/reference-audio")
            assert resp.status_code == 200
            assert outsider.exists()
            assert pm.load_project("demo")["characters"]["Alice"].get("reference_audio") == ""

    def test_character_audio_ref_replace_ignores_out_of_project_old_path(self, tmp_path, monkeypatch):
        """替换时的旧文件清理同样受项目目录约束，越界的存量值不触发删除。"""
        client, pm = _client(monkeypatch, tmp_path)
        outsider = tmp_path / "outsider.wav"
        outsider.write_bytes(b"do-not-delete")
        pm.update_character_reference_audio("demo", "Alice", f"../../{outsider.name}")

        async def _fake_duration(content, suffix):
            return 3.0

        monkeypatch.setattr(files, "probe_audio_duration_seconds", _fake_duration)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("v2.mp3", b"fake-mp3-bytes", "audio/mpeg")},
            )
            assert resp.status_code == 200
            assert outsider.exists()
            assert (
                pm.load_project("demo")["characters"]["Alice"]["reference_audio"] == "characters/refs_audio/Alice.mp3"
            )

    def test_delete_character_reference_audio_ignores_path_outside_refs_audio(self, tmp_path, monkeypatch):
        """reference_audio 越权指向项目内 refs_audio 之外的文件（如 project.json）时，只清字段，不得删该文件。"""
        client, pm = _client(monkeypatch, tmp_path)
        project_json = pm.get_project_path("demo") / "project.json"
        assert project_json.exists()
        pm.update_character_reference_audio("demo", "Alice", "project.json")

        with client:
            resp = client.delete("/api/v1/projects/demo/characters/Alice/reference-audio")
            assert resp.status_code == 200
            assert project_json.exists()
            assert pm.load_project("demo")["characters"]["Alice"].get("reference_audio") == ""

    def test_character_audio_ref_replace_ignores_stale_path_outside_refs_audio(self, tmp_path, monkeypatch):
        """替换时的旧文件清理同样限定在 refs_audio 目录内，落在项目目录内其它位置的存量值不触发删除。"""
        client, pm = _client(monkeypatch, tmp_path)
        project_dir = pm.get_project_path("demo")
        project_json = project_dir / "project.json"
        pm.update_character_reference_audio("demo", "Alice", "project.json")

        async def _fake_duration(content, suffix):
            return 3.0

        monkeypatch.setattr(files, "probe_audio_duration_seconds", _fake_duration)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/character_audio_ref?name=Alice",
                files={"file": ("v2.mp3", b"fake-mp3-bytes", "audio/mpeg")},
            )
            assert resp.status_code == 200
            assert project_json.exists()
            assert (
                pm.load_project("demo")["characters"]["Alice"]["reference_audio"] == "characters/refs_audio/Alice.mp3"
            )

    def test_product_ref_upload_preserves_original_bytes(self, tmp_path, monkeypatch):
        """商品原图是保真验收锚点：保存管线保留原件字节，不做阈值压缩/重编码。"""
        client, pm = _client(monkeypatch, tmp_path)

        # 构造一张 >2MB 的 PNG（其他资产上传在该阈值会被压成 JPEG q85）：
        # 噪声像素不可压缩，保证体积越过阈值
        import os as _os

        image = Image.frombytes("RGB", (1200, 1200), _os.urandom(1200 * 1200 * 3))
        buf = BytesIO()
        image.save(buf, format="PNG")
        original = buf.getvalue()
        assert len(original) > 2 * 1024 * 1024

        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/product_ref?name=保温杯",
                files={"file": ("photo.png", original, "image/png")},
            )
            assert resp.status_code == 200
            path = resp.json()["path"]
            assert path.startswith("products/refs/")
            assert path.endswith(".png")

            saved = pm.get_project_path("demo") / path
            assert saved.read_bytes() == original

            project = pm.load_project("demo")
            assert project["products"]["保温杯"]["reference_images"] == [path]

    def test_product_ref_multiple_uploads_accumulate(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            paths = []
            for fname in ("front.jpg", "back.jpg"):
                resp = client.post(
                    "/api/v1/projects/demo/upload/product_ref?name=保温杯",
                    files={"file": (fname, _img_bytes("JPEG"), "image/jpeg")},
                )
                assert resp.status_code == 200
                paths.append(resp.json()["path"])

            assert len(set(paths)) == 2
            project = pm.load_project("demo")
            assert project["products"]["保温杯"]["reference_images"] == paths
            project_dir = pm.get_project_path("demo")
            for p in paths:
                assert (project_dir / p).exists()

    def test_product_ref_upload_resolves_nfd_registered_host(self, tmp_path, monkeypatch):
        """宿主资产的存量 key 可能是 NFD：上传入口按坐标系解析存在性，
        否则闸口把 name 归一到 NFC 后会先返回 404，写回侧的解析根本走不到。"""
        import unicodedata

        name_nfc = unicodedata.normalize("NFC", "Hiếu")
        name_nfd = unicodedata.normalize("NFD", "Hiếu")

        client, pm = _client(monkeypatch, tmp_path)

        def _mutate(project: dict) -> None:
            project.setdefault("products", {})[name_nfd] = {"description": "存量 NFD 商品"}

        pm.update_project("demo", _mutate)

        with client:
            resp = client.post(
                f"/api/v1/projects/demo/upload/product_ref?name={name_nfc}",
                files={"file": ("x.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert resp.status_code == 200, resp.text
            products = pm.load_project("demo")["products"]
            assert name_nfc not in products  # 不因上传新造一条 NFC 商品
            assert products[name_nfd]["reference_images"] == [resp.json()["path"]]

    def test_product_ref_unknown_product_404(self, tmp_path, monkeypatch):
        """原图列表是文件的唯一指针：商品不存在时拒收，避免落下孤儿文件。"""
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/product_ref?name=不存在",
                files={"file": ("x.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert resp.status_code == 404
            refs_dir = pm.get_project_path("demo") / "products" / "refs"
            assert not refs_dir.exists() or not any(refs_dir.iterdir())

    def test_product_ref_invalid_image_rejected(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/product_ref?name=保温杯",
                files={"file": ("bad.png", b"not-image", "image/png")},
            )
            assert resp.status_code == 400

    def test_product_sheet_upload_updates_metadata(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/product?name=保温杯",
                files={"file": ("sheet.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert resp.status_code == 200
            assert resp.json()["path"] == "products/保温杯.jpg"
            project = pm.load_project("demo")
            assert project["products"]["保温杯"]["product_sheet"] == "products/保温杯.jpg"

    def test_list_files_includes_products(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            client.post(
                "/api/v1/projects/demo/upload/product?name=保温杯",
                files={"file": ("sheet.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            listed = client.get("/api/v1/projects/demo/files")
            assert listed.status_code == 200
            assert any(item["name"] == "保温杯.jpg" for item in listed.json()["files"]["products"])

    def test_style_image_endpoints(self, tmp_path, monkeypatch):
        from lib.backends.text_generator import TextGenerator

        client, pm = _client(monkeypatch, tmp_path)
        captured: dict[str, object] = {}
        original_create = TextGenerator.create

        async def capture_create(task_type, project_name=None, *, purpose):
            captured["purpose"] = purpose
            return await original_create(task_type, project_name, purpose=purpose)

        monkeypatch.setattr(TextGenerator, "create", capture_create)
        monkeypatch.setattr(_FakeTextBackend, "requests", [])

        # 预置 style_template_id + 展开后的 style prompt，验证上传后被强制清掉（互斥）
        project = pm.load_project("demo")
        project["style_template_id"] = "live_premium_drama"
        project["style"] = "画风：真人电视剧风格，精品短剧画风，大师级构图"
        pm.save_project("demo", project)

        with client:
            upload_style = client.post(
                "/api/v1/projects/demo/style-image",
                files={"file": ("style.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert upload_style.status_code == 200
            assert upload_style.json()["style_description"] == "cinematic, high contrast"
            assert captured["purpose"] is CallPurpose.STYLE_ANALYSIS
            (request,) = _FakeTextBackend.requests
            assert request.prompt == builtin_templates.render("text/style_analysis")
            assert "Do NOT describe the subject matter" in request.prompt
            after = pm.load_project("demo")
            assert after.get("style_image", "").startswith("style_reference")
            assert "style_template_id" not in after
            # 互斥语义关键断言：模板展开到 style 的 prompt 也要被清空，
            # 否则生成链路会把模板 prompt 与 style_description 一起喂给 LLM。
            assert after.get("style", "") == ""

            bad_style_ext = client.post(
                "/api/v1/projects/demo/style-image",
                files={"file": ("style.gif", b"gif", "image/gif")},
            )
            assert bad_style_ext.status_code == 400

    def test_security_and_error_paths(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)

        outside = tmp_path / "projects" / "outside.txt"
        outside.write_text("outside", encoding="utf-8")

        with client:
            traverse = client.get("/api/v1/files/demo/%2E%2E/outside.txt")
            assert traverse.status_code == 403

            missing_project = client.get("/api/v1/projects/missing/files")
            assert missing_project.status_code == 404

            missing_source = client.put(
                "/api/v1/projects/missing/source/a.txt",
                content="x",
                headers={"content-type": "text/plain"},
            )
            assert missing_source.status_code == 404

    def test_upload_without_name_and_keyerror_tolerance(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            ref_no_name = client.post(
                "/api/v1/projects/demo/upload/character_ref",
                files={"file": ("no_name.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert ref_no_name.status_code == 200
            assert ref_no_name.json()["path"] == "characters/refs/no_name.jpg"

            clue_missing_entity = client.post(
                "/api/v1/projects/demo/upload/prop?name=不存在道具",
                files={"file": ("x.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert clue_missing_entity.status_code == 200
            assert clue_missing_entity.json()["path"] == "props/不存在道具.jpg"

            character_missing_entity = client.post(
                "/api/v1/projects/demo/upload/character?name=不存在角色",
                files={"file": ("x.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert character_missing_entity.status_code == 200
            assert character_missing_entity.json()["path"] == "characters/不存在角色.jpg"

    def test_upload_rejects_unsafe_name_for_every_type(self, tmp_path, monkeypatch):
        """name 会被拼进落盘路径：含分隔符 / .. / 控制字符的名字在所有上传类型下都应被 400 拒绝。"""
        client, _ = _client(monkeypatch, tmp_path)

        # 快照范围取 tmp_path 而非 projects 根：越界名的目标本就在 projects 之外，只扫项目内看不见。
        # 连同内容一起快照：越界写入若命中既有文件（如 ../project.json），路径集合不变，只比路径发现不了
        def _snapshot() -> dict[Path, bytes]:
            return {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

        # 预置 "../existing" 会解析到的既有文件：没有真实的覆写目标时，内容快照验证不到覆写这一维。
        # 逐 subdir 铺哨兵——嵌套类型（characters/refs 等）的 ".." 落在 characters/ 而非项目根
        project_dir = tmp_path / "projects" / "demo"
        for spec in files.UPLOAD_SPECS.values():
            parent = project_dir.joinpath(*spec.subdir).parent
            parent.mkdir(parents=True, exist_ok=True)
            for ext in (".png", ".jpg", ".wav", ".txt"):
                (parent / f"existing{ext}").write_bytes(b"original")

        before = _snapshot()
        unsafe_names = [
            "../existing",
            "../../evil",
            "../../../evil",
            str(tmp_path / "absolute-escape"),
            "sub/dir",
            "back\\slash",
            "..",
            "trailing.",
            "CON",
            "ctrl\x01char",
        ]
        payloads = {
            "character_audio_ref": ("v.wav", wav_bytes(3), "audio/wav"),
            "bgm": ("m.wav", wav_bytes(3), "audio/wav"),
            # source 不使用 name，但校验在其早返分支之前，同样应拒
            "source": ("novel.txt", b"chapter one", "text/plain"),
        }
        default_payload = ("x.jpg", _img_bytes("JPEG"), "image/jpeg")

        with client:
            for upload_type in files.UPLOAD_SPECS:
                for unsafe in unsafe_names:
                    resp = client.post(
                        f"/api/v1/projects/demo/upload/{upload_type}",
                        params={"name": unsafe},
                        files={"file": payloads.get(upload_type, default_payload)},
                    )
                    assert resp.status_code == 400, (upload_type, unsafe, resp.status_code)
                    assert resp.json()["detail"] == zh_assets.MESSAGES["asset_invalid_name"].format(name=unsafe)

            # 越界名字不得留下任何落盘产物：项目内外都不得新增文件，既有文件的内容也不得被改写
            assert _snapshot() == before

    def test_upload_unsafe_name_message_is_localized(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        expected = {
            "en": en_assets.MESSAGES["asset_invalid_name"],
            "vi": vi_assets.MESSAGES["asset_invalid_name"],
            "zh": zh_assets.MESSAGES["asset_invalid_name"],
        }

        with client:
            for locale, template in expected.items():
                resp = client.post(
                    "/api/v1/projects/demo/upload/character",
                    params={"name": "../evil"},
                    files={"file": ("x.jpg", _img_bytes("JPEG"), "image/jpeg")},
                    headers={"Accept-Language": locale},
                )
                assert resp.status_code == 400
                assert resp.json()["detail"] == template.format(name="../evil")

    def test_upload_name_is_stripped_before_use(self, tmp_path, monkeypatch):
        """校验谓词会 strip 名字，落盘路径与元数据都应使用规范化后的值。"""
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/character",
                params={"name": "  Alice  "},
                files={"file": ("x.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert resp.status_code == 200
            assert resp.json()["path"] == "characters/Alice.jpg"
            assert pm.load_project("demo")["characters"]["Alice"]["character_sheet"] == "characters/Alice.jpg"

    def test_upload_empty_name_falls_back_to_filename(self, tmp_path, monkeypatch):
        """空串 name 等同未提供：校验只对真值生效，落盘仍回退到原文件名 stem。"""
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/character",
                params={"name": ""},
                files={"file": ("x.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
            assert resp.status_code == 200
            assert resp.json()["path"] == "characters/x.jpg"

    def test_upload_spec_table_drives_extensions(self):
        """ALLOWED_EXTENSIONS 由 UPLOAD_SPECS 派生，两者不得漂移。"""
        assert set(files.ALLOWED_EXTENSIONS) == set(files.UPLOAD_SPECS)
        for upload_type, spec in files.UPLOAD_SPECS.items():
            assert files.ALLOWED_EXTENSIONS[upload_type] == list(spec.allowed_exts)
        # source 一项被 frontend/src/utils/source-files.ts 镜像，取值变动需同步前端
        assert files.ALLOWED_EXTENSIONS["source"] == [".txt", ".md", ".docx", ".epub", ".pdf"]

    def test_upload_spec_host_fields_must_be_paired(self):
        """登记宿主约束却漏配 404 文案时，构造期即失败，而非拒收时取到空翻译 key。"""
        with pytest.raises(ValueError, match=r"host_bucket 与 host_not_found_key 必须成对登记"):
            files.UploadSpec(
                allowed_exts=(".png",),
                subdir=("x",),
                naming="stable_png",
                content_check="validate_image",
                host_bucket="products",
            )
        # 反方向同样是配置错误：登记了文案却没有宿主约束，该文案永远取不到
        with pytest.raises(ValueError, match=r"host_bucket 与 host_not_found_key 必须成对登记"):
            files.UploadSpec(
                allowed_exts=(".png",),
                subdir=("x",),
                naming="stable_png",
                content_check="validate_image",
                host_not_found_key="product_not_found",
            )

        # 参考音频的元数据字段是文件唯一指针；清理旧文件的类型不能漏掉宿主存在性约束。
        with pytest.raises(ValueError, match=r"tracks_stale_audio 必须登记宿主约束"):
            files.UploadSpec(
                allowed_exts=(".wav",),
                subdir=("x",),
                naming="keep_ext",
                content_check="audio",
                tracks_stale_audio=True,
            )

    def test_upload_rejects_oversized_payload_for_any_type(self, tmp_path, monkeypatch):
        """max_bytes 是通用请求体闸门：登记了上限的类型无论 content_check 为何都应拒收超限请求。"""
        client, _ = _client(monkeypatch, tmp_path)
        limit = 1024 * 1024
        monkeypatch.setitem(
            files.UPLOAD_SPECS,
            "prop",
            dataclasses.replace(files.UPLOAD_SPECS["prop"], max_bytes=limit),
        )
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/prop",
                params={"name": "道具"},
                files={"file": ("x.jpg", b"\x00" * (limit + 1), "image/jpeg")},
            )
            assert resp.status_code == 400
            # 上限取整 MB，文案里的 max_mb 才是对用户有意义的数字
            assert resp.json()["detail"] == zh_errors.MESSAGES["upload_too_large"].format(max_mb=1)

    def test_source_decode_and_draft_mode_helpers(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        project_dir = pm.get_project_path("demo")
        source_dir = project_dir / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "binary.txt").write_bytes(b"\xff\xfe")

        with client:
            bad_encoding = client.get("/api/v1/projects/demo/source/binary.txt")
            assert bad_encoding.status_code == 400

            # switch content_mode to drama so step files use normalized-script mapping
            project_json = project_dir / "project.json"
            payload = json.loads(project_json.read_text(encoding="utf-8"))
            payload["content_mode"] = "drama"
            project_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            # drama script_plan 落 .json：任意文本被拒（400）
            reject_text = client.put(
                "/api/v1/projects/demo/drafts/2/script_plan",
                content="drama draft",
                headers={"content-type": "text/plain"},
            )
            assert reject_text.status_code == 400

            # 合法 JSON 但 scenes 为空 → 被拒（400）：与 _load_drama_script_plan_content 的非空 scenes 契约同口径
            reject_empty = client.put(
                "/api/v1/projects/demo/drafts/2/script_plan",
                content='{"title": "第二集", "scenes": []}',
                headers={"content-type": "text/plain"},
            )
            assert reject_empty.status_code == 400

            # scenes 含非对象项（数字 / 字符串）→ 被拒（400）：与 _load_drama_script_plan_content 的逐项对象契约同口径
            reject_non_dict_scene = client.put(
                "/api/v1/projects/demo/drafts/2/script_plan",
                content='{"title": "第二集", "scenes": [{"scene_id": "E2S01"}, 42]}',
                headers={"content-type": "text/plain"},
            )
            assert reject_non_dict_scene.status_code == 400

            # scene 缺 scene_id → 被拒（400）：与 _load_drama_script_plan_content 的 scene_id 非空字符串契约同口径，
            # 避免写入端放行、消费端必失败的"保存成功但生成必失败"断层
            reject_missing_scene_id = client.put(
                "/api/v1/projects/demo/drafts/2/script_plan",
                content='{"title": "第二集", "scenes": [{}]}',
                headers={"content-type": "text/plain"},
            )
            assert reject_missing_scene_id.status_code == 400

            # scene_id 为空串 → 被拒（400）
            reject_empty_scene_id = client.put(
                "/api/v1/projects/demo/drafts/2/script_plan",
                content='{"title": "第二集", "scenes": [{"scene_id": ""}]}',
                headers={"content-type": "text/plain"},
            )
            assert reject_empty_scene_id.status_code == 400

            # 含非空 scenes 的合法 JSON 被接受（200），落到结构化草稿路径
            update_drama = client.put(
                "/api/v1/projects/demo/drafts/2/script_plan",
                content='{"title": "第二集", "scenes": [{"scene_id": "E2S01"}]}',
                headers={"content-type": "text/plain"},
            )
            assert update_drama.status_code == 200
            assert update_drama.json()["path"] == "drafts/episode_2/script_plan_normalized_script.json"

            missing_stage = client.delete("/api/v1/projects/demo/drafts/2/unknown_stage")
            assert missing_stage.status_code == 400

            # 未登记 content_mode 回落到 drama 结构化文件名时同样触发校验（按目标文件名而非 content_mode）：
            # 任意文本不再绕过校验被写成结构化 drama JSON，拖到生成阶段才失败
            payload["content_mode"] = "future_unregistered_mode"
            project_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            reject_dirty_mode = client.put(
                "/api/v1/projects/demo/drafts/3/script_plan",
                content="not json at all",
                headers={"content-type": "text/plain"},
            )
            assert reject_dirty_mode.status_code == 400

            # 除脚本规划外的阶段名一律无效
            prompt_authoring_resp = client.get("/api/v1/projects/demo/drafts/1/prompt_authoring")
            assert prompt_authoring_resp.status_code == 400

            unknown_stage_resp = client.put(
                "/api/v1/projects/demo/drafts/1/unknown_stage",
                content="test",
                headers={"content-type": "text/plain"},
            )
            assert unknown_stage_resp.status_code == 400

            unknown_draft = client.delete("/api/v1/projects/demo/drafts/9/script_plan")
            assert unknown_draft.status_code == 404

    def test_plain_script_plan_save_is_rejected_once_confirmed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        client, pm = _client(monkeypatch, tmp_path)
        project_dir = pm.get_project_path("demo")
        plan_path = project_dir / "drafts" / "episode_1" / "script_plan_segments.json"
        plan_path.parent.mkdir(parents=True)
        plan_path.write_text('{"episode": 1, "segments": []}', encoding="utf-8")
        # 该集已产出正式剧本，确认记录记的是当前规划。
        (project_dir / "scripts").mkdir(exist_ok=True)
        (project_dir / "scripts" / "episode_1.json").write_text('{"episode": 1, "segments": []}', encoding="utf-8")
        fingerprint = script_review.content_fingerprint(plan_path)
        assert fingerprint is not None

        def _confirm(project: dict) -> None:
            project["episodes"] = [{"episode": 1, "script_file": "scripts/episode_1.json"}]
            script_review.apply_confirmation(project, 1, fingerprint, "2026-01-01T00:00:00+00:00")

        pm.update_project("demo", _confirm)
        before = plan_path.read_bytes()

        with client:
            refused = client.put(
                "/api/v1/projects/demo/drafts/1/script_plan",
                content='{"episode": 1, "segments": [{"segment_id": "E1S01"}]}',
                headers={"content-type": "text/plain"},
            )

        assert refused.status_code == 409, refused.text
        assert refused.json()["diagnostic"] == {"code": "script_plan_confirmed"}
        assert plan_path.read_bytes() == before

    def test_plain_script_plan_save_registers_active_manifest_and_rolls_back_on_registration_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, pm = _client(monkeypatch, tmp_path)
        project_dir = pm.get_project_path("demo")

        def _activate(project: dict) -> None:
            project["schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION
            project["content_mode"] = "narration"
            project["generation_mode"] = "storyboard"
            project["episodes"] = [
                {
                    "episode": 1,
                    "title": "第一集",
                    "script_file": "scripts/episode_1.json",
                    "ledger_status": "planned",
                }
            ]

        pm.update_project("demo", _activate)
        source = project_dir / "source" / "episode_1.txt"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("第一集原文", encoding="utf-8")
        content = json.dumps(
            {
                "episode": 1,
                "segments": [
                    {
                        "segment_id": "E1S01",
                        "novel_text": "第一集原文",
                        "duration_seconds": 6,
                        "segment_break": False,
                        "characters_in_segment": [],
                        "scenes": [],
                        "props": [],
                    }
                ],
            },
            ensure_ascii=False,
        )

        with client:
            response = client.put(
                "/api/v1/projects/demo/drafts/1/script_plan",
                content=content,
                headers={"content-type": "text/plain"},
            )
        assert response.status_code == 200, response.text
        draft_path = project_dir / response.json()["path"]
        key = ArtifactKey.episode_script_plan(1)
        adapter = ProjectArtifactManifestAdapter(project_dir)
        entry = adapter.get_entry(key)
        assert entry is not None
        assert entry.artifact_path == draft_path.relative_to(project_dir).as_posix()
        assert (
            ArtifactCurrencyResolver(project_dir).compare(key, artifact_path=entry.artifact_path).status
            is ArtifactStatus.CURRENT
        )

        draft_before = draft_path.read_bytes()
        manifest_before = (project_dir / MANIFEST_FILENAME).read_bytes()

        def _fail_registration(*_args, **_kwargs):
            raise RuntimeError("manifest unavailable")

        with monkeypatch.context() as registration_patch:
            registration_patch.setattr(
                "lib.artifacts.artifact_activation.register_current_artifact_if_provable",
                _fail_registration,
            )
            with pytest.raises(RuntimeError, match="manifest unavailable"):
                files._write_plain_draft(
                    "demo",
                    project_dir,
                    1,
                    draft_path,
                    content.replace("第一集原文", "修改后原文"),
                    lambda key, **_kwargs: key,
                )

        assert draft_path.read_bytes() == draft_before
        assert (project_dir / MANIFEST_FILENAME).read_bytes() == manifest_before

        with client:
            deleted = client.delete("/api/v1/projects/demo/drafts/1/script_plan")
        assert deleted.status_code == 200, deleted.text
        assert not draft_path.exists()
        assert adapter.get_entry(key) is None

    def test_cache_control_immutable_with_version_param(self, tmp_path, monkeypatch):
        """带 ?v= 参数时应返回 immutable 缓存头"""
        client, pm = _client(monkeypatch, tmp_path)
        project_path = pm.get_project_path("demo")
        (project_path / "storyboards").mkdir(exist_ok=True)
        (project_path / "storyboards" / "test.png").write_bytes(b"img")

        with client:
            resp = client.get("/api/v1/files/demo/storyboards/test.png?v=1710288000")
            assert resp.status_code == 200
            assert "immutable" in resp.headers.get("cache-control", "")
            assert "max-age=31536000" in resp.headers.get("cache-control", "")

    def test_cache_control_immutable_for_version_files(self, tmp_path, monkeypatch):
        """versions/ 路径下的文件应返回 immutable 缓存头"""
        client, pm = _client(monkeypatch, tmp_path)
        project_path = pm.get_project_path("demo")
        (project_path / "versions" / "storyboards").mkdir(parents=True)
        (project_path / "versions" / "storyboards" / "E1S01_v1.png").write_bytes(b"img")

        with client:
            resp = client.get("/api/v1/files/demo/versions/storyboards/E1S01_v1.png")
            assert resp.status_code == 200
            assert "immutable" in resp.headers.get("cache-control", "")
            assert resp.headers["x-content-type-options"] == "nosniff"

    def test_revalidate_without_version(self, tmp_path, monkeypatch):
        """无 ?v= 参数且非 versions 路径时要求每次再验证；ETag 命中回 304，文件变更后回新内容"""
        client, pm = _client(monkeypatch, tmp_path)
        project_path = pm.get_project_path("demo")
        (project_path / "storyboards").mkdir(exist_ok=True)
        target = project_path / "storyboards" / "test.png"
        target.write_bytes(b"img")
        os.utime(target, (1_700_000_000, 1_700_000_000))

        with client:
            resp = client.get("/api/v1/files/demo/storyboards/test.png")
            assert resp.status_code == 200
            assert resp.headers["cache-control"] == "no-cache"
            etag = resp.headers["etag"]

            revalidated = client.get("/api/v1/files/demo/storyboards/test.png", headers={"If-None-Match": etag})
            assert revalidated.status_code == 304
            assert revalidated.content == b""

            target.write_bytes(b"img-v2")
            os.utime(target, (1_700_000_060, 1_700_000_060))
            changed = client.get("/api/v1/files/demo/storyboards/test.png", headers={"If-None-Match": etag})
            assert changed.status_code == 200
            assert changed.content == b"img-v2"

    def test_range_request_still_partial(self, tmp_path, monkeypatch):
        """视频拖动依赖的 Range 请求仍返回 206"""
        client, pm = _client(monkeypatch, tmp_path)
        project_path = pm.get_project_path("demo")
        (project_path / "videos").mkdir(exist_ok=True)
        (project_path / "videos" / "clip.mp4").write_bytes(b"0123456789")

        with client:
            resp = client.get("/api/v1/files/demo/videos/clip.mp4?v=1", headers={"Range": "bytes=2-5"})
            assert resp.status_code == 206
            assert resp.content == b"2345"

    def test_files_helper_functions(self, tmp_path):
        assert files._stage_files("narration") == {"script_plan": "script_plan_segments.json"}
        assert files._stage_files("drama") == {"script_plan": "script_plan_normalized_script.json"}
        # reference_video 走独立的结构化 script_plan 文件
        assert files._stage_files("drama", "reference_video") == {"script_plan": "script_plan_reference_units.json"}
        assert files._stage_files("narration", "reference_video") == {"script_plan": "script_plan_reference_units.json"}
        # 其他 generation_mode 回落到 content_mode
        assert files._stage_files("narration", "storyboard") == {"script_plan": "script_plan_segments.json"}

    def test_resolve_script_plan_path_narration_prefers_own_legacy_md(self, tmp_path):
        """narration script_plan 缺 .json 时优先回落自家旧 .md，不被跨模式遗留 reference_units.md 抢占。"""
        drafts_dir = tmp_path / "drafts" / "episode_1"
        drafts_dir.mkdir(parents=True)
        (drafts_dir / "script_plan_reference_units.md").write_text("ref leftover", encoding="utf-8")
        (drafts_dir / "script_plan_segments.md").write_text("narration legacy", encoding="utf-8")
        resolved = files._resolve_script_plan_path(drafts_dir, "script_plan", drafts_dir / "script_plan_segments.json")
        assert resolved.name == "script_plan_segments.md"

    def test_draft_content_reference_video_mode(self, tmp_path, monkeypatch):
        """参考生视频下读/写 script_plan_reference_units.json，避免被按 content_mode 错误路由；
        旧 .md 仅存量兼读，写入经 ScriptReviewService 单一出口做结构校验后落结构化 .json"""
        client, pm = _client(monkeypatch, tmp_path)
        project_dir = pm.get_project_path("demo")

        # 设置项目为 参考生视频（content_mode 仍是 narration 测试正交性）
        project_json = project_dir / "project.json"
        payload = json.loads(project_json.read_text(encoding="utf-8"))
        payload["generation_mode"] = "reference_video"
        # 分集须在账本里（save_content 的写入前置）：登记为自带原文的集
        payload["episodes"] = [
            {"episode": 1, "title": "", "script_file": "scripts/episode_1.json", "source_origin": "own"}
        ]
        project_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (project_dir / "source").mkdir(parents=True, exist_ok=True)
        (project_dir / "source" / "episode_1.txt").write_text("原文", encoding="utf-8")

        drafts_dir = project_dir / "drafts" / "episode_1"
        drafts_dir.mkdir(parents=True, exist_ok=True)
        # 主文件缺失时旧 .md 作为读取候选（存量在制品兼读）
        (drafts_dir / "script_plan_reference_units.md").write_text("E1U1 stub", encoding="utf-8")

        with client:
            resp = client.get("/api/v1/projects/demo/drafts/1/script_plan")
            assert resp.status_code == 200
            assert resp.text == "E1U1 stub"

            # 裸文本 / 非法结构不再直写正式 script_plan（旁路已收敛到单一写盘出口）
            bad = client.put(
                "/api/v1/projects/demo/drafts/1/script_plan",
                content="raw text",
                headers={"content-type": "text/plain"},
            )
            assert bad.status_code == 400
            invalid = client.put(
                "/api/v1/projects/demo/drafts/1/script_plan",
                content='{"units": [{"bogus": 1}]}',
                headers={"content-type": "text/plain"},
            )
            assert invalid.status_code == 422

            # 合法结构化内容按 generation_mode 路由到 script_plan_reference_units.json
            unit = {"unit_id": "E1U01", "text": "镜头描述", "duration_seconds": 8}
            update = client.put(
                "/api/v1/projects/demo/drafts/1/script_plan",
                content=json.dumps({"units": [unit]}, ensure_ascii=False),
                headers={"content-type": "text/plain"},
            )
            assert update.status_code == 200
            assert update.json()["path"] == "drafts/episode_1/script_plan_reference_units.json"

            # 结构化 .json 存在后优先于旧 .md；落盘的是校验后的结构化内容
            resp = client.get("/api/v1/projects/demo/drafts/1/script_plan")
            assert resp.status_code == 200
            saved = json.loads(resp.text)
            assert saved["units"][0]["unit_id"] == "E1U01"

    def test_draft_content_fallback_when_mode_mismatches_file(self, tmp_path, monkeypatch):
        """content_mode=narration 但磁盘上只有 reference_units 文件（集级模式切换/历史项目）也能读到"""
        client, pm = _client(monkeypatch, tmp_path)
        project_dir = pm.get_project_path("demo")  # narration by default

        drafts_dir = project_dir / "drafts" / "episode_3"
        drafts_dir.mkdir(parents=True, exist_ok=True)
        (drafts_dir / "script_plan_reference_units.md").write_text("fallback content", encoding="utf-8")

        with client:
            resp = client.get("/api/v1/projects/demo/drafts/3/script_plan")
            assert resp.status_code == 200
            assert resp.text == "fallback content"

    def test_draft_content_routes_by_project_generation_mode(self, tmp_path, monkeypatch):
        """草稿文件名按项目生成模式路由：参考生视频全项目落 script_plan_reference_units.json。"""
        client, pm = _client(monkeypatch, tmp_path)
        project_dir = pm.get_project_path("demo")

        project_json = project_dir / "project.json"
        payload = json.loads(project_json.read_text(encoding="utf-8"))
        payload["generation_mode"] = "reference_video"
        payload["episodes"] = [
            {"episode": 2, "title": "", "script_file": "scripts/episode_2.json", "source_origin": "own"}
        ]
        project_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        drafts_dir = project_dir / "drafts" / "episode_2"
        drafts_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "source").mkdir(parents=True, exist_ok=True)
        (project_dir / "source" / "episode_2.txt").write_text("原文", encoding="utf-8")

        with client:
            unit = {"unit_id": "E2U01", "text": "镜头描述", "duration_seconds": 8}
            update = client.put(
                "/api/v1/projects/demo/drafts/2/script_plan",
                content=json.dumps({"units": [unit]}, ensure_ascii=False),
                headers={"content-type": "text/plain"},
            )
            assert update.status_code == 200
            assert update.json()["path"] == "drafts/episode_2/script_plan_reference_units.json"

        # _load_project_modes 走 load_project：不存在项目 → ("drama", None) 回退
        content_mode, gen_mode = files._load_project_modes("no-such-project")
        assert content_mode == "drama"
        assert gen_mode is None
        # demo 项目 content_mode=narration（fixture 默认），生成模式取项目字段
        content_mode, gen_mode = files._load_project_modes("demo")
        assert content_mode == "narration"
        assert gen_mode == "reference_video"

    def test_draft_event_emission(self, tmp_path, monkeypatch):
        """PUT drafts 端点应发射 draft:created/updated 事件"""
        from unittest.mock import patch

        client, _ = _client(monkeypatch, tmp_path)

        with client, patch("server.routers.files.emit_project_change_batch") as mock_emit:
            # 首次创建 → action="created", important=True
            resp = client.put(
                "/api/v1/projects/demo/drafts/1/script_plan",
                content="new draft",
                headers={"content-type": "text/plain"},
            )
            assert resp.status_code == 200
            mock_emit.assert_called_once()
            args = mock_emit.call_args
            change = args[0][1][0]  # second positional arg, first item in list
            assert change["entity_type"] == "draft"
            assert change["action"] == "created"
            assert change["episode"] == 1
            assert change["important"] is True
            assert "分镜拆分" in change["label"]

            mock_emit.reset_mock()

            # 再次更新 → action="updated", important=False
            resp2 = client.put(
                "/api/v1/projects/demo/drafts/1/script_plan",
                content="updated draft",
                headers={"content-type": "text/plain"},
            )
            assert resp2.status_code == 200
            mock_emit.assert_called_once()
            change2 = mock_emit.call_args[0][1][0]
            assert change2["action"] == "updated"
            assert change2["important"] is False

    def test_serve_global_asset_image(self, tmp_path, monkeypatch):
        """全局资产图片能够被正确读取返回"""
        client, pm = _client(monkeypatch, tmp_path)
        target = pm.get_global_assets_root() / "character" / "abc.png"
        target.write_bytes(b"img-bytes")

        with client:
            resp = client.get("/api/v1/global-assets/character/abc.png")
            assert resp.status_code == 200
            assert resp.content == b"img-bytes"

    @pytest.mark.parametrize("query", ["?fp=2026-01-01T00:00:00", "?v=1"])
    def test_serve_global_asset_version_key_is_immutable(self, tmp_path, monkeypatch, query):
        """前端以 ?fp=<updated_at> 作版本键（?v= 同义），带版本键即 immutable"""
        client, pm = _client(monkeypatch, tmp_path)
        (pm.get_global_assets_root() / "character" / "abc.png").write_bytes(b"img-bytes")

        with client:
            resp = client.get(f"/api/v1/global-assets/character/abc.png{query}")
            assert resp.status_code == 200
            assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"
            assert resp.headers["x-content-type-options"] == "nosniff"

    def test_serve_global_asset_without_version_revalidates(self, tmp_path, monkeypatch):
        """无版本键时要求再验证，ETag 命中回 304"""
        client, pm = _client(monkeypatch, tmp_path)
        (pm.get_global_assets_root() / "character" / "abc.png").write_bytes(b"img-bytes")

        with client:
            resp = client.get("/api/v1/global-assets/character/abc.png")
            assert resp.headers["cache-control"] == "no-cache"
            revalidated = client.get(
                "/api/v1/global-assets/character/abc.png", headers={"If-None-Match": resp.headers["etag"]}
            )
            assert revalidated.status_code == 304

    def test_serve_global_asset_scene_and_prop(self, tmp_path, monkeypatch):
        """scene/prop 子目录也能正确读取"""
        client, pm = _client(monkeypatch, tmp_path)
        root = pm.get_global_assets_root()
        (root / "scene" / "s.png").write_bytes(b"scene-bytes")
        (root / "prop" / "p.png").write_bytes(b"prop-bytes")

        with client:
            r_scene = client.get("/api/v1/global-assets/scene/s.png")
            assert r_scene.status_code == 200
            assert r_scene.content == b"scene-bytes"

            r_prop = client.get("/api/v1/global-assets/prop/p.png")
            assert r_prop.status_code == 200
            assert r_prop.content == b"prop-bytes"

    def test_global_asset_invalid_type_returns_400(self, tmp_path, monkeypatch):
        """非法 asset_type 返回 400"""
        client, _ = _client(monkeypatch, tmp_path)

        with client:
            resp = client.get("/api/v1/global-assets/invalid/abc.png")
            assert resp.status_code == 400

    def test_global_asset_missing_file_returns_404(self, tmp_path, monkeypatch):
        """文件不存在时返回 404"""
        client, _ = _client(monkeypatch, tmp_path)

        with client:
            resp = client.get("/api/v1/global-assets/character/nonexistent.png")
            assert resp.status_code == 404

    def test_global_asset_path_traversal_rejected(self, tmp_path, monkeypatch):
        """filename 中包含 .. 应被阻止（400/403/404 均可接受）"""
        client, _ = _client(monkeypatch, tmp_path)

        with client:
            # URL 编码的 ../evil.png
            resp = client.get("/api/v1/global-assets/character/..%2Fevil.png")
            assert resp.status_code in (400, 403, 404)

    def test_global_asset_symlink_escape_returns_403(self, tmp_path, monkeypatch):
        """在 global_assets/character/ 里放一个指向外部文件的 symlink,应被 resolve-relative 检查拦截为 403。"""
        import os
        import sys

        if sys.platform == "win32":
            import pytest

            pytest.skip("symlinks require admin on Windows")

        client, pm = _client(monkeypatch, tmp_path)

        # 在 tmp_path 下(但不在 global_assets 里)创建一个外部目标文件
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"secret")

        # 在 global_assets/character/ 下建立指向外部目标的 symlink
        global_dir = pm.get_global_assets_root() / "character"
        global_dir.mkdir(parents=True, exist_ok=True)
        link = global_dir / "evil.png"
        os.symlink(outside, link)

        with client:
            r = client.get("/api/v1/global-assets/character/evil.png")
            assert r.status_code == 403


class TestPublicFileRouteServesMediaOnly:
    """公开文件路由只放行媒体目录内的媒体扩展名文件，其余与文件不存在同形返回 404。"""

    @pytest.mark.parametrize(
        "rel_path",
        [
            "storyboards/scene_E1S01.png",
            "end_frames/scene_E1S01.png",
            "videos/scene_E1S01.mp4",
            "renders/episode_1/tl-0000abcd/final_cut.without_narration.no_subtitles.mp4",
            "reference_videos/E1U1.mp4",
            "reference_videos/thumbnails/E1U1.jpg",
            "thumbnails/scene_E1S01.jpg",
            "characters/Alice.png",
            "characters/refs/Alice.webp",
            "characters/refs_audio/Alice.mp3",
            "characters/derivatives/Alice/young.png",
            "scenes/酒馆.jpeg",
            "props/玉佩.png",
            "products/refs/cup_1.jpg",
            "grids/grid_1.png",
            "audio/segment_E1S01.wav",
            "versions/storyboards/scene_E1S01_v1_20260101T000000.png",
            "versions/audio/segment_E1S01_v2_20260101T000000.wav",
            "style_reference.png",
        ],
    )
    def test_media_file_is_served_with_nosniff(self, tmp_path, monkeypatch, rel_path):
        client, pm = _client(monkeypatch, tmp_path)
        target = pm.get_project_path("demo") / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"media-bytes")

        with client:
            resp = client.get(f"/api/v1/files/demo/{rel_path}")
        assert resp.status_code == 200
        assert resp.content == b"media-bytes"
        assert resp.headers["x-content-type-options"] == "nosniff"

    @pytest.mark.parametrize(
        "rel_path",
        [
            "project.json",
            "scripts/episode_1.json",
            "source/chapter.txt",
            "drafts/episode_1/step1_segments.md",
            "subtitles/episode_1/segments.json",
            "output/final.mp4",
            "renders/episode_1/tl-0000abcd/.final_cut.abcd.partial.mp4",
            "renders/episode_1/tl-0000abcd/.final_cut.abcd.work/segment_0000.mp4",
            "renders/episode_1/tl-0000abcd/final_cut.render.json",
            "renders/episode_1/tl-0000abcd/jianying_draft.without_narration.zip",
            "renders/episode_1/tl-0000abcd/.jianying_draft.with_narration.abcd.partial.zip",
            "renders/episode_1/tl-0000abcd/.jianying_draft.with_narration.abcd.work/c1.png",
            "renders/episode_1/tl-0000abcd/jianying_draft.with_narration.render.json",
            "versions/versions.json",
            "grids/grid_1.json",
            "storyboards/page.html",
            "storyboards/page.htm",
            "characters/icon.svg",
            "videos/meta.xml",
            "audio/notes.txt",
            "thumbnails/app.js",
            "versions/scripts/episode_1.png",
            "versions/scene_E1S01.png",
            "style_reference.svg",
            "style_reference.mp4",
            "STYLE_REFERENCE.png",
            "Storyboards/scene_E1S01.png",
            "videos/scene_E1S01.webm",
            "cover.png",
            ".claude/settings.json",
        ],
    )
    def test_non_media_file_returns_404(self, tmp_path, monkeypatch, rel_path):
        client, pm = _client(monkeypatch, tmp_path)
        target = pm.get_project_path("demo") / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"content")

        with client:
            resp = client.get(f"/api/v1/files/demo/{rel_path}")
        assert resp.status_code == 404
        assert b"content" not in resp.content

    def test_non_media_file_and_missing_file_share_not_found_response(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        not_found = zh_errors.MESSAGES["file_not_found"]

        with client:
            existing = client.get("/api/v1/files/demo/project.json")
            missing = client.get("/api/v1/files/demo/missing.json")
        assert existing.status_code == missing.status_code == 404
        assert existing.json() == {"detail": not_found.format(path="project.json")}
        assert missing.json() == {"detail": not_found.format(path="missing.json")}

    def test_media_named_symlink_to_non_media_file_returns_404(self, tmp_path, monkeypatch):
        if sys.platform == "win32":
            pytest.skip("symlinks require admin on Windows")
        client, pm = _client(monkeypatch, tmp_path)
        project_dir = pm.get_project_path("demo")
        link = project_dir / "storyboards" / "scene_E1S01.png"
        link.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(project_dir / "project.json", link)

        with client:
            resp = client.get("/api/v1/files/demo/storyboards/scene_E1S01.png")
        assert resp.status_code == 404

    def test_extension_check_is_case_insensitive(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        target = pm.get_project_path("demo") / "storyboards" / "scene_E1S01.PNG"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"img")

        with client:
            resp = client.get("/api/v1/files/demo/storyboards/scene_E1S01.PNG")
        assert resp.status_code == 200

    def test_global_asset_media_is_served_with_nosniff(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        (pm.get_global_assets_root() / "character" / "abc.webp").write_bytes(b"img")

        with client:
            resp = client.get("/api/v1/global-assets/character/abc.webp")
        assert resp.status_code == 200
        assert resp.headers["x-content-type-options"] == "nosniff"

    @pytest.mark.parametrize("filename", ["abc.svg", "abc.html", "abc.json", "abc.txt"])
    def test_global_asset_non_media_returns_404(self, tmp_path, monkeypatch, filename):
        client, pm = _client(monkeypatch, tmp_path)
        (pm.get_global_assets_root() / "character" / filename).write_bytes(b"content")

        with client:
            resp = client.get(f"/api/v1/global-assets/character/{filename}")
        assert resp.status_code == 404


def _write_png(path: Path, size: tuple[int, int]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (255, 0, 0)).save(path, format="PNG")
    return path


class TestServeProjectFileThumbnails:
    """公开文件路由的 ?w= 缩略图：WebP、档位吸附、小图回原图、非图片忽略，白名单与缓存头不变。"""

    def test_returns_webp_thumbnail_at_snapped_width(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        _write_png(pm.get_project_path("demo") / "storyboards" / "a.png", (1600, 800))

        with client:
            resp = client.get("/api/v1/files/demo/storyboards/a.png?v=1&w=300")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/webp"
        assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert resp.headers["x-content-type-options"] == "nosniff"
        with Image.open(BytesIO(resp.content)) as thumbnail:
            assert thumbnail.format == "WEBP"
            assert thumbnail.size == (320, 160)

    def test_cache_lives_outside_the_project_directory(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        project_dir = pm.get_project_path("demo")
        _write_png(project_dir / "storyboards" / "a.png", (1600, 800))
        before = sorted(p.relative_to(project_dir) for p in project_dir.rglob("*"))

        with client:
            first = client.get("/api/v1/files/demo/storyboards/a.png?w=640")
            second = client.get("/api/v1/files/demo/storyboards/a.png?w=640")

        assert first.status_code == second.status_code == 200
        assert first.content == second.content
        assert first.headers["etag"] == second.headers["etag"]
        assert sorted(p.relative_to(project_dir) for p in project_dir.rglob("*")) == before
        cached = list((pm.layout.image_thumbnail_cache_dir / "demo").rglob("*.webp"))
        assert len(cached) == 1
        assert cached[0].read_bytes() == first.content

    def test_thumbnail_etag_differs_from_original_and_revalidates_to_304(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        _write_png(pm.get_project_path("demo") / "storyboards" / "a.png", (1600, 800))

        with client:
            original = client.get("/api/v1/files/demo/storyboards/a.png")
            thumb = client.get("/api/v1/files/demo/storyboards/a.png?w=320")
            assert thumb.headers["cache-control"] == "no-cache"
            assert thumb.headers["etag"] != original.headers["etag"]
            assert thumb.headers["content-length"] == str(len(thumb.content))

            revalidated = client.get(
                "/api/v1/files/demo/storyboards/a.png?w=320", headers={"If-None-Match": thumb.headers["etag"]}
            )
            assert revalidated.status_code == 304
            assert revalidated.content == b""

            # 原图的 ETag 校验不到缩略图上
            mismatched = client.get(
                "/api/v1/files/demo/storyboards/a.png?w=320", headers={"If-None-Match": original.headers["etag"]}
            )
            assert mismatched.status_code == 200

    def test_source_change_produces_a_new_thumbnail(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        source = _write_png(pm.get_project_path("demo") / "storyboards" / "a.png", (1600, 800))
        os.utime(source, (1_700_000_000, 1_700_000_000))

        with client:
            first = client.get("/api/v1/files/demo/storyboards/a.png?w=320")
            _write_png(source, (1600, 1600))
            os.utime(source, (1_700_000_060, 1_700_000_060))
            second = client.get(
                "/api/v1/files/demo/storyboards/a.png?w=320", headers={"If-None-Match": first.headers["etag"]}
            )

        assert second.status_code == 200
        with Image.open(BytesIO(second.content)) as thumbnail:
            assert thumbnail.size == (320, 320)

    def test_source_replaced_while_thumbnailing_falls_back_to_the_current_original(self, tmp_path, monkeypatch):
        # 生成缩略图期间源图被替换，缩略图放弃发布；回退的原图按替换后的文件计算长度与 ETag
        client, pm = _client(monkeypatch, tmp_path)
        source = _write_png(pm.get_project_path("demo") / "storyboards" / "a.png", (1600, 800))
        os.utime(source, (1_700_000_000, 1_700_000_000))

        real_open = Image.open
        replaced: list[bool] = []

        def _replaced_while_decoding(fp, *args, **kwargs):
            # 缩略图开始解码源图前，源图被重新生成替换
            if not replaced and Path(fp) == source:
                replaced.append(True)
                _write_png(source, (2400, 1800))
                os.utime(source, (1_700_000_060, 1_700_000_060))
            return real_open(fp, *args, **kwargs)

        monkeypatch.setattr(Image, "open", _replaced_while_decoding)

        with client:
            resp = client.get("/api/v1/files/demo/storyboards/a.png?w=320")
            current = client.get("/api/v1/files/demo/storyboards/a.png")

        assert resp.status_code == 200
        assert resp.content == source.read_bytes()
        assert resp.headers["content-length"] == str(len(resp.content))
        assert resp.headers["etag"] == current.headers["etag"]

    def test_original_not_wider_than_target_is_served_as_is(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        source = _write_png(pm.get_project_path("demo") / "storyboards" / "a.png", (300, 900))

        with client:
            resp = client.get("/api/v1/files/demo/storyboards/a.png?w=320")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/png"
        assert resp.content == source.read_bytes()

    @pytest.mark.parametrize("w", ["abc", "0", "-5", "", "1.5"])
    def test_invalid_width_serves_original(self, tmp_path, monkeypatch, w):
        client, pm = _client(monkeypatch, tmp_path)
        source = _write_png(pm.get_project_path("demo") / "storyboards" / "a.png", (1600, 800))

        with client:
            resp = client.get(f"/api/v1/files/demo/storyboards/a.png?w={w}")

        assert resp.status_code == 200
        assert resp.content == source.read_bytes()

    @pytest.mark.parametrize("rel_path", ["videos/a.mp4", "audio/a.wav"])
    def test_non_image_ignores_width(self, tmp_path, monkeypatch, rel_path):
        client, pm = _client(monkeypatch, tmp_path)
        target = pm.get_project_path("demo") / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"0123456789")

        with client:
            resp = client.get(f"/api/v1/files/demo/{rel_path}?w=320")
            ranged = client.get(f"/api/v1/files/demo/{rel_path}?w=320", headers={"Range": "bytes=2-5"})

        assert resp.status_code == 200
        assert resp.content == b"0123456789"
        assert ranged.status_code == 206
        assert ranged.content == b"2345"

    def test_traversal_with_width_is_still_forbidden(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        _write_png(tmp_path / "projects" / "outside.png", (1600, 800))

        with client:
            resp = client.get("/api/v1/files/demo/%2E%2E/outside.png?w=320")

        assert resp.status_code == 403
        assert not pm.layout.image_thumbnail_cache_dir.exists()

    @pytest.mark.parametrize("rel_path", ["cover.png", "versions/scene_E1S01.png", ".hidden/a.png"])
    def test_non_whitelisted_image_with_width_returns_404(self, tmp_path, monkeypatch, rel_path):
        client, pm = _client(monkeypatch, tmp_path)
        _write_png(pm.get_project_path("demo") / rel_path, (1600, 800))

        with client:
            resp = client.get(f"/api/v1/files/demo/{rel_path}?w=320")

        assert resp.status_code == 404
        assert not pm.layout.image_thumbnail_cache_dir.exists()

    def test_versions_snapshot_thumbnail_is_immutable(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        rel_path = "versions/storyboards/scene_E1S01_v1_20260101T000000.png"
        _write_png(pm.get_project_path("demo") / rel_path, (1600, 800))

        with client:
            resp = client.get(f"/api/v1/files/demo/{rel_path}?w=160")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/webp"
        assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"


# ==================== Source 多格式上传 ====================

import io

from tests.auth_deps import AUTH_DEPENDENCIES


def _upload_source(client, project_name: str, filename: str, content: bytes, on_conflict: str | None = None):
    url = f"/api/v1/projects/{project_name}/upload/source"
    if on_conflict:
        url += f"?on_conflict={on_conflict}"
    return client.post(
        url,
        files={"file": (filename, io.BytesIO(content), "application/octet-stream")},
    )


class TestSourceMultiFormatUpload:
    def test_upload_source_utf8_txt_normalized(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            resp = _upload_source(client, "demo", "novel.txt", "纯 UTF-8".encode())
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["normalized"] is True
            assert body["used_encoding"] == "utf-8"
            assert body["original_kept"] is False
            assert body["chapter_count"] == 0

    def test_upload_source_gbk_txt_normalized_and_raw_kept(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            raw = ("第一章\n" * 30).encode("gbk")
            resp = _upload_source(client, "demo", "old.txt", raw)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["normalized"] is True
            assert body["used_encoding"]
            assert body["used_encoding"].lower() != "utf-8"
            assert body["original_kept"] is True

    def test_upload_source_doc_rejected_with_400(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            resp = _upload_source(client, "demo", "x.doc", b"binary")
            assert resp.status_code == 400

    def test_upload_source_conflict_returns_409_with_suggestion(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            _upload_source(client, "demo", "novel.txt", "首次".encode())
            resp = _upload_source(client, "demo", "novel.txt", "再次".encode())
            assert resp.status_code == 409
            body = resp.json()
            assert body["detail"]["existing"] == "novel.txt"
            assert body["detail"]["suggested_name"] == "novel_1"

    def test_upload_source_on_conflict_replace(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            _upload_source(client, "demo", "novel.txt", "旧内容".encode())
            resp = _upload_source(client, "demo", "novel.txt", "新内容".encode(), on_conflict="replace")
            assert resp.status_code == 200, resp.text
            # 通过 GET 拉文本验证已替换
            get_resp = client.get("/api/v1/projects/demo/source/novel.txt")
            assert get_resp.status_code == 200
            assert get_resp.text == "新内容"

    def test_upload_source_on_conflict_rename(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            _upload_source(client, "demo", "novel.txt", "首次".encode())
            resp = _upload_source(client, "demo", "novel.txt", "新版".encode(), on_conflict="rename")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["filename"] == "novel_1.txt"

    def test_delete_source_cascades_raw(self, tmp_path, monkeypatch):
        client, pm = _client(monkeypatch, tmp_path)
        with client:
            raw = ("第一章\n" * 30).encode("gbk")
            _upload_source(client, "demo", "to_delete.txt", raw)
            # 上传后应当存在 raw 备份
            project_dir = pm.get_project_path("demo")
            raw_path = project_dir / "source" / "raw" / "to_delete.txt"
            assert raw_path.exists()

            resp = client.delete("/api/v1/projects/demo/source/to_delete.txt")
            assert resp.status_code == 200
            assert not raw_path.exists()

    def test_upload_source_invalid_on_conflict_returns_422(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/source?on_conflict=bogus",
                files={"file": ("x.txt", io.BytesIO(b"hi"), "text/plain")},
            )
            # FastAPI 用 Literal 自动校验 query param，非法值返回 422
            assert resp.status_code == 422

    def test_upload_source_rejects_oversized_upload_by_content_length(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        from lib.script.source_loader import SourceLoader

        # We don't actually send 50MB+ of data — instead post a small body with a fake
        # content-length header. Starlette validates content-length vs actual body length
        # for multipart, so we need to send a real oversized payload OR rely on the
        # natural stat-based check. Skip the header fake and exercise the stat path:
        body = b"a" * (SourceLoader.DEFAULT_MAX_BYTES + 1024)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/source",
                files={"file": ("big.txt", io.BytesIO(body), "text/plain")},
            )
            assert resp.status_code == 413

    def test_list_files_source_includes_raw_filename(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            raw = ("第一章\n" * 30).encode("gbk")
            _upload_source(client, "demo", "old.txt", raw)
            resp = client.get("/api/v1/projects/demo/files")
            body = resp.json()
            source = body["files"]["source"]
            entry = next(e for e in source if e["name"] == "old.txt")
            assert entry["raw_filename"] == "old.txt"

    def test_list_files_source_raw_filename_none_for_pure_utf8(self, tmp_path, monkeypatch):
        client, _ = _client(monkeypatch, tmp_path)
        with client:
            _upload_source(client, "demo", "novel.txt", "纯 UTF-8".encode())
            resp = client.get("/api/v1/projects/demo/files")
            body = resp.json()
            entry = next(e for e in body["files"]["source"] if e["name"] == "novel.txt")
            assert entry["raw_filename"] is None


def _client_with_pm_raising(monkeypatch, sentinel: str):
    """构造一个最小 app，其 get_project_manager 调用即抛 RuntimeError。

    RuntimeError 不属于 FileNotFoundError / ValueError / UnicodeDecodeError /
    HTTPException，会落到各路由的 except Exception 兜底分支，被映射成通用 500。
    """

    def _raise():
        raise RuntimeError(sentinel)

    monkeypatch.setattr(files, "get_project_manager", _raise)

    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(files.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.include_router(files.public_router, prefix="/api/v1")
    return TestClient(app)


class TestFilesUnexpectedErrorsMapTo500:
    """未预期异常应映射为通用 500，且不在响应体泄露内部异常细节。"""

    def test_upload_file_unexpected_error_maps_to_500(self, monkeypatch):
        sentinel = "upload-boom-a1b2"
        client = _client_with_pm_raising(monkeypatch, sentinel)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/upload/character?name=Alice",
                files={"file": ("alice.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
        assert resp.status_code == 500
        assert sentinel not in resp.text

    def test_list_project_files_unexpected_error_maps_to_500(self, monkeypatch):
        sentinel = "list-boom-c3d4"
        client = _client_with_pm_raising(monkeypatch, sentinel)
        with client:
            resp = client.get("/api/v1/projects/demo/files")
        assert resp.status_code == 500
        assert sentinel not in resp.text

    def test_get_source_file_unexpected_error_maps_to_500(self, monkeypatch):
        sentinel = "get-source-boom-e5f6"
        client = _client_with_pm_raising(monkeypatch, sentinel)
        with client:
            resp = client.get("/api/v1/projects/demo/source/chapter.txt")
        assert resp.status_code == 500
        assert sentinel not in resp.text

    def test_update_source_file_unexpected_error_maps_to_500(self, monkeypatch):
        sentinel = "update-source-boom-7890"
        client = _client_with_pm_raising(monkeypatch, sentinel)
        with client:
            resp = client.put(
                "/api/v1/projects/demo/source/chapter.txt",
                content="updated",
                headers={"content-type": "text/plain"},
            )
        assert resp.status_code == 500
        assert sentinel not in resp.text

    def test_delete_source_file_unexpected_error_maps_to_500(self, monkeypatch):
        sentinel = "delete-source-boom-1a2b"
        client = _client_with_pm_raising(monkeypatch, sentinel)
        with client:
            resp = client.delete("/api/v1/projects/demo/source/chapter.txt")
        assert resp.status_code == 500
        assert sentinel not in resp.text

    def test_upload_style_image_unexpected_error_maps_to_500(self, monkeypatch):
        sentinel = "style-image-boom-3c4d"
        client = _client_with_pm_raising(monkeypatch, sentinel)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/style-image",
                files={"file": ("style.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
        assert resp.status_code == 500
        assert sentinel not in resp.text

    def test_upload_style_image_vision_unsupported_maps_to_localized_400(self, tmp_path, monkeypatch):
        """简单档模型不支持 vision 时，400 detail 走 i18n 翻译，不透出裸中文技术消息。"""
        from lib.backends.text_backends.base import TextTaskType
        from lib.config.resolver import VisionCapabilityError

        async def _raise_vision_error(*args, **kwargs):
            raise VisionCapabilityError(
                task_type=TextTaskType.STYLE_ANALYSIS,
                provider_id="gemini-aistudio",
                model_id="gemini-3.1-flash-lite",
            )

        client, _ = _client(monkeypatch, tmp_path)
        monkeypatch.setattr("lib.backends.text_generator.create_text_backend_for_task", _raise_vision_error)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/style-image",
                files={"file": ("style.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "gemini-aistudio/gemini-3.1-flash-lite" in detail
        assert "vision" in detail
        # 英文 zh 环境默认无 Accept-Language，走中文翻译文案，而非 __str__ 的英文技术消息
        assert "不支持图像输入" in detail

    def test_upload_style_image_backend_value_error_maps_to_500_not_leaked(self, tmp_path, monkeypatch):
        """非 vision 校验的后端构造 ValueError（如凭证文件路径缺失 project_id）不得原样透出为 400。"""
        sentinel = "/secret/vertex_keys/service-account-9f8e.json"

        async def _raise_backend_error(*args, **kwargs):
            raise ValueError(f"凭证文件 {sentinel} 中未找到 project_id")

        client, _ = _client(monkeypatch, tmp_path)
        monkeypatch.setattr("lib.backends.text_generator.create_text_backend_for_task", _raise_backend_error)
        with client:
            resp = client.post(
                "/api/v1/projects/demo/style-image",
                files={"file": ("style.jpg", _img_bytes("JPEG"), "image/jpeg")},
            )
        assert resp.status_code == 500
        assert sentinel not in resp.text
