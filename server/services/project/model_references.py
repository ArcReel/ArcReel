"""Model references in project ``project.json``: the ``provider/model`` referenced by each model-setting field."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from lib.config import resolver
from lib.config.model_setting_keys import PROJECT_MODEL_SETTING_KEYS


@dataclass(frozen=True)
class ProjectModelReferences:
    name: str
    title: str | None
    #: Model-setting field to referenced ``provider/model``; nonempty strings only.
    references: Mapping[str, str]


def project_model_references() -> list[ProjectModelReferences]:
    """Read each project's ``project.json`` for model-setting references; skip unreadable projects.

    Read JSON only, without locks or migration (``ProjectManager.load_project``); time scales with project count. Synchronous file I/O: async callers offload to threads.
    """
    # Read through module attributes: tests replace project managers through lib.config.resolver
    pm = resolver.get_project_manager()
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
