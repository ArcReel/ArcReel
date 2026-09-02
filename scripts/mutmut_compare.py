#!/usr/bin/env python3
"""比对两轮 mutmut 结果，按 CONTRIBUTING「变异测试」的三层验收判据出报告。

输入是两个目录下的 `*.meta` 文件（mutmut 写在 `mutants/` 里，每个源模块一份），
只读其中的 `exit_code_by_key`（mutant 名 → exit code），不读 mutmut 的终端汇总——
汇总只枚举它认识的几个 exit code，段错误之类两头不落。

三层：
1. 本次改造针对的 mutant（`--reworked` 名单）：须全部 killed；
2. 基线里 killed 的 mutant：不得变成存活；变成超时须新进程复核；
3. 基线里存活且本次未改造的 mutant：被杀死只登记不报警；
   其中判为等价变异体的（`--equivalent` 名单）被杀死即整轮作废。

`exit code == 1` 视为 killed，`0` 视为存活，其余列为待复核，与 runbook 的判据一致；有待复核项时不判通过。
基线与本轮的 mutant 名集合不一致、或同名函数的源码哈希不同时直接失败：源码一变 `mutants/` 重生成，比对本身不成立；
改造名单为空同样视为无效输入。

零第三方依赖。用法见 `--help`，流程见 `docs/testing/mutmut-runbook.md`。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

KILLED = 1
SURVIVED = 0

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INVALID = 2


@dataclass
class ComparisonReport:
    """三层验收的结果。`invalid` 非空时其余字段不可信。"""

    invalid: list[str] = field(default_factory=list)
    reworked_killed: list[str] = field(default_factory=list)
    reworked_survived: list[str] = field(default_factory=list)
    reworked_recheck: list[tuple[str, int]] = field(default_factory=list)
    baseline_killed_total: int = 0
    regressed: list[str] = field(default_factory=list)
    baseline_killed_recheck: list[tuple[str, int]] = field(default_factory=list)
    untouched_total: int = 0
    untouched_killed: list[str] = field(default_factory=list)
    equivalent_killed: list[str] = field(default_factory=list)
    equivalent_recheck: list[tuple[str, int]] = field(default_factory=list)

    @property
    def pending_recheck(self) -> bool:
        """有 mutant 变成超时之类的非 0/1 exit code，结论要等新进程复核。

        第三层只对等价变异体复核：其余未改造 mutant 被杀只登记，异常 exit code 不影响结论。
        """
        return bool(self.reworked_recheck or self.baseline_killed_recheck or self.equivalent_recheck)

    @property
    def passed(self) -> bool:
        return not (
            self.invalid or self.reworked_survived or self.regressed or self.equivalent_killed or self.pending_recheck
        )


@dataclass(frozen=True)
class MetaSnapshot:
    """一轮 mutmut 的结果：mutant 名 → exit code，以及被变异函数的源码哈希。"""

    exit_codes: Mapping[str, int | None]
    function_hashes: Mapping[str, str]


def load_meta(root: Path) -> MetaSnapshot:
    """合并目录下全部 `*.meta` 的 `exit_code_by_key` 与 `hash_by_function_name`。

    哈希以「模块.函数」为键：mutant 名是 `lib.a.x_f__mutmut_3`，同一 .meta 里的 `hash_by_function_name`
    键是 `x_f`，模块前缀取自该文件的 mutant 名。缺少 `hash_by_function_name`、或有 mutant 对应的函数
    没有哈希，一律拒绝：源码哈希核对是比对有效性的前提，缺项会让它静默失效。
    """
    exit_codes: dict[str, int | None] = {}
    function_hashes: dict[str, str] = {}
    for meta in sorted(root.rglob("*.meta")):
        with meta.open(encoding="utf-8") as f:
            data = json.load(f)
        codes = data.get("exit_code_by_key")
        hashes = data.get("hash_by_function_name")
        if not isinstance(codes, dict) or not isinstance(hashes, dict):
            raise ValueError(f"{meta} 缺少 exit_code_by_key 或 hash_by_function_name")
        modules: set[str] = set()
        for key, code in codes.items():
            if not isinstance(key, str) or not (code is None or isinstance(code, int)):
                raise ValueError(f"{meta} 的 exit_code_by_key 含非法项：{key!r}: {code!r}")
            if key in exit_codes:
                raise ValueError(f"mutant {key} 在 {root} 下出现多次")
            exit_codes[key] = code
            modules.add(key.partition("__mutmut_")[0].rpartition(".")[0])
        if len(modules) > 1:
            raise ValueError(f"{meta} 混有多个模块的 mutant：{sorted(modules)}")
        module = modules.pop() if modules else ""
        unhashed = sorted({key.partition("__mutmut_")[0].rpartition(".")[2] for key in codes} - set(hashes))
        if unhashed:
            raise ValueError(f"{meta} 的 hash_by_function_name 缺少 mutant 对应的函数：{unhashed}")
        for func, digest in hashes.items():
            if not isinstance(func, str) or not isinstance(digest, str):
                raise ValueError(f"{meta} 的 hash_by_function_name 含非法项：{func!r}: {digest!r}")
            function_hashes[f"{module}.{func}" if module else func] = digest
    if not exit_codes:
        raise ValueError(f"{root} 下没有任何 *.meta 或其 exit_code_by_key 为空")
    return MetaSnapshot(exit_codes=exit_codes, function_hashes=function_hashes)


def read_names(path: Path) -> list[str]:
    """一行一个 mutant 名，忽略空行与 `#` 开头的行。"""
    names: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            names.append(stripped)
    return names


def compare(
    baseline: Mapping[str, int | None],
    current: Mapping[str, int | None],
    reworked: Iterable[str],
    equivalent: Iterable[str] = (),
    *,
    baseline_hashes: Mapping[str, str] | None = None,
    current_hashes: Mapping[str, str] | None = None,
) -> ComparisonReport:
    reworked_set = set(reworked)
    equivalent_set = set(equivalent)
    invalid: list[str] = []

    if not reworked_set:
        invalid.append("改造名单为空：没有任何 mutant 可验收，检查名单文件是否生成正确")
    changed_functions = sorted(
        func
        for func, digest in (baseline_hashes or {}).items()
        if func in (current_hashes or {}) and (current_hashes or {})[func] != digest
    )
    if changed_functions:
        invalid.append(
            f"基线与本轮有 {len(changed_functions)} 个函数源码哈希不同：mutant 名可能没变但语义已变，先重建基线"
        )
        invalid.extend(f"  源码已变 {func}" for func in changed_functions[:10])
    if baseline_hashes is not None or current_hashes is not None:
        hash_only_baseline = sorted(set(baseline_hashes or {}) - set(current_hashes or {}))
        hash_only_current = sorted(set(current_hashes or {}) - set(baseline_hashes or {}))
        if hash_only_baseline or hash_only_current:
            invalid.append(
                f"基线与本轮的函数哈希集合不一致（基线独有 {len(hash_only_baseline)}，"
                f"本轮独有 {len(hash_only_current)}）：源码变了或 .meta 不完整，先重建基线"
            )
            invalid.extend(f"  基线独有哈希 {func}" for func in hash_only_baseline[:10])
            invalid.extend(f"  本轮独有哈希 {func}" for func in hash_only_current[:10])
    missing = sorted(set(baseline) - set(current))
    extra = sorted(set(current) - set(baseline))
    if missing or extra:
        invalid.append(
            f"基线与本轮的 mutant 名集合不一致（基线独有 {len(missing)}，本轮独有 {len(extra)}）：源码变了，先重建基线"
        )
        invalid.extend(f"  基线独有 {name}" for name in missing[:10])
        invalid.extend(f"  本轮独有 {name}" for name in extra[:10])
    invalid.extend(f"改造名单里的 mutant 不在基线中：{name}" for name in sorted(reworked_set - set(baseline)))
    invalid.extend(
        f"改造名单里的 mutant 在基线已是 killed，无从证明改造有效：{name}"
        for name in sorted(reworked_set & set(baseline))
        if baseline[name] == KILLED
    )
    invalid.extend(
        f"同一 mutant 既在改造名单又在等价变异体名单：{name}" for name in sorted(equivalent_set & reworked_set)
    )
    invalid.extend(f"等价变异体名单里的 mutant 不在基线中：{name}" for name in sorted(equivalent_set - set(baseline)))
    invalid.extend(
        f"等价变异体名单里的 mutant 在基线已是 killed：等价变异体不可能被杀，基线本身可疑，先查假杀死链：{name}"
        for name in sorted(equivalent_set & set(baseline))
        if baseline[name] == KILLED
    )
    if invalid:
        return ComparisonReport(invalid=invalid)

    report = ComparisonReport()
    for name in sorted(baseline):
        before = baseline[name]
        after = current[name]
        if name in reworked_set:
            if after == KILLED:
                report.reworked_killed.append(name)
            elif after == SURVIVED:
                report.reworked_survived.append(name)
            else:
                report.reworked_recheck.append((name, _code(after)))
        elif before == KILLED:
            report.baseline_killed_total += 1
            if after == SURVIVED:
                report.regressed.append(name)
            elif after != KILLED:
                report.baseline_killed_recheck.append((name, _code(after)))
        else:
            report.untouched_total += 1
            if after == KILLED:
                report.untouched_killed.append(name)
                if name in equivalent_set:
                    report.equivalent_killed.append(name)
            elif after != SURVIVED and name in equivalent_set:
                report.equivalent_recheck.append((name, _code(after)))
    return report


def _code(value: int | None) -> int:
    # None 是 mutmut 的「未检查」；用一个不与任何真实 exit code 冲突的值占位，让报告可排序可打印。
    return -1 if value is None else value


def render(report: ComparisonReport) -> str:
    lines: list[str] = []
    if report.invalid:
        lines.append("比对无效：")
        lines.extend(report.invalid)
        return "\n".join(lines)

    lines.append("| 层 | 数量 | 结果 |")
    lines.append("| --- | ---: | --- |")
    reworked_total = len(report.reworked_killed) + len(report.reworked_survived) + len(report.reworked_recheck)
    lines.append(
        f"| 本次改造针对的 mutant | {reworked_total} | killed {len(report.reworked_killed)}，"
        f"存活 {len(report.reworked_survived)}，待复核 {len(report.reworked_recheck)} |"
    )
    lines.append(
        f"| 基线 killed 的 mutant | {report.baseline_killed_total} | 回退 {len(report.regressed)}，"
        f"待复核 {len(report.baseline_killed_recheck)} |"
    )
    lines.append(
        f"| 基线存活且未改造的 mutant | {report.untouched_total} | 顺手杀死 {len(report.untouched_killed)}，"
        f"其中等价变异体 {len(report.equivalent_killed)}，等价变异体待复核 {len(report.equivalent_recheck)} |"
    )

    _section(lines, "改造后仍存活（改造未生效）", report.reworked_survived)
    _section(lines, "改造后待复核（新进程只跑关联用例，1 failed 即 killed）", _with_codes(report.reworked_recheck))
    _section(lines, "回退（基线 killed、本轮存活）", report.regressed)
    _section(
        lines,
        "基线 killed 变待复核（新进程复核，1 failed 即护栏成立；只有全部通过才是回退）",
        _with_codes(report.baseline_killed_recheck),
    )
    _section(lines, "顺手杀死（只登记）", report.untouched_killed)
    _section(lines, "等价变异体被杀死——整轮作废，先查假杀死链", report.equivalent_killed)
    _section(
        lines,
        "等价变异体待复核（新进程复核，1 failed 即被杀死、整轮作废）",
        _with_codes(report.equivalent_recheck),
    )
    lines.append("")
    if report.passed:
        lines.append("验收通过")
    elif report.reworked_survived or report.regressed or report.equivalent_killed:
        lines.append("验收未通过")
    else:
        lines.append("验收未完成：有待复核项，新进程复核后按结果改基线或改本轮 .meta 再比对")
    return "\n".join(lines)


def _with_codes(items: list[tuple[str, int]]) -> list[str]:
    return [f"{name}  exit code {code}" for name, code in items]


def _section(lines: list[str], title: str, items: list[str]) -> None:
    if not items:
        return
    lines.append("")
    lines.append(f"## {title}（{len(items)}）")
    lines.extend(f"- {item}" for item in items)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline", type=Path, required=True, help="改造前那轮的 *.meta 所在目录")
    parser.add_argument("--current", type=Path, required=True, help="改造后复跑的 *.meta 所在目录，通常是 mutants/")
    parser.add_argument("--reworked", type=Path, required=True, help="本次改造针对的 mutant 名单，一行一个")
    parser.add_argument("--equivalent", type=Path, help="判为等价变异体的 mutant 名单，一行一个；被杀死即整轮作废")
    args = parser.parse_args(argv)

    try:
        baseline = load_meta(args.baseline)
        current = load_meta(args.current)
        reworked = read_names(args.reworked)
        equivalent = read_names(args.equivalent) if args.equivalent else []
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"读取输入失败：{exc}", file=sys.stderr)
        return EXIT_INVALID

    report = compare(
        baseline.exit_codes,
        current.exit_codes,
        reworked,
        equivalent,
        baseline_hashes=baseline.function_hashes,
        current_hashes=current.function_hashes,
    )
    print(render(report))
    if report.invalid:
        return EXIT_INVALID
    return EXIT_OK if report.passed else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
