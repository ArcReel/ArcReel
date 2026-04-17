"""SDK 验证脚本：跑四家供应商的参考生视频真实能力矩阵。

用法:
    python scripts/verify_reference_video_sdks.py --provider ark --refs 9 --duration 10 --multi-shot
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from lib.video_backends.base import VideoBackend, VideoCapabilities, VideoGenerationRequest
from scripts.fixtures.reference_video.generate_fixtures import generate_color_refs


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
) -> RunResult:
    ref_dir = work_dir / "refs"
    ref_paths = generate_color_refs(ref_dir, count=refs)
    out_path = work_dir / f"{provider}_{int(time.time())}.mp4"
    prompt = DEFAULT_PROMPT_MULTI if multi_shot else DEFAULT_PROMPT_SINGLE
    request = VideoGenerationRequest(
        prompt=prompt,
        output_path=out_path,
        duration_seconds=duration,
        reference_images=ref_paths,
    )
    # 粗估请求体大小（prompt + 图片字节数），用于 Grok gRPC 上限观测
    request_bytes = len(prompt.encode("utf-8")) + sum(p.stat().st_size for p in ref_paths)
    start = time.monotonic()
    try:
        await backend.generate(request)
        elapsed = time.monotonic() - start
        return RunResult(
            provider=provider,
            model=backend.model,
            refs=refs,
            duration=duration,
            multi_shot=multi_shot,
            success=True,
            elapsed_sec=elapsed,
            request_bytes=request_bytes,
            error=None,
            video_path=out_path,
            note="",
        )
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - start
        return RunResult(
            provider=provider,
            model=backend.model,
            refs=refs,
            duration=duration,
            multi_shot=multi_shot,
            success=False,
            elapsed_sec=elapsed,
            request_bytes=request_bytes,
            error=f"{type(exc).__name__}: {exc}",
            video_path=None,
            note="",
        )


def clamp_refs_for_backend(*, requested: int, caps: VideoCapabilities) -> tuple[int, str]:
    if not caps.reference_images:
        raise ValueError("Backend does not support reference_images")
    if requested <= caps.max_reference_images:
        return requested, ""
    note = f"clamped {requested} → {caps.max_reference_images} (backend max)"
    return caps.max_reference_images, note


# Provider → backend factory（懒加载 import，避免未配置环境启动时爆炸）
_BACKEND_FACTORIES: dict[Provider, Callable[[], VideoBackend]] = {}


def _register_factory(provider: Provider, factory: Callable[[], VideoBackend]) -> None:
    _BACKEND_FACTORIES[provider] = factory


def resolve_backend(provider: Provider) -> VideoBackend:
    if provider not in _BACKEND_FACTORIES:
        _lazy_register_factories()
    return _BACKEND_FACTORIES[provider]()


def _lazy_register_factories() -> None:
    """按需 import 各家后端，避免一个家配置缺失就整个脚本启不来。"""
    try:
        from lib.video_backends.ark import ArkVideoBackend

        _register_factory(Provider.ARK, lambda: ArkVideoBackend())
    except Exception:  # noqa: BLE001
        pass
    try:
        from lib.video_backends.grok import GrokVideoBackend

        _register_factory(Provider.GROK, lambda: GrokVideoBackend())
    except Exception:  # noqa: BLE001
        pass
    try:
        from lib.video_backends.gemini import GeminiVideoBackend

        _register_factory(Provider.VEO, lambda: GeminiVideoBackend())
    except Exception:  # noqa: BLE001
        pass
    try:
        from lib.video_backends.openai import OpenAIVideoBackend

        _register_factory(Provider.SORA, lambda: OpenAIVideoBackend())
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    args = parse_args()
    print(f"[verify] provider={args.provider} refs={args.refs} duration={args.duration}s multi_shot={args.multi_shot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
