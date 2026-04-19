#!/usr/bin/env python3
"""
generate_script.py - 使用 Gemini 生成 JSON 剧本

用法:
    python generate_script.py --episode <N>
    python generate_script.py --episode <N> --output <path>
    python generate_script.py --episode <N> --dry-run

示例:
    python generate_script.py --episode 1
    python generate_script.py --episode 1 --output scripts/ep1.json
"""

import argparse
import sys
from pathlib import Path

# 允许从仓库任意工作目录直接运行该脚本
PROJECT_ROOT = Path(__file__).resolve().parents[4]  # .claude/skills/generate-script/scripts -> repo root
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from lib.project_manager import ProjectManager
from lib.script_generator import ScriptGenerator


def main():
    parser = argparse.ArgumentParser(
        description="使用 Gemini 生成 JSON 剧本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    %(prog)s --episode 1
    %(prog)s --episode 1 --output scripts/ep1.json
    %(prog)s --episode 1 --dry-run
        """,
    )

    parser.add_argument("--episode", "-e", type=int, required=True, help="剧集编号")

    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="输出文件路径（默认: scripts/episode_N.json）",
    )

    parser.add_argument("--dry-run", action="store_true", help="仅显示 Prompt，不实际调用 API")

    args = parser.parse_args()

    # 构建项目路径
    pm, project_name = ProjectManager.from_cwd()
    project_path = pm.get_project_path(project_name)

    # 识别 generation_mode（项目 + 集级），由 lib.project_manager.effective_mode 统一解析
    import json as _json

    from lib.project_manager import effective_mode

    project_data: dict = {}
    try:
        project_data = _json.loads((project_path / "project.json").read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        pass  # project.json 缺失或损坏时降级；ScriptGenerator 会再次尝试并报出精确错误
    content_mode = project_data.get("content_mode", "narration")
    episode_dict = next(
        (ep for ep in (project_data.get("episodes") or []) if ep.get("episode") == args.episode),
        {},
    )
    generation_mode = effective_mode(project=project_data, episode=episode_dict)

    drafts_path = project_path / "drafts" / f"episode_{args.episode}"
    if generation_mode == "reference_video":
        step1_path = drafts_path / "step1_reference_units.md"
        step1_hint = "split-reference-video-units subagent（Step 1）"
    elif content_mode == "drama":
        step1_path = drafts_path / "step1_normalized_script.md"
        step1_hint = "normalize_drama_script.py"
    else:
        step1_path = drafts_path / "step1_segments.md"
        step1_hint = "片段拆分（Step 1）"

    if not step1_path.exists():
        print(f"❌ 未找到 Step 1 文件: {step1_path}")
        print(f"   请先完成 {step1_hint}")
        sys.exit(1)

    try:
        if args.dry_run:
            # dry-run 不需要 client
            generator = ScriptGenerator(project_path)
            print("=" * 60)
            print("DRY RUN - 以下是将发送给 Gemini 的 Prompt:")
            print("=" * 60)
            prompt = generator.build_prompt(args.episode)
            print(prompt)
            print("=" * 60)
            return

        # 实际生成（异步）
        import asyncio

        async def _run():
            generator = await ScriptGenerator.create(project_path)
            output_path = Path(args.output) if args.output else None
            return await generator.generate(
                episode=args.episode,
                output_path=output_path,
            )

        result_path = asyncio.run(_run())

        print(f"\n✅ 剧本生成完成: {result_path}")

    except FileNotFoundError as e:
        print(f"❌ 文件错误: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 生成失败: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
