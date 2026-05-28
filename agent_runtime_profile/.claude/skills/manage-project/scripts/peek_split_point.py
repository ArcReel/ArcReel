#!/usr/bin/env python3
"""
peek_split_point.py - 切分点探测脚本

展示目标阅读单位附近的上下文,帮助 agent 和用户决定自然断点。

「阅读单位」按 source_language 定义:zh 数汉字 + CJK 标点,en/vi 数 word。
与 _text_utils.count_chars 的字符级度量分工:本脚本展示的是用户心智模型
里的「字数」,而切分点定位走 find_reading_unit_offset (按原文顺序累计扫描),
再回填字符级 split_target_chars 给 split_episode.py。

用法:
    python peek_split_point.py --source source/novel.txt --target 1000
    python peek_split_point.py --source source/novel.txt --target 1000 --language en
"""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

# 导入共享工具
sys.path.insert(0, str(Path(__file__).parent))
from _text_utils import count_chars, find_natural_breakpoints  # noqa: E402

# vendored from lib/text_metrics.py —— 故意复制而非 import,避免依赖
# Path(__file__) 回溯到仓库根。profile 物化到 ARCREEL_DATA_DIR/AI_ANIME_PROJECTS
# 指向的项目目录后,脚本父目录链上没有 pyproject.toml,运行时无法定位 lib。
# 改 lib/text_metrics.py 时同步更新这里 —— 接口稳定,改动罕见。
_ZH_UNIT_PATTERN = re.compile("[㐀-鿿豈-﫿　-〿＀-￯𠀀-𲎯]")
_LATIN_WORD_PATTERN = re.compile(r"\b\w+\b", re.UNICODE)


def _pattern_for(language: str | None) -> "re.Pattern[str]":
    code = (language or "").strip().lower()
    if code in ("en", "vi"):
        return _LATIN_WORD_PATTERN
    return _ZH_UNIT_PATTERN


def count_reading_units(text: str, language: str | None) -> int:
    if not text:
        return 0
    return len(_pattern_for(language).findall(text))


def find_reading_unit_offset(text: str, target_units: int, language: str | None) -> int:
    if target_units <= 0 or not text:
        return 0
    count = 0
    last_end = 0
    for match in _pattern_for(language).finditer(text):
        count += 1
        last_end = match.end()
        if count >= target_units:
            return last_end
    return len(text)


def _resolve_source_in_project(arg_source: str) -> Path:
    """强约束:cwd 必须含 project.json,source 必须位于 cwd/source/ 之内。

    peek 是只读探测,不写出文件,但仍按相同围栏校验输入,与 split_episode 一致。
    防御点同 split_episode:cwd/source 不能是符号链接,否则 resolve 后会双双
    落到项目外目录、绕过 is_relative_to,把"探测项目内文件"变成"探测项目外"。
    """
    cwd = Path.cwd().resolve()
    if not (cwd / "project.json").is_file():
        print(f"❌ 必须在项目目录内运行(当前 cwd={cwd} 不含 project.json)", file=sys.stderr)
        sys.exit(1)
    source_dir_unresolved = cwd / "source"
    if source_dir_unresolved.is_symlink():
        print(
            f"❌ source/ 不能是符号链接(避免探测项目外文件): {source_dir_unresolved}",
            file=sys.stderr,
        )
        sys.exit(1)
    source_dir = source_dir_unresolved.resolve()
    if not source_dir.is_dir():
        print(f"❌ 项目缺 source/ 目录: {source_dir}", file=sys.stderr)
        sys.exit(1)
    source_path = (cwd / arg_source).resolve() if not Path(arg_source).is_absolute() else Path(arg_source).resolve()
    if not source_path.is_relative_to(source_dir):
        print(f"❌ 源文件必须位于 {source_dir} 内,收到: {source_path}", file=sys.stderr)
        sys.exit(1)
    if not source_path.is_file():
        print(f"❌ 源文件不存在或不是普通文件: {source_path}", file=sys.stderr)
        sys.exit(1)
    return source_path


_SUPPORTED_LANGUAGES = ("zh", "en", "vi")


