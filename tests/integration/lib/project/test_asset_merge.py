"""资产合并命令：真实 ProjectManager 走 扫描 → 校验 → 落盘，以 dry_run 报告与执行后的项目状态断言。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib.artifacts.artifact_currency import ArtifactCurrencyResolver, resolve_current_artifact_target
from lib.artifacts.artifact_manifest import (
    ArtifactKey,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.artifacts.version_manager import VersionManager
from lib.artifacts.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.artifacts.video_visual_provenance import resolve_video_aspect_ratio
from lib.artifacts.visual_artifact_provenance import build_reference_video_artifact_visual_basis
from lib.episode.episode_paths import NARRATION_SCRIPT_PLAN_QUARANTINE_FILENAME
from lib.infra.json_io import atomic_write_json, load_json_or_none
from lib.project.asset_derivatives import derivative_artifact_key
from lib.project.asset_merge import AssetMergeEpisodeImpact, AssetMergeNotFoundError, AssetMergeRejectedError
from lib.project.project_manager import ProjectManager
from lib.script.plan_new_assets import resolve_new_assets
from lib.script.reference_video.request_projection import (
    FilesystemReferenceAssets,
    hydrate_reference_assets,
    resolve_reference_assets,
    unit_reference_declarations,
)
from lib.script.script_review import (
    apply_confirmation,
    content_fingerprint,
    formal_script_plan_confirmed,
    review_status,
    script_plan_path,
)
from lib.speech.speech_artifact_provenance import build_video_duration_basis, build_video_speech_basis
from lib.speech.speech_composition import admit_script_unit

PROJECT = "demo"


@pytest.fixture
def pm(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(str(tmp_path))
    manager.create_project(PROJECT)
    manager.create_project_metadata(PROJECT, "Demo", "Anime", "narration")
    manager.upsert_assets(
        PROJECT,
        "characters",
        {
            "王建国": {"description": "五十岁的杂货店老板", "aliases": ["王老板"]},
            "老王": {"description": "街坊口中的杂货店老王", "aliases": ["王叔"]},
        },
    )
    manager.upsert_assets(PROJECT, "scenes", {"杂货店": {"description": "街角的小店"}})
    return manager


def _narration_script() -> dict[str, Any]:
    return {
        "episode": 1,
        "title": "开张",
        "content_mode": "narration",
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "segments": [
            {
                "segment_id": "E1S01",
                "duration_seconds": 4,
                "novel_text": "原文",
                "characters_in_segment": ["老王", "王建国"],
                "scenes": ["杂货店"],
                "props": [],
                "image_prompt": {
                    "scene": "@[老王] 推开 @[杂货店] 的门",
                    "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
                },
                "video_prompt": {
                    "action": "推门",
                    "camera_motion": "Static",
                    "ambiance_audio": "风铃",
                    "dialogue": [{"speaker": "老王", "line": "开张了"}],
                },
            }
        ],
    }


def _reference_script() -> dict[str, Any]:
    return {
        "episode": 2,
        "title": "进货",
        "content_mode": "narration",
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第二章"},
        "video_units": [
            {"unit_id": "E2U1", "text": "@[老王] 走进 @[杂货店]\n@[老王]{今天进货}", "duration_seconds": 8}
        ],
    }


def _script(pm: ProjectManager, episode: int) -> dict[str, Any]:
    return pm.load_script(PROJECT, f"episode_{episode}.json")


class TestMergeIntoAsset:
    def test_merging_rewrites_every_reference_and_records_the_merged_names_as_aliases(self, pm: ProjectManager) -> None:
        pm.save_script(PROJECT, _narration_script(), "episode_1.json")
        pm.save_script(PROJECT, _reference_script(), "episode_2.json")
        draft_dir = pm.get_project_path(PROJECT) / "drafts" / "episode_2"
        draft_dir.mkdir(parents=True)
        atomic_write_json(
            draft_dir / "script_plan_reference_units.json",
            {"units": [{"unit_id": "E2U1", "text": "@[老王] 清点货架", "duration_seconds": 8}]},
        )

        pm.merge_asset(PROJECT, "characters", "老王", "王建国")

        project = pm.load_project(PROJECT)
        assert "老王" not in project["characters"]
        kept = project["characters"]["王建国"]
        assert kept["description"] == "五十岁的杂货店老板"
        assert kept["aliases"] == ["王老板", "老王", "王叔"]
        segment = _script(pm, 1)["segments"][0]
        assert segment["characters_in_segment"] == ["王建国"]
        assert segment["image_prompt"]["scene"] == "@[王建国] 推开 @[杂货店] 的门"
        assert segment["video_prompt"]["dialogue"][0]["speaker"] == "王建国"
        assert _script(pm, 2)["video_units"][0]["text"] == "@[王建国] 走进 @[杂货店]\n@[王建国]{今天进货}"
        draft = load_json_or_none(draft_dir / "script_plan_reference_units.json")
        assert draft == {"units": [{"unit_id": "E2U1", "text": "@[王建国] 清点货架", "duration_seconds": 8}]}

    def test_dry_run_reports_per_episode_without_writing_and_matches_the_execution(self, pm: ProjectManager) -> None:
        pm.save_script(PROJECT, _narration_script(), "episode_1.json")
        pm.save_script(PROJECT, _reference_script(), "episode_2.json")
        drafts = pm.get_project_path(PROJECT) / "drafts"
        (drafts / "episode_1").mkdir(parents=True)
        atomic_write_json(
            drafts / "episode_1" / NARRATION_SCRIPT_PLAN_QUARANTINE_FILENAME,
            {"segments": [{"segment_id": "E1S01", "characters_in_segment": ["老王"]}]},
        )
        (drafts / "episode_2").mkdir(parents=True)
        atomic_write_json(
            drafts / "episode_2" / "script_plan_reference_units.json",
            {"units": [{"unit_id": "E2U1", "text": "@[老王] 清点货架", "duration_seconds": 8}]},
        )
        project_before = pm.load_project(PROJECT)

        preview = pm.merge_asset(PROJECT, "characters", "老王", "王建国", dry_run=True)

        assert preview.dry_run is True
        assert preview.aliases_added == ("老王", "王叔")
        assert preview.episodes == (
            AssetMergeEpisodeImpact(episode=1, script=1, draft=1, prompt_text=1, speaker=1),
            AssetMergeEpisodeImpact(episode=2, prompt_text=3),
        )
        assert pm.load_project(PROJECT) == project_before
        assert _script(pm, 1)["segments"][0]["characters_in_segment"] == ["老王", "王建国"]

        executed = pm.merge_asset(PROJECT, "characters", "老王", "王建国")

        assert executed.dry_run is False
        assert (executed.episodes, executed.aliases_added) == (preview.episodes, preview.aliases_added)
        draft = load_json_or_none(drafts / "episode_1" / NARRATION_SCRIPT_PLAN_QUARANTINE_FILENAME)
        assert draft == {"segments": [{"segment_id": "E1S01", "characters_in_segment": ["王建国"]}]}


def _write_confirmed_plan(pm: ProjectManager, plan: dict[str, Any]) -> Path:
    """写下第 1 集的正式脚本规划并确认它。"""
    pm.save_script(PROJECT, _narration_script(), "episode_1.json")
    project_dir = pm.get_project_path(PROJECT)
    path = script_plan_path(project_dir, pm.load_project(PROJECT), 1)
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, plan)
    fingerprint = content_fingerprint(path)
    assert fingerprint is not None

    def confirm(project: dict[str, Any]) -> None:
        apply_confirmation(project, 1, fingerprint, "2026-10-01T00:00:00+00:00")

    pm.update_project(PROJECT, confirm)
    return path


def _plan_review(pm: ProjectManager) -> tuple[str, bool]:
    """第 1 集的内容确认状态，以及正式脚本规划是否因已确认而只读。"""
    project_dir = pm.get_project_path(PROJECT)
    project = pm.load_project(PROJECT)
    return review_status(project_dir, project, 1), formal_script_plan_confirmed(project_dir, project, 1)


def _plan_with_merged_new_asset() -> dict[str, Any]:
    return {
        "segments": [{"segment_id": "E1S01", "characters_in_segment": ["老王", "小王"], "scenes": [], "props": []}],
        "new_assets": [
            {"type": "character", "name": "小王", "decision": "merge", "reason": "同一人", "target": "老王"}
        ],
    }


class TestMergeKeepsScriptPlanConfirmation:
    """合并只换资产的名字：已确认的集不退回待确认，规划里「并入」项的目标随之改指保留方。"""

    def test_confirmed_episode_stays_confirmed_and_read_only(self, pm: ProjectManager) -> None:
        path = _write_confirmed_plan(pm, _plan_with_merged_new_asset())
        assert _plan_review(pm) == ("confirmed", True)

        pm.merge_asset(PROJECT, "characters", "老王", "王建国")

        assert _plan_review(pm) == ("confirmed", True)
        plan = load_json_or_none(path)
        assert plan is not None
        assert plan["segments"][0]["characters_in_segment"] == ["王建国", "小王"]
        assert plan["new_assets"][0]["target"] == "王建国"

    def test_merged_new_asset_target_is_counted_and_still_resolves(self, pm: ProjectManager) -> None:
        path = _write_confirmed_plan(pm, _plan_with_merged_new_asset())

        preview = pm.merge_asset(PROJECT, "characters", "老王", "王建国", dry_run=True)
        pm.merge_asset(PROJECT, "characters", "老王", "王建国")

        impact = next(item for item in preview.episodes if item.episode == 1)
        assert impact.script_plan == 2  # 引用数组一处 + 并入目标一处
        plan = load_json_or_none(path)
        assert plan is not None
        resolution = resolve_new_assets(pm.load_project(PROJECT), plan["new_assets"])
        assert resolution.aliases == [("character", "王建国", "小王")]

    def test_merging_as_a_derivative_points_the_target_at_the_base(self, pm: ProjectManager) -> None:
        path = _write_confirmed_plan(pm, _plan_with_merged_new_asset())

        pm.merge_asset(PROJECT, "characters", "老王", "王建国", as_derivative=True)

        assert _plan_review(pm) == ("confirmed", True)
        plan = load_json_or_none(path)
        assert plan is not None
        assert plan["segments"][0]["characters_in_segment"] == ["王建国/老王", "小王"]
        assert plan["new_assets"][0]["target"] == "王建国"

    def test_plan_edited_after_confirmation_stays_pending(self, pm: ProjectManager) -> None:
        """确认指纹本就对不上（确认后又改过规划）时不平移：合并不替用户确认一份没看过的规划。"""
        path = _write_confirmed_plan(pm, _plan_with_merged_new_asset())
        edited = _plan_with_merged_new_asset()
        edited["segments"][0]["scenes"] = ["杂货店"]
        atomic_write_json(path, edited)
        assert _plan_review(pm) == ("pending_review", False)

        pm.merge_asset(PROJECT, "characters", "老王", "王建国")

        assert _plan_review(pm) == ("pending_review", False)


class TestMergeRejections:
    def test_products_cannot_merge(self, pm: ProjectManager) -> None:
        with pytest.raises(AssetMergeRejectedError) as rejected:
            pm.merge_asset(PROJECT, "products", "饮料", "汽水")

        assert rejected.value.reason == "type_not_mergeable"

    def test_an_asset_cannot_merge_into_itself(self, pm: ProjectManager) -> None:
        with pytest.raises(AssetMergeRejectedError) as rejected:
            pm.merge_asset(PROJECT, "characters", "老王", " 老王 ")

        assert rejected.value.reason == "same_asset"

    @pytest.mark.parametrize(("source", "target"), [("不存在", "王建国"), ("老王", "不存在")])
    def test_both_sides_must_exist(self, pm: ProjectManager, source: str, target: str) -> None:
        with pytest.raises(AssetMergeNotFoundError) as missing:
            pm.merge_asset(PROJECT, "characters", source, target)

        assert missing.value.name == "不存在"
        assert set(pm.load_project(PROJECT)["characters"]) == {"王建国", "老王"}


def _drama_script() -> dict[str, Any]:
    return {
        "episode": 1,
        "title": "夜访",
        "content_mode": "drama",
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "scenes": [
            {
                "scene_id": "E1S01",
                "duration_seconds": 8,
                "scene_type": "剧情",
                "characters_in_scene": ["黑衣人"],
                "scenes": ["杂货店"],
                "props": [],
                "utterances": [{"kind": "dialogue", "speaker": "黑衣人", "text": "站住"}],
                "image_prompt": {
                    "scene": "@[黑衣人] 立在 @[杂货店] 门外",
                    "composition": {"shot_type": "Medium Shot", "lighting": "冷光", "ambiance": "夜雾"},
                },
                "video_prompt": {"action": "抬头", "camera_motion": "Static", "ambiance_audio": "风声"},
            }
        ],
    }


class TestMergeAsDerivative:
    @pytest.fixture
    def stranger(self, pm: ProjectManager) -> ProjectManager:
        pm.upsert_assets(PROJECT, "characters", {"黑衣人": {"description": "一身黑衣，斗笠遮面", "aliases": ["怪客"]}})
        return pm

    def test_visual_references_point_at_the_derivative_and_speakers_at_the_base(self, stranger: ProjectManager) -> None:
        stranger.save_script(PROJECT, _drama_script(), "episode_1.json")
        reference = _reference_script()
        reference["video_units"][0]["text"] = "@[黑衣人] 现身\n@[黑衣人]{把货交出来}"
        stranger.save_script(PROJECT, reference, "episode_2.json")

        report = stranger.merge_asset(PROJECT, "characters", "黑衣人", "王建国", as_derivative=True)

        assert report.derivative_created == "黑衣人"
        assert report.aliases_added == ()
        kept = stranger.load_project(PROJECT)["characters"]["王建国"]
        assert kept["aliases"] == ["王老板"]
        assert kept["derivatives"] == {"黑衣人": {"description": "一身黑衣，斗笠遮面", "character_sheet": ""}}
        scene = _script(stranger, 1)["scenes"][0]
        assert scene["characters_in_scene"] == ["王建国/黑衣人"]
        assert scene["utterances"][0]["speaker"] == "王建国"
        assert scene["image_prompt"]["scene"] == "@[王建国/黑衣人] 立在 @[杂货店] 门外"
        assert _script(stranger, 2)["video_units"][0]["text"] == "@[王建国/黑衣人] 现身\n@[王建国]{把货交出来}"

    def test_scenes_cannot_merge_as_a_derivative(self, stranger: ProjectManager) -> None:
        stranger.upsert_assets(PROJECT, "scenes", {"小店": {"description": "街角的小店"}})

        with pytest.raises(AssetMergeRejectedError) as rejected:
            stranger.merge_asset(PROJECT, "scenes", "小店", "杂货店", as_derivative=True)

        assert rejected.value.reason == "derivative_needs_character"
        assert "小店" in stranger.load_project(PROJECT)["scenes"]


def _write_artifact(project_dir: Path, relative_path: str) -> Path:
    path = project_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(relative_path.encode("utf-8"))
    return path


def _claim_current(project_dir: Path, key: ArtifactKey) -> None:
    entry = resolve_current_artifact_target(project_dir, key)
    assert entry is not None
    ProjectArtifactManifestAdapter(project_dir).put_entry(key, entry)


def _claim_reference_video(projects: ProjectManager, episode: int, unit: dict[str, Any]) -> None:
    project_dir = projects.get_project_path(PROJECT)
    project = projects.load_project(PROJECT)
    hydration = hydrate_reference_assets(
        unit_reference_declarations(project, unit),
        resolve_reference_assets(project, project_dir, unit),
        FilesystemReferenceAssets(project_dir),
    )
    assert not hydration.missing
    visual = build_reference_video_artifact_visual_basis(
        unit=unit,
        request_assets=hydration.available,
        style=project.get("style"),
        style_description=project.get("style_description", ""),
        aspect_ratio=resolve_video_aspect_ratio(project),
    )
    speech = build_video_speech_basis(admit_script_unit("video_units", unit).preparation)
    duration = build_video_duration_basis(4)
    currency = VideoArtifactCurrencyFacts(
        episode=episode,
        request_duration_seconds=4,
        visual_basis=visual,
        speech_basis=speech,
        duration_basis=duration,
        video_basis=compose_video_artifact_basis(visual=visual, speech=speech, duration=duration),
        voice_style_speakers=(),
        duration_tiers=(4,),
        reference_image_limit=None,
        parent_version=0,
    )
    path = _write_artifact(project_dir, unit["generated_assets"]["video_clip"])
    VersionManager(project_dir).add_version(
        "reference_videos",
        unit["unit_id"],
        unit["text"],
        source_file=path,
        artifact_video_currency=currency.to_dict(),
        execution_checkpoint_schema_version=3,
        execution_duration_seconds=4,
        execution_request_digest="a" * 64,
        execution_script_file=f"scripts/episode_{episode}.json",
        execution_provider_media=[],
        execution_narration={"delivery": "post_production"},
    )
    _claim_current(project_dir, ArtifactKey.episode_video(episode, unit["unit_id"]))


def _write_episode(projects: ProjectManager, episode: int, script: dict[str, Any]) -> None:
    scripts = projects.get_project_path(PROJECT) / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / f"episode_{episode}.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    projects.add_episode(PROJECT, episode, f"第{episode}集", f"scripts/episode_{episode}.json")


def _segment(segment_id: str, cast: list[str], *, generated: bool = True) -> dict[str, Any]:
    assets = {"storyboard_image": f"storyboards/scene_{segment_id}.png"} if generated else {}
    return {
        "segment_id": segment_id,
        "novel_text": "店里来了客人",
        "image_prompt": "店里来了客人",
        "characters_in_segment": cast,
        "generated_assets": assets,
    }


def _unit(unit_id: str, text: str) -> dict[str, Any]:
    return {
        "unit_id": unit_id,
        "text": text,
        "duration_seconds": 4,
        "generated_assets": {"video_clip": f"reference_videos/{unit_id}.mp4"},
    }


class TestMergeWithProducedArtifacts:
    """产物文件、输入依据与版本记录均落盘，现行与过期由真实产物清单判定。"""

    @pytest.fixture
    def produced(self, tmp_path: Path) -> ProjectManager:
        projects = ProjectManager(tmp_path / "projects")
        projects.create_project(PROJECT)
        projects.create_project_metadata(PROJECT, "Demo")
        projects.add_character(PROJECT, "王建国", "五十岁的杂货店老板")
        projects.add_character(PROJECT, "老王", "街坊口中的杂货店老王", voice_style="沙哑")
        project_dir = projects.get_project_path(PROJECT)

        def _derivatives(project: dict[str, Any]) -> None:
            project["characters"]["老王"]["derivatives"] = {
                "战损": {"description": "衣服破损", "character_sheet": "characters/derivatives/老王/战损.png"},
                "便装": {"description": "汗衫", "character_sheet": "characters/derivatives/老王/便装.png"},
            }
            project["characters"]["王建国"]["derivatives"] = {
                "便装": {"description": "旧衬衫", "character_sheet": "characters/derivatives/王建国/便装.png"},
            }
            project["characters"]["老王"]["reference_image"] = "characters/refs/老王.png"
            project["characters"]["老王"]["reference_audio"] = "characters/refs_audio/老王.wav"

        projects.update_project(PROJECT, _derivatives)
        _write_artifact(project_dir, "characters/refs/老王.png")
        _write_artifact(project_dir, "characters/refs_audio/老王.wav")
        for name in ("王建国", "老王"):
            relative_path = f"characters/{name}.png"
            sheet = _write_artifact(project_dir, relative_path)
            projects.update_project_character_sheet(PROJECT, name, relative_path)
            VersionManager(project_dir).add_version("characters", name, "资产图", source_file=sheet)
            _claim_current(project_dir, ArtifactKey.asset_sheet("character", name))
        for owner, derivative in (("老王", "战损"), ("老王", "便装"), ("王建国", "便装")):
            _write_artifact(project_dir, f"characters/derivatives/{owner}/{derivative}.png")
            _claim_current(project_dir, derivative_artifact_key(owner, derivative))

        _write_episode(
            projects,
            1,
            {
                "episode": 1,
                "segments": [
                    _segment("E1S01", ["老王"]),
                    _segment("E1S02", ["王建国"]),
                    _segment("E1S03", ["老王/战损"]),
                    _segment("E1S04", ["老王"], generated=False),
                ],
            },
        )
        for resource_id in ("E1S01", "E1S02", "E1S03"):
            _write_artifact(project_dir, f"storyboards/scene_{resource_id}.png")
            _claim_current(project_dir, ArtifactKey.episode_storyboard(1, resource_id))
        units = [
            _unit("E2U1", "@[老王] 推门"),
            _unit("E2U2", "@[王建国] 等候"),
            _unit("E2U3", "@[王建国] 点头\n@[老王]{来两斤米}"),
        ]
        _write_episode(projects, 2, {"episode": 2, "video_units": units})
        for unit in units:
            _claim_reference_video(projects, 2, unit)
        return projects

    @staticmethod
    def _current(projects: ProjectManager, key: ArtifactKey, artifact_path: str) -> bool:
        resolver = ArtifactCurrencyResolver(projects.get_project_path(PROJECT))
        return resolver.compare(key, artifact_path=artifact_path).status is ArtifactStatus.CURRENT

    def test_the_report_counts_exactly_the_artifacts_the_merge_outdates(self, produced: ProjectManager) -> None:
        preview = produced.merge_asset(PROJECT, "characters", "老王", "王建国", dry_run=True)

        assert [(item.episode, item.storyboards, item.videos) for item in preview.episodes] == [(1, 2, 0), (2, 0, 2)]

        produced.merge_asset(PROJECT, "characters", "老王", "王建国")

        storyboards = {
            resource_id: self._current(
                produced, ArtifactKey.episode_storyboard(1, resource_id), f"storyboards/scene_{resource_id}.png"
            )
            for resource_id in ("E1S01", "E1S02", "E1S03")
        }
        videos = {
            unit_id: self._current(produced, ArtifactKey.episode_video(2, unit_id), f"reference_videos/{unit_id}.mp4")
            for unit_id in ("E2U1", "E2U2", "E2U3")
        }
        assert storyboards == {"E1S01": False, "E1S02": True, "E1S03": False}
        assert videos == {"E2U1": False, "E2U2": True, "E2U3": False}  # E2U3 只换了说话人

    def test_derivatives_move_to_the_kept_character_and_same_named_ones_fold(self, produced: ProjectManager) -> None:
        project_dir = produced.get_project_path(PROJECT)

        report = produced.merge_asset(PROJECT, "characters", "老王", "王建国")

        assert (report.derivatives_moved, report.derivatives_folded) == (("战损",), ("便装",))
        derivatives = produced.load_project(PROJECT)["characters"]["王建国"]["derivatives"]
        assert derivatives == {
            "便装": {"description": "旧衬衫", "character_sheet": "characters/derivatives/王建国/便装.png"},
            "战损": {"description": "衣服破损", "character_sheet": "characters/derivatives/王建国/战损.png"},
        }
        assert (project_dir / "characters/derivatives/王建国/战损.png").exists()
        assert (project_dir / "characters/derivatives/王建国/便装.png").read_bytes() == (
            b"characters/derivatives/\xe7\x8e\x8b\xe5\xbb\xba\xe5\x9b\xbd/\xe4\xbe\xbf\xe8\xa3\x85.png"
        )
        assert not (project_dir / "characters/derivatives/老王").exists()
        moved = ArtifactCurrencyResolver(project_dir).compare(
            derivative_artifact_key("王建国", "战损"), artifact_path="characters/derivatives/王建国/战损.png"
        )
        assert moved.status is ArtifactStatus.STALE  # 登记随图迁来，本体换了，图待重新生成
        adapter = ProjectArtifactManifestAdapter(project_dir)
        assert adapter.get_entry(derivative_artifact_key("老王", "便装")) is None
        assert adapter.get_entry(derivative_artifact_key("老王", "战损")) is None

    def test_the_merged_asset_keeps_no_sheet_history_voice_or_uploads(self, produced: ProjectManager) -> None:
        project_dir = produced.get_project_path(PROJECT)

        produced.merge_asset(PROJECT, "characters", "老王", "王建国")

        assert "老王" not in produced.load_project(PROJECT)["characters"]
        assert (
            ProjectArtifactManifestAdapter(project_dir).get_entry(ArtifactKey.asset_sheet("character", "老王")) is None
        )
        assert VersionManager(project_dir).get_versions("characters", "老王")["versions"] == []
        for relative_path in ("characters/老王.png", "characters/refs/老王.png", "characters/refs_audio/老王.wav"):
            assert not (project_dir / relative_path).exists()
        assert self._current(produced, ArtifactKey.asset_sheet("character", "王建国"), "characters/王建国.png")
