"""重生一张资产图的连带影响：会有多少张分镜图、多少段视频、多少张衍生资产图转为过期。

资产图重生后字节变了，以它为参考图的现行产物的登记依据随之对不上，由现行转为过期。这里只数
**现在是现行**、且生成输入里带着这张资产图的产物——已经过期的不重复计入：

- 分镜图：分镜的引用字段（``characters_in_*`` / ``scenes`` / ``props`` / ``products_in_shot``）
  写到了这个名字。衍生引用 ``本体/衍生`` 用的是衍生自己的资产图，不算引用本体。
- 视频：只有参考生视频直接以资产图为参考；分镜图生视频的输入是分镜图，资产图重生不会
  直接让它过期。参考生视频单元的引用取正文 ``@[名称]`` 与引用字段。
- 衍生资产图：本体资产图是衍生资产图唯一的输入，本体重生后它的现行衍生图全部过期。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from lib.artifacts.artifact_activation import ArtifactCurrencyResolver, active_artifact_currency_resolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactStatus
from lib.generation.generation_result import observe_artifact_status
from lib.project.asset_derivatives import derivative_artifact_id, derivative_artifact_key, derivative_table
from lib.project.asset_types import ASSET_SPECS, asset_name_comparison_key, resolve_asset_key
from lib.project.project_manager import ProjectManager
from lib.script.script_editor import ScriptEditError, resolve_items
from lib.script.script_references import payload_reference_names

_REFERENCE_LIST_FIELDS: frozenset[str] = frozenset(
    field for spec in ASSET_SPECS.values() for field in spec.reference_list_fields
)


@dataclass(frozen=True, slots=True)
class AssetRegenerationImpact:
    storyboards: int
    videos: int
    derivatives: int


def _visual_reference_names(item: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for field_name in _REFERENCE_LIST_FIELDS:
        value = item.get(field_name)
        if isinstance(value, list):
            names.update(asset_name_comparison_key(name) for name in value if isinstance(name, str))
    return names


def _is_current(resolver: ArtifactCurrencyResolver, key: ArtifactKey, artifact_path: object) -> bool:
    status, _blocker = observe_artifact_status(resolver=resolver, key=key, artifact_path=artifact_path)
    return status is ArtifactStatus.CURRENT


def _episode_scripts(
    projects: ProjectManager, project_name: str, project: Mapping[str, Any]
) -> Iterable[tuple[int, dict[str, Any]]]:
    for entry in project.get("episodes") or []:
        if not isinstance(entry, Mapping):
            continue
        episode = entry.get("episode")
        script_file = entry.get("script_file")
        if type(episode) is not int or not isinstance(script_file, str) or not script_file:
            continue
        try:
            yield episode, projects.load_script_readonly(project_name, script_file)
        except (FileNotFoundError, ValueError):
            continue


def asset_regeneration_impact(
    projects: ProjectManager,
    project_name: str,
    asset_type: str,
    name: str,
    *,
    derivative_name: str | None = None,
) -> AssetRegenerationImpact:
    """重生指定本体或衍生资产图会让多少件现行产物过期；资产不存在抛 ``KeyError``。"""

    spec = ASSET_SPECS[asset_type]
    project = projects.load_project(project_name)
    bucket = project.get(spec.bucket_key)
    key = resolve_asset_key(bucket, name)
    entry = bucket.get(key) if isinstance(bucket, Mapping) and key is not None else None
    if key is None or not isinstance(entry, Mapping):
        raise KeyError(name)
    resolver = active_artifact_currency_resolver(projects.get_project_path(project_name), project)
    target = asset_name_comparison_key(key)
    if derivative_name is not None:
        derivative_key = (
            resolve_asset_key(derivative_table(entry), derivative_name) if spec.supports_derivatives else None
        )
        if derivative_key is None:
            raise KeyError(derivative_name)
        target = derivative_artifact_id(target, asset_name_comparison_key(derivative_key))

    derivatives = 0
    if spec.supports_derivatives and derivative_name is None:
        for derivative_name, derivative in derivative_table(entry).items():
            if isinstance(derivative, Mapping) and _is_current(
                resolver,
                derivative_artifact_key(target, asset_name_comparison_key(derivative_name)),
                derivative.get(spec.sheet_field),
            ):
                derivatives += 1

    storyboards = 0
    videos = 0
    for episode, script in _episode_scripts(projects, project_name, project):
        try:
            items, id_field, kind = resolve_items(script)
        except ScriptEditError:
            continue
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get(id_field), str):
                continue
            resource_id = item[id_field]
            generated = item.get("generated_assets")
            generated = generated if isinstance(generated, Mapping) else {}
            if kind == "video_units":
                if target in payload_reference_names({"video_units": [item]}) and _is_current(
                    resolver, ArtifactKey.episode_video(episode, resource_id), generated.get("video_clip")
                ):
                    videos += 1
                continue
            if target in _visual_reference_names(item) and _is_current(
                resolver, ArtifactKey.episode_storyboard(episode, resource_id), generated.get("storyboard_image")
            ):
                storyboards += 1
    return AssetRegenerationImpact(storyboards=storyboards, videos=videos, derivatives=derivatives)


__all__ = ["AssetRegenerationImpact", "asset_regeneration_impact"]
