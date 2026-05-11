"""权限模型 — fork 私有

集中放置角色常量、纯函数判断、命名校验等无状态逻辑。
角色集只有 ``admin`` / ``user``，复用上游已有字面量；``admin`` 在本 fork 里就是"超管"。
"""

from __future__ import annotations

import re
from typing import Final

# ---------------------------------------------------------------------------
# 角色常量
# ---------------------------------------------------------------------------

ROLE_ADMIN: Final = "admin"
ROLE_USER: Final = "user"

VALID_ROLES: Final[frozenset[str]] = frozenset({ROLE_ADMIN, ROLE_USER})

# 用户名保留字 — 任何 username 不得等于角色字面量，
# 避免在路径段（projects/<owner>__<project>/）和鉴权字段里互相串味。
RESERVED_PRINCIPAL_NAMES: Final[frozenset[str]] = frozenset({ROLE_ADMIN, ROLE_USER})

_PRINCIPAL_NAME_RE: Final = re.compile(r"^[a-z0-9][a-z0-9_-]{2,31}$")


# ---------------------------------------------------------------------------
# Role helpers
# ---------------------------------------------------------------------------


def is_admin(role: str | None) -> bool:
    """是否具有管理员（超管）权限。"""
    return role == ROLE_ADMIN


# ---------------------------------------------------------------------------
# Project name <-> owner mapping
# ---------------------------------------------------------------------------

# 项目目录扁平布局：``projects/<owner>__<project>/``。``__`` 是 owner 段与
# project 段的唯一切分符，username 校验里已禁用 ``__`` 出现。
PROJECT_NAME_SEPARATOR: Final = "__"


def parse_project_name(name: str) -> tuple[str | None, str]:
    """拆解项目目录名 ``<owner>__<project>`` 为 ``(owner, project)``。

    - 标准格式 ``alice__demo`` → ``("alice", "demo")``
    - 历史遗留 / 无 owner 前缀 ``demo`` → ``(None, "demo")``
    - 多个 ``__`` 出现时，按首个分隔符切分（owner 段不允许包含 ``__``，
      因此剩余部分整体视为 project 段）

    Returns:
        ``(owner, project)``。owner 为 None 表示项目无 owner 前缀，
        access 判断中视为仅 admin 可见。
    """
    if not isinstance(name, str) or not name:
        return None, name or ""
    sep_idx = name.find(PROJECT_NAME_SEPARATOR)
    if sep_idx <= 0:
        return None, name
    owner = name[:sep_idx]
    project = name[sep_idx + len(PROJECT_NAME_SEPARATOR) :]
    if not project:
        return None, name
    return owner, project


def make_project_name(owner: str, project: str) -> str:
    """拼接 ``<owner>__<project>`` 项目目录名。"""
    return f"{owner}{PROJECT_NAME_SEPARATOR}{project}"


def can_access_project(role: str | None, sub: str | None, project_name: str) -> bool:
    """当前用户能否访问该项目。

    - admin：全部可见
    - user：仅访问 ``<self>__*``；无 owner 前缀的历史项目对 user 不可见
    """
    if is_admin(role):
        return True
    if not sub:
        return False
    owner, _ = parse_project_name(project_name)
    return owner is not None and owner == sub


# ---------------------------------------------------------------------------
# Principal name validation
# ---------------------------------------------------------------------------


class PrincipalNameError(ValueError):
    """主体名（username / tenant_name）不合法。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_principal_name(name: str, kind: str = "username") -> str:
    """校验用户名 / 租户名字面量。

    - 字符集：``[a-z0-9][a-z0-9_-]{2,31}``（小写字母数字开头，下划线/短横，3-32 长度）
    - 不允许包含 ``__``（与项目目录路径切分符冲突）
    - 不允许等于角色保留字（避免 username == role 字面量带来的歧义）

    Args:
        name: 待校验的字面量
        kind: 用途标签（仅用于错误消息），如 "username" / "tenant_name"

    Returns:
        合法的 name（与输入相同，未做归一化）

    Raises:
        PrincipalNameError: 校验失败，``.code`` 给出 i18n 友好的错误码
    """
    if not isinstance(name, str):
        raise PrincipalNameError("principal_name_not_string", f"{kind} 必须是字符串")
    if not _PRINCIPAL_NAME_RE.match(name):
        raise PrincipalNameError(
            "principal_name_invalid",
            f"{kind} 必须为 3-32 位小写字母/数字/下划线/短横，且以字母或数字开头",
        )
    if "__" in name:
        raise PrincipalNameError(
            "principal_name_double_underscore",
            f"{kind} 不能包含连续下划线（与项目路径切分符冲突）",
        )
    if name in RESERVED_PRINCIPAL_NAMES:
        raise PrincipalNameError(
            "principal_name_reserved",
            f"{kind} 不能使用保留字：{name}",
        )
    return name
