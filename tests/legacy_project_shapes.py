"""旧版本代码写出的项目目录形态，供迁移与读侧测试共用。

形态清单见 ``docs/agents/project-migrations.md``「已知旧形态」。构造出的目录停在
``schema_version`` 参数指定的版本；``advance_project_schema`` 按迁移链逐级推进到指定版本，
模拟已经升级过的安装。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from lib.artifact_manifest import ArtifactKey, ArtifactManifestEntry, ProjectArtifactManifestAdapter
from lib.grid.models import GridGeneration, build_frame_chain
from lib.project_migrations.runner import MIGRATORS
from lib.script_review import content_fingerprint
from lib.source_revision import SourceScope, compute_source_revision

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


def write_legacy_reference_video_project(
    root: Path,
    name: str = "legacy-reference",
    *,
    schema_version: int = 7,
    unit_ids: tuple[str, ...] = ("E1U01", "E1U02"),
    with_legacy_audio: bool = False,
    style: str = "写实",
    style_description: str = "电影感",
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
    "write_legacy_reference_video_project",
    "write_legacy_script_plan_project",
    "write_legacy_storyboard_project",
    "write_legacy_style_project",
    "write_undescribed_style_bases_project",
]
