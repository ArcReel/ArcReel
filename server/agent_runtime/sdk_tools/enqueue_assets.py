"""SDK MCP tools for asset image generation (character / scene / prop)."""

from __future__ import annotations

from typing import Any

from claude_agent_sdk import tool

from lib.asset_types import ASSET_SPECS, AssetSpec
from lib.generation_queue_client import (
    BatchTaskSpec,
    batch_enqueue_and_wait,
)
from lib.project_manager import ProjectManager
from server.agent_runtime.sdk_tools._context import ToolContext, tool_error

# Asset-type emoji shown in tool output. Other display fields (bucket_key,
# label_zh, subdir) come from lib.asset_types.ASSET_SPECS — the cross-app
# source of truth.
_EMOJI: dict[str, str] = {"character": "🧑", "scene": "🏠", "prop": "📦"}

ALL_TYPES: tuple[str, ...] = tuple(ASSET_SPECS.keys())

_PENDING_DISPATCH = {
    "character": lambda pm, name: pm.get_pending_characters(name),
    "scene": lambda pm, name: pm.get_pending_project_scenes(name),
    "prop": lambda pm, name: pm.get_pending_project_props(name),
}


def _get_pending(pm: ProjectManager, project_name: str, asset_type: str) -> list[dict]:
    return _PENDING_DISPATCH[asset_type](pm, project_name)


def _build_specs(
    pm: ProjectManager,
    project_name: str,
    asset_type: str,
    names: list[str] | None,
    warnings: list[str],
) -> list[BatchTaskSpec]:
    spec: AssetSpec = ASSET_SPECS[asset_type]
    project = pm.load_project(project_name)
    assets_dict = project.get(spec.bucket_key, {})

    if names:
        resolved: list[str] = []
        for name in names:
            if name not in assets_dict:
                warnings.append(f"⚠️  {spec.label_zh} '{name}' 不存在于 project.json 中，跳过")
                continue
            if not assets_dict[name].get("description"):
                warnings.append(f"⚠️  {spec.label_zh} '{name}' 缺少描述，跳过")
                continue
            resolved.append(name)
    else:
        pending = _get_pending(pm, project_name, asset_type)
        resolved = []
        for item in pending:
            name = item["name"]
            if not assets_dict.get(name, {}).get("description"):
                warnings.append(f"⚠️  {spec.label_zh} '{name}' 缺少描述，跳过")
                continue
            resolved.append(name)

    return [
        BatchTaskSpec(
            task_type=spec.asset_type,
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
                spec = ASSET_SPECS[t]
                pending = _get_pending(ctx.pm, ctx.project_name, t)
                if not pending:
                    lines.append(f"✅ 项目 '{ctx.project_name}' 所有{spec.label_zh}都已有设计图")
                    continue
                total += len(pending)
                lines.append(f"\n📋 待生成的{spec.label_zh} ({len(pending)} 个):")
                for item in pending:
                    desc = item.get("description", "") or ""
                    desc_preview = desc[:60] + "..." if len(desc) > 60 else desc
                    lines.append(f"  {_EMOJI[t]} {item['name']} — {desc_preview}")
            if not asset_type and total == 0:
                lines.append(f"\n✅ 项目 '{ctx.project_name}' 所有资产均已有设计图")
            return {"content": [{"type": "text", "text": "\n".join(lines)}]}
        except Exception as exc:  # noqa: BLE001
            return tool_error("list_pending_assets", exc)

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
                spec = ASSET_SPECS[t]
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
                    file_path = (br.result or {}).get("file_path") or f"{spec.subdir}/{br.resource_id}.png"
                    details.append(f"  ✓ {spec.label_zh} '{br.resource_id}' → {file_path}{version_text}")
                for br in failures_acc:
                    details.append(f"  ✗ {spec.label_zh} '{br.resource_id}': {br.error}")
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
            return tool_error("generate_assets", exc)

    return _handler


__all__ = [
    "ALL_TYPES",
    "list_pending_assets_tool",
    "generate_assets_tool",
]
