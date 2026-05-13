"""SDK MCP tools for asset image generation (character / scene / prop)."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from lib.generation_queue_client import (
    BatchTaskSpec,
    batch_enqueue_and_wait,
)
from lib.project_manager import ProjectManager
from server.agent_runtime.sdk_tools._context import ToolContext

TYPE_CONFIG: dict[str, dict[str, Any]] = {
    "character": {
        "project_key": "characters",
        "pending_method": "get_pending_characters",
        "task_type": "character",
        "label": "角色",
        "emoji": "🧑",
        "default_dir": "characters",
    },
    "scene": {
        "project_key": "scenes",
        "pending_method": "get_pending_project_scenes",
        "task_type": "scene",
        "label": "场景",
        "emoji": "🏠",
        "default_dir": "scenes",
    },
    "prop": {
        "project_key": "props",
        "pending_method": "get_pending_project_props",
        "task_type": "prop",
        "label": "道具",
        "emoji": "📦",
        "default_dir": "props",
    },
}

ALL_TYPES: tuple[str, ...] = ("character", "scene", "prop")


def _get_pending(pm: ProjectManager, project_name: str, asset_type: str) -> list[dict]:
    method = getattr(pm, TYPE_CONFIG[asset_type]["pending_method"])
    return method(project_name)


def _build_specs(
    pm: ProjectManager,
    project_name: str,
    asset_type: str,
    names: list[str] | None,
    warnings: list[str],
) -> list[BatchTaskSpec]:
    cfg = TYPE_CONFIG[asset_type]
    project = pm.load_project(project_name)
    assets_dict = project.get(cfg["project_key"], {})

    if names:
        resolved: list[str] = []
        for name in names:
            if name not in assets_dict:
                warnings.append(f"⚠️  {cfg['label']} '{name}' 不存在于 project.json 中，跳过")
                continue
            if not assets_dict[name].get("description"):
                warnings.append(f"⚠️  {cfg['label']} '{name}' 缺少描述，跳过")
                continue
            resolved.append(name)
    else:
        pending = _get_pending(pm, project_name, asset_type)
        resolved = []
        for item in pending:
            name = item["name"]
            if not assets_dict.get(name, {}).get("description"):
                warnings.append(f"⚠️  {cfg['label']} '{name}' 缺少描述，跳过")
                continue
            resolved.append(name)

    return [
        BatchTaskSpec(
            task_type=cfg["task_type"],
            media_type="image",
            resource_id=name,
            payload={"prompt": assets_dict[name]["description"]},
        )
        for name in resolved
    ]


def list_pending_assets_tool(ctx: ToolContext):
    @tool(
        "list_pending_assets",
        "列出项目内待生成设计图的角色/场景/道具。type 省略则汇总所有类型。",
        {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["character", "scene", "prop"],
                    "description": "资产类型；不传则列出所有类型的 pending",
                },
            },
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            asset_type = args.get("type")
            types = (asset_type,) if asset_type else ALL_TYPES
            lines: list[str] = []
            total = 0
            for t in types:
                cfg = TYPE_CONFIG[t]
                pending = _get_pending(ctx.pm, ctx.project_name, t)
                if not pending:
                    lines.append(f"✅ 项目 '{ctx.project_name}' 所有{cfg['label']}都已有设计图")
                    continue
                total += len(pending)
                lines.append(f"\n📋 待生成的{cfg['label']} ({len(pending)} 个):")
                for item in pending:
                    desc = item.get("description", "") or ""
                    desc_preview = desc[:60] + "..." if len(desc) > 60 else desc
                    lines.append(f"  {cfg['emoji']} {item['name']} — {desc_preview}")
            if not asset_type and total == 0:
                lines.append(f"\n✅ 项目 '{ctx.project_name}' 所有资产均已有设计图")
            return {"content": [{"type": "text", "text": "\n".join(lines)}]}
        except Exception as exc:  # noqa: BLE001
            return {
                "content": [{"type": "text", "text": f"list_pending_assets 失败: {exc}"}],
                "is_error": True,
            }

    return _handler


def generate_assets_tool(ctx: ToolContext):
    @tool(
        "generate_assets",
        "批量生成角色/场景/道具设计图。"
        "type 省略则按 character→scene→prop 顺序每类独立 batch；"
        "names 指定具体名称（必须同时给 type）；all=true 表示该 type 的全部 pending。",
        {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["character", "scene", "prop"],
                    "description": "资产类型；不传等于全部三类",
                },
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "目标资产名称列表；必须配合 type 使用",
                },
                "all": {
                    "type": "boolean",
                    "description": "是否扫描所有 pending（与 names 互斥；默认 false 但当未提供 names 时等同 true）",
                },
            },
        },
    )
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            asset_type = args.get("type")
            names = args.get("names")
            if names and not asset_type:
                return {
                    "content": [{"type": "text", "text": "names 必须配合 type 使用"}],
                    "is_error": True,
                }

            types = (asset_type,) if asset_type else ALL_TYPES
            warnings: list[str] = []
            total_success = 0
            total_failure = 0
            details: list[str] = []

            for t in types:
                cfg = TYPE_CONFIG[t]
                specs = _build_specs(ctx.pm, ctx.project_name, t, names, warnings)
                if not specs:
                    continue

                successes_acc, failures_acc = await batch_enqueue_and_wait(
                    project_name=ctx.project_name,
                    specs=specs,
                )

                for br in successes_acc:
                    version = (br.result or {}).get("version")
                    version_text = f" (v{version})" if version is not None else ""
                    file_path = (br.result or {}).get("file_path") or f"{cfg['default_dir']}/{br.resource_id}.png"
                    details.append(f"  ✓ {cfg['label']} '{br.resource_id}' → {file_path}{version_text}")
                for br in failures_acc:
                    details.append(f"  ✗ {cfg['label']} '{br.resource_id}': {br.error}")
                total_success += len(successes_acc)
                total_failure += len(failures_acc)

            header = f"generate_assets summary: {total_success} succeeded, {total_failure} failed"
            body_parts = warnings + ([header] if (total_success or total_failure) else [])
            if total_success == 0 and total_failure == 0:
                body_parts.append("✅ 没有需要生成的资产")
            body_parts.extend(details)
            return {
                "content": [{"type": "text", "text": "\n".join(body_parts)}],
                "is_error": total_failure > 0,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "content": [{"type": "text", "text": f"generate_assets 失败: {exc}"}],
                "is_error": True,
            }

    return _handler


__all__ = [
    "TYPE_CONFIG",
    "ALL_TYPES",
    "list_pending_assets_tool",
    "generate_assets_tool",
]
