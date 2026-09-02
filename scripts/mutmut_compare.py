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

`exit code == 1` 视为 killed，其余一律列为待复核，与 runbook 的判据一致；有待复核项时不判通过。
基线与本轮的 mutant 名集合不一致时直接失败：源码一变 `mutants/` 重生成，比对本身不成立。

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

    @property
    def pending_recheck(self) -> bool:
        """有 mutant 变成超时之类的非 0/1 exit code，结论要等新进程复核。"""
        return bool(self.reworked_recheck or self.baseline_killed_recheck)

    @property
    def passed(self) -> bool:
        return not (
            self.invalid or self.reworked_survived or self.regressed or self.equivalent_killed or self.pending_recheck
        )


def load_exit_codes(root: Path) -> dict[str, int | None]:
    """合并目录下全部 `*.meta` 的 `exit_code_by_key`。"""
    merged: dict[str, int | None] = {}
    for meta in sorted(root.rglob("*.meta")):
        with meta.open(encoding="utf-8") as f:
            data = json.load(f)
        codes = data.get("exit_code_by_key")
        if not isinstance(codes, dict):
            raise ValueError(f"{meta} 缺少 exit_code_by_key")
        for key, code in codes.items():
            if not isinstance(key, str) or not (code is None or isinstance(code, int)):
                raise ValueError(f"{meta} 的 exit_code_by_key 含非法项：{key!r}: {code!r}")
            if key in merged:
                raise ValueError(f"mutant {key} 在 {root} 下出现多次")
            merged[key] = code
    if not merged:
        raise ValueError(f"{root} 下没有任何 *.meta 或其 exit_code_by_key 为空")
    return merged


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
) -> ComparisonReport:
    reworked_set = set(reworked)
    equivalent_set = set(equivalent)
    invalid: list[str] = []

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
        f"其中等价变异体 {len(report.equivalent_killed)} |"
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
        baseline = load_exit_codes(args.baseline)
        current = load_exit_codes(args.current)
        reworked = read_names(args.reworked)
        equivalent = read_names(args.equivalent) if args.equivalent else []
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"读取输入失败：{exc}", file=sys.stderr)
        return EXIT_INVALID

    report = compare(baseline, current, reworked, equivalent)
    print(render(report))
    if report.invalid:
        return EXIT_INVALID
    return EXIT_OK if report.passed else EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
