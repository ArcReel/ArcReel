"""SDK 验证脚本：跑四家供应商的参考生视频真实能力矩阵。

用法:
    python scripts/verify_reference_video_sdks.py --provider ark --refs 9 --duration 10 --multi-shot
"""

from __future__ import annotations

import argparse
import asyncio
import time
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path

from lib.video_backends import (
    PROVIDER_ARK,
    PROVIDER_GEMINI,
    PROVIDER_GROK,
    PROVIDER_OPENAI,
    VideoBackend,
    VideoGenerationRequest,
    create_backend,
)
from lib.video_backends.base import VideoCapabilities
from scripts.fixtures.reference_video.generate_fixtures import generate_color_refs


class Provider(StrEnum):
    ARK = "ark"
    GROK = "grok"
    VEO = "veo"
    SORA = "sora"


def _positive_int(value: str) -> int:
    """argparse type：拒绝 0 / 负值，避免 --refs 0 或 --duration 0 污染报告。"""
    try:
        ivalue = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"必须是整数: {value!r}") from exc
    if ivalue < 1:
        raise argparse.ArgumentTypeError(f"必须 >= 1: {ivalue}")
    return ivalue


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
    p.add_argument("--refs", type=_positive_int, default=3, help="Number of reference images (>=1, default: 3)")
    p.add_argument("--duration", type=_positive_int, default=5, help="Video duration in seconds (>=1, default: 5)")
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


DEFAULT_PROMPT_SINGLE = "A cinematic establishing shot of [图1]."
DEFAULT_PROMPT_MULTI = (
    "Shot 1 (3s): medium shot of [图1] walking into the room.\nShot 2 (5s): close-up of [图1] reacting to [图2]."
)


async def run_once(
    *,
    provider: Provider,
    backend: VideoBackend,
    refs: int,
    duration: int,
    multi_shot: bool,
    work_dir: Path,
    note: str = "",
) -> RunResult:
    ref_dir = work_dir / "refs"
    ref_paths = generate_color_refs(ref_dir, count=refs)
    out_path = work_dir / f"{provider.value}_{int(time.time())}.mp4"
    prompt = DEFAULT_PROMPT_MULTI if multi_shot else DEFAULT_PROMPT_SINGLE
    request = VideoGenerationRequest(
        prompt=prompt,
        output_path=out_path,
        duration_seconds=duration,
        reference_images=ref_paths,
    )
    # raw byte sum only; actual wire size varies by encoding (multipart ~1x, base64 ~1.33x).
    request_bytes = len(prompt.encode("utf-8")) + sum(p.stat().st_size for p in ref_paths)
    base: dict = {
        "provider": provider,
        "model": backend.model,
        "refs": refs,
        "duration": duration,
        "multi_shot": multi_shot,
        "request_bytes": request_bytes,
        "note": note,
    }
    start = time.monotonic()
    try:
        await backend.generate(request)
        # 防 false-positive：backend 返回成功但视频文件未落盘 / 为空时应判 FAIL
        if not out_path.exists() or out_path.stat().st_size == 0:
            raise RuntimeError(f"output video missing or empty: {out_path}")
        return RunResult(
            **base,
            success=True,
            elapsed_sec=time.monotonic() - start,
            error=None,
            video_path=out_path,
        )
    except Exception as exc:  # noqa: BLE001
        return RunResult(
            **base,
            success=False,
            elapsed_sec=time.monotonic() - start,
            error=f"{type(exc).__name__}: {exc}",
            video_path=None,
        )


def clamp_refs_for_backend(*, requested: int, caps: VideoCapabilities) -> tuple[int, str]:
    """把 requested 夹到 backend 上限；若 backend 不支持 reference_images 则直接抛 ValueError。"""
    if not caps.reference_images:
        raise ValueError("Backend does not support reference_images")
    if requested <= caps.max_reference_images:
        return requested, ""
    note = f"clamped {requested} → {caps.max_reference_images} (backend max)"
    return caps.max_reference_images, note


_PROVIDER_TO_BACKEND: dict[Provider, str] = {
    Provider.ARK: PROVIDER_ARK,
    Provider.GROK: PROVIDER_GROK,
    Provider.VEO: PROVIDER_GEMINI,
    Provider.SORA: PROVIDER_OPENAI,
}


def resolve_backend(provider: Provider) -> VideoBackend:
    """直接复用 lib.video_backends 的注册表——import lib.video_backends 已自动注册全部后端。"""
    return create_backend(_PROVIDER_TO_BACKEND[provider])


def _extract_data_rows(report_lines: list[str]) -> list[str]:
    """从已有 Markdown 报告中挑出表格数据行（跳过 header / 分隔符）。

    用行前缀精确识别表头和分隔行，避免 Result/Note 字段含 "Provider"
    / "---" 等关键字时被误过滤（如 FAIL: "Provider timeout"）。
    """
    return [
        ln
        for ln in report_lines
        if ln.startswith("| ") and not ln.startswith("| Provider |") and not ln.startswith("|---")
    ]


async def run_with_backend(
    *,
    provider: Provider,
    refs: int,
    duration: int,
    multi_shot: bool,
    report_dir: Path,
    work_dir: Path,
) -> int:
    backend = resolve_backend(provider)
    clamped, note = clamp_refs_for_backend(
        requested=refs,
        caps=backend.video_capabilities,
    )
    result = await run_once(
        provider=provider,
        backend=backend,
        refs=clamped,
        duration=duration,
        multi_shot=multi_shot,
        work_dir=work_dir,
        note=note,
    )
    report_dir.mkdir(parents=True, exist_ok=True)
    fname = report_dir / f"reference-video-sdks-{date.today():%Y-%m-%d}.md"
    # 多次运行追加模式：读原文件剥离 header、合并行
    existing_rows: list[str] = []
    if fname.exists():
        existing = fname.read_text(encoding="utf-8").splitlines()
        existing_rows = _extract_data_rows(existing)
    md = render_report([result])
    if existing_rows:
        lines = md.splitlines()
        # 把已有数据行塞回表尾
        lines.extend(existing_rows)
        md = "\n".join(lines) + "\n"
    fname.write_text(md, encoding="utf-8")
    return 0 if result.success else 2


def main() -> int:
    args = parse_args()
    work_dir = Path(".verify_work") / args.provider.value
    return asyncio.run(
        run_with_backend(
            provider=args.provider,
            refs=args.refs,
            duration=args.duration,
            multi_shot=args.multi_shot,
            report_dir=args.report_dir,
            work_dir=work_dir,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
