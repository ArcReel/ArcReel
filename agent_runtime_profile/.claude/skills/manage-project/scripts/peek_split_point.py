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


def main():
    parser = argparse.ArgumentParser(description="探测切分点附近上下文")
    parser.add_argument("--source", required=True, help="源文件路径")
    parser.add_argument("--target", required=True, type=int, help="目标字数（有效字数）")
    parser.add_argument("--context", default=200, type=int, help="上下文字数（默认 200）")
    args = parser.parse_args()

    source_path = Path(args.source).resolve()
    if not source_path.is_relative_to(Path.cwd().resolve()):
        print(f"错误：源文件路径超出当前项目目录: {source_path}", file=sys.stderr)
        sys.exit(1)
    if not source_path.exists():
        print(f"错误：源文件不存在: {source_path}", file=sys.stderr)
        sys.exit(1)

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
