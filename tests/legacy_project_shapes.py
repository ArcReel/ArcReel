"""早于产物清单（schema 8）写出的项目目录形态，供迁移与读侧测试共用。

字段形状逐一照抄旧版本落盘的真实项目：视频版本记录只有 ``version/file/prompt/created_at/
duration_seconds`` 五个字段（``duration_seconds`` 可能是字符串）、脚本规划草稿是 ``.md``、
源文用上传时的原名、没有 ``source/episode_N.txt``。构造出的目录停在 ``schema_version``
参数指定的版本；``advance_project_schema`` 按迁移链逐级推进到指定版本，模拟已经升级过的
安装。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.project_migrations.runner import MIGRATORS
from lib.source_revision import SourceScope, compute_source_revision

LEGACY_SNAPSHOT_TIMESTAMP = "20260302T145652"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def legacy_video_version_record(
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
        "file": f"versions/{resource_type}/{resource_id}_v{version}_{LEGACY_SNAPSHOT_TIMESTAMP}.mp4",
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
        record = legacy_video_version_record("videos", unit_id, duration_seconds="4" if index == 0 else 4)
        _write_legacy_video(project_dir, "videos", unit_id, record)
        videos[unit_id] = {"current_version": 1, "versions": [record]}
    _write_versions(project_dir, {"videos": videos})
    _mark_asset_inventory_current(project_dir)
    return project_dir


def write_legacy_reference_video_project(
    root: Path,
    name: str = "legacy-reference",
    *,
    schema_version: int = 7,
    unit_ids: tuple[str, ...] = ("E1U01", "E1U02"),
    with_legacy_audio: bool = False,
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
            "style": "写实",
            "style_description": "电影感",
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
                "generated_assets": assets,
            }
        )
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
        record = legacy_video_version_record("reference_videos", unit_id, duration_seconds=8)
        _write_legacy_video(project_dir, "reference_videos", unit_id, record)
        videos[unit_id] = {"current_version": 1, "versions": [record]}
        if with_legacy_audio:
            wav = project_dir / "audio" / f"segment_{unit_id}.wav"
            wav.parent.mkdir(parents=True, exist_ok=True)
            wav.write_bytes(f"tts-{unit_id}".encode())
            snapshot_rel = f"versions/audio/{unit_id}_v1_{LEGACY_SNAPSHOT_TIMESTAMP}.wav"
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


def _mark_asset_inventory_current(project_dir: Path) -> None:
    """旧项目都跑过资产分析：把清点标记写成与当前源文一致，制作状态不再停在资产清点。"""

    project_path = project_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    revision = compute_source_revision(project_dir, project, SourceScope(kind="all")).revision
    project["workflow"] = {"asset_inventory": {"scope": {"kind": "all", "files": []}, "source_revision": revision}}
    _write_json(project_path, project)


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
    "LEGACY_SNAPSHOT_TIMESTAMP",
    "advance_project_schema",
    "legacy_video_version_record",
    "write_legacy_reference_video_project",
    "write_legacy_storyboard_project",
]
