"""资产级联重命名端到端测试：真实 ProjectManager 走 扫描 → 校验 → 落盘 全路径。

覆盖四类资产、各 content_mode 骨架的引用改写（引用数组 / speaker / mention）、step1 草稿、
关联文件与版本历史迁移、NFC/NFD 冲突拒绝与 dry-run 预览一致性。speaker 与 mention 不在
DataValidator 引用扫描范围内，须直接断言改写结果，不能只看校验无新增 error。
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from lib.asset_rename import (
    AssetRenameConflictError,
    AssetRenameFileCollisionError,
    AssetRenameHistoryCollisionError,
    AssetRenameNotFoundError,
    rewrite_payload_references,
)
from lib.episode_paths import (
    REFERENCE_VIDEO_STEP1_QUARANTINE_FILENAME,
    REFERENCE_VIDEO_STEP2_QUARANTINE_FILENAME,
)
from lib.json_io import atomic_write_json
from lib.project_manager import ProjectManager, _rename_agnostic_errors
from lib.validation_messages import ValidationMessage, ValidationResult
from lib.version_manager import VersionManager

pytestmark = pytest.mark.unit


def _narration_script(**overrides: Any) -> dict[str, Any]:
    segment = {
        "segment_id": "E1S01",
        "duration_seconds": 4,
        "novel_text": "原文",
        "characters_in_segment": ["角色A"],
        "scenes": ["场景A"],
        "props": ["道具A"],
        "image_prompt": {
            "scene": "场景描述",
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
    }
    script = {
        "episode": 1,
        "title": "标题",
        "content_mode": "narration",
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "segments": [segment],
    }
    script.update(overrides)
    return script


def _drama_script() -> dict[str, Any]:
    scene = {
        "scene_id": "E1S01",
        "duration_seconds": 8,
        "scene_type": "剧情",
        "characters_in_scene": ["角色A"],
        "scenes": ["场景A"],
        "props": [],
        "utterances": [
            {"kind": "dialogue", "speaker": "角色A", "text": "台词"},
            {"kind": "voiceover", "speaker": None, "text": "旁白"},
        ],
        "image_prompt": {
            "scene": "场景描述",
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
    }
    return {
        "episode": 1,
        "title": "标题",
        "content_mode": "drama",
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "scenes": [scene],
    }


def _ad_script() -> dict[str, Any]:
    shot = {
        "shot_id": "E1S01",
        "section": "hook",
        "duration_seconds": 5,
        "voiceover_text": "口播文案",
        "characters_in_shot": ["角色A"],
        "scenes": [],
        "props": [],
        "products_in_shot": ["产品A"],
        "image_prompt": {
            "scene": "场景描述",
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {
            "action": "转身",
            "camera_motion": "Static",
            "ambiance_audio": "风声",
            "dialogue": [{"speaker": "角色A", "line": "广告词"}],
        },
    }
    return {"episode": 1, "title": "标题", "content_mode": "ad", "shots": [shot]}


def _reference_script(episode: int = 1) -> dict[str, Any]:
    return {
        "episode": episode,
        "title": "标题",
        "content_mode": "narration",
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "video_units": [
            {
                "unit_id": "E1U1",
                "shots": [{"text": "@[角色A] 走进 @[场景A]"}],
                "references": [
                    {"type": "character", "name": "角色A"},
                    {"type": "scene", "name": "场景A"},
                ],
                "duration_seconds": 8,
            }
        ],
    }


@pytest.fixture
def pm(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(str(tmp_path))
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo", "Anime", "narration")
    manager.upsert_assets("demo", "characters", {"角色A": {"description": "主角"}})
    manager.upsert_assets("demo", "scenes", {"场景A": {"description": "村口"}})
    manager.upsert_assets("demo", "props", {"道具A": {"description": "长剑"}})
    return manager


def _project_dir(pm: ProjectManager) -> Path:
    return pm.get_project_path("demo")


def _load_script(pm: ProjectManager) -> dict[str, Any]:
    return pm.load_script("demo", "episode_1.json")


class TestRewritePayloadReferences:
    def test_only_matching_type_rewritten(self) -> None:
        payload = _narration_script()
        count = rewrite_payload_references(payload, "scene", "场景A", "新场景")
        assert count == 1
        segment = payload["segments"][0]
        assert segment["scenes"] == ["新场景"]
        assert segment["characters_in_segment"] == ["角色A"]

    def test_drama_speaker_rewritten_voiceover_untouched(self) -> None:
        payload = _drama_script()
        count = rewrite_payload_references(payload, "character", "角色A", "新角色")
        scene = payload["scenes"][0]
        assert scene["characters_in_scene"] == ["新角色"]
        assert scene["utterances"][0]["speaker"] == "新角色"
        assert scene["utterances"][1]["speaker"] is None
        assert count == 2

    def test_ad_dialogue_speaker_and_products(self) -> None:
        payload = _ad_script()
        assert rewrite_payload_references(payload, "product", "产品A", "新产品") == 1
        assert payload["shots"][0]["products_in_shot"] == ["新产品"]
        assert rewrite_payload_references(payload, "character", "角色A", "新角色") == 2
        shot = payload["shots"][0]
        assert shot["characters_in_shot"] == ["新角色"]
        assert shot["video_prompt"]["dialogue"][0]["speaker"] == "新角色"

    def test_narration_video_prompt_dialogue_speaker(self) -> None:
        # speaker 不在 DataValidator 引用扫描范围内，须直接断言改写（narration 的
        # dialogue 挂在 segments[].video_prompt 下，不经 shots 分支）。
        payload = _narration_script()
        payload["segments"][0]["video_prompt"]["dialogue"] = [
            {"speaker": "角色A", "line": "台词"},
            {"speaker": "路人", "line": "别的台词"},
        ]
        count = rewrite_payload_references(payload, "character", "角色A", "新角色")
        dialogue = payload["segments"][0]["video_prompt"]["dialogue"]
        assert dialogue[0]["speaker"] == "新角色"
        assert dialogue[1]["speaker"] == "路人"
        assert count == 2  # characters_in_segment + dialogue speaker

    def test_ad_reference_unit_product_reference(self) -> None:
        payload = _ad_script()
        payload["reference_units"] = [
            {
                "unit_id": "E1U1",
                "shots": [{"text": "@[产品A] 特写"}],
                "references": [{"type": "product", "name": "产品A"}],
                "duration_seconds": 5,
            }
        ]
        count = rewrite_payload_references(payload, "product", "产品A", "新产品")
        unit = payload["reference_units"][0]
        assert unit["references"][0] == {"type": "product", "name": "新产品"}
        assert unit["shots"][0]["text"] == "@[新产品] 特写"
        assert count == 3  # products_in_shot + reference + mention

    def test_reference_units_and_mentions(self) -> None:
        payload = _reference_script()
        count = rewrite_payload_references(payload, "character", "角色A", "新角色")
        unit = payload["video_units"][0]
        assert unit["shots"][0]["text"] == "@[新角色] 走进 @[场景A]"
        assert unit["references"][0] == {"type": "character", "name": "新角色"}
        assert unit["references"][1] == {"type": "scene", "name": "场景A"}
        assert count == 2

    def test_nfd_text_forms_matched(self) -> None:
        nfd = unicodedata.normalize("NFD", "café")
        payload = _narration_script()
        payload["segments"][0]["characters_in_segment"] = [nfd]
        count = rewrite_payload_references(payload, "character", "café", "咖啡师")
        assert count == 1
        assert payload["segments"][0]["characters_in_segment"] == ["咖啡师"]

    def test_legacy_embedded_equivalent_keys_collapsed(self) -> None:
        """内嵌镜像里 NFC / NFD 并存时一并收编，留一条会顶着旧名带失效 sheet 路径残留。"""
        nfd = unicodedata.normalize("NFD", "café")
        payload = _narration_script(
            characters={
                nfd: {"character_sheet": f"characters/{nfd}.png"},
                "café": {"character_sheet": "characters/café.png"},
            }
        )
        rewrite_payload_references(payload, "character", "café", "咖啡师")
        assert list(payload["characters"]) == ["咖啡师"]
        assert payload["characters"]["咖啡师"]["character_sheet"] == "characters/咖啡师.png"

    def test_legacy_embedded_encoding_only_rename_collapses(self) -> None:
        """纯改编码形式的改名：胜出 key 已等于新名，另一条等价 key 仍须一并收编。"""
        nfd = unicodedata.normalize("NFD", "café")
        payload = _narration_script(
            characters={
                nfd: {"character_sheet": f"characters/{nfd}.png"},
                "café": {"character_sheet": "characters/café.png"},
            }
        )
        rewrite_payload_references(payload, "character", nfd, "café")
        assert list(payload["characters"]) == ["café"]
        assert payload["characters"]["café"]["character_sheet"] == "characters/café.png"

    def test_legacy_embedded_characters_rekeyed(self) -> None:
        payload = _narration_script(characters={"角色A": {"character_sheet": "characters/角色A.png"}})
        rewrite_payload_references(payload, "character", "角色A", "新角色")
        assert "角色A" not in payload["characters"]
        assert payload["characters"]["新角色"]["character_sheet"] == "characters/新角色.png"


class TestRenameAssetCascade:
    def test_character_rename_cascades_across_modes(self, pm: ProjectManager) -> None:
        pm.save_script("demo", _narration_script(), "episode_1.json")
        pm.save_script("demo", _reference_script(2), "episode_2.json")

        project_dir = _project_dir(pm)
        sheet = project_dir / "characters" / "角色A.png"
        sheet.write_bytes(b"png")
        ref_dir = project_dir / "characters" / "refs"
        ref_dir.mkdir(parents=True)
        (ref_dir / "角色A.png").write_bytes(b"ref")

        def _set_paths(project: dict) -> None:
            entry = project["characters"]["角色A"]
            entry["character_sheet"] = "characters/角色A.png"
            entry["reference_image"] = "characters/refs/角色A.png"

        pm.update_project("demo", _set_paths)

        report = pm.rename_asset("demo", "characters", "角色A", "主角甲")

        assert report.episodes == 2
        assert report.references == 3  # 分段引用数组 + 参考单元 references + mention
        assert report.files == 2

        project = pm.load_project("demo")
        assert "角色A" not in project["characters"]
        entry = project["characters"]["主角甲"]
        assert entry["character_sheet"] == "characters/主角甲.png"
        assert entry["reference_image"] == "characters/refs/主角甲.png"
        assert (project_dir / "characters" / "主角甲.png").exists()
        assert not sheet.exists()
        assert (ref_dir / "主角甲.png").exists()

        assert _load_script(pm)["segments"][0]["characters_in_segment"] == ["主角甲"]
        unit = pm.load_script("demo", "episode_2.json")["video_units"][0]
        assert unit["shots"][0]["text"] == "@[主角甲] 走进 @[场景A]"
        assert unit["references"][0]["name"] == "主角甲"

    def test_rename_keeps_reference_integrity(self, pm: ProjectManager) -> None:
        from lib.data_validator import DataValidator

        pm.save_script("demo", _narration_script(), "episode_1.json")
        pm.rename_asset("demo", "scenes", "场景A", "新场景")

        validator = DataValidator(str(pm.projects_root))
        result = validator.validate_episode("demo", "episode_1.json")
        assert not [e for e in result.errors if "新场景" in e or "场景A" in e]
        assert _load_script(pm)["segments"][0]["scenes"] == ["新场景"]

    def test_step1_draft_rewritten(self, pm: ProjectManager) -> None:
        draft_dir = _project_dir(pm) / "drafts" / "episode_1"
        draft_dir.mkdir(parents=True)
        draft = {
            "units": [
                {
                    "unit_id": "E1U1",
                    "shots": [{"text": "@[角色A] 在河边"}],
                    "duration_seconds": 8,
                    "references": [{"type": "character", "name": "角色A"}],
                }
            ]
        }
        atomic_write_json(draft_dir / "step1_reference_units.json", draft)

        report = pm.rename_asset("demo", "characters", "角色A", "主角甲")

        assert report.episodes == 1
        assert report.references == 2
        saved = json.loads((draft_dir / "step1_reference_units.json").read_text(encoding="utf-8"))
        assert saved["units"][0]["shots"][0]["text"] == "@[主角甲] 在河边"
        assert saved["units"][0]["references"][0]["name"] == "主角甲"

    def test_sibling_with_numeric_suffix_untouched(self, pm: ProjectManager) -> None:
        """``旧名_2`` 是合法资产名：兄弟资产的设计图不得被序号形态的 stem 匹配卷走。"""
        pm.upsert_assets("demo", "characters", {"角色A_2": {"description": "副手"}})
        project_dir = _project_dir(pm)
        sibling = project_dir / "characters" / "角色A_2.png"
        sibling.write_bytes(b"sibling")
        (project_dir / "characters" / "角色A.png").write_bytes(b"png")

        def _set_paths(project: dict) -> None:
            project["characters"]["角色A"]["character_sheet"] = "characters/角色A.png"
            project["characters"]["角色A_2"]["character_sheet"] = "characters/角色A_2.png"

        pm.update_project("demo", _set_paths)

        report = pm.rename_asset("demo", "characters", "角色A", "主角甲")

        assert report.files == 1
        assert sibling.exists()
        project = pm.load_project("demo")
        assert project["characters"]["角色A_2"]["character_sheet"] == "characters/角色A_2.png"
        assert project["characters"]["主角甲"]["character_sheet"] == "characters/主角甲.png"

    def test_product_sequenced_files_and_paths(self, tmp_path: Path) -> None:
        pm = ProjectManager(str(tmp_path))
        pm.create_project("demo", content_mode="ad")
        pm.create_project_metadata("demo", "Demo", "Anime", "ad")
        pm.upsert_assets("demo", "products", {"产品A": {"description": "饮料"}})
        pm.upsert_assets("demo", "characters", {"角色A": {"description": "代言人"}})
        pm.save_script("demo", _ad_script(), "episode_1.json")

        project_dir = pm.get_project_path("demo")
        refs = project_dir / "products" / "refs"
        refs.mkdir(parents=True)
        (refs / "产品A_1.png").write_bytes(b"a")
        (refs / "产品A_2.png").write_bytes(b"b")

        def _set_paths(project: dict) -> None:
            project["products"]["产品A"]["reference_images"] = [
                "products/refs/产品A_1.png",
                "products/refs/产品A_2.png",
            ]

        pm.update_project("demo", _set_paths)

        report = pm.rename_asset("demo", "products", "产品A", "爆款")

        assert report.files == 2
        assert sorted(f.name for f in refs.iterdir() if f.is_file() and not f.name.startswith(".")) == [
            "爆款_1.png",
            "爆款_2.png",
        ]
        project = pm.load_project("demo")
        assert project["products"]["爆款"]["reference_images"] == [
            "products/refs/爆款_1.png",
            "products/refs/爆款_2.png",
        ]
        assert pm.load_script("demo", "episode_1.json")["shots"][0]["products_in_shot"] == ["爆款"]

    def test_version_history_migrated(self, pm: ProjectManager) -> None:
        project_dir = _project_dir(pm)
        sheet = project_dir / "characters" / "角色A.png"
        sheet.write_bytes(b"v1")
        vm = VersionManager(project_dir)
        vm.add_version("characters", "角色A", "第一版", source_file=sheet)

        report = pm.rename_asset("demo", "characters", "角色A", "主角甲")

        assert report.files == 2  # sheet + 1 个版本快照
        info = vm.get_versions("characters", "主角甲")
        assert info["current_version"] == 1
        version_file = project_dir / info["versions"][0]["file"]
        assert version_file.exists()
        assert version_file.name.startswith("主角甲_v1_")
        assert vm.get_versions("characters", "角色A") == {"current_version": 0, "versions": []}

    def test_conflict_rejected_atomically(self, pm: ProjectManager) -> None:
        pm.save_script("demo", _narration_script(), "episode_1.json")
        nfd = unicodedata.normalize("NFD", "café")

        def _add_nfd_key(project: dict) -> None:
            project["characters"][nfd] = {"description": "存量 NFD key"}

        pm.update_project("demo", _add_nfd_key)

        with pytest.raises(AssetRenameConflictError) as exc_info:
            pm.rename_asset("demo", "characters", "角色A", "café")

        assert exc_info.value.conflict_name == nfd
        project = pm.load_project("demo")
        assert "角色A" in project["characters"]
        assert _load_script(pm)["segments"][0]["characters_in_segment"] == ["角色A"]

    def test_orphan_destination_file_rejected_atomically(self, pm: ProjectManager) -> None:
        """新名下的孤儿文件没有对应资产，资产桶冲突检查看不见它，须在迁移前独立拦下。"""
        pm.save_script("demo", _narration_script(), "episode_1.json")
        project_dir = _project_dir(pm)
        sheet = project_dir / "characters" / "角色A.png"
        sheet.write_bytes(b"sheet")
        orphan = project_dir / "characters" / "主角甲.png"
        orphan.write_bytes(b"orphan")

        # 预览同样拒绝：占用在扫描阶段就已知，不该等到用户确认后才失败。
        with pytest.raises(AssetRenameFileCollisionError):
            pm.rename_asset("demo", "characters", "角色A", "主角甲", dry_run=True)

        with pytest.raises(AssetRenameFileCollisionError) as exc_info:
            pm.rename_asset("demo", "characters", "角色A", "主角甲")

        assert exc_info.value.destination == orphan
        assert orphan.read_bytes() == b"orphan"
        assert sheet.read_bytes() == b"sheet"
        assert "角色A" in pm.load_project("demo")["characters"]
        assert _load_script(pm)["segments"][0]["characters_in_segment"] == ["角色A"]

    def test_case_only_rename_not_treated_as_collision(self, pm: ProjectManager) -> None:
        """大小写不敏感的文件系统上目标解析回源文件自身，那不是占用。

        文件系统区分大小写时（多数 Linux）走不到该豁免分支，改名照样通过，断言仍成立。
        """
        pm.upsert_assets("demo", "characters", {"Alice": {"description": "主角"}})
        sheet = _project_dir(pm) / "characters" / "Alice.png"
        sheet.write_bytes(b"sheet")

        report = pm.rename_asset("demo", "characters", "Alice", "alice")

        assert report.files == 1
        assert "alice" in pm.load_project("demo")["characters"]
        assert (_project_dir(pm) / "characters" / "alice.png").read_bytes() == b"sheet"

    def test_duplicate_destination_rejected(self, pm: ProjectManager) -> None:
        """两个视觉同名的存量文件（NFC / NFD）会撞到同一目标，后一次迁移吃掉前一次的成果。"""
        chars = _project_dir(pm) / "characters"
        nfd = unicodedata.normalize("NFD", "café")
        nfc = unicodedata.normalize("NFC", "café")
        (chars / f"{nfd}.png").write_bytes(b"nfd")
        (chars / f"{nfc}.png").write_bytes(b"nfc")
        if len([p for p in chars.iterdir() if p.suffix == ".png"]) < 2:
            pytest.skip("文件系统归一化文件名，两种编码落到同一文件，构造不出同批撞车")
        pm.upsert_assets("demo", "characters", {nfc: {"description": "存量"}})

        with pytest.raises(AssetRenameFileCollisionError):
            pm.rename_asset("demo", "characters", nfc, "主角甲")

        assert (chars / f"{nfd}.png").read_bytes() == b"nfd"
        assert (chars / f"{nfc}.png").read_bytes() == b"nfc"

    def test_quarantine_drafts_rewritten(self, pm: ProjectManager) -> None:
        """隔离草稿晋升后会回流为正式内容，漏改会让旧名经晋升重新进入剧本。

        草稿装的是扁平书写层产物：mention 落在 ``content.units[].text``，结构字段（``shots`` /
        ``references``）尚未派生，按信封原形构造（见 lib/reference_video/quarantine.py）。
        """
        draft_dir = _project_dir(pm) / "drafts" / "episode_1"
        draft_dir.mkdir(parents=True)
        drafts = {
            REFERENCE_VIDEO_STEP1_QUARANTINE_FILENAME: {
                "kind": "reference_video_step1",
                "episode": 1,
                "meta": {},
                "violations": [],
                "content": {"units": [{"duration_seconds": 8, "source_text": "原文", "text": "@[角色A] 在河边"}]},
            },
            REFERENCE_VIDEO_STEP2_QUARANTINE_FILENAME: {
                "kind": "reference_video_step2",
                "episode": 1,
                "meta": {},
                "violations": [],
                "content": {"title": "标题", "units": [{"text": "@[角色A] 抬头"}]},
            },
        }
        for filename, payload in drafts.items():
            atomic_write_json(draft_dir / filename, payload)

        report = pm.rename_asset("demo", "characters", "角色A", "主角甲")

        step1 = json.loads((draft_dir / REFERENCE_VIDEO_STEP1_QUARANTINE_FILENAME).read_text(encoding="utf-8"))
        step2 = json.loads((draft_dir / REFERENCE_VIDEO_STEP2_QUARANTINE_FILENAME).read_text(encoding="utf-8"))
        assert step1["content"]["units"][0]["text"] == "@[主角甲] 在河边"
        assert step2["content"]["units"][0]["text"] == "@[主角甲] 抬头"
        assert report.references == 2
        assert report.episodes == 1

    def test_history_under_new_name_rejected_atomically(self, pm: ProjectManager) -> None:
        """删除资产只删资产桶 key、版本历史留存，改名过去会不可恢复地覆盖它——整体拒绝。"""
        project_dir = _project_dir(pm)
        pm.upsert_assets("demo", "characters", {"角色B": {"description": "配角"}})
        sheet_b = project_dir / "characters" / "角色B.png"
        sheet_b.write_bytes(b"b1")
        vm = VersionManager(project_dir)
        vm.add_version("characters", "角色B", "配角初版", source_file=sheet_b)
        # 与 delete_entry 路由同形：只删资产桶 key，版本记录与快照留在原地。
        pm.update_project("demo", lambda project: project["characters"].pop("角色B"))
        sheet_b.unlink()

        sheet_a = project_dir / "characters" / "角色A.png"
        sheet_a.write_bytes(b"a1")
        vm.add_version("characters", "角色A", "主角初版", source_file=sheet_a)

        for dry_run in (True, False):
            with pytest.raises(AssetRenameHistoryCollisionError):
                pm.rename_asset("demo", "characters", "角色A", "角色B", dry_run=dry_run)

        assert vm.get_versions("characters", "角色B")["current_version"] == 1
        assert vm.get_versions("characters", "角色A")["current_version"] == 1
        assert "角色A" in pm.load_project("demo")["characters"]

    def test_history_under_equivalent_key_is_same_asset(self, pm: ProjectManager) -> None:
        """新名解析回记录自身（仅换编码形式）时是同一份历史，不算占用。"""
        project_dir = _project_dir(pm)
        pm.upsert_assets("demo", "characters", {"café": {"description": "存量"}})
        nfd = unicodedata.normalize("NFD", "café")
        sheet = project_dir / "characters" / f"{nfd}.png"
        sheet.write_bytes(b"v1")
        vm = VersionManager(project_dir)
        vm.add_version("characters", nfd, "第一版", source_file=sheet)

        vm.rename_resource("characters", nfd, "café")

        bucket = json.loads(vm.versions_file.read_text(encoding="utf-8"))["characters"]
        assert list(bucket) == ["café"]
        assert vm.get_versions("characters", "café")["current_version"] == 1

    def test_equivalent_bucket_keys_collapsed(self, pm: ProjectManager) -> None:
        """NFC / NFD 并存的存量资产桶 key 一并收编，否则等价 key 顶着旧名带失效路径残留。"""
        project = pm.load_project("demo")
        nfd = unicodedata.normalize("NFD", "café")
        project["characters"] = {
            nfd: {"description": "存量 NFD", "character_sheet": f"characters/{nfd}.png"},
            "café": {"description": "存量 NFC", "character_sheet": "characters/café.png"},
        }
        atomic_write_json(_project_dir(pm) / "project.json", project)

        pm.rename_asset("demo", "characters", "café", "主角甲")

        characters = pm.load_project("demo")["characters"]
        assert list(characters) == ["主角甲"]
        assert characters["主角甲"]["description"] == "存量 NFC"

    def test_equivalent_version_history_keys_collapsed(self, pm: ProjectManager) -> None:
        """版本桶按原始 resource_id 建 key，NFC / NFD 两条记录一并收编到新名下。"""
        project_dir = _project_dir(pm)
        nfd = unicodedata.normalize("NFD", "café")
        pm.upsert_assets("demo", "characters", {"café": {"description": "存量"}})
        sheet = project_dir / "characters" / "café.png"
        sheet.write_bytes(b"v1")
        vm = VersionManager(project_dir)
        vm.add_version("characters", nfd, "NFD 记录", source_file=sheet)
        vm.add_version("characters", "café", "NFC 记录", source_file=sheet)

        vm.rename_resource("characters", "café", "咖啡师")

        bucket = json.loads(vm.versions_file.read_text(encoding="utf-8"))["characters"]
        assert list(bucket) == ["咖啡师"]

    def test_leading_dot_name_files_migrated(self, pm: ProjectManager) -> None:
        """前导点是合法资产名：其落盘文件须随改名迁移，否则 entry 路径字段指向不存在的文件。"""
        project_dir = _project_dir(pm)
        pm.upsert_assets("demo", "characters", {".甲": {"description": "点号开头"}})
        sheet = project_dir / "characters" / ".甲.png"
        sheet.write_bytes(b"png")
        pm.update_project(
            "demo",
            lambda project: project["characters"][".甲"].update({"character_sheet": "characters/.甲.png"}),
        )

        pm.rename_asset("demo", "characters", ".甲", "主角甲")

        assert not sheet.exists()
        assert (project_dir / "characters" / "主角甲.png").exists()
        assert pm.load_project("demo")["characters"]["主角甲"]["character_sheet"] == "characters/主角甲.png"

    def test_missing_old_name_hints_idempotency(self, pm: ProjectManager) -> None:
        with pytest.raises(AssetRenameNotFoundError) as exc_info:
            pm.rename_asset("demo", "characters", "不存在", "角色A")
        assert "可能上次重命名已成功" in str(exc_info.value)

        with pytest.raises(AssetRenameNotFoundError) as plain:
            pm.rename_asset("demo", "characters", "不存在", "全新名字")
        assert "可能上次重命名已成功" not in str(plain.value)

    def test_dry_run_previews_without_writing(self, pm: ProjectManager) -> None:
        pm.save_script("demo", _narration_script(), "episode_1.json")
        sheet = _project_dir(pm) / "characters" / "角色A.png"
        sheet.write_bytes(b"png")

        preview = pm.rename_asset("demo", "characters", "角色A", "主角甲", dry_run=True)

        assert preview.dry_run is True
        assert sheet.exists()
        assert "角色A" in pm.load_project("demo")["characters"]
        assert _load_script(pm)["segments"][0]["characters_in_segment"] == ["角色A"]

        executed = pm.rename_asset("demo", "characters", "角色A", "主角甲")
        assert (executed.episodes, executed.references, executed.files) == (
            preview.episodes,
            preview.references,
            preview.files,
        )

    def test_preexisting_error_naming_the_asset_does_not_block_rename(self, pm: ProjectManager) -> None:
        """历史遗留错误里点名了该资产时，改名不算「更坏」：错误随名字换了措辞，不是新增。"""
        project = pm.load_project("demo")
        project["characters"]["角色A"]["description"] = ""
        atomic_write_json(_project_dir(pm) / "project.json", project)

        report = pm.rename_asset("demo", "characters", "角色A", "主角甲")

        assert report.new_name == "主角甲"
        assert "主角甲" in pm.load_project("demo")["characters"]

    def test_dry_run_leaves_no_version_directory(self, pm: ProjectManager) -> None:
        """零写入承诺覆盖目录：预演不得在项目下建出空的 ``versions/`` 目录树。"""
        pm.save_script("demo", _narration_script(), "episode_1.json")
        versions_dir = _project_dir(pm) / "versions"
        assert not versions_dir.exists()

        pm.rename_asset("demo", "characters", "角色A", "主角甲", dry_run=True)

        assert not versions_dir.exists()

    def test_invalid_new_name_rejected(self, pm: ProjectManager) -> None:
        with pytest.raises(ValueError):
            pm.rename_asset("demo", "characters", "角色A", "坏/名字")
        with pytest.raises(ValueError):
            pm.rename_asset("demo", "unknown_table", "角色A", "新名")


class TestRenameAgnosticErrors:
    """错误指纹折叠的边界：折回新名只认确定形态，不做任意子串替换。"""

    @staticmethod
    def _fingerprints(*messages: ValidationMessage) -> set[Any]:
        result = ValidationResult(valid=False, error_messages=list(messages))
        return set(_rename_agnostic_errors(result, "角色A", "甲").keys())

    def test_folds_exact_name_param(self) -> None:
        renamed = ValidationMessage("val_asset_missing_description", {"asset_type": "角色", "name": "甲"})
        original = ValidationMessage("val_asset_missing_description", {"asset_type": "角色", "name": "角色A"})
        assert self._fingerprints(renamed) == self._fingerprints(original)

    def test_folds_bracketed_field_path(self) -> None:
        renamed = ValidationMessage("val_invalid_path", {"field": "characters[甲].character_sheet"})
        original = ValidationMessage("val_invalid_path", {"field": "characters[角色A].character_sheet"})
        assert self._fingerprints(renamed) == self._fingerprints(original)

    def test_keeps_unrelated_value_merely_containing_the_new_name(self) -> None:
        """新名只是无关文本的子串时不得折叠，否则改写后真正新增的错误会被静默吞掉。"""
        renamed = ValidationMessage("val_invalid_path", {"field": "assets/甲板/图.png"})
        original = ValidationMessage("val_invalid_path", {"field": "assets/角色A板/图.png"})
        assert self._fingerprints(renamed) != self._fingerprints(original)
