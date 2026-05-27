#!/usr/bin/env python3
"""
peek_split_point.py - 切分点探测脚本

展示目标阅读单位附近的上下文,帮助 agent 和用户决定自然断点。

「阅读单位」按 source_language 定义:zh 数汉字 + CJK 标点,en/vi 数 word。
与 _text_utils.count_chars 的字符级度量分工:本脚本展示的是用户心智模型
里的「字数」,而切分点定位仍走原 find_char_offset(字符偏移),
由本脚本在内部按比例换算桥接。

用法:
    python peek_split_point.py --source source/novel.txt --target 1000
    python peek_split_point.py --source source/novel.txt --target 1000 --language en
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

# 导入共享工具
sys.path.insert(0, str(Path(__file__).parent))
from _text_utils import count_chars, find_char_offset, find_natural_breakpoints  # noqa: E402


def _find_repo_root(start: Path) -> Path:
    """向上回溯定位含 pyproject.toml 的目录,覆盖源/物化/editable 三种部署形态。"""
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError(f"无法从 {start} 向上找到 pyproject.toml。请确认脚本位于 ArcReel 仓库内。")


def _load_text_metrics():
    """单文件加载 lib/text_metrics.py,绕过 lib/__init__.py 的 ProjectManager 重链。

    走 importlib.util.spec_from_file_location 而非 `from lib.text_metrics import X`:
    后者会先触发 lib/__init__.py 顶层 `from .project_manager import ProjectManager`,
    把 portalocker / SQLAlchemy 等整套服务端运行时依赖拉进来。peek 是 agent 在
    project cwd 内跑的轻量探测脚本,只需 count_reading_units 一个纯函数。
    """
    repo_root = _find_repo_root(Path(__file__).resolve())
    module_path = repo_root / "lib" / "text_metrics.py"
    spec = importlib.util.spec_from_file_location("_arcreel_text_metrics", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.count_reading_units


count_reading_units = _load_text_metrics()


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

    source_path = _resolve_source_in_project(args.source)
    language = _resolve_language(args.language)

    text = source_path.read_text(encoding="utf-8")
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

    # 阅读单位 → 原文字符的比例换算:find_char_offset 按"非空行字符数"累计,
    # 与阅读单位的度量口径不同。先按 total 比例把 target_units 换算回字符级
    # target_chars,再喂给字符级偏移定位器。这样切分点定位逻辑不动,展示
    # 给用户/agent 的所有「字数」统一是阅读单位口径。
    # split_target_chars 同时回给 agent —— split_episode.py 的 --target 按
    # 字符级 count_chars 解读,agent 须用这个值而非原始 target_units,否则
    # 在 ASCII 占比高(zh 混排)或 word 语种(en/vi)场景会让 split 的锚点
    # 搜索窗口偏离 peek 选定位置、可能落空或错选同名锚点。
    char_total = count_chars(text)
    target_chars = max(1, int(args.target * char_total / total_units))
    target_offset = find_char_offset(text, target_chars)

    # 查找附近的自然断点
    breakpoints = find_natural_breakpoints(text, target_offset, window=args.context)

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
        "split_target_chars": target_chars,
        "target_offset": target_offset,
        "context_before": before_context,
        "context_after": after_context,
        "nearby_breakpoints": breakpoints[:10],
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
