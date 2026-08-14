"""迁移步共用的 ``episodes[].script_file`` → 剧本路径解析。"""

from __future__ import annotations

import posixpath
from pathlib import Path

from lib.path_safety import safe_join

_SCRIPTS_DIR = "scripts"


def resolve_bound_script_path(project_dir: Path, script_file: str) -> Path:
    """按 ``script_file`` 解析剧本路径，口径与读取方一致。

    读取方（``ProjectManager.normalize_script_filename`` + ``_read_script_unlocked``）剥掉
    ``scripts/`` 前缀后一律相对 ``scripts/`` 解析：``episode_1.json``、``scripts/episode_1.json``、
    ``season_1/episode_1.json`` 都指向 ``scripts/`` 下的同一份剧本，外部归档里还可能带 Windows
    分隔符。迁移若按字面拼接，这些写法会被判成「文件不存在」而整份剧本跳过，可 ``project.json``
    照常盖上新版本号——留下版本号已升、剧本仍是旧契约的项目，之后再也不会被迁移链回收。
    候选顺序以读取方为准：先按 ``scripts/`` 下的对应路径，再退到字面路径，取第一个在盘上
    存在的。两处同名文件并存时（项目根一份、``scripts/`` 一份）只有后者会被运行时读到，
    先按字面取会去升级一份没人读的文件，真正在用的剧本反而留在旧契约上。

    都不存在时返回 ``scripts/`` 下的候选，让调用方按该路径报「缺失」或跳过；返回的候选
    可能是符号链接，普通文件判定仍由调用方负责。
    """
    normalized = script_file.replace("\\", "/")
    under_scripts = posixpath.join(_SCRIPTS_DIR, posixpath.normpath(normalized).removeprefix(f"{_SCRIPTS_DIR}/"))
    candidates = [under_scripts]
    if under_scripts != normalized:
        candidates.append(normalized)
    resolved = [safe_join(project_dir, candidate) for candidate in candidates]
    for path in resolved:
        if path.exists() or path.is_symlink():
            return path
    return resolved[0]
