"""v8→v9：视频单元收敛为「一段正文 + 编排时长」。

参考生视频的单元此前把正文切成 ``shots[]`` 落盘，另派生一份 ``references[]``。两者都是机器
切分的结果、没有创作者价值（见 ADR 0064），本迁移把它们去掉：

- ``shots[*].text`` 按数组顺序用换行拼回 ``text``。不重新注入任何分段前缀——正文里原本写着的
  ``镜头N：`` 字面因此成为普通正文，逐字保留，不删不改。仍挂在镜头上的存量时长先收编到
  unit 级（``lib.reference_video.duration_migration``），此前那步只由加载链惰性完成，而
  ``shots`` 在本迁移之后就不存在了。
- 删除 ``references[]``：参考图改由执行期从正文的 ``@[名称]`` 按首次提及顺序解析。
- 删除 ``migration_requires_content_replan``：它记录的是 v6→v7 折叠镜头结构留下的证据，镜头
  结构不复存在后这条 provenance 没有可指向的对象；它蕴含的 ``needs_replan`` 原样保留，问题
  单元仍然阻断生成、仍需重新规划。

同一套改写作用于剧集脚本与 script_plan 草稿：草稿是同一份正文的上一形态，留在旧形状会让审阅 gate
读不出内容。

写入顺序沿用本目录约定：先只读预检全部文件并算出目标载荷，全部通过后才创建备份、再逐文件
原子替换，最后提交 ``project.json`` 版本。``project.json`` 的备份也由本迁移在这个提交边界上
自建（runner 的 ``_MIGRATORS_WITH_OWNED_BACKUP``），否则 runner 会在预检之前先落一个备份文件。
任一文件损坏时项目目录一个字节都没被动过，runner 据此落「需要修复」裁决。
"""

from __future__ import annotations

import copy
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from lib.episode_paths import episode_drafts_dir
from lib.json_io import atomic_write_json, load_json
from lib.path_safety import safe_join
from lib.project_migration_failure import ProjectMigrationError
from lib.reference_video.duration_migration import migrate_unit_durations
from lib.script_models import ReferenceScriptPlanUnit, ReferenceVideoUnit

_TARGET_VERSION = 9

#: 本步可能遇到的两个参考生视频脚本规划草稿名，按查找顺序排列。起点低于 v8 的项目在 v7→v8
#: 已被前置改名为 ``script_plan_*``；起点正好是 v8 的项目没经过那一步，草稿仍是 v8 的落盘事实
#: ``step1_*``。两个名字都是历史事实，写死在这一步，不跟随当前命名。
_REFERENCE_UNITS_FILENAMES = ("script_plan_reference_units.json", "step1_reference_units.json")


def _reference_units_draft_path(project_dir: Path, episode: int) -> Path:
    """取该集在盘上的脚本规划草稿路径；两个名字都不在时返回首选名（读取侧按不存在处理）。"""

    drafts_dir = episode_drafts_dir(project_dir, episode)
    for name in _REFERENCE_UNITS_FILENAMES:
        candidate = drafts_dir / name
        if candidate.exists():
            return candidate
    return drafts_dir / _REFERENCE_UNITS_FILENAMES[0]


def _unit_text(unit: dict[str, Any], location: str) -> str:
    """把 ``shots[*].text`` 按序拼回单元正文；已是正文形态的单元原样返回。"""

    if "shots" not in unit:
        text = unit.get("text")
        if not isinstance(text, str):
            raise ValueError(f"{location} 既没有 shots 也没有字符串 text")
        return text
    shots = unit.get("shots")
    if not isinstance(shots, list):
        raise ValueError(f"{location}.shots 必须是数组")
    parts: list[str] = []
    for index, shot in enumerate(shots):
        if not isinstance(shot, dict):
            raise ValueError(f"{location}.shots[{index}] 必须是对象")
        text = shot.get("text")
        if text is not None and not isinstance(text, str):
            raise ValueError(f"{location}.shots[{index}].text 必须是字符串")
        parts.append(text or "")
    return "\n".join(parts)


def _migrate_unit(unit: object, location: str, model: type[BaseModel]) -> dict[str, Any]:
    """转换一个单元并按目标模型自检。

    校验只覆盖单元本身：本迁移只改单元形状，把整份剧本拿去校验会让一处与镜头无关的存量脏
    字段（缺 title 之类）把整个项目判成「需要修复」。
    """

    if not isinstance(unit, dict):
        raise ValueError(f"{location} 必须是对象")
    migrated = copy.deepcopy(unit)
    # 时长曾挂在 shots[*].duration 上，此前只由加载链惰性收编——存量落盘里仍可能只有镜头时长。
    # shots 在下面就被删掉，这里是最后一次能读到那份时长的时刻，故先收编再拼正文。
    migrate_unit_durations([migrated])
    text = _unit_text(migrated, location)
    migrated.pop("shots", None)
    migrated.pop("references", None)
    # 旧标记只为镜头结构服务；它蕴含的 needs_replan 在下一行显式保留。
    if migrated.pop("migration_requires_content_replan", None) is True:
        migrated["needs_replan"] = True
    migrated["text"] = text
    model.model_validate(migrated)
    return migrated


