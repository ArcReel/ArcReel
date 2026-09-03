"""Agent 记忆两级目录的路径派生：项目记忆与用户记忆的单一真相源。

零 I/O 纯函数——目录不存在也照常派生。会话装配（重定向原生 auto memory）、
用户记忆存储服务与 ``AgentAccessPolicy`` 的两层围栏投影都从这里取路径：
围栏放行的目录与实际写入的目录一旦各自拼串，任一处笔误都会让 Agent 写到
围栏外并被静默拒绝。

- 项目记忆：``<项目目录>/.arcreel/memory/``
- 用户记忆：``<数据根>/.arcreel/users/<user_id>/memory/``

落 ``.arcreel/`` 而非 ``.claude/``：后者是 profile 物化树，manifest 失配与
「恢复内置 profile」都会整树删除。数据根下 ``_`` 前缀目录表示跨项目共享素材库，
语义不同，故用户记忆走 ``.arcreel/users/`` 而非 ``_users/``。
"""

from __future__ import annotations

from pathlib import Path

#: ArcReel 内部状态目录名：校验器跳过点目录、归档导出只拷贝可见项，记忆因此
#: 天然不入归档、不报未识别目录。
ARCREEL_DIRNAME = ".arcreel"

#: 记忆目录名。两级共用同一层级名，便于「记忆」在两处一眼可辨。
MEMORY_DIRNAME = "memory"

#: 用户记忆在数据根下的命名空间目录名。
USERS_DIRNAME = "users"


def project_memory_dir(project_dir: Path) -> Path:
    """项目记忆目录：``<项目目录>/.arcreel/memory/``。"""
    return project_dir / ARCREEL_DIRNAME / MEMORY_DIRNAME


def user_memory_dir(projects_root: Path, user_id: str) -> Path:
    """用户记忆目录：``<数据根>/.arcreel/users/<user_id>/memory/``。

    ``user_id`` 须是单个路径段：它直接构成目录名，含 ``/`` / ``\\`` / ``.`` /
    ``..`` 或为空时会让用户记忆目录逃出数据根，进而把围栏放行范围扩到任意路径。
    非法值抛 ``ValueError``——调用方在围栏里应按 fail-closed 处置（见
    ``AgentAccessPolicy._user_memory_dir_or_none``）。
    """
    if not is_valid_memory_user_id(user_id):
        raise ValueError(f"user_id 必须是单个路径段，不能为空或含路径分隔符：{user_id!r}")
    return projects_root / ARCREEL_DIRNAME / USERS_DIRNAME / user_id / MEMORY_DIRNAME


def is_valid_memory_user_id(user_id: str) -> bool:
    """``user_id`` 是否可安全用作用户记忆目录的单个路径段。"""
    return bool(user_id) and user_id not in {".", ".."} and "/" not in user_id and "\\" not in user_id
