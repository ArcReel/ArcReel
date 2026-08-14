"""项目数据版本闸门：未完成数据升级的项目不进入创作流程。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lib.api_errors import ConflictError
from lib.project_migrations.runner import CURRENT_SCHEMA_VERSION


def require_current_schema(project: Mapping[str, Any], *, name: str) -> None:
    """项目不是当前 schema 版本就抛 409。

    只放行确认为当前版本的项目：bool 与非整数值（``"7"``、``null``）会被迁移 runner 当作不可解析
    而跳过，放行等于按新契约读未迁移字段。按契约取字段的端点必须先过这道闸——迁移失败留下的项目
    仍是旧字段形态，缺 ``creation_type`` 时按兜底值路由，narration / ad 会被当成 drama 落到错误
    的草稿文件上，而这类静默降级正是本闸门要挡的。
    """
    schema_version = project.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != CURRENT_SCHEMA_VERSION
    ):
        raise ConflictError(
            "project_schema_incompatible",
            name=name,
            schema_version=schema_version,
            expected=CURRENT_SCHEMA_VERSION,
        )
