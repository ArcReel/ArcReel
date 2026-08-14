"""迁移步共用的 ``episodes[].script_file`` → 剧本路径解析。"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from lib.path_safety import safe_join

_SCRIPTS_DIR = "scripts"


def resolve_bound_script_path(project_dir: Path, script_file: str) -> Path:
    """按 ``script_file`` 解析剧本路径，口径与读取方一致。

    ``scripts/episode_1.json`` 与裸文件名 ``episode_1.json`` 是同一剧本的合法别名
    （``ProjectManager.normalize_script_filename``），外部归档里还可能带 Windows 分隔符。
    迁移若按字面拼接，这些写法会被判成「文件不存在」而整份剧本跳过，可 ``project.json``
    照常盖上新版本号——留下版本号已升、剧本仍是旧契约的项目，之后再也不会被迁移链回收。
    候选顺序与 ``DataValidator._resolve_existing_path`` 相同：先按字面，裸文件名再退到
    ``scripts/`` 下的同名文件，取第一个在盘上存在的。

    都不存在时返回字面候选，让调用方按原路径报「缺失」或跳过；返回的候选可能是符号链接，
    普通文件判定仍由调用方负责。
    """
    normalized = script_file.replace("\\", "/")
    candidates = [normalized]
    if len(PurePosixPath(normalized).parts) == 1:
        candidates.append(f"{_SCRIPTS_DIR}/{normalized}")
    resolved = [safe_join(project_dir, candidate) for candidate in candidates]
    for path in resolved:
        if path.exists() or path.is_symlink():
            return path
    return resolved[0]
