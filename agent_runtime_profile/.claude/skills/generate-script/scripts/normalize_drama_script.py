#!/usr/bin/env python3
"""
normalize_drama_script.py - 使用 Gemini Pro 生成规范化剧本

将 source/ 小说原文转化为 Markdown 格式的规范化剧本（step1_normalized_script.md），
供 generate_script.py 消费。

用法:
    python normalize_drama_script.py --episode <N>
    python normalize_drama_script.py --episode <N> --source <file>
    python normalize_drama_script.py --episode <N> --dry-run
"""

import argparse
import sys
from pathlib import Path

# 允许从仓库任意工作目录直接运行该脚本
PROJECT_ROOT = (
    Path(__file__).resolve().parents[4]
)  # .claude/skills/generate-script/scripts -> repo root
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import asyncio

from lib.text_backends.factory import create_text_backend_for_task
from lib.text_backends.base import TextGenerationRequest, TextTaskType
from lib.project_manager import ProjectManager


def build_normalize_prompt(
    novel_text: str,
    project_overview: dict,
    style: str,
    characters: dict,
    clues: dict,
) -> str:
    """构建规范化剧本的 Prompt"""

    char_list = "\n".join(f"- {name}" for name in characters.keys()) or "（暂无）"
    clue_list = "\n".join(f"- {name}" for name in clues.keys()) or "（暂无）"

    return f"""你的任务是将小说原文改编为结构化的分镜场景表（Markdown 格式），用于后续 AI 视频生成。

## 项目信息

<overview>
{project_overview.get("synopsis", "")}

题材类型：{project_overview.get("genre", "")}
核心主题：{project_overview.get("theme", "")}
世界观设定：{project_overview.get("world_setting", "")}
</overview>

<style>
{style}
</style>

<characters>
{char_list}
</characters>

<clues>
{clue_list}
</clues>

## 小说原文

<novel>
{novel_text}
</novel>

## 输出要求

将小说改编为场景列表，使用 Markdown 表格格式：

| 场景 ID | 场景描述 | 时长 | 场景类型 | segment_break |
|---------|---------|------|---------|---------------|
| E{{N}}S01 | 详细的场景描述... | 8 | 剧情 | 是 |
| E{{N}}S02 | 详细的场景描述... | 8 | 对话 | 否 |

规则：
- 场景 ID 格式：E{{集数}}S{{两位序号}}（如 E1S01, E1S02）
- 场景描述：改编后的剧本化描述，包含角色动作、对话、环境，适合视觉化呈现
- 时长：4、6 或 8 秒（默认 8 秒，简单画面可用 4 或 6 秒）
- 场景类型：剧情、动作、对话、过渡、空镜
- segment_break：场景切换点标记"是"，同一连续场景标"否"
- 每个场景应为一个独立的视觉画面，可以在指定时长内完成
- 避免一个场景包含多个不同的动作或画面切换

仅输出 Markdown 表格，不要包含其他解释文字。
"""


def main():
    parser = argparse.ArgumentParser(
        description="使用 Gemini Pro 生成规范化剧本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    %(prog)s --episode 1
    %(prog)s --episode 1 --source source/chapter1.txt
    %(prog)s --episode 1 --dry-run
        """,
    )

    parser.add_argument("--episode", "-e", type=int, required=True, help="剧集编号")
    parser.add_argument(
        "--source",
        "-s",
        type=str,
        default=None,
        help="指定小说源文件路径（默认读取 source/ 目录下所有文件）",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="仅显示 Prompt，不实际调用 API"
    )

    args = parser.parse_args()

    # 构建项目路径
    pm, project_name = ProjectManager.from_cwd()
    project_path = pm.get_project_path(project_name)
    project = pm.load_project(project_name)

    # 读取小说原文
    if args.source:
        source_path = (project_path / args.source).resolve()
        if not source_path.is_relative_to(project_path.resolve()):
            print(f"❌ 路径超出项目目录: {source_path}")
            sys.exit(1)
        if not source_path.exists():
            print(f"❌ 未找到源文件: {source_path}")
            sys.exit(1)
        novel_text = source_path.read_text(encoding="utf-8")
    else:
        source_dir = project_path / "source"
        if not source_dir.exists() or not any(source_dir.iterdir()):
            print(f"❌ source/ 目录为空或不存在: {source_dir}")
            sys.exit(1)
        # 按文件名排序读取所有文本文件
        texts = []
        for f in sorted(source_dir.iterdir()):
            if f.suffix in (".txt", ".md", ".text"):
                texts.append(f.read_text(encoding="utf-8"))
        novel_text = "\n\n".join(texts)

    if not novel_text.strip():
        print("❌ 小说原文为空")
        sys.exit(1)

    # 构建 Prompt
    prompt = build_normalize_prompt(
        novel_text=novel_text,
        project_overview=project.get("overview", {}),
        style=project.get("style", ""),
        characters=project.get("characters", {}),
        clues=project.get("clues", {}),
    )

    if args.dry_run:
        print("=" * 60)
        print("DRY RUN - 以下是将发送给 Gemini 的 Prompt:")
        print("=" * 60)
        print(prompt)
        print("=" * 60)
        print(f"\nPrompt 长度: {len(prompt)} 字符")
        return

    # 调用 TextBackend
    backend = asyncio.run(create_text_backend_for_task(TextTaskType.SCRIPT))
    print(f"正在使用 {backend.model} 生成规范化剧本...")
    result = asyncio.run(backend.generate(TextGenerationRequest(prompt=prompt)))
    response = result.text

    # 保存文件
    drafts_dir = project_path / "drafts" / f"episode_{args.episode}"
    drafts_dir.mkdir(parents=True, exist_ok=True)

    step1_path = drafts_dir / "step1_normalized_script.md"
    step1_path.write_text(response.strip(), encoding="utf-8")
    print(f"✅ 规范化剧本已保存: {step1_path}")

    # 简要统计
    lines = [l for l in response.split("\n") if l.strip().startswith("|") and "场景 ID" not in l and "---" not in l]
    scene_count = len(lines)
    print(f"\n📊 生成统计: {scene_count} 个场景")


if __name__ == "__main__":
    main()
