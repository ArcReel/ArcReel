"""旧版本代码写出的项目目录形态，供迁移与读侧测试共用。

形态清单见 ``docs/agents/project-migrations.md``「已知旧形态」。构造出的目录停在
``schema_version`` 参数指定的版本；``advance_project_schema`` 按迁移链逐级推进到指定版本，
模拟已经升级过的安装。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from lib.artifacts.artifact_manifest import (
    ArtifactBasis,
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.artifacts.version_manager import VersionManager
from lib.artifacts.video_artifact_facts import VideoArtifactCurrencyFacts
from lib.artifacts.visual_artifact_provenance import build_storyboard_video_artifact_visual_basis
from lib.project.project_migrations.runner import MIGRATORS
from lib.project.source_revision import SourceScope, compute_source_revision
from lib.script.grid.models import GridGeneration, build_frame_chain
from lib.script.script_review import content_fingerprint
from lib.speech.narration_config import TtsSynthesisSettings
from lib.speech.narration_delivery import build_narration_audio_basis
from lib.speech.speech_artifact_provenance import (
    SelectedMediaEvidence,
    build_video_duration_basis,
    build_video_speech_basis,
)
from lib.speech.speech_composition import admit_script_unit
from lib.speech.speech_presentation import (
    PresentationMedia,
    materialize_speech_presentation,
    presentation_artifact_paths,
)

_LEGACY_SNAPSHOT_TIMESTAMP = "20260302T145652"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _legacy_video_version_record(
    resource_type: str,
    resource_id: str,
    *,
    version: int = 1,
    prompt: str = "Action: 环顾四周\nCamera_Motion: Pan Right\n",
    duration_seconds: int | str = 4,
) -> dict[str, Any]:
    """一条旧版视频版本记录：没有任何类型化来源字段。"""

    return {
        "version": version,
        "file": f"versions/{resource_type}/{resource_id}_v{version}_{_LEGACY_SNAPSHOT_TIMESTAMP}.mp4",
        "prompt": prompt,
        "created_at": "2026-03-02T14:56:52Z",
        "duration_seconds": duration_seconds,
    }


def _write_legacy_video(project_dir: Path, resource_type: str, resource_id: str, record: dict[str, Any]) -> None:
    content = f"provider-video-{resource_id}".encode()
    current = (
        project_dir
        / resource_type
        / (f"scene_{resource_id}.mp4" if resource_type == "videos" else f"{resource_id}.mp4")
    )
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_bytes(content)
    snapshot = project_dir / record["file"]
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(content)


def _write_versions(project_dir: Path, buckets: dict[str, dict[str, dict[str, Any]]]) -> None:
    resource_types = (
        "storyboards",
        "end_frames",
        "videos",
        "characters",
        "scenes",
        "props",
        "products",
        "grids",
        "reference_videos",
        "audio",
    )
    data: dict[str, Any] = {resource_type: buckets.get(resource_type, {}) for resource_type in resource_types}
    _write_json(project_dir / "versions" / "versions.json", data)


def write_legacy_storyboard_project(
    root: Path,
    name: str = "legacy-storyboard",
    *,
    schema_version: int = 7,
    unit_ids: tuple[str, ...] = ("E1S1", "E1S2"),
) -> Path:
    """narration + storyboard 路线的旧项目：分镜图 + 视频齐全，版本记录是旧形态。"""

    project_dir = root / name
    project_dir.mkdir(parents=True)
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": schema_version,
            "title": "旧项目",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "source_kind": "novel",
            "source_language": "中文",
            "style": "写实",
            "style_description": "电影感",
            "aspect_ratio": "9:16",
            "grid_storyboard": False,
            "characters": {},
            "scenes": {},
            "props": {},
            "products": {},
            "episodes": [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
        },
    )
    segments = []
    for index, unit_id in enumerate(unit_ids, start=1):
        segments.append(
            {
                "segment_id": unit_id,
                "episode": 1,
                "duration_seconds": 4,
                "novel_text": f"第{index}段旁白。",
                "characters_in_segment": [],
                "scenes": [],
                "props": [],
                "image_prompt": {"scene": f"画面 {index}"},
                "video_prompt": {"action": f"动作 {index}", "camera_motion": "Pan Right"},
                "transition_to_next": "fade" if index == 1 else "cut",
                "generated_assets": {
                    "storyboard_image": f"storyboards/scene_{unit_id}.png",
                    "video_clip": f"videos/scene_{unit_id}.mp4",
                    "status": "completed",
                },
            }
        )
    _write_json(
        project_dir / "scripts" / "episode_1.json",
        {"episode": 1, "title": "第一集", "content_mode": "narration", "segments": segments},
    )
    (project_dir / "source").mkdir()
    (project_dir / "source" / "1-7-0227.txt").write_text("第一段旁白。第二段旁白。", encoding="utf-8")
    drafts = project_dir / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    draft_name = "step1_segments.md" if schema_version < 10 else "script_plan_segments.md"
    (drafts / draft_name).write_text("# 分段\n\n1. 第一段旁白。\n2. 第二段旁白。\n", encoding="utf-8")
    videos: dict[str, dict[str, Any]] = {}
    for index, unit_id in enumerate(unit_ids):
        (project_dir / "storyboards").mkdir(exist_ok=True)
        (project_dir / "storyboards" / f"scene_{unit_id}.png").write_bytes(f"storyboard-{unit_id}".encode())
        record = _legacy_video_version_record("videos", unit_id, duration_seconds="4" if index == 0 else 4)
        _write_legacy_video(project_dir, "videos", unit_id, record)
        videos[unit_id] = {"current_version": 1, "versions": [record]}
    _write_versions(project_dir, {"videos": videos})
    _mark_asset_inventory_current(project_dir)
    return project_dir


def write_legacy_tts_narration_project(
    root: Path,
    name: str = "legacy-tts-narration",
    *,
    settings: tuple[TtsSynthesisSettings, ...] = (
        TtsSynthesisSettings("dashscope", "qwen3-tts-flash", "Cherry", None),
        TtsSynthesisSettings("dashscope", "qwen3-tts-flash", "Ethan", 1.2),
    ),
) -> Path:
    """在 ``write_legacy_storyboard_project`` 上补旁白配音：每个分镜一条选中的音频版本，带完整 TTS 设置。

    ``project.json`` 是旧口径：没有旁白交付方式，音频后端写裸供应商，音色与语速「留空跟随全局默认」。
    第 i 个分镜的旁白用 ``settings[i]`` 合成，``created_at`` 按分镜顺序递增。
    """

    unit_ids = tuple(f"E1S{index}" for index in range(1, len(settings) + 1))
    project_dir = write_legacy_storyboard_project(root, name, unit_ids=unit_ids)
    project_path = project_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["audio_backend"] = "dashscope"
    _write_json(project_path, project)
    script_path = project_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    versions = VersionManager(project_dir)
    for index, (segment, synthesis) in enumerate(zip(script["segments"], settings, strict=True), start=1):
        unit_id = segment["segment_id"]
        audio_path = f"audio/segment_{unit_id}.wav"
        segment["generated_assets"]["narration_audio"] = audio_path
        speech = {key: value for key, value in segment.items() if key != "transition_to_next"}
        basis = ArtifactBasisDescriptor.from_basis(
            build_narration_audio_basis(admit_script_unit("segments", speech).preparation, synthesis)
        )
        audio = project_dir / audio_path
        audio.parent.mkdir(exist_ok=True)
        audio.write_bytes(f"tts-{unit_id}".encode())
        versions.add_version(
            "audio",
            unit_id,
            segment["novel_text"],
            source_file=audio,
            artifact_episode=1,
            artifact_audio_basis=basis.to_dict(),
            execution_script_file="episode_1.json",
            tts_actual_duration_seconds=3.0,
            tts_provider_id=synthesis.provider_id,
            tts_model_id=synthesis.model_id,
            tts_voice=synthesis.voice,
            tts_speed=synthesis.speed,
            tts_basis_digest=basis.digest,
        )
        metadata_path = project_dir / "versions" / "versions.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["audio"][unit_id]["versions"][-1]["created_at"] = f"2026-04-0{index}T10:00:00Z"
        _write_json(metadata_path, metadata)
    _write_json(script_path, script)
    return project_dir


def write_legacy_unregistrable_asset_sheet_project(root: Path) -> Path:
    """旧资产图有缺原图、空描述和依赖不可登记本体的衍生。"""

    project_dir = write_legacy_storyboard_project(root, "legacy-asset-sheet-unregistrable-input")
    project_path = project_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["characters"] = {
        "张三": {
            "description": "主角",
            "reference_image": "characters/refs/张三.png",
            "character_sheet": "characters/张三.png",
            "derivatives": {
                "劲装": {"description": "换上劲装", "character_sheet": "characters/derivatives/张三/劲装.png"}
            },
        },
        "李四": {
            "description": "配角",
            "character_sheet": "characters/李四.png",
            "derivatives": {
                "便装": {"description": "换上便装", "character_sheet": "characters/derivatives/李四/便装.png"}
            },
        },
    }
    project["scenes"] = {"祠堂": {"description": "", "scene_sheet": "scenes/祠堂.png"}}
    for relative in (
        "characters/张三.png",
        "characters/derivatives/张三/劲装.png",
        "characters/李四.png",
        "characters/derivatives/李四/便装.png",
        "scenes/祠堂.png",
    ):
        (project_dir / relative).parent.mkdir(parents=True, exist_ok=True)
        (project_dir / relative).write_bytes(f"sheet-{relative}".encode())
    revision = compute_source_revision(project_dir, project, SourceScope(kind="all")).revision
    project["workflow"] = {"asset_inventory": {"scope": {"kind": "all", "files": []}, "source_revision": revision}}
    _write_json(project_path, project)
    return project_dir


def write_legacy_reference_video_project(
    root: Path,
    name: str = "legacy-reference",
    *,
    schema_version: int = 7,
    unit_ids: tuple[str, ...] = ("E1U01", "E1U02"),
    with_legacy_audio: bool = False,
    style: str = "写实",
    style_description: str = "电影感",
    split_unit_text: bool = False,
) -> Path:
    """drama + reference_video 路线的旧项目：视频单元直出，版本记录是旧形态。"""

    project_dir = root / name
    project_dir.mkdir(parents=True)
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": schema_version,
            "title": "旧参考生视频项目",
            "content_mode": "drama",
            "generation_mode": "reference_video",
            "source_kind": "novel",
            "source_language": "中文",
            "style": style,
            "style_description": style_description,
            "aspect_ratio": "9:16",
            "default_duration": 8,
            "characters": {},
            "scenes": {},
            "props": {},
            "products": {},
            "episodes": [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
        },
    )
    units = []
    for index, unit_id in enumerate(unit_ids, start=1):
        assets: dict[str, Any] = {"video_clip": f"reference_videos/{unit_id}.mp4", "status": "completed"}
        if with_legacy_audio:
            assets["narration_audio"] = f"audio/segment_{unit_id}.wav"
        units.append(
            {
                "unit_id": unit_id,
                "duration_seconds": 8,
                "text": f"第{index}个单元的画面描述。",
                "transition_to_next": "dissolve" if index == 1 else "cut",
                "generated_assets": assets,
            }
        )
    if split_unit_text:
        for unit in units:
            text = unit.pop("text")
            unit["shots"] = [{"shot_id": f"{unit['unit_id']}S1", "text": text}]
            unit["references"] = []
    _write_json(
        project_dir / "scripts" / "episode_1.json",
        {"episode": 1, "title": "第一集", "content_mode": "drama", "video_units": units},
    )
    (project_dir / "source").mkdir()
    (project_dir / "source" / "原著.txt").write_text("原著正文。", encoding="utf-8")
    drafts = project_dir / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    draft_name = "step1_reference_units.md" if schema_version < 10 else "script_plan_reference_units.md"
    (drafts / draft_name).write_text("# 单元\n\n1. 第一个单元。\n2. 第二个单元。\n", encoding="utf-8")
    videos: dict[str, dict[str, Any]] = {}
    audio: dict[str, dict[str, Any]] = {}
    for unit_id in unit_ids:
        record = _legacy_video_version_record("reference_videos", unit_id, duration_seconds=8)
        _write_legacy_video(project_dir, "reference_videos", unit_id, record)
        videos[unit_id] = {"current_version": 1, "versions": [record]}
        if with_legacy_audio:
            wav = project_dir / "audio" / f"segment_{unit_id}.wav"
            wav.parent.mkdir(parents=True, exist_ok=True)
            wav.write_bytes(f"tts-{unit_id}".encode())
            snapshot_rel = f"versions/audio/{unit_id}_v1_{_LEGACY_SNAPSHOT_TIMESTAMP}.wav"
            snapshot = project_dir / snapshot_rel
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_bytes(wav.read_bytes())
            audio[unit_id] = {
                "current_version": 1,
                "versions": [
                    {
                        "version": 1,
                        "file": snapshot_rel,
                        "prompt": "旁白",
                        "created_at": "2026-03-02T14:56:52Z",
                    }
                ],
            }
    _write_versions(project_dir, {"reference_videos": videos, "audio": audio})
    _mark_asset_inventory_current(project_dir)
    return project_dir


def write_legacy_ad_reference_video_project(root: Path, *, converted: bool = False) -> Path:
    """schema 6 广告的旧镜头索引，或镜头已转换、项目版本尚未提交的中断形态。"""

    project_dir = write_legacy_reference_video_project(root, schema_version=6)
    project_path = project_dir / "project.json"
    project = json.loads(project_path.read_bytes())
    project["content_mode"] = "ad"
    project["target_duration"] = 16
    _write_json(project_path, project)
    script_path = project_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_bytes())
    script["content_mode"] = "ad"
    units = script.pop("video_units")
    script["duration_seconds"] = 16
    if converted:
        script["video_units"] = [
            {**unit, "text": f"镜头{index}；缓慢转动", "note": None} for index, unit in enumerate(units, start=1)
        ]
    else:
        script["shots"] = [
            {
                "shot_id": f"E1S{index}",
                "duration_seconds": 8,
                "image_prompt": {"scene": f"镜头{index}"},
                "video_prompt": {"action": "缓慢转动"},
                "transition_to_next": unit["transition_to_next"],
            }
            for index, unit in enumerate(units, start=1)
        ]
        script["reference_units"] = [
            {
                "unit_id": unit["unit_id"],
                "shot_ids": [f"E1S{index}"],
                "generated_assets": unit["generated_assets"],
            }
            for index, unit in enumerate(units, start=1)
        ]
    _write_json(script_path, script)
    return project_dir


def write_legacy_style_project(
    root: Path,
    name: str = "legacy-style",
    *,
    schema_version: int = 7,
    style: str = "画风：写实电影感",
    style_description: str = "淡彩",
    style_template_id: str | None = None,
) -> Path:
    """风格值还是遗留形态的旧项目：资产图、宫格与单张分镜图齐全，四类视觉依据都在场。

    ``style`` 收「画风：」前缀值或 ``Photographic`` 这类短标签；``style_template_id`` 为 None
    时不写该字段，正是短标签解析的前置条件。宫格与单张分镜图同时存在，因为风格值只在其中
    两类依据里归一化，两边都要能被断言。
    """

    project_dir = root / name
    project_dir.mkdir(parents=True)
    project: dict[str, Any] = {
        "schema_version": schema_version,
        "title": "旧风格项目",
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "source_kind": "novel",
        "source_language": "中文",
        "style": style,
        "style_description": style_description,
        "aspect_ratio": "9:16",
        "grid_storyboard": True,
        "characters": {"阿离": {"description": "银发旅人", "character_sheet": "characters/阿离.png"}},
        "scenes": {"雨巷": {"description": "湿漉石板路", "scene_sheet": "scenes/雨巷.png"}},
        "props": {},
        "products": {},
        "episodes": [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
    }
    if style_template_id is not None:
        project["style_template_id"] = style_template_id
    _write_json(project_dir / "project.json", project)

    grid_id = "grid_123456789abc"
    grid_members = ("E1S01", "E1S02")
    segments: list[dict[str, Any]] = [
        {
            "segment_id": resource_id,
            "episode": 1,
            "duration_seconds": 4,
            "novel_text": f"{resource_id} 的旁白。",
            "characters_in_segment": [],
            "scenes": [],
            "props": [],
            "image_prompt": {"scene": f"{resource_id} 的画面", "composition": {"shot_type": "Medium Shot"}},
            "video_prompt": {"action": f"{resource_id} 的动作"},
            "transition_to_next": "cut",
            "generated_assets": {
                "storyboard_image": f"storyboards/scene_{resource_id}.png",
                "grid_id": grid_id,
                "grid_cell_index": index,
            },
        }
        for index, resource_id in enumerate(grid_members)
    ]
    segments.append(
        {
            "segment_id": "E1S03",
            "episode": 1,
            "duration_seconds": 4,
            "novel_text": "第三段旁白。",
            "segment_break": True,
            "characters_in_segment": [],
            "scenes": [],
            "props": [],
            "image_prompt": {
                "scene": "巷口回望",
                "composition": {"shot_type": "Wide Shot", "lighting": "夜色", "ambiance": "清冷"},
            },
            "video_prompt": {"action": "回望"},
            "transition_to_next": "fade",
            "generated_assets": {"storyboard_image": "storyboards/scene_E1S03.png", "status": "completed"},
        }
    )
    _write_json(
        project_dir / "scripts" / "episode_1.json",
        {"episode": 1, "title": "第一集", "content_mode": "narration", "segments": segments},
    )
    _write_json(
        project_dir / "drafts" / "episode_1" / "script_plan_segments.json", {"segments": [{"novel_text": "雨夜"}]}
    )
    (project_dir / "source").mkdir()
    (project_dir / "source" / "episode_1.txt").write_text("雨夜", encoding="utf-8")

    grid = GridGeneration(
        id=grid_id,
        episode=1,
        script_file="episode_1.json",
        scene_ids=list(grid_members),
        grid_image_path=f"grids/{grid_id}.png",
        rows=2,
        cols=2,
        cell_count=4,
        frame_chain=build_frame_chain(list(grid_members), 2, 2),
        status="completed",
        prompt="grid",
        provider="provider",
        model="model",
        grid_size="grid_4",
        created_at="2026-01-01T00:00:00Z",
        split_at="2026-01-01T00:01:00Z",
        video_aspect_ratio="9:16",
    )
    _write_json(project_dir / "grids" / f"{grid.id}.json", grid.to_dict())
    (project_dir / "grids" / f"{grid.id}.png").write_bytes(b"composite")
    (project_dir / "storyboards").mkdir()
    for resource_id in (*grid_members, "E1S03"):
        (project_dir / "storyboards" / f"scene_{resource_id}.png").write_bytes(resource_id.encode())
    (project_dir / "characters").mkdir()
    (project_dir / "characters" / "阿离.png").write_bytes(b"character-sheet")
    (project_dir / "scenes").mkdir()
    (project_dir / "scenes" / "雨巷.png").write_bytes(b"scene-sheet")
    _write_versions(project_dir, {})
    _mark_asset_inventory_current(project_dir)
    return project_dir


ScriptPlanVariantName = Literal["drama", "narration", "reference_video"]

#: 退役的条目指纹与整集指纹：样本里只要求字段在场，取值无关紧要。
_LEGACY_ENTRY_REVISION = "sha256-legacy-entry-revision"
_LEGACY_SCRIPT_REVISION = "sha256-legacy-script-revision"


def _legacy_plan_entry(variant: ScriptPlanVariantName, episode: int, index: int) -> dict[str, Any]:
    if variant == "reference_video":
        return {
            "unit_id": f"E{episode}U{index:02d}",
            "text": f"第{episode}集第{index}个单元的画面。",
            "duration_seconds": 8,
            "source_text": f"第{episode}集第{index}段原文。",
        }
    if variant == "narration":
        return {
            "segment_id": f"E{episode}S{index:02d}",
            "novel_text": f"第{episode}集第{index}段旁白。",
            "duration_seconds": 4,
            "segment_break": False,
            "characters_in_segment": [],
            "scenes": [],
            "props": [],
        }
    return {
        "scene_id": f"E{episode}S{index:02d}",
        "duration_seconds": 8,
        "segment_break": False,
        "characters_in_scene": [],
        "scenes": [],
        "props": [],
        "scene_description": f"第{episode}集第{index}镜的视觉改编。",
        "utterances": [{"kind": "voiceover", "speaker": None, "text": f"第{episode}集第{index}句旁白。"}],
        "source_text": f"第{episode}集第{index}段原文。",
    }


def _legacy_script_entry(
    variant: ScriptPlanVariantName, plan_entry: dict[str, Any], *, authored: bool, with_revisions: bool
) -> dict[str, Any]:
    """已有正式脚本里的一条：``authored=False`` 是视觉层两侧皆空、也没有待编写标记的旧形态。"""

    entry = dict(plan_entry)
    if variant == "reference_video":
        # 旧参考单元不带对应原文。
        entry.pop("source_text")
    else:
        if variant == "drama":
            # 旧 drama 分镜不带视觉改编描述。
            entry.pop("scene_description")
        entry["image_prompt"] = {"scene": "画面", "composition": {"shot_type": "Medium Shot"}} if authored else None
        entry["video_prompt"] = {"action": "动作"} if authored else None
    if with_revisions:
        entry["script_plan_entry_revision"] = _LEGACY_ENTRY_REVISION
    entry["transition_to_next"] = "cut"
    return entry


def write_legacy_script_plan_project(
    root: Path,
    name: str = "legacy-script-plan",
    *,
    variant: ScriptPlanVariantName,
    schema_version: int = 14,
) -> Path:
    """条目指纹时期写出的脚本规划项目，三集各是一种要在 v15 收编的旧形态。

    - 第 1 集：已确认，正式脚本带条目指纹与整集指纹；第 2 条视觉层为空而无待编写标记；drama 分镜
      缺视觉改编描述、参考单元缺对应原文。
    - 第 2 集：已确认，绑定的正式脚本尚不在盘上（旧版由提示词编写产出，确认本身不转出正式脚本）。
    - 第 3 集：有正式脚本与脚本规划、无确认记录（grandfather 集）。

    ``schema_version < 10`` 时草稿、确认记录与整集指纹用 v9 之前的旧名，条目指纹不写（那时还
    没有）；产物清单由链上 v7→v8 激活补录。
    """

    content_mode = "narration" if variant == "narration" else "drama"
    generation_mode = "reference_video" if variant == "reference_video" else "storyboard"
    legacy_names = schema_version < 10
    plan_filename = {
        "drama": "normalized_script.json",
        "narration": "segments.json",
        "reference_video": "reference_units.json",
    }[variant]
    plan_filename = f"{'step1' if legacy_names else 'script_plan'}_{plan_filename}"
    review_field = "step1_review" if legacy_names else "script_plan_review"
    metadata_field = "step1_revision" if legacy_names else "script_plan_revision"
    with_revisions = schema_version >= 14
    items_key = {"drama": "scenes", "narration": "segments", "reference_video": "units"}[variant]
    script_items_key = "video_units" if variant == "reference_video" else items_key

    project_dir = root / name
    project_dir.mkdir(parents=True)
    episodes: list[dict[str, Any]] = []
    for episode in (1, 2, 3):
        plan_entries = [_legacy_plan_entry(variant, episode, index) for index in (1, 2)]
        plan: dict[str, Any] = {items_key: plan_entries}
        if variant == "drama":
            plan["title"] = f"规划第{episode}集"
        plan_path = project_dir / "drafts" / f"episode_{episode}" / plan_filename
        _write_json(plan_path, plan)
        (project_dir / "source").mkdir(exist_ok=True)
        (project_dir / "source" / f"episode_{episode}.txt").write_text(f"第{episode}集原文。", encoding="utf-8")
        script_file = f"scripts/episode_{episode}.json"
        ledger: dict[str, Any] = {"episode": episode, "title": f"第{episode}集", "script_file": script_file}
        if episode in (1, 2):
            ledger[review_field] = {
                "fingerprint": content_fingerprint(plan_path),
                "confirmed_at": "2026-01-01T00:00:00Z",
            }
        if episode in (1, 3):
            script: dict[str, Any] = {
                "episode": episode,
                "title": f"第{episode}集",
                "content_mode": content_mode,
                script_items_key: [
                    _legacy_script_entry(variant, entry, authored=index == 0, with_revisions=with_revisions)
                    for index, entry in enumerate(plan_entries)
                ],
                "metadata": {metadata_field: content_fingerprint(plan_path)},
            }
            _write_json(project_dir / script_file, script)
        episodes.append(ledger)
    project: dict[str, Any] = {
        "schema_version": schema_version,
        "title": "旧脚本规划项目",
        "content_mode": content_mode,
        "generation_mode": generation_mode,
        "source_kind": "novel",
        "source_language": "中文",
        "style": "写实",
        "style_description": "电影感",
        "aspect_ratio": "9:16",
        "default_duration": 8,
        "characters": {},
        "scenes": {},
        "props": {},
        "products": {},
        "episodes": episodes,
    }
    _write_json(project_dir / "project.json", project)
    _write_versions(project_dir, {})
    _mark_asset_inventory_current(project_dir)
    return project_dir


def write_legacy_drama_storyboard_project(
    root: Path,
    name: str = "legacy-drama-storyboard",
    *,
    schema_version: int = 7,
) -> Path:
    """剧情演绎项目，``project.json`` 没有 ``aspect_ratio`` 字段；第 1 集首条分镜已有分镜图。

    脚本规划与正式脚本形态同 ``write_legacy_script_plan_project(variant="drama")``。
    """

    project_dir = write_legacy_script_plan_project(root, name, variant="drama", schema_version=schema_version)
    project_path = project_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project.pop("aspect_ratio")
    _write_json(project_path, project)
    script_path = project_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    first = script["scenes"][0]
    storyboard = f"storyboards/scene_{first['scene_id']}.png"
    first["generated_assets"] = {"storyboard_image": storyboard, "status": "completed"}
    _write_json(script_path, script)
    (project_dir / storyboard).parent.mkdir(parents=True, exist_ok=True)
    (project_dir / storyboard).write_bytes(f"storyboard-{first['scene_id']}".encode())
    return project_dir


def _mark_asset_inventory_current(project_dir: Path) -> None:
    """旧项目都跑过资产分析：清点标记与当前源文一致，制作状态越过资产清点门。"""

    project_path = project_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    revision = compute_source_revision(project_dir, project, SourceScope(kind="all")).revision
    project["workflow"] = {"asset_inventory": {"scope": {"kind": "all", "files": []}, "source_revision": revision}}
    _write_json(project_path, project)


def write_undescribed_style_bases_project(
    root: Path,
    name: str,
    *,
    route: Literal["grid", "reference_video"],
    style: str = "写实电影感",
    style_description: str = "胶片颗粒，低饱和",
    schema_version: Literal[12, 13] = 13,
) -> Path:
    """停在 v12 或 v13 的自定义风格项目：宫格、切格分镜或参考视频的依据不含风格描述。

    0.27 起的版本记录冻结类型化依据，那时的依据构造不记 ``style_description``：项目以空描述走完
    迁移链登记全部产物，再写入描述，得到的清单与版本记录正是那时留下的形态。停在 v12 的样本
    代表 0.27–0.29 留下的项目，两者盘上形态相同，只差 ``schema_version``。资产图与单张分镜图的
    依据一向记描述，同法构造后它们在迁移前就是过期的——描述出现在它们登记之后。``style`` 可传
    遗留风格值，与描述补记叠加。
    """

    if route == "grid":
        project_dir = write_legacy_style_project(root, name, style=style, style_description="")
    else:
        project_dir = write_legacy_reference_video_project(root, name, style=style, style_description="")
    advance_project_schema(project_dir, to_version=13)
    project_path = project_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["style_description"] = style_description
    project["schema_version"] = schema_version
    _write_json(project_path, project)
    return project_dir


def bind_episode_script_to_filename(project_dir: Path, episode: int, filename: str) -> None:
    """SSE 索引同步曾把带 ``episode`` 整数的任意 ``scripts/*.json`` 登记为集绑定（schema ≤ 14）。

    把该集剧本改名为 ``scripts/<filename>`` 并改绑；清单里该集剧本的登记随之指向新文件名。
    """

    script_file = f"scripts/{filename}"
    project_path = project_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    ledger = next(entry for entry in project["episodes"] if entry["episode"] == episode)
    source = project_dir / ledger["script_file"]
    if source.is_file():
        source.rename(project_dir / script_file)
    ledger["script_file"] = script_file
    _write_json(project_path, project)
    adapter = ProjectArtifactManifestAdapter(project_dir)
    key = ArtifactKey.episode_script(episode)
    entry = adapter.get_entry(key)
    if entry is not None:
        adapter.put_entry(key, ArtifactManifestEntry(artifact_path=script_file, basis_digest=entry.basis_digest))


def write_legacy_presentation_project(
    root: Path,
    name: str = "legacy-presentation",
    *,
    transition: str = "fade",
    schema_version: int = 7,
) -> Path:
    """旁白项目，唯一分镜的后期配音呈现模型已物化；剧本条目与呈现模型文件都带转场。

    呈现模型依据是当时的口径：输入里有 ``transition_to_next``。视频版本记录带类型化来源，
    呈现模型文件自证成立，迁移链上的激活据此登记它与字幕草稿。
    """

    project_dir = root / name
    project_dir.mkdir(parents=True)
    _write_json(
        project_dir / "project.json",
        {
            "schema_version": schema_version,
            "title": "旧呈现模型项目",
            "content_mode": "narration",
            "generation_mode": "storyboard",
            "source_kind": "novel",
            "source_language": "中文",
            "style": "水墨",
            "style_description": "",
            "aspect_ratio": "9:16",
            "grid_storyboard": False,
            "characters": {},
            "scenes": {},
            "props": {},
            "products": {},
            "episodes": [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
        },
    )
    item: dict[str, Any] = {
        "segment_id": "E1S01",
        "duration_seconds": 4,
        "novel_text": "雨夜",
        "image_prompt": "阿离站在雨中",
        "video_prompt": "阿离转身",
        "characters_in_segment": [],
        "scenes": [],
        "props": [],
        "transition_to_next": transition,
        "generated_assets": {
            "storyboard_image": "storyboards/scene_E1S01.png",
            "video_clip": "videos/scene_E1S01.mp4",
        },
    }
    _write_json(
        project_dir / "scripts" / "episode_1.json",
        {"episode": 1, "title": "第一集", "content_mode": "narration", "segments": [item]},
    )
    (project_dir / "source").mkdir()
    (project_dir / "source" / "episode_1.txt").write_text("雨夜", encoding="utf-8")
    _write_json(
        project_dir / "drafts" / "episode_1" / "script_plan_segments.json", {"segments": [{"novel_text": "雨夜"}]}
    )
    storyboard = project_dir / "storyboards" / "scene_E1S01.png"
    storyboard.parent.mkdir(parents=True)
    storyboard.write_bytes(b"storyboard")
    video = project_dir / "videos" / "scene_E1S01.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"paid-video")

    preparation = admit_script_unit("segments", item).preparation
    visual = build_storyboard_video_artifact_visual_basis(
        resource_id="E1S01",
        visual_prompt=item["video_prompt"],
        storyboard_image=storyboard,
        end_frame_image=None,
        aspect_ratio="9:16",
    )
    speech = build_video_speech_basis(preparation)
    duration = build_video_duration_basis(4)
    facts = VideoArtifactCurrencyFacts(
        episode=1,
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
    versions = VersionManager(project_dir)
    selected_version = versions.add_version(
        "videos",
        "E1S01",
        "paid",
        source_file=video,
        execution_checkpoint_schema_version=3,
        execution_duration_seconds=4,
        execution_request_digest="a" * 64,
        execution_script_file="episode_1.json",
        execution_provider_media=[],
        execution_generate_audio=True,
        artifact_video_currency=facts.to_dict(),
    )
    selected = next(
        record
        for record in versions.get_versions("videos", "E1S01")["versions"]
        if record["version"] == selected_version
    )
    media = PresentationMedia(
        artifact_path=selected["file"],
        version=selected_version,
        selection="current",
        currency="current",
        evidence=SelectedMediaEvidence.from_file(
            basis=facts.video_basis, path=project_dir / selected["file"], actual_duration_seconds=4.0
        ),
    )
    presentation = materialize_speech_presentation(
        preparation, variant="post_production", video=media, provider_audio_enabled=True
    )
    legacy_basis = legacy_transition_presentation_basis(
        variant="post_production",
        transition=transition,
        video=media.evidence.basis_input(),
        subtitle=ArtifactBasisDescriptor.from_basis(presentation.subtitle_basis).to_dict(),
        narration_audio=None,
        provider_audio_enabled=True,
    )
    subtitle_path, presentation_path = presentation_artifact_paths(1, "E1S01", "post_production")
    _write_json(project_dir / subtitle_path, presentation.subtitle_artifact_dict())
    _write_json(
        project_dir / presentation_path,
        {
            "episode": 1,
            "resource_type": "videos",
            "script_file": "episode_1.json",
            "transition_to_next": transition,
            "subtitle_artifact_path": subtitle_path,
            "presentation_artifact_path": presentation_path,
            "persisted": True,
            **presentation.to_dict(),
            "presentation_basis": ArtifactBasisDescriptor.from_basis(legacy_basis).to_dict(),
        },
    )
    _mark_asset_inventory_current(project_dir)
    return project_dir


def legacy_transition_presentation_basis(
    *,
    variant: str,
    transition: str,
    video: dict[str, object],
    subtitle: dict[str, object],
    narration_audio: dict[str, object] | None,
    provider_audio_enabled: bool,
) -> ArtifactBasis:
    """schema ≤ 15 的代码给呈现模型算的依据：输入含脚本条目上的转场。"""

    return ArtifactBasis.build(
        "artifact-speech/presentation",
        kind_version=2,
        inputs={
            "variant": variant,
            "transition_to_next": transition,
            "video": video,
            "subtitle": subtitle,
            "narration_audio": narration_audio,
            "mix_policy": {
                "kind": "provider-original-plus-optional-tts",
                "version": 1,
                "provider_video_gain": 1.0,
                "narration_audio_gain": 1.0,
                "provider_audio_enabled": provider_audio_enabled,
            },
        },
    )


def advance_project_schema(project_dir: Path, *, to_version: int) -> None:
    """按迁移链把项目从当前 ``schema_version`` 逐级推进到 ``to_version``。"""

    version = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))["schema_version"]
    while version < to_version:
        MIGRATORS[version](project_dir)
        version += 1
    actual = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))["schema_version"]
    if actual != to_version:
        raise AssertionError(f"schema advanced to {actual}, expected {to_version}")


__all__ = [
    "ScriptPlanVariantName",
    "advance_project_schema",
    "bind_episode_script_to_filename",
    "legacy_transition_presentation_basis",
    "write_legacy_ad_reference_video_project",
    "write_legacy_drama_storyboard_project",
    "write_legacy_presentation_project",
    "write_legacy_reference_video_project",
    "write_legacy_script_plan_project",
    "write_legacy_storyboard_project",
    "write_legacy_style_project",
    "write_legacy_tts_narration_project",
    "write_undescribed_style_bases_project",
]
