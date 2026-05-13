"""资产库 owner 可见性（fork 私有）。

不动 `Asset` schema 的前提下，通过 `Asset.source_project` 字段反推 owner：

- 直接上传到库的资产：`source_project` 写成 ``<owner>__@library`` 哨兵，
  API 返回时由 :func:`display_source_project` 把哨兵后缀隐藏成 ``None``。
- 从项目导入的资产：``source_project`` 本身就是项目目录名 ``<owner>__<proj>``。
- 历史遗留资产（``source_project`` 为 None 或不含 ``__``）owner 不可知，
  按 admin-only 处理（fail-safe）。

所有逻辑集中在本文件，[server/routers/assets.py](server/routers/assets.py)
只需 import + 各端点 1 行调用。
"""

from __future__ import annotations

from fastapi import HTTPException

from lib.fork_permissions import (
    can_access_project,
    is_admin,
    make_project_name,
    parse_project_name,
)
from lib.i18n import Translator
from server.auth import CurrentUserInfo

LIBRARY_SENTINEL_PROJECT = "@library"


def direct_upload_source_project(user: CurrentUserInfo) -> str:
    """直接上传到资产库时写入 ``Asset.source_project`` 的值。"""
    return make_project_name(user.sub, LIBRARY_SENTINEL_PROJECT)


def asset_owner_of(asset) -> str | None:
    owner, _ = parse_project_name(asset.source_project or "")
    return owner


def can_see_asset(user: CurrentUserInfo, asset) -> bool:
    """当前用户是否可见此资产。admin 见全部；普通用户仅见 owner == self。"""
    if is_admin(user.role):
        return True
    return asset_owner_of(asset) == user.sub


def display_source_project(asset) -> str | None:
    """对外输出 ``source_project``：库哨兵不暴露给前端。"""
    sp = asset.source_project
    if not sp:
        return None
    _owner, proj = parse_project_name(sp)
    if proj == LIBRARY_SENTINEL_PROJECT:
        return None
    return sp


def require_project_access(project_name: str, user: CurrentUserInfo, _t: Translator) -> None:
    """从资产库读 / 写其他用户项目时的入口拦截。"""
    if can_access_project(user.role, user.sub, project_name):
        return
    raise HTTPException(status_code=403, detail=_t("forbidden_access"))
