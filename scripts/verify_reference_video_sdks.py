"""SDK 验证脚本：跑四家供应商的参考生视频真实能力矩阵。

用法:
    python scripts/verify_reference_video_sdks.py --provider ark --refs 9 --duration 10 --multi-shot
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class Provider(StrEnum):
    ARK = "ark"
    GROK = "grok"
    VEO = "veo"
    SORA = "sora"


@dataclass
class Args:
    provider: Provider
    refs: int
    duration: int
    multi_shot: bool
    report_dir: Path


def parse_args(argv: list[str] | None = None) -> Args:
    p = argparse.ArgumentParser(description="Reference-to-video SDK verifier")
    p.add_argument(
        "--provider",
        type=Provider,
        choices=list(Provider),
        required=True,
        help="Provider to test",
    )
    p.add_argument("--refs", type=int, default=3, help="Number of reference images (default: 3)")
    p.add_argument("--duration", type=int, default=5, help="Video duration in seconds (default: 5)")
    p.add_argument(
        "--multi-shot",
        action="store_true",
        help="Use multi-shot prompt (Shot 1 / Shot 2 ...)",
    )
    p.add_argument(
        "--report-dir",
        type=Path,
        default=Path("docs/verification-reports"),
        help="Directory to write Markdown report",
    )
    ns = p.parse_args(argv)
    return Args(
        provider=ns.provider,
        refs=ns.refs,
        duration=ns.duration,
        multi_shot=ns.multi_shot,
        report_dir=ns.report_dir,
    )


@dataclass
class RunResult:
    provider: Provider
    model: str
    refs: int
    duration: int
    multi_shot: bool
    success: bool
    elapsed_sec: float
    request_bytes: int
    error: str | None
    video_path: Path | None
    note: str


def render_report(results: list[RunResult], *, generated_at: datetime | None = None) -> str:
    ts = (generated_at or datetime.now()).isoformat(sep=" ", timespec="seconds")
    lines: list[str] = [
        "# Reference-to-Video SDK 验证报告",
        "",
        f"生成时间：{ts}",
        "",
    ]
    if not results:
        lines.append("_no results_")
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            "| Provider | Model | Refs | Duration | Multi-shot | Result | Elapsed | Bytes | Note |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for r in results:
        outcome = "PASS" if r.success else f"FAIL: {r.error or ''}".strip()
        lines.append(
            f"| {r.provider} | {r.model} | {r.refs} | {r.duration}s "
            f"| {'yes' if r.multi_shot else 'no'} | {outcome} "
            f"| {r.elapsed_sec:.1f}s | {r.request_bytes} | {r.note} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    print(f"[verify] provider={args.provider} refs={args.refs} duration={args.duration}s multi_shot={args.multi_shot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
