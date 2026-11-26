"""资产级联重命名端到端测试：真实 ProjectManager 走 扫描 → 校验 → 落盘 全路径。

覆盖四类资产、各 content_mode 骨架的引用改写（引用数组 / speaker / mention）、script_plan 草稿、
关联文件与版本历史迁移、NFC/NFD 冲突拒绝与 dry-run 预览一致性，以及复用同一套扫描的删除前引用预览。speaker 与 mention 不在
DataValidator 引用扫描范围内，须直接断言改写结果，不能只看校验无新增 error。
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from lib.artifacts.artifact_manifest import (
    LOCK_FILENAME,
    MANIFEST_FILENAME,
    ArtifactEntryRekeyPlan,
    ArtifactKey,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.version_manager import VersionManager
from lib.episode.episode_paths import (
    DRAMA_SCRIPT_PLAN_QUARANTINE_FILENAME,
    NARRATION_SCRIPT_PLAN_QUARANTINE_FILENAME,
    REFERENCE_VIDEO_PROMPT_AUTHORING_QUARANTINE_FILENAME,
    REFERENCE_VIDEO_SCRIPT_PLAN_QUARANTINE_FILENAME,
)
from lib.infra.json_io import atomic_write_json
from lib.project.asset_rename import (
    AssetRenameConflictError,
    AssetRenameFileCollisionError,
    AssetRenameHistoryCollisionError,
    AssetRenameNotFoundError,
)
from lib.project.project_manager import ProjectManager
from lib.script.script_review import (
    apply_confirmation,
    content_fingerprint,
    formal_script_plan_confirmed,
    review_status,
    script_plan_path,
)
from tests.asset_rename_scripts import ad_script, drama_script, make_reference_script, narration_script
from tests.fakes import fail_project_json_write


@pytest.fixture
def pm_with_assets(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(str(tmp_path))
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo", "Anime", "narration")
    manager.upsert_assets("demo", "characters", {"角色A": {"description": "主角"}})
    manager.upsert_assets("demo", "scenes", {"场景A": {"description": "村口"}})
    manager.upsert_assets("demo", "props", {"道具A": {"description": "长剑"}})
    return manager


def _project_dir(pm_with_assets: ProjectManager) -> Path:
    return pm_with_assets.get_project_path("demo")


def _load_script(pm_with_assets: ProjectManager) -> dict[str, Any]:
    return pm_with_assets.load_script("demo", "episode_1.json")


def _write_confirmed_plan(manager: ProjectManager, plan: dict[str, Any]) -> Path:
    """写下第 1 集的正式脚本规划并确认它。"""
    manager.save_script("demo", narration_script(), "episode_1.json")
    path = script_plan_path(_project_dir(manager), manager.load_project("demo"), 1)
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, plan)
    fingerprint = content_fingerprint(path)
    assert fingerprint is not None

    def confirm(project: dict[str, Any]) -> None:
        apply_confirmation(project, 1, fingerprint, "2026-10-01T00:00:00+00:00")

    manager.update_project("demo", confirm)
    return path


def _plan_review(manager: ProjectManager) -> tuple[str, bool]:
    """第 1 集的内容确认状态，以及正式脚本规划是否因已确认而只读。"""
    project = manager.load_project("demo")
    project_dir = _project_dir(manager)
    return review_status(project_dir, project, 1), formal_script_plan_confirmed(project_dir, project, 1)


class TestRenameAssetCascade:
    def test_character_rename_cascades_across_modes(self, pm_with_assets: ProjectManager) -> None:
        pm_with_assets.save_script("demo", narration_script(), "episode_1.json")
        pm_with_assets.save_script("demo", make_reference_script(2), "episode_2.json")

        project_dir = _project_dir(pm_with_assets)
        sheet = project_dir / "characters" / "角色A.png"
        sheet.write_bytes(b"png")
        ref_dir = project_dir / "characters" / "refs"
        ref_dir.mkdir(parents=True)
        (ref_dir / "角色A.png").write_bytes(b"ref")

        def _set_paths(project: dict) -> None:
            entry = project["characters"]["角色A"]
            entry["character_sheet"] = "characters/角色A.png"
            entry["reference_image"] = "characters/refs/角色A.png"

        pm_with_assets.update_project("demo", _set_paths)

        report = pm_with_assets.rename_asset("demo", "characters", "角色A", "主角甲")

        assert report.episodes == 2
        assert report.references == 2
        assert report.files == 2

        project = pm_with_assets.load_project("demo")
        assert "角色A" not in project["characters"]
        entry = project["characters"]["主角甲"]
        assert entry["character_sheet"] == "characters/主角甲.png"
        assert entry["reference_image"] == "characters/refs/主角甲.png"
        assert (project_dir / "characters" / "主角甲.png").exists()
        assert not sheet.exists()
        assert (ref_dir / "主角甲.png").exists()

        assert _load_script(pm_with_assets)["segments"][0]["characters_in_segment"] == ["主角甲"]
        unit = pm_with_assets.load_script("demo", "episode_2.json")["video_units"][0]
        assert unit["text"] == "@[主角甲] 走进 @[场景A]"

    def test_script_without_a_canonical_name_is_left_alone(self, pm_with_assets: ProjectManager) -> None:
        """文件名不是规范名的 JSON 不属于任何一集，不随资产改名改写。"""
        pm_with_assets.save_script("demo", narration_script(), "episode_1.json")
        scripts_dir = _project_dir(pm_with_assets) / "scripts"
        orphan = scripts_dir / "custom.json"
        atomic_write_json(orphan, narration_script())
        before = orphan.read_bytes()

        pm_with_assets.rename_asset("demo", "characters", "角色A", "主角甲")

        assert orphan.read_bytes() == before

    def test_rename_keeps_reference_integrity(self, pm_with_assets: ProjectManager) -> None:
        from lib.project.data_validator import DataValidator

        pm_with_assets.save_script("demo", narration_script(), "episode_1.json")
        pm_with_assets.rename_asset("demo", "scenes", "场景A", "新场景")

        validator = DataValidator(str(pm_with_assets.projects_dir))
        result = validator.validate_episode("demo", "episode_1.json")
        assert not [e for e in result.errors if "新场景" in e or "场景A" in e]
        assert _load_script(pm_with_assets)["segments"][0]["scenes"] == ["新场景"]

    def test_script_plan_draft_rewritten(self, pm_with_assets: ProjectManager) -> None:
        draft_dir = _project_dir(pm_with_assets) / "drafts" / "episode_1"
        draft_dir.mkdir(parents=True)
        draft = {
            "units": [
                {
                    "unit_id": "E1U1",
                    "text": "@[角色A] 在河边",
                    "duration_seconds": 8,
                }
            ]
        }
        atomic_write_json(draft_dir / "script_plan_reference_units.json", draft)

        report = pm_with_assets.rename_asset("demo", "characters", "角色A", "主角甲")

        assert report.episodes == 1
        assert report.references == 1
        saved = json.loads((draft_dir / "script_plan_reference_units.json").read_text(encoding="utf-8"))
        assert saved["units"][0]["text"] == "@[主角甲] 在河边"

    def test_confirmed_episode_stays_confirmed_and_its_merge_target_follows(
        self, pm_with_assets: ProjectManager
    ) -> None:
        """改名只换名字：已确认的集不退回待确认，规划里并入该资产的新增项随之改指新名。"""
        path = _write_confirmed_plan(
            pm_with_assets,
            {
                "segments": [{"segment_id": "E1S01", "characters_in_segment": ["角色A"], "scenes": [], "props": []}],
                "new_assets": [
                    {"type": "character", "name": "小A", "decision": "merge", "reason": "同一人", "target": "角色A"}
                ],
            },
        )
        assert _plan_review(pm_with_assets) == ("confirmed", True)

        report = pm_with_assets.rename_asset("demo", "characters", "角色A", "主角甲")

        assert _plan_review(pm_with_assets) == ("confirmed", True)
        plan = json.loads(path.read_text(encoding="utf-8"))
        assert plan["segments"][0]["characters_in_segment"] == ["主角甲"]
        assert plan["new_assets"][0]["target"] == "主角甲"
        assert report.references == 3

    def test_sibling_with_numeric_suffix_untouched(self, pm_with_assets: ProjectManager) -> None:
        """``旧名_2`` 是合法资产名：兄弟资产的资产图不得被序号形态的 stem 匹配卷走。"""
        pm_with_assets.upsert_assets("demo", "characters", {"角色A_2": {"description": "副手"}})
        project_dir = _project_dir(pm_with_assets)
        sibling = project_dir / "characters" / "角色A_2.png"
        sibling.write_bytes(b"sibling")
        (project_dir / "characters" / "角色A.png").write_bytes(b"png")

        def _set_paths(project: dict) -> None:
            project["characters"]["角色A"]["character_sheet"] = "characters/角色A.png"
            project["characters"]["角色A_2"]["character_sheet"] = "characters/角色A_2.png"

        pm_with_assets.update_project("demo", _set_paths)

        report = pm_with_assets.rename_asset("demo", "characters", "角色A", "主角甲")

        assert report.files == 1
        assert sibling.exists()
        project = pm_with_assets.load_project("demo")
        assert project["characters"]["角色A_2"]["character_sheet"] == "characters/角色A_2.png"
        assert project["characters"]["主角甲"]["character_sheet"] == "characters/主角甲.png"

    def test_product_sequenced_files_and_paths(self, tmp_path: Path) -> None:
        pm_with_assets = ProjectManager(str(tmp_path))
        pm_with_assets.create_project("demo", content_mode="ad")
        pm_with_assets.create_project_metadata("demo", "Demo", "Anime", "ad")
        pm_with_assets.upsert_assets("demo", "products", {"商品A": {"description": "饮料"}})
        pm_with_assets.upsert_assets("demo", "characters", {"角色A": {"description": "代言人"}})
        pm_with_assets.save_script("demo", ad_script(), "episode_1.json")

        project_dir = pm_with_assets.get_project_path("demo")
        refs = project_dir / "products" / "refs"
        refs.mkdir(parents=True)
        (refs / "商品A_1.png").write_bytes(b"a")
        (refs / "商品A_2.png").write_bytes(b"b")

        def _set_paths(project: dict) -> None:
            project["products"]["商品A"]["reference_images"] = [
                "products/refs/商品A_1.png",
                "products/refs/商品A_2.png",
            ]

        pm_with_assets.update_project("demo", _set_paths)

        report = pm_with_assets.rename_asset("demo", "products", "商品A", "爆款")

        assert report.files == 2
        assert sorted(f.name for f in refs.iterdir() if f.is_file() and not f.name.startswith(".")) == [
            "爆款_1.png",
            "爆款_2.png",
        ]
        project = pm_with_assets.load_project("demo")
        assert project["products"]["爆款"]["reference_images"] == [
            "products/refs/爆款_1.png",
            "products/refs/爆款_2.png",
        ]
        assert pm_with_assets.load_script("demo", "episode_1.json")["shots"][0]["products_in_shot"] == ["爆款"]

    def test_version_history_migrated(self, pm_with_assets: ProjectManager) -> None:
        project_dir = _project_dir(pm_with_assets)
        sheet = project_dir / "characters" / "角色A.png"
        sheet.write_bytes(b"v1")
        vm = VersionManager(project_dir)
        vm.add_version("characters", "角色A", "第一版", source_file=sheet)

        report = pm_with_assets.rename_asset("demo", "characters", "角色A", "主角甲")

        assert report.files == 2
        info = vm.get_versions("characters", "主角甲")
        assert info["current_version"] == 1
        version_file = project_dir / info["versions"][0]["file"]
        assert version_file.exists()
        assert version_file.name.startswith("主角甲_v1_")
        assert vm.get_versions("characters", "角色A") == {"current_version": 0, "versions": []}

    def test_active_manifest_claim_migrates_with_sheet_identity(self, pm_with_assets: ProjectManager) -> None:
        project_dir = _project_dir(pm_with_assets)
        sheet = project_dir / "characters" / "角色A.png"
        sheet.write_bytes(b"current-sheet")
        pm_with_assets.update_project(
            "demo",
            lambda project: project["characters"]["角色A"].update({"character_sheet": "characters/角色A.png"}),
        )
        adapter = ProjectArtifactManifestAdapter(project_dir)
        old_key = ArtifactKey.asset_sheet("character", "角色A")
        old_entry = ArtifactManifestEntry(
            artifact_path="characters/角色A.png",
            basis_digest="sha256-v1:" + "a" * 64,
        )
        adapter.put_entry(old_key, old_entry)

        pm_with_assets.rename_asset("demo", "characters", "角色A", "主角甲")

        assert adapter.get_entry(old_key) is None
        assert adapter.get_entry(ArtifactKey.asset_sheet("character", "主角甲")) == ArtifactManifestEntry(
            artifact_path="characters/主角甲.png",
            basis_digest=old_entry.basis_digest,
        )

    def test_project_binding_commits_before_manifest_claim_rekey(
        self,
        pm_with_assets: ProjectManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        project_dir = _project_dir(pm_with_assets)
        sheet = project_dir / "characters" / "角色A.png"
        sheet.write_bytes(b"current-sheet")
        pm_with_assets.update_project(
            "demo",
            lambda project: project["characters"]["角色A"].update({"character_sheet": "characters/角色A.png"}),
        )
        adapter = ProjectArtifactManifestAdapter(project_dir)
        adapter.put_entry(
            ArtifactKey.asset_sheet("character", "角色A"),
            ArtifactManifestEntry("characters/角色A.png", "sha256-v1:" + "a" * 64),
        )
        original_commit = ArtifactEntryRekeyPlan.commit
        project_seen_during_commit: dict[str, Any] = {}

        def _commit(plan: ArtifactEntryRekeyPlan):
            project_seen_during_commit.update(json.loads((project_dir / "project.json").read_text(encoding="utf-8")))
            return original_commit(plan)

        monkeypatch.setattr(ArtifactEntryRekeyPlan, "commit", _commit)

        pm_with_assets.rename_asset("demo", "characters", "角色A", "主角甲")

        assert "主角甲" in project_seen_during_commit["characters"]
        assert "角色A" not in project_seen_during_commit["characters"]

    def test_retry_recovers_claim_after_project_binding_committed_first(self, pm_with_assets: ProjectManager) -> None:
        project_dir = _project_dir(pm_with_assets)
        old_sheet = project_dir / "characters" / "角色A.png"
        new_sheet = project_dir / "characters" / "主角甲.png"
        old_sheet.write_bytes(b"current-sheet")
        pm_with_assets.update_project(
            "demo",
            lambda project: project["characters"]["角色A"].update({"character_sheet": "characters/角色A.png"}),
        )
        adapter = ProjectArtifactManifestAdapter(project_dir)
        old_key = ArtifactKey.asset_sheet("character", "角色A")
        new_key = ArtifactKey.asset_sheet("character", "主角甲")
        old_entry = ArtifactManifestEntry("characters/角色A.png", "sha256-v1:" + "a" * 64)
        adapter.put_entry(old_key, old_entry)
        old_sheet.replace(new_sheet)

        def _commit_project_binding(project: dict[str, object]) -> None:
            characters = project["characters"]
            assert isinstance(characters, dict)
            entry = characters.pop("角色A")
            assert isinstance(entry, dict)
            entry["character_sheet"] = "characters/主角甲.png"
            characters["主角甲"] = entry

        pm_with_assets.update_project("demo", _commit_project_binding)

        report = pm_with_assets.rename_asset("demo", "characters", "角色A", "主角甲")

        assert (report.episodes, report.references, report.files, report.dry_run) == (0, 0, 0, False)
        assert adapter.get_entry(old_key) is None
        assert adapter.get_entry(new_key) == ArtifactManifestEntry(
            "characters/主角甲.png",
            old_entry.basis_digest,
        )

    def test_project_write_failure_compensates_manifest_claim_rekey(
        self,
        pm_with_assets: ProjectManager,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        project_dir = _project_dir(pm_with_assets)
        sheet = project_dir / "characters" / "角色A.png"
        sheet.write_bytes(b"current-sheet")
        pm_with_assets.update_project(
            "demo",
            lambda project: project["characters"]["角色A"].update({"character_sheet": "characters/角色A.png"}),
        )
        adapter = ProjectArtifactManifestAdapter(project_dir)
        old_key = ArtifactKey.asset_sheet("character", "角色A")
        old_entry = ArtifactManifestEntry(
            artifact_path="characters/角色A.png",
            basis_digest="sha256-v1:" + "a" * 64,
        )
        adapter.put_entry(old_key, old_entry)
        project_before = (project_dir / "project.json").read_bytes()
        fail_project_json_write(monkeypatch, project_dir / "project.json", after_write=True)

        with pytest.raises(OSError, match="injected project write failure"):
            pm_with_assets.rename_asset("demo", "characters", "角色A", "主角甲")

        assert adapter.get_entry(old_key) == old_entry
        assert adapter.get_entry(ArtifactKey.asset_sheet("character", "主角甲")) is None
        assert (project_dir / "project.json").read_bytes() == project_before
        assert "角色A" in pm_with_assets.load_project("demo")["characters"]

    def test_manifest_rekey_retry_updates_path_after_the_file_move_already_completed(
        self,
        pm_with_assets: ProjectManager,
    ) -> None:
        project_dir = _project_dir(pm_with_assets)
        old_sheet = project_dir / "characters" / "角色A.png"
        new_sheet = project_dir / "characters" / "主角甲.png"
        old_sheet.write_bytes(b"current-sheet")
        pm_with_assets.update_project(
            "demo",
            lambda project: project["characters"]["角色A"].update({"character_sheet": "characters/角色A.png"}),
        )
        adapter = ProjectArtifactManifestAdapter(project_dir)
        old_key = ArtifactKey.asset_sheet("character", "角色A")
        old_entry = ArtifactManifestEntry(
            artifact_path="characters/角色A.png",
            basis_digest="sha256-v1:" + "a" * 64,
        )
        adapter.put_entry(old_key, old_entry)
        old_sheet.replace(new_sheet)

        pm_with_assets.rename_asset("demo", "characters", "角色A", "主角甲")

        assert adapter.get_entry(old_key) is None
        assert adapter.get_entry(ArtifactKey.asset_sheet("character", "主角甲")) == ArtifactManifestEntry(
            artifact_path="characters/主角甲.png",
            basis_digest=old_entry.basis_digest,
        )

    def test_conflict_rejected_atomically(self, pm_with_assets: ProjectManager) -> None:
        pm_with_assets.save_script("demo", narration_script(), "episode_1.json")
        nfd = unicodedata.normalize("NFD", "café")

        def _add_nfd_key(project: dict) -> None:
            project["characters"][nfd] = {"description": "存量 NFD key"}

        pm_with_assets.update_project("demo", _add_nfd_key)

        with pytest.raises(AssetRenameConflictError) as exc_info:
            pm_with_assets.rename_asset("demo", "characters", "角色A", "café")

        assert exc_info.value.conflict_name == nfd
        project = pm_with_assets.load_project("demo")
        assert "角色A" in project["characters"]
        assert _load_script(pm_with_assets)["segments"][0]["characters_in_segment"] == ["角色A"]

    def test_orphan_destination_file_rejected_atomically(self, pm_with_assets: ProjectManager) -> None:
        """新名下的孤儿文件没有对应资产，资产桶冲突检查看不见它，须在迁移前独立拦下。"""
        pm_with_assets.save_script("demo", narration_script(), "episode_1.json")
        project_dir = _project_dir(pm_with_assets)
        sheet = project_dir / "characters" / "角色A.png"
        sheet.write_bytes(b"sheet")
        orphan = project_dir / "characters" / "主角甲.png"
        orphan.write_bytes(b"orphan")

        with pytest.raises(AssetRenameFileCollisionError):
            pm_with_assets.rename_asset("demo", "characters", "角色A", "主角甲", dry_run=True)

        with pytest.raises(AssetRenameFileCollisionError) as exc_info:
            pm_with_assets.rename_asset("demo", "characters", "角色A", "主角甲")

        assert exc_info.value.destination == orphan
        assert orphan.read_bytes() == b"orphan"
        assert sheet.read_bytes() == b"sheet"
        assert "角色A" in pm_with_assets.load_project("demo")["characters"]
        assert _load_script(pm_with_assets)["segments"][0]["characters_in_segment"] == ["角色A"]

    def test_case_only_rename_not_treated_as_collision(self, pm_with_assets: ProjectManager) -> None:
        """大小写不敏感的文件系统上目标解析回源文件自身，那不是占用。

        文件系统区分大小写时（多数 Linux）走不到该豁免分支，改名照样通过，断言仍成立。
        """
        pm_with_assets.upsert_assets("demo", "characters", {"Alice": {"description": "主角"}})
        sheet = _project_dir(pm_with_assets) / "characters" / "Alice.png"
        sheet.write_bytes(b"sheet")

        report = pm_with_assets.rename_asset("demo", "characters", "Alice", "alice")

        assert report.files == 1
        assert "alice" in pm_with_assets.load_project("demo")["characters"]
        assert (_project_dir(pm_with_assets) / "characters" / "alice.png").read_bytes() == b"sheet"

    def test_duplicate_destination_rejected(self, pm_with_assets: ProjectManager) -> None:
        """两个视觉同名的存量文件（NFC / NFD）会撞到同一目标，后一次迁移吃掉前一次的成果。"""
        chars = _project_dir(pm_with_assets) / "characters"
        nfd = unicodedata.normalize("NFD", "café")
        nfc = unicodedata.normalize("NFC", "café")
        (chars / f"{nfd}.png").write_bytes(b"nfd")
        (chars / f"{nfc}.png").write_bytes(b"nfc")
        if len([p for p in chars.iterdir() if p.suffix == ".png"]) < 2:
            pytest.skip("文件系统归一化文件名，两种编码落到同一文件，构造不出同批撞车")
        pm_with_assets.upsert_assets("demo", "characters", {nfc: {"description": "存量"}})

        with pytest.raises(AssetRenameFileCollisionError):
            pm_with_assets.rename_asset("demo", "characters", nfc, "主角甲")

        assert (chars / f"{nfd}.png").read_bytes() == b"nfd"
        assert (chars / f"{nfc}.png").read_bytes() == b"nfc"

    def test_legacy_quarantine_drafts_are_not_scanned_or_modified(self, pm_with_assets: ProjectManager) -> None:
        """废弃的 4 个 *.invalid.json 已从资产级联扫描清单剔除，改名不扫描、不改写也不删除它们。"""
        draft_dir = _project_dir(pm_with_assets) / "drafts" / "episode_1"
        draft_dir.mkdir(parents=True)
        drafts = {
            REFERENCE_VIDEO_SCRIPT_PLAN_QUARANTINE_FILENAME: {
                "kind": "reference_video_script_plan",
                "episode": 1,
                "meta": {},
                "violations": [],
                "content": {"units": [{"duration_seconds": 8, "source_text": "原文", "text": "@[角色A] 在河边"}]},
            },
            REFERENCE_VIDEO_PROMPT_AUTHORING_QUARANTINE_FILENAME: {
                "kind": "reference_video_prompt_authoring",
                "episode": 1,
                "meta": {},
                "violations": [],
                "content": {"title": "标题", "units": [{"text": "@[角色A] 抬头"}]},
            },
            DRAMA_SCRIPT_PLAN_QUARANTINE_FILENAME: {
                "kind": "drama_script_plan",
                "episode": 1,
                "meta": {},
                "violations": [],
                "content": {
                    "title": "标题",
                    "scenes": [
                        {
                            "scene_id": "E1S01",
                            "duration_seconds": 8,
                            "segment_break": False,
                            "characters_in_scene": ["角色A"],
                            "scenes": [],
                            "props": [],
                            "scene_description": "河边",
                            "utterances": [{"kind": "dialogue", "speaker": "角色A", "text": "我来了"}],
                            "source_text": "原文",
                        }
                    ],
                },
            },
            NARRATION_SCRIPT_PLAN_QUARANTINE_FILENAME: {
                "kind": "narration_script_plan",
                "episode": 1,
                "meta": {},
                "violations": [],
                "content": {
                    "segments": [
                        {
                            "segment_id": "E1S01",
                            "novel_text": "原文",
                            "duration_seconds": 8,
                            "segment_break": False,
                            "characters_in_segment": ["角色A"],
                            "scenes": [],
                            "props": [],
                        }
                    ]
                },
            },
        }
        before_bytes: dict[str, bytes] = {}
        for filename, payload in drafts.items():
            path = draft_dir / filename
            atomic_write_json(path, payload)
            before_bytes[filename] = path.read_bytes()

        report = pm_with_assets.rename_asset("demo", "characters", "角色A", "主角甲")

        assert report.references == 0
        assert report.episodes == 0
        for filename, raw in before_bytes.items():
            assert (draft_dir / filename).read_bytes() == raw

    def test_history_under_new_name_rejected_atomically(self, pm_with_assets: ProjectManager) -> None:
        """删除资产只删资产桶 key、版本历史留存，改名过去会不可恢复地覆盖它——整体拒绝。"""
        project_dir = _project_dir(pm_with_assets)
        pm_with_assets.upsert_assets("demo", "characters", {"角色B": {"description": "配角"}})
        sheet_b = project_dir / "characters" / "角色B.png"
        sheet_b.write_bytes(b"b1")
        vm = VersionManager(project_dir)
        vm.add_version("characters", "角色B", "配角初版", source_file=sheet_b)
        pm_with_assets.update_project("demo", lambda project: project["characters"].pop("角色B"))
        sheet_b.unlink()

        sheet_a = project_dir / "characters" / "角色A.png"
        sheet_a.write_bytes(b"a1")
        vm.add_version("characters", "角色A", "主角初版", source_file=sheet_a)

        for dry_run in (True, False):
            with pytest.raises(AssetRenameHistoryCollisionError):
                pm_with_assets.rename_asset("demo", "characters", "角色A", "角色B", dry_run=dry_run)

        assert vm.get_versions("characters", "角色B")["current_version"] == 1
        assert vm.get_versions("characters", "角色A")["current_version"] == 1
        assert "角色A" in pm_with_assets.load_project("demo")["characters"]

    def test_history_under_equivalent_key_is_same_asset(self, pm_with_assets: ProjectManager) -> None:
        """新名解析回记录自身（仅换编码形式）时是同一份历史，不算占用。"""
        project_dir = _project_dir(pm_with_assets)
        pm_with_assets.upsert_assets("demo", "characters", {"café": {"description": "存量"}})
        nfd = unicodedata.normalize("NFD", "café")
        sheet = project_dir / "characters" / f"{nfd}.png"
        sheet.write_bytes(b"v1")
        vm = VersionManager(project_dir)
        vm.add_version("characters", nfd, "第一版", source_file=sheet)

        vm.rename_resource("characters", nfd, "café")

        bucket = vm.sqlite.snapshot()["characters"]
        assert list(bucket) == ["café"]
        assert vm.get_versions("characters", "café")["current_version"] == 1

    def test_equivalent_bucket_keys_collapsed(self, pm_with_assets: ProjectManager) -> None:
        """NFC / NFD 并存的存量资产桶 key 一并收编，否则等价 key 顶着旧名带失效路径残留。"""
        project = pm_with_assets.load_project("demo")
        nfd = unicodedata.normalize("NFD", "café")
        project["characters"] = {
            nfd: {"description": "存量 NFD", "character_sheet": f"characters/{nfd}.png"},
            "café": {"description": "存量 NFC", "character_sheet": "characters/café.png"},
        }
        atomic_write_json(_project_dir(pm_with_assets) / "project.json", project)

        pm_with_assets.rename_asset("demo", "characters", "café", "主角甲")

        characters = pm_with_assets.load_project("demo")["characters"]
        assert list(characters) == ["主角甲"]
        assert characters["主角甲"]["description"] == "存量 NFC"

    def test_equivalent_version_history_keys_collapsed(self, pm_with_assets: ProjectManager) -> None:
        """版本桶按原始 resource_id 建 key，NFC / NFD 两条记录一并收编到新名下。"""
        project_dir = _project_dir(pm_with_assets)
        nfd = unicodedata.normalize("NFD", "café")
        pm_with_assets.upsert_assets("demo", "characters", {"café": {"description": "存量"}})
        sheet = project_dir / "characters" / "café.png"
        sheet.write_bytes(b"v1")
        vm = VersionManager(project_dir)
        vm.add_version("characters", nfd, "NFD 记录", source_file=sheet)
        vm.add_version("characters", "café", "NFC 记录", source_file=sheet)

        vm.rename_resource("characters", "café", "咖啡师")

        bucket = vm.sqlite.snapshot()["characters"]
        assert list(bucket) == ["咖啡师"]

    def test_leading_dot_name_files_migrated(self, pm_with_assets: ProjectManager) -> None:
        """前导点是合法资产名：其落盘文件须随改名迁移，否则 entry 路径字段指向不存在的文件。"""
        project_dir = _project_dir(pm_with_assets)
        pm_with_assets.upsert_assets("demo", "characters", {".甲": {"description": "点号开头"}})
        sheet = project_dir / "characters" / ".甲.png"
        sheet.write_bytes(b"png")
        pm_with_assets.update_project(
            "demo",
            lambda project: project["characters"][".甲"].update({"character_sheet": "characters/.甲.png"}),
        )

        pm_with_assets.rename_asset("demo", "characters", ".甲", "主角甲")

        assert not sheet.exists()
        assert (project_dir / "characters" / "主角甲.png").exists()
        assert pm_with_assets.load_project("demo")["characters"]["主角甲"]["character_sheet"] == "characters/主角甲.png"

    def test_missing_old_name_hints_idempotency(self, pm_with_assets: ProjectManager) -> None:
        with pytest.raises(AssetRenameNotFoundError) as exc_info:
            pm_with_assets.rename_asset("demo", "characters", "不存在", "角色A")
        assert "可能上次重命名已成功" in str(exc_info.value)

        with pytest.raises(AssetRenameNotFoundError) as plain:
            pm_with_assets.rename_asset("demo", "characters", "不存在", "全新名字")
        assert "可能上次重命名已成功" not in str(plain.value)

    def test_dry_run_previews_without_writing(self, pm_with_assets: ProjectManager) -> None:
        pm_with_assets.save_script("demo", narration_script(), "episode_1.json")
        project_dir = _project_dir(pm_with_assets)
        sheet = project_dir / "characters" / "角色A.png"
        sheet.write_bytes(b"png")
        (project_dir / MANIFEST_FILENAME).unlink(missing_ok=True)
        (project_dir / LOCK_FILENAME).unlink(missing_ok=True)

        preview = pm_with_assets.rename_asset("demo", "characters", "角色A", "主角甲", dry_run=True)

        assert preview.dry_run is True
        assert sheet.exists()
        assert not (project_dir / MANIFEST_FILENAME).exists()
        assert not (project_dir / LOCK_FILENAME).exists()
        assert "角色A" in pm_with_assets.load_project("demo")["characters"]
        assert _load_script(pm_with_assets)["segments"][0]["characters_in_segment"] == ["角色A"]

        executed = pm_with_assets.rename_asset("demo", "characters", "角色A", "主角甲")
        assert (executed.episodes, executed.references, executed.files) == (
            preview.episodes,
            preview.references,
            preview.files,
        )

    def test_preexisting_error_naming_the_asset_does_not_block_rename(self, pm_with_assets: ProjectManager) -> None:
        """历史遗留错误里点名了该资产时，改名不算「更坏」：错误随名字换了措辞，不是新增。"""
        project = pm_with_assets.load_project("demo")
        project["characters"]["角色A"]["description"] = ""
        atomic_write_json(_project_dir(pm_with_assets) / "project.json", project)

        report = pm_with_assets.rename_asset("demo", "characters", "角色A", "主角甲")

        assert report.new_name == "主角甲"
        assert "主角甲" in pm_with_assets.load_project("demo")["characters"]

    def test_dry_run_preserves_version_directory(self, pm_with_assets: ProjectManager) -> None:
        """零写入承诺覆盖目录：预演不得在项目下建出空的 ``versions/`` 目录树。"""
        pm_with_assets.save_script("demo", narration_script(), "episode_1.json")
        versions_dir = _project_dir(pm_with_assets) / "versions"
        before = set(versions_dir.rglob("*"))

        pm_with_assets.rename_asset("demo", "characters", "角色A", "主角甲", dry_run=True)

        assert set(versions_dir.rglob("*")) == before

    def test_invalid_new_name_rejected(self, pm_with_assets: ProjectManager) -> None:
        with pytest.raises(ValueError, match=r"含非法字符"):
            pm_with_assets.rename_asset("demo", "characters", "角色A", "坏/名字")
        with pytest.raises(ValueError, match=r"未知资产表"):
            pm_with_assets.rename_asset("demo", "unknown_table", "角色A", "新名")


class TestDerivativeReferenceCascade:
    """``角色/衍生`` 的级联改名（docs/adr/0072）：本体改名连带，衍生改名只动自己那一套。"""

    @staticmethod
    def _register(manager: ProjectManager, *derivatives: str) -> None:
        def _add(project: dict) -> None:
            project["characters"]["角色A"]["derivatives"] = {
                name: {"description": f"{name}的变化", "character_sheet": ""} for name in derivatives
            }

        manager.update_project("demo", _add)

    @staticmethod
    def _script_with_derivative_references() -> dict[str, Any]:
        script = narration_script()
        script["segments"][0]["characters_in_segment"] = ["角色A", "角色A/劲装"]
        return script

    def test_character_rename_rewrites_every_derivative_form(self, pm_with_assets: ProjectManager) -> None:
        self._register(pm_with_assets, "劲装")
        pm_with_assets.save_script("demo", self._script_with_derivative_references(), "episode_1.json")
        reference_script = make_reference_script(2)
        reference_script["video_units"][0]["text"] = "@[角色A/劲装] 推门。@[角色A/劲装]{我来了}"
        pm_with_assets.save_script("demo", reference_script, "episode_2.json")

        pm_with_assets.rename_asset("demo", "characters", "角色A", "主角甲")

        assert _load_script(pm_with_assets)["segments"][0]["characters_in_segment"] == ["主角甲", "主角甲/劲装"]
        unit = pm_with_assets.load_script("demo", "episode_2.json")["video_units"][0]
        assert unit["text"] == "@[主角甲/劲装] 推门。@[主角甲/劲装]{我来了}"
        assert list(pm_with_assets.load_project("demo")["characters"]["主角甲"]["derivatives"]) == ["劲装"]

    def test_derivative_rename_rewrites_only_that_form(self, pm_with_assets: ProjectManager) -> None:
        self._register(pm_with_assets, "劲装", "兽化")
        script = self._script_with_derivative_references()
        script["segments"][0]["characters_in_segment"] = ["角色A", "角色A/劲装", "角色A/兽化"]
        pm_with_assets.save_script("demo", script, "episode_1.json")
        reference_script = make_reference_script(2)
        reference_script["video_units"][0]["text"] = "@[角色A] @[角色A/劲装] @[角色A/兽化]"
        pm_with_assets.save_script("demo", reference_script, "episode_2.json")

        entry = pm_with_assets.rename_asset_derivative("character", "demo", "角色A", "劲装", "夜行衣")

        assert list(entry["derivatives"]) == ["夜行衣", "兽化"]
        assert _load_script(pm_with_assets)["segments"][0]["characters_in_segment"] == [
            "角色A",
            "角色A/夜行衣",
            "角色A/兽化",
        ]
        unit = pm_with_assets.load_script("demo", "episode_2.json")["video_units"][0]
        assert unit["text"] == "@[角色A] @[角色A/夜行衣] @[角色A/兽化]"

    def test_derivative_rename_skips_an_unbound_script_without_an_episode_number(
        self, pm_with_assets: ProjectManager
    ) -> None:
        """衍生改名与本体改名同一口径：锁内认不出归属的无集号剧本跳过，不中断整次改名。"""
        self._register(pm_with_assets, "劲装")
        pm_with_assets.save_script("demo", self._script_with_derivative_references(), "episode_1.json")
        scripts_dir = _project_dir(pm_with_assets) / "scripts"
        unbound = scripts_dir / "custom.json"
        atomic_write_json(unbound, self._script_with_derivative_references())
        before = unbound.read_bytes()

        pm_with_assets.rename_asset_derivative("character", "demo", "角色A", "劲装", "夜行衣")

        assert _load_script(pm_with_assets)["segments"][0]["characters_in_segment"] == ["角色A", "角色A/夜行衣"]
        assert unbound.read_bytes() == before

    def test_derivative_rename_keeps_a_confirmed_episode_confirmed(self, pm_with_assets: ProjectManager) -> None:
        self._register(pm_with_assets, "劲装")
        path = _write_confirmed_plan(
            pm_with_assets,
            {"segments": [{"segment_id": "E1S01", "characters_in_segment": ["角色A/劲装"], "scenes": [], "props": []}]},
        )

        pm_with_assets.rename_asset_derivative("character", "demo", "角色A", "劲装", "夜行衣")

        assert _plan_review(pm_with_assets) == ("confirmed", True)
        plan = json.loads(path.read_text(encoding="utf-8"))
        assert plan["segments"][0]["characters_in_segment"] == ["角色A/夜行衣"]

    def test_derivative_rename_keeps_the_description(self, pm_with_assets: ProjectManager) -> None:
        self._register(pm_with_assets, "劲装")

        pm_with_assets.rename_asset_derivative("character", "demo", "角色A", "劲装", "夜行衣")

        derivatives = pm_with_assets.load_project("demo")["characters"]["角色A"]["derivatives"]
        assert derivatives["夜行衣"]["description"] == "劲装的变化"

    def test_derivative_rename_rejects_a_name_taken_by_a_sibling(self, pm_with_assets: ProjectManager) -> None:
        self._register(pm_with_assets, "劲装", "兽化")

        with pytest.raises(AssetRenameConflictError):
            pm_with_assets.rename_asset_derivative("character", "demo", "角色A", "劲装", "兽化")

        assert list(pm_with_assets.load_project("demo")["characters"]["角色A"]["derivatives"]) == ["劲装", "兽化"]

    def test_derivative_rename_reports_a_missing_derivative(self, pm_with_assets: ProjectManager) -> None:
        self._register(pm_with_assets, "劲装")

        with pytest.raises(AssetRenameNotFoundError):
            pm_with_assets.rename_asset_derivative("character", "demo", "角色A", "夜行衣", "便装")

    def test_derivative_rename_reports_a_missing_character(self, pm_with_assets: ProjectManager) -> None:
        with pytest.raises(KeyError):
            pm_with_assets.rename_asset_derivative("character", "demo", "无此角色", "劲装", "夜行衣")

    def test_derivative_rename_rejects_an_illegal_name_before_touching_anything(
        self, pm_with_assets: ProjectManager
    ) -> None:
        self._register(pm_with_assets, "劲装")

        with pytest.raises(ValueError, match="非法字符"):
            pm_with_assets.rename_asset_derivative("character", "demo", "角色A", "劲装", "夜/行衣")

        assert list(pm_with_assets.load_project("demo")["characters"]["角色A"]["derivatives"]) == ["劲装"]

    def test_derivative_rename_leaves_the_speaker_field_alone(self, pm_with_assets: ProjectManager) -> None:
        """``speaker`` 只承载本体名，衍生改名不该动它。"""
        self._register(pm_with_assets, "劲装")
        pm_with_assets.save_script("demo", drama_script(), "episode_1.json")

        pm_with_assets.rename_asset_derivative("character", "demo", "角色A", "劲装", "夜行衣")

        assert _load_script(pm_with_assets)["scenes"][0]["utterances"][0]["speaker"] == "角色A"

    def test_derivative_rename_leaves_legacy_quarantined_drafts_untouched(self, pm_with_assets: ProjectManager) -> None:
        self._register(pm_with_assets, "劲装")
        draft_path = (
            _project_dir(pm_with_assets) / "drafts" / "episode_1" / REFERENCE_VIDEO_SCRIPT_PLAN_QUARANTINE_FILENAME
        )
        draft_path.parent.mkdir(parents=True)
        atomic_write_json(draft_path, {"units": [{"unit_id": "E1U1", "text": "@[角色A/劲装] 推门"}]})
        before = draft_path.read_bytes()

        pm_with_assets.rename_asset_derivative("character", "demo", "角色A", "劲装", "夜行衣")

        assert draft_path.read_bytes() == before


class TestAssetDeletionPreview:
    """删除资产的 dry_run：只读扫描，引用数与重命名同一套扫描，按集列出。"""

    @staticmethod
    def _snapshot(project_dir: Path) -> dict[str, bytes]:
        """项目目录下全部文件的字节，包括已有锁文件。"""
        return {
            str(path.relative_to(project_dir)): path.read_bytes() for path in project_dir.rglob("*") if path.is_file()
        }

    def test_preview_lists_references_by_episode_without_writing(self, pm_with_assets: ProjectManager) -> None:
        TestDerivativeReferenceCascade._register(pm_with_assets, "劲装")
        pm_with_assets.save_script("demo", drama_script(), "episode_1.json")
        reference_script = make_reference_script(3)
        reference_script["video_units"][0]["text"] = "@[角色A] 回头，@[角色A/劲装] 推门"
        pm_with_assets.save_script("demo", reference_script, "episode_3.json")
        draft_path = _project_dir(pm_with_assets) / "drafts" / "episode_2" / "script_plan_reference_units.json"
        draft_path.parent.mkdir(parents=True)
        atomic_write_json(draft_path, {"units": [{"unit_id": "E2U1", "text": "@[角色A] 在河边"}]})
        project_dir = _project_dir(pm_with_assets)
        before = self._snapshot(project_dir)

        preview = pm_with_assets.preview_asset_deletion("demo", "characters", "角色A")

        after = self._snapshot(project_dir)
        added = after.keys() - before.keys()
        assert all(Path(path).suffix == ".lock" and after[path] == b"" for path in added)
        assert {path: content for path, content in after.items() if path not in added} == before
        assert preview.name == "角色A"
        assert [(item.episode, item.references) for item in preview.episodes] == [(1, 2), (2, 1), (3, 2)]
        renamed = pm_with_assets.rename_asset("demo", "characters", "角色A", "主角甲", dry_run=True)
        assert preview.references == renamed.references == 5
        assert self._snapshot(project_dir) == after

    def test_unreferenced_asset_previews_empty(self, pm_with_assets: ProjectManager) -> None:
        pm_with_assets.save_script("demo", make_reference_script(1), "episode_1.json")

        preview = pm_with_assets.preview_asset_deletion("demo", "props", "道具A")

        assert (preview.references, preview.episodes) == (0, ())

    def test_missing_asset_raises_key_error(self, pm_with_assets: ProjectManager) -> None:
        with pytest.raises(KeyError):
            pm_with_assets.preview_asset_deletion("demo", "characters", "不存在")