def migrate_reference_script(payload: dict[str, Any], *, location: str) -> dict[str, Any]:
    """纯转换一份参考生视频剧集脚本；已是 v9 形状的脚本原样校验后返回。"""

    units = payload.get("video_units")
    if not isinstance(units, list):
        raise ValueError(f"{location}.video_units 必须是数组")
    migrated = copy.deepcopy(payload)
    migrated["video_units"] = [
        _migrate_unit(unit, f"{location}.video_units[{index}]", ReferenceVideoUnit) for index, unit in enumerate(units)
    ]
    return migrated


def migrate_reference_script_plan_draft(payload: dict[str, Any], *, location: str) -> dict[str, Any]:
    """纯转换一份 script_plan 草稿；已是 v9 形状的草稿原样校验后返回。"""

    units = payload.get("units")
    if not isinstance(units, list):
        raise ValueError(f"{location}.units 必须是数组")
    migrated = copy.deepcopy(payload)
    migrated["units"] = [
        _migrate_unit(unit, f"{location}.units[{index}]", ReferenceScriptPlanUnit) for index, unit in enumerate(units)
    ]
    return migrated


def _episode_entries(project_dir: Path, project: dict[str, Any]) -> list[tuple[Path, int]]:
    episodes = project.get("episodes")
    if not isinstance(episodes, list):
        raise ValueError("project.episodes 必须是数组")
    result: list[tuple[Path, int]] = []
    seen: set[Path] = set()
    for index, entry in enumerate(episodes):
        if not isinstance(entry, dict):
            raise ValueError(f"project.episodes[{index}] 必须是对象")
        episode = entry.get("episode")
        script_file = entry.get("script_file")
        if not isinstance(episode, int) or isinstance(episode, bool) or episode <= 0:
            raise ValueError(f"project.episodes[{index}].episode 必须是正整数")
        if not isinstance(script_file, str) or not script_file:
            raise ValueError(f"project.episodes[{index}].script_file 必须是非空字符串")
        path = safe_join(project_dir, script_file)
        if path in seen:
            raise ValueError(f"多个 episode 指向同一剧本文件: {script_file}")
        seen.add(path)
        result.append((path, episode))
    return result


def _readable_file(path: Path, label: str) -> dict[str, Any] | None:
    if path.is_symlink():
        raise ValueError(f"{label} 不是普通文件")
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError(f"{label} 不是普通文件")
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"{label} 必须是对象")
    return payload


def _ensure_backup(path: Path) -> None:
    if any(path.parent.glob(f"{path.name}.bak.v8-*")):
        return
    shutil.copy2(path, path.with_name(f"{path.name}.bak.v8-{time.time_ns()}"))


@contextmanager
def _located(episode: int, file: str) -> Iterator[None]:
    """把预检抛出的结构违约补成带定位事实的迁移错误。

    「需要修复」裁决按 ``(episode, file)`` 给用户与 Agent 导航，仅凭消息文本无法定位到集与文件。
    """

    try:
        yield
    except ProjectMigrationError:
        raise
    except ValueError as exc:
        raise ProjectMigrationError(str(exc), episode=episode, file=file) from exc


def migrate_v8_to_v9(project_dir: Path) -> None:
    """启动扫描与归档导入共用的单一入口（经 ``migrate_project_dir`` 调用）。"""

    project_dir = Path(project_dir)
    project_file = project_dir / "project.json"
    if not project_file.is_file():
        return
    project = load_json(project_file)
    if not isinstance(project, dict):
        raise ValueError("project.json 必须是对象")
    if int(project.get("schema_version") or 0) >= _TARGET_VERSION:
        return

    plans: list[tuple[Path, dict[str, Any]]] = []
    if project.get("generation_mode") == "reference_video":
        # 此循环只读：任一文件损坏时还没有备份或业务文件写入。
        for script_path, episode in _episode_entries(project_dir, project):
            label = script_path.name
            with _located(episode, label):
                payload = _readable_file(script_path, f"剧本 {label}")
                if payload is not None:
                    plans.append((script_path, migrate_reference_script(payload, location=f"剧本 {label}")))

            draft_path = _reference_units_draft_path(project_dir, episode)
            draft_label = f"第 {episode} 集 {draft_path.name}"
            with _located(episode, draft_path.name):
                draft = _readable_file(draft_path, draft_label)
                if draft is not None:
                    plans.append((draft_path, migrate_reference_script_plan_draft(draft, location=draft_label)))

    # 预检全部成功后才创建备份；所有文件（含 project.json）先备份完，再开始替换。
    for path, _payload in [*plans, (project_file, project)]:
        _ensure_backup(path)
    for path, payload in plans:
        atomic_write_json(path, payload)

    migrated_project = copy.deepcopy(project)
    migrated_project["schema_version"] = _TARGET_VERSION
    atomic_write_json(project_file, migrated_project)


__all__ = ["migrate_reference_script", "migrate_reference_script_plan_draft", "migrate_v8_to_v9"]
