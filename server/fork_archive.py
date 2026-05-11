"""项目归档服务 — fork 私有归属重写

子类化 :class:`ProjectArchiveService`，在导入 ZIP 时按当前登录用户重写目标
项目名的 owner 段，避免：

1. 用户 A 导出的 ZIP 被用户 B 导入后归属仍是 A（B 在列表里看不到、强访问
   会被 :func:`can_access_project` 拒）；
2. 历史 ZIP（无 owner 前缀）导入后落到无 owner 目录，非 admin 不可见；
3. 用 ``conflict_policy=overwrite`` 时跨账户覆盖他人项目。

admin 角色导入保留原 owner（迁移 / 接管场景）。
"""

from __future__ import annotations

from typing import Any

from lib.fork_permissions import is_admin, make_project_name, parse_project_name
from server.auth import CurrentUserInfo
from server.services.project_archive import ProjectArchiveService


class ForkProjectArchiveService(ProjectArchiveService):
    """覆盖 ``_resolve_target_project_name``，强制非 admin 导入归当前用户名下。"""

    def __init__(self, project_manager: Any, *, current_user: CurrentUserInfo) -> None:
        super().__init__(project_manager)
        self._current_user = current_user

    def _resolve_target_project_name(
        self,
        project: dict[str, Any],
        *,
        manifest: dict[str, Any] | None,
        root_parts: tuple[str, ...],
        uploaded_filename: str | None,
    ) -> str:
        original = super()._resolve_target_project_name(
            project,
            manifest=manifest,
            root_parts=root_parts,
            uploaded_filename=uploaded_filename,
        )
        if is_admin(self._current_user.role):
            return original
        _, project_part = parse_project_name(original)
        # parse_project_name 保证 project_part 非空（无前缀时返回整个 name）
        return make_project_name(self._current_user.sub, project_part or original)


def get_user_archive_service(
    project_manager: Any,
    current_user: CurrentUserInfo,
) -> ForkProjectArchiveService:
    """工厂：构造按当前用户重写归属的 archive service 实例。"""
    return ForkProjectArchiveService(project_manager, current_user=current_user)
