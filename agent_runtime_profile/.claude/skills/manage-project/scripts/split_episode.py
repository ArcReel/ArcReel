#!/usr/bin/env python3
"""
split_episode.py - 执行分集切分

使用目标字数 + 锚点文本配合定位切分位置，将小说切分为 episode_N.txt 和 _remaining.txt。
目标字数缩小搜索窗口，锚点文本精确定位。

用法:
    # Dry run（仅预览）
    python split_episode.py --source source/novel.txt --episode 1 --target 1000 --anchor "他转身离开了。" --dry-run

    # 实际执行
    python split_episode.py --source source/novel.txt --episode 1 --target 1000 --anchor "他转身离开了。"
"""

import argparse
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _text_utils import find_char_offset


def _resolve_source_in_project(arg_source: str) -> tuple[Path, Path]:
    """强约束：cwd 必须含 project.json，source 必须位于 cwd/source/ 之内。

    返回 (source_path, source_dir)；source_dir 同时是后续 output 的强制根。
    output 写入位置不再跟随 source 的父目录，避免 cwd 漂移 / agent 传错
    路径时把分集文件落到项目目录之外。

    防御 source/ 软链接绕过：若 `cwd/source` 本身是符号链接（指向 /tmp/x），
    resolve 后 source_dir 与 source_path 双双落到 /tmp/x，is_relative_to
    校验会放行——但产物实际写到了项目目录之外。这里在 resolve 前显式拒绝。
    """
    cwd = Path.cwd().resolve()
    if not (cwd / "project.json").is_file():
        print(f"❌ 必须在项目目录内运行（当前 cwd={cwd} 不含 project.json）", file=sys.stderr)
        sys.exit(1)
    source_dir_unresolved = cwd / "source"
    if source_dir_unresolved.is_symlink():
        print(
            f"❌ source/ 不能是符号链接（避免分集产物落到项目外）: {source_dir_unresolved}",
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
    return source_path, source_dir


def find_anchor_near_target(text: str, anchor: str, target_offset: int, window: int = 500) -> list[int]:
    """在目标偏移附近的窗口内查找锚点文本，返回匹配末尾偏移列表（按距离排序）。"""
    search_start = max(0, target_offset - window)
    search_end = min(len(text), target_offset + window)
    search_region = text[search_start:search_end]

    positions = []
    start = 0
    while True:
        idx = search_region.find(anchor, start)
        if idx == -1:
            break
        abs_pos = search_start + idx + len(anchor)  # 锚点末尾的绝对偏移
        positions.append(abs_pos)
        start = idx + 1

    # 按距离 target_offset 排序
    positions.sort(key=lambda p: abs(p - target_offset))
    return positions


def _positive_int(value: str) -> int:
    """argparse type：拒绝负数和 0，避免生成 episode_-1.txt / episode_0.txt。"""
    ivalue = int(value)
    if ivalue <= 0:
        raise argparse.ArgumentTypeError(f"必须是正整数，收到: {value}")
    return ivalue


def main():
    parser = argparse.ArgumentParser(description="执行分集切分")
    parser.add_argument("--source", required=True, help="源文件路径")
    parser.add_argument("--episode", required=True, type=_positive_int, help="集数编号（正整数）")
    parser.add_argument("--target", required=True, type=int, help="目标字数（与 peek 的 --target 一致）")
    parser.add_argument("--anchor", required=True, help="切分点前的文本片段（10-20 字符）")
    parser.add_argument("--context", default=500, type=int, help="搜索窗口大小（默认 500 字符）")
    parser.add_argument("--dry-run", action="store_true", help="仅展示切分预览，不写文件")
    args = parser.parse_args()

    source_path, source_dir = _resolve_source_in_project(args.source)

    # NFC normalize 与 peek_split_point 同坐标系:peek 已对源文件 NFC normalize,
    # 输出的 anchor 字符串在 NFC 空间。这里同样 normalize,确保 anchor 搜索一致。
    # 输出 episode_N.txt / _remaining.txt 也将以 NFC 写入(原 NFD 源文件在
    # macOS/外部导入越南语场景才出现,统一 NFC 是 industry standard)。
    text = unicodedata.normalize("NFC", source_path.read_text(encoding="utf-8"))

    # 用目标字数计算大致偏移位置
    target_offset = find_char_offset(text, args.target)

    # 在目标偏移附近搜索锚点
    positions = find_anchor_near_target(text, args.anchor, target_offset, window=args.context)

    if len(positions) == 0:
        print(
            f'错误：在目标字数 {args.target} 附近（±{args.context} 字符窗口）未找到锚点文本: "{args.anchor}"',
            file=sys.stderr,
        )
        sys.exit(1)

    if len(positions) > 1:
        print(
            f"警告：锚点文本在窗口内匹配到 {len(positions)} 处，使用距离目标最近的匹配。",
            file=sys.stderr,
        )
        for i, pos in enumerate(positions):
            ctx_start = max(0, pos - len(args.anchor) - 10)
            ctx_end = min(len(text), pos + 10)
            distance = abs(pos - target_offset)
            marker = " ← 选中" if i == 0 else ""
            print(f"  匹配 {i + 1} (距离 {distance}): ...{text[ctx_start:ctx_end]}...{marker}", file=sys.stderr)

    split_pos = positions[0]
    part_before = text[:split_pos]
    part_after = text[split_pos:]

    # 展示切分预览
    preview_len = 50
    before_preview = part_before[-preview_len:] if len(part_before) > preview_len else part_before
    after_preview = part_after[:preview_len] if len(part_after) > preview_len else part_after

    print(f"目标字数: {args.target}，目标偏移: {target_offset}")
    print(f"切分位置: 第 {split_pos} 字符处")
    print(f"前文末尾: ...{before_preview}")
    print(f"后文开头: {after_preview}...")
    print(f"前半部分: {len(part_before)} 字符")
    print(f"后半部分: {len(part_after)} 字符")

    if args.dry_run:
        print("\n[Dry Run] 未写入文件。确认无误后去掉 --dry-run 参数执行。")
        return

    # output 强制落在 cwd/source/，不跟随 source_path.parent
    episode_file = source_dir / f"episode_{args.episode}.txt"
    remaining_file = source_dir / "_remaining.txt"

    episode_file.write_text(part_before, encoding="utf-8")
    remaining_file.write_text(part_after, encoding="utf-8")

    print("\n已生成:")
    print(f"  {episode_file} ({len(part_before)} 字符)")
    print(f"  {remaining_file} ({len(part_after)} 字符)")
    print(f"  原文件未修改: {source_path}")


if __name__ == "__main__":
    main()
