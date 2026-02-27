#!/usr/bin/env python3
"""
generate_script.py - 使用 Gemini 生成 JSON 剧本

用法:
    python generate_script.py <project_name> --episode <N>
    python generate_script.py <project_name> --episode <N> --output <path>
    python generate_script.py <project_name> --episode <N> --dry-run

示例:
    python generate_script.py test0205 --episode 1
    python generate_script.py 赡养人类 --episode 1 --output scripts/ep1.json
"""

import argparse
from pathlib import Path

from lib.script_generator import ScriptGenerator


def main():
    parser = argparse.ArgumentParser(
        description="使用 Gemini 生成 JSON 剧本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    %(prog)s test0205 --episode 1
    %(prog)s 赡养人类 --episode 1 --output scripts/ep1.json
    %(prog)s test0205 --episode 1 --dry-run
        """,
    )

    parser.add_argument("project", type=str, help="项目名称（projects/ 下的目录名）")

    parser.add_argument("--episode", "-e", type=int, required=True, help="剧集编号")

    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="输出文件路径（默认: scripts/episode_N.json）",
    )

    parser.add_argument(
        "--dry-run", action="store_true", help="仅显示 Prompt，不实际调用 API"
    )

    args = parser.parse_args()

    # 构建项目路径
    project_path = PROJECT_ROOT / "projects" / args.project

    if not project_path.exists():
        print(f"❌ 项目不存在: {project_path}")
        sys.exit(1)

    # 检查中间文件是否存在
    drafts_path = project_path / "drafts" / f"episode_{args.episode}"
    step1_path = drafts_path / "step1_segments.md"

    if not step1_path.exists():
        print(f"❌ 未找到 Step 1 文件: {step1_path}")
        print("   请先完成片段拆分（Step 1）")
        sys.exit(1)

    try:
        generator = ScriptGenerator(project_path)

        if args.dry_run:
            # 仅显示 Prompt
            print("=" * 60)
            print("DRY RUN - 以下是将发送给 Gemini 的 Prompt:")
            print("=" * 60)
            prompt = generator.build_prompt(args.episode)
            print(prompt)
            print("=" * 60)
            return

        # 实际生成
        output_path = Path(args.output) if args.output else None
        result_path = generator.generate(
            episode=args.episode,
            output_path=output_path,
        )

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
