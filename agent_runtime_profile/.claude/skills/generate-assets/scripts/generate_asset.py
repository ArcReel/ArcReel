#!/usr/bin/env python3
"""
Asset Generator - 使用 Gemini API 生成角色 / 场景 / 道具设计图

Usage:
    python generate_asset.py --all                                   # 生成所有类型的待处理资产
    python generate_asset.py --type character --all                  # 指定类型批量
    python generate_asset.py --type scene --name "村口老槐树"          # 指定类型单个
    python generate_asset.py --type prop --names "玉佩" "密信"        # 指定类型多个
    python generate_asset.py --list                                  # 列出所有类型 pending
    python generate_asset.py --type character --list                 # 列出指定类型 pending
"""

import argparse
import sys
from pathlib import Path

from lib.generation_queue_client import (
    BatchTaskResult,
    BatchTaskSpec,
    batch_enqueue_and_wait_sync,
)
from lib.generation_queue_client import (
    enqueue_and_wait_sync as enqueue_and_wait,
)
from lib.project_manager import ProjectManager

# 每种资产类型的字段映射与展示用常量
TYPE_CONFIG: dict[str, dict] = {
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
    """调用对应类型的 pending 方法。"""
    method = getattr(pm, TYPE_CONFIG[asset_type]["pending_method"])
    return method(project_name)


def _get_asset_description(project: dict, asset_type: str, name: str) -> str | None:
    """从 project.json 取资产 description。找不到返回 None。"""
    assets = project.get(TYPE_CONFIG[asset_type]["project_key"], {})
    if name not in assets:
        return None
    return assets[name].get("description") or None


def generate_single(asset_type: str, name: str) -> Path:
    """生成单个资产设计图。

    description 直接作为 prompt 文本下发；最终 prompt（含布局 / 防崩短语 / 反向提示词）
    由 server 的 lib.prompt_builders 在执行任务时统一拼接，确保 WebUI 与 Skill 入口一致。
    """
    cfg = TYPE_CONFIG[asset_type]
    pm, project_name = ProjectManager.from_cwd()
    project_dir = pm.get_project_path(project_name)
    project = pm.load_project(project_name)

    description = _get_asset_description(project, asset_type, name)
    if not description:
        raise ValueError(f"{cfg['label']} '{name}' 的描述为空或不存在于 project.json，请先添加描述")

    print(f"🎨 正在生成{cfg['label']}设计图: {name}")
    print(f"   描述: {description[:50]}..." if len(description) > 50 else f"   描述: {description}")

    queued = enqueue_and_wait(
        project_name=project_name,
        task_type=cfg["task_type"],
        media_type="image",
        resource_id=name,
        payload={"prompt": description},
        source="skill",
    )
    result = queued.get("result") or {}
    relative_path = result.get("file_path") or f"{cfg['default_dir']}/{name}.png"
    output_path = project_dir / relative_path
    version = result.get("version")
    version_text = f" (版本 v{version})" if version is not None else ""
    print(f"✅ {cfg['label']}设计图已保存: {output_path}{version_text}")
    return output_path


def _list_pending_for_type(pm: ProjectManager, project_name: str, asset_type: str) -> int:
    """打印指定类型的 pending 列表，返回 pending 数量。"""
    cfg = TYPE_CONFIG[asset_type]
    pending = _get_pending(pm, project_name, asset_type)

    if not pending:
        print(f"✅ 项目 '{project_name}' 中所有{cfg['label']}都已有设计图")
        return 0

    print(f"\n📋 待生成的{cfg['label']} ({len(pending)} 个):\n")
    for item in pending:
        print(f"  {cfg['emoji']} {item['name']}")
        desc = item.get("description", "")
        print(f"     描述: {desc[:60]}..." if len(desc) > 60 else f"     描述: {desc}")
        print()
    return len(pending)


def list_pending(asset_type: str | None = None) -> None:
    """列出指定类型（或所有类型）的 pending 资产。"""
    pm, project_name = ProjectManager.from_cwd()
    types = (asset_type,) if asset_type else ALL_TYPES
    total = 0
    for t in types:
        total += _list_pending_for_type(pm, project_name, t)
    if not total and not asset_type:
        print(f"\n✅ 项目 '{project_name}' 所有资产均已有设计图")


def _build_specs(
    pm: ProjectManager,
    project_name: str,
    asset_type: str,
    names: list[str] | None,
) -> list[BatchTaskSpec]:
    """为指定类型构造 BatchTaskSpec 列表（names=None 表示全部 pending）。"""
    cfg = TYPE_CONFIG[asset_type]
    project = pm.load_project(project_name)
    assets_dict = project.get(cfg["project_key"], {})

    if names:
        resolved: list[str] = []
        for name in names:
            if name not in assets_dict:
                print(f"⚠️  {cfg['label']} '{name}' 不存在于 project.json 中，跳过")
                continue
            if not assets_dict[name].get("description"):
                print(f"⚠️  {cfg['label']} '{name}' 缺少描述，跳过")
                continue
            resolved.append(name)
    else:
        pending = _get_pending(pm, project_name, asset_type)
        resolved = []
        for item in pending:
            name = item["name"]
            if not assets_dict.get(name, {}).get("description"):
                print(f"⚠️  {cfg['label']} '{name}' 缺少描述，跳过")
                continue
            resolved.append(name)

    specs: list[BatchTaskSpec] = []
    for name in resolved:
        # description 直接作为 prompt 提交；server 端 build_*_prompt 负责拼接布局 / 防崩 / 反向。
        specs.append(
            BatchTaskSpec(
                task_type=cfg["task_type"],
                media_type="image",
                resource_id=name,
                payload={"prompt": assets_dict[name]["description"]},
            )
        )
    return specs


def _run_batch_for_type(
    pm: ProjectManager,
    project_name: str,
    asset_type: str,
    names: list[str] | None,
) -> tuple[int, int]:
    """为单一 type 入队 + 等待，返回 (成功数, 失败数)。"""
    cfg = TYPE_CONFIG[asset_type]
    specs = _build_specs(pm, project_name, asset_type, names)
    if not specs:
        return (0, 0)

    print(f"\n🚀 批量提交 {len(specs)} 个{cfg['label']}设计图到生成队列...\n")

    def on_success(br: BatchTaskResult) -> None:
        version = (br.result or {}).get("version")
        version_text = f" (版本 v{version})" if version is not None else ""
        print(f"✅ {cfg['label']}设计图: {br.resource_id} 完成{version_text}")

    def on_failure(br: BatchTaskResult) -> None:
        print(f"❌ {cfg['label']}设计图: {br.resource_id} 失败 - {br.error}")

    successes, failures = batch_enqueue_and_wait_sync(
        project_name=project_name,
        specs=specs,
        on_success=on_success,
        on_failure=on_failure,
    )
    return (len(successes), len(failures))


def generate_batch(
    asset_type: str | None = None,
    names: list[str] | None = None,
) -> tuple[int, int]:
    """
    批量生成资产设计图。

    - asset_type=None 且 names=None → 按 character / scene / prop 顺序，每类独立 batch
      （避免同名资产跨 type 在单批 task_ids 映射中互相覆盖）
    - asset_type=<t> 且 names=None → 扫描该类型的所有 pending
    - asset_type=<t> 且 names=[...] → 生成指定资产
    - asset_type=None 且 names=[...] → 不合法（必须提供 --type 才能指定名字）
    """
    pm, project_name = ProjectManager.from_cwd()

    if asset_type is None and names:
        raise ValueError("--name/--names 必须配合 --type 使用")

    types = (asset_type,) if asset_type else ALL_TYPES
    total_success = 0
    total_failure = 0
    for t in types:
        s, f = _run_batch_for_type(pm, project_name, t, names)
        total_success += s
        total_failure += f

    if total_success == 0 and total_failure == 0:
        print("✅ 没有需要生成的资产")
        return (0, 0)

    print(f"\n{'=' * 40}")
    print("生成完成!")
    print(f"   ✅ 成功: {total_success}")
    print(f"   ❌ 失败: {total_failure}")
    print(f"{'=' * 40}")

    return (total_success, total_failure)


def main():
    parser = argparse.ArgumentParser(
        description="生成资产设计图（角色 / 场景 / 道具统一入口）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--type",
        choices=list(ALL_TYPES),
        help="资产类型：character | scene | prop。不指定则对所有类型生效。",
    )
    parser.add_argument("--all", action="store_true", help="生成所有待处理的资产")
    parser.add_argument("--name", help="指定单个资产名称（需配合 --type）")
    parser.add_argument("--names", nargs="+", help="指定多个资产名称（需配合 --type）")
    parser.add_argument("--list", action="store_true", help="列出待生成的资产")

    args = parser.parse_args()

    try:
        if args.list:
            list_pending(args.type)
        elif args.all:
            _, fail = generate_batch(asset_type=args.type)
            sys.exit(0 if fail == 0 else 1)
        elif args.names:
            if not args.type:
                parser.error("--names 必须配合 --type 使用")
            _, fail = generate_batch(asset_type=args.type, names=args.names)
            sys.exit(0 if fail == 0 else 1)
        elif args.name:
            if not args.type:
                parser.error("--name 必须配合 --type 使用")
            output_path = generate_single(args.type, args.name)
            print(f"\n🖼️  请查看生成的图片: {output_path}")
        else:
            parser.print_help()
            print("\n❌ 请指定 --all、--names、--name 或 --list")
            sys.exit(1)

    except Exception as e:
        print(f"❌ 错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
