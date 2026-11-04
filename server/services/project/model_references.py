"""项目 ``project.json`` 里的模型引用：每个项目的模型设置字段各引用了哪个 ``provider/model``。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from lib.config import resolver
from lib.config.model_setting_keys import PROJECT_MODEL_SETTING_KEYS


@dataclass(frozen=True)
class ProjectModelReferences:
    name: str
    title: str | None
    #: 模型设置字段 → 所引的 ``provider/model``；只收非空字符串值。
    references: Mapping[str, str]


def project_model_references() -> list[ProjectModelReferences]:
    """逐个读项目的 ``project.json``，取出模型设置字段的引用；读不了的项目跳过。

    只读 JSON，不取锁、不迁移（``ProjectManager.load_project``），耗时与项目数成正比。同步文件 I/O，异步调用方放到线程里。
    """
    pm = resolver.get_project_manager()  # 经模块属性取：用例按 lib.config.resolver 替换项目管理器
    found: list[ProjectModelReferences] = []
    for name in pm.list_projects():
        try:
            project = pm.load_project(name)
        except (OSError, ValueError):
            continue
        title = project.get("title")
        found.append(
            ProjectModelReferences(
                name=name,
                title=title if isinstance(title, str) and title else None,
                references={
                    key: value
                    for key in PROJECT_MODEL_SETTING_KEYS
                    if isinstance(value := project.get(key), str) and value
                },
            )
        )
    return found
