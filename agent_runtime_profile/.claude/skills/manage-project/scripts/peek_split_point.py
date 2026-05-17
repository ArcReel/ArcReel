#!/usr/bin/env python3
"""
peek_split_point.py - 切分点探测脚本

展示目标字数附近的上下文，帮助 agent 和用户决定自然断点。

用法:
    python peek_split_point.py --source source/novel.txt --target 1000
    python peek_split_point.py --source source/novel.txt --target 1000 --context 300
"""

import argparse
import json
import sys
from pathlib import Path

# 导入共享工具
sys.path.insert(0, str(Path(__file__).parent))
from _text_utils import count_chars, find_char_offset, find_natural_breakpoints


def _resolve_source_in_project(arg_source: str) -> Path:
    """强约束：cwd 必须含 project.json，source 必须位于 cwd/source/ 之内。

    peek 是只读探测，不写出文件，但仍按相同围栏校验输入，与 split_episode 一致。
    防御点同 split_episode：cwd/source 不能是符号链接，否则 resolve 后会双双
    落到项目外目录、绕过 is_relative_to，把"探测项目内文件"变成"探测项目外"。
    """
    cwd = Path.cwd().resolve()
    if not (cwd / "project.json").is_file():
        print(f"❌ 必须在项目目录内运行（当前 cwd={cwd} 不含 project.json）", file=sys.stderr)
        sys.exit(1)
    source_dir_unresolved = cwd / "source"
    if source_dir_unresolved.is_symlink():
        print(
            f"❌ source/ 不能是符号链接（避免探测项目外文件）: {source_dir_unresolved}",
            file=sys.stderr,
        )
        sys.exit(1)
    source_dir = source_dir_unresolved.resolve()
    if not source_dir.is_dir():
        print(f"❌ 项目缺 source/ 目录: {source_dir}", file=sys.stderr)
        sys.exit(1)
    source_path = (cwd / arg_source).resolve() if not Path(arg_source).is_absolute() else Path(arg_source).resolve()
    if not source_path.is_relative_to(source_dir):
        print(f"❌ 源文件必须位于 {source_dir} 内，收到: {source_path}", file=sys.stderr)
        sys.exit(1)
    if not source_path.is_file():
        print(f"❌ 源文件不存在或不是普通文件: {source_path}", file=sys.stderr)
        sys.exit(1)
    return source_path


def main():
    parser = argparse.ArgumentParser(description="探测切分点附近上下文")
    parser.add_argument("--source", required=True, help="源文件路径")
    parser.add_argument("--target", required=True, type=int, help="目标字数（有效字数）")
    parser.add_argument("--context", default=200, type=int, help="上下文字数（默认 200）")
    args = parser.parse_args()

    source_path = _resolve_source_in_project(args.source)

    text = source_path.read_text(encoding="utf-8")
    total_chars = count_chars(text)

    if args.target >= total_chars:
        print(f"错误：目标字数 ({args.target}) 超过或等于总有效字数 ({total_chars})", file=sys.stderr)
        sys.exit(1)

    # 定位目标字数对应的原文偏移
    target_offset = find_char_offset(text, args.target)

    # 查找附近的自然断点
    breakpoints = find_natural_breakpoints(text, target_offset, window=args.context)

    # 提取上下文
    ctx_start = max(0, target_offset - args.context)
    ctx_end = min(len(text), target_offset + args.context)
    before_context = text[ctx_start:target_offset]
    after_context = text[target_offset:ctx_end]

    # 输出结果
    result = {
        "source": str(source_path),
        "total_chars": total_chars,
        "target_chars": args.target,
        "target_offset": target_offset,
        "context_before": before_context,
        "context_after": after_context,
        "nearby_breakpoints": breakpoints[:10],  # 只取最近的 10 个
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