def _resolve_language(cli_arg: str | None) -> str:
    """优先 --language;否则读 cwd/project.json 的 source_language;缺则 zh。

    校验:必须是 {zh, en, vi} 之一,否则报错退出 —— 避免落到「输出 JSON 写错语言、
    内部度量静默回落 zh」的误导路径。
    """
    raw: str | None
    if cli_arg:
        raw = cli_arg
    else:
        raw = None
        project_json = Path.cwd().resolve() / "project.json"
        if project_json.is_file():
            try:
                data = json.loads(project_json.read_text(encoding="utf-8"))
                stored = data.get("source_language")
                raw = str(stored) if stored else None
            except (json.JSONDecodeError, OSError):
                pass
    if raw is None:
        return "zh"
    normalized = raw.strip().lower()
    if normalized not in _SUPPORTED_LANGUAGES:
        print(
            f"❌ 不支持的 language={raw!r}(可选: {list(_SUPPORTED_LANGUAGES)})。"
            f"修正 --language 或 project.json 的 source_language 后重试。",
            file=sys.stderr,
        )
        sys.exit(1)
    return normalized


def main():
    parser = argparse.ArgumentParser(description="探测切分点附近上下文")
    parser.add_argument("--source", required=True, help="源文件路径")
    parser.add_argument("--target", required=True, type=int, help="目标阅读单位数(按 source_language 解读)")
    parser.add_argument("--context", default=200, type=int, help="上下文字符数(默认 200)")
    parser.add_argument(
        "--language",
        default=None,
        help="阅读单位语言(zh/en/vi),缺省时从 project.json 的 source_language 读取,再缺则 zh",
    )
    args = parser.parse_args()

    if args.target < 1:
        print(f"❌ --target ({args.target}) 必须 >= 1", file=sys.stderr)
        sys.exit(1)

    source_path = _resolve_source_in_project(args.source)
    language = _resolve_language(args.language)

    # NFC normalize 边界:越南语 NFD/组合重音(macOS 文件名等场景)会让 \w word
    # boundary 把 Hôm 拆成 H + om,导致 count_reading_units 偏多、断点 offset 偏移。
    # 统一 NFC 后,下游 anchor 字符串与 split_target_chars 也都在 NFC 空间一致。
    text = unicodedata.normalize("NFC", source_path.read_text(encoding="utf-8"))
    total_units = count_reading_units(text, language)

    if total_units == 0:
        print(f"❌ 源文件无可计阅读单位(language={language}): {source_path}", file=sys.stderr)
        sys.exit(1)

    if args.target >= total_units:
        print(
            f"错误:目标阅读单位 ({args.target}) 超过或等于总阅读单位 ({total_units})",
            file=sys.stderr,
        )
        sys.exit(1)

    # 按原文顺序累计阅读单位找到精准 offset(早期版本用全局比例换算,在 en/vi
    # 或 zh 混排 ASCII/数字分布不均时会偏移,把后续 split 锚点搜索带出窗口)。
    # split_episode.py 的 --target 是非空白字符数口径(_text_utils.count_chars
    # 与 find_char_offset 同口径,跳过所有空白)。en/vi 含空格场景下 count_chars
    # 不再多算行内空白,split.find_char_offset 接到 split_target_chars 能找到等价
    # offset、anchor 搜索窗口对齐。
    target_offset = find_reading_unit_offset(text, args.target, language)
    split_target_chars = count_chars(text[:target_offset])

    # 查找附近的自然断点(传 language:en/vi 用 ASCII `. ! ?`,zh 用 `。！？`)
    breakpoints = find_natural_breakpoints(text, target_offset, window=args.context, language=language)

    # 提取上下文
    ctx_start = max(0, target_offset - args.context)
    ctx_end = min(len(text), target_offset + args.context)
    before_context = text[ctx_start:target_offset]
    after_context = text[target_offset:ctx_end]

    result = {
        "source": str(source_path),
        "language": language,
        "total_units": total_units,
        "target_units": args.target,
        "split_target_chars": split_target_chars,
        "target_offset": target_offset,
        "context_before": before_context,
        "context_after": after_context,
        "nearby_breakpoints": breakpoints[:10],
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
