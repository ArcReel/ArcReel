"""SDK 验证脚本：跑四家供应商的参考生视频真实能力矩阵。

用法:
    python scripts/verify_reference_video_sdks.py --provider ark --refs 9 --duration 10 --multi-shot
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path

from lib.video_backends.base import VideoBackend, VideoCapabilities, VideoGenerationRequest
from scripts.fixtures.reference_video.generate_fixtures import generate_color_refs

logger = logging.getLogger(__name__)


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
    # 粗估请求体大小：仅累加原始文件字节数，用于 Grok gRPC 上限观测。
    # 注意不含序列化开销——各供应商编码方式不同：
    #   - Ark:  multipart form-data（约 1× 原字节 + 极小边界头）
    #   - Grok: multipart（约 1×，部分接口用 Base64 时 ≈1.33×）
    #   - Veo/Sora: JSON+Base64（≈1.33× 原字节）
    # 要观察"实际发送"请参考各 backend 日志；此处仅做量级监控。
    request_bytes = len(prompt.encode("utf-8")) + sum(p.stat().st_size for p in ref_paths)
    start = time.monotonic()
    try:
        await backend.generate(request)
        # 防 false-positive：backend 返回成功但视频文件未落盘 / 为空时应判 FAIL
        if not out_path.exists() or out_path.stat().st_size == 0:
            raise RuntimeError(f"output video missing or empty: {out_path}")
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
    """返回指定 provider 的 backend 实例；若未注册给出具体原因，而非晦涩的 KeyError。"""
    if provider not in _BACKEND_FACTORIES:
        _lazy_register_factories()
    if provider not in _BACKEND_FACTORIES:
        reason = _REGISTRATION_FAILURES.get(provider, "unknown reason")
        raise RuntimeError(
            f"backend {provider} not available: {reason}. 已知可用 provider: {sorted(_BACKEND_FACTORIES)}"
        )
    return _BACKEND_FACTORIES[provider]()


# 记录每家 backend 懒加载失败的原因，resolve_backend 用来给出可读错误
_REGISTRATION_FAILURES: dict[Provider, str] = {}


def _try_register(provider: Provider, import_and_build: Callable[[], Callable[[], VideoBackend]]) -> None:
    """统一 try/except 模板：import + 构造 factory，失败时 log warning 并登记原因。"""
    try:
        factory = import_and_build()
    except Exception as exc:  # noqa: BLE001
        reason = f"{type(exc).__name__}: {exc}"
        _REGISTRATION_FAILURES[provider] = reason
        logger.warning("provider %s 未注册: %s", provider, reason)
        return
    _register_factory(provider, factory)
    _REGISTRATION_FAILURES.pop(provider, None)


def _lazy_register_factories() -> None:
    """按需 import 各家后端，避免一个家配置缺失就整个脚本启不来。

    失败原因通过 logger.warning 输出并记录在 _REGISTRATION_FAILURES，
    resolve_backend 报错时引用，以给出可读信息。
    """

    def _ark() -> Callable[[], VideoBackend]:
        from lib.video_backends.ark import ArkVideoBackend

        return lambda: ArkVideoBackend()

    def _grok() -> Callable[[], VideoBackend]:
        from lib.video_backends.grok import GrokVideoBackend

        return lambda: GrokVideoBackend()

    def _veo() -> Callable[[], VideoBackend]:
        from lib.video_backends.gemini import GeminiVideoBackend

        return lambda: GeminiVideoBackend()

    def _sora() -> Callable[[], VideoBackend]:
        from lib.video_backends.openai import OpenAIVideoBackend

        return lambda: OpenAIVideoBackend()

    _try_register(Provider.ARK, _ark)
    _try_register(Provider.GROK, _grok)
    _try_register(Provider.VEO, _veo)
    _try_register(Provider.SORA, _sora)


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
    )
    if note:
        result.note = note
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
    work_dir = Path(".verify_work") / args.provider
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
