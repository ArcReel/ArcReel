# PR1 · M1 SDK 验证脚本 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 产出可重复运行的 SDK 验证 CLI + 四家供应商能力矩阵报告，锁定 Sora/Grok 的真实能力边界，作为后续 PR 的前置。

**Architecture:** 复用现有 `lib.video_backends.*` 的 `VideoBackend` 协议与 `VideoGenerationRequest`（已带 `reference_images` 字段），**不增加任何后端代码**；新增一个 CLI orchestrator 按 `--provider` / `--refs` / `--duration` 调不同 backend，把结果（成功/失败/耗时/请求体大小/响应错误）收集为一张 Markdown 矩阵表。

**Tech Stack:** Python 3.11+ / asyncio / argparse / pytest

## 参考设计

- Roadmap: `docs/superpowers/plans/2026-04-17-reference-to-video-roadmap.md`
- Spec: `docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md` §8.1、附录 B
- 现有后端：`lib/video_backends/{ark,grok,gemini,openai}.py`
- `VideoGenerationRequest` 契约：`lib/video_backends/base.py:128-150`

## 文件结构

### 新增

| 文件 | 职责 |
|---|---|
| `scripts/verify_reference_video_sdks.py` | CLI 入口 + orchestrator |
| `scripts/fixtures/reference_video/generate_fixtures.py` | 按需生成测试用参考图（纯色 PNG） |
| `tests/scripts/__init__.py` | 测试包 init |
| `tests/scripts/test_verify_reference_video_sdks.py` | CLI argparse + 报告格式单测 |
| `docs/verification-reports/.gitkeep` | 报告目录占位 |

### 改造

无（本 PR 不动现有代码）。

---

## Task 1：Fixture 生成器

**Files:**
- Create: `scripts/fixtures/reference_video/__init__.py`（空文件）
- Create: `scripts/fixtures/reference_video/generate_fixtures.py`
- Test: `tests/scripts/__init__.py`（空文件）
- Test: `tests/scripts/test_fixture_generator.py`

- [ ] **Step 1：写失败测试**

创建 `tests/scripts/test_fixture_generator.py`：

```python
from pathlib import Path

import pytest
from PIL import Image

from scripts.fixtures.reference_video.generate_fixtures import generate_color_refs


def test_generate_color_refs_creates_n_pngs(tmp_path: Path):
    paths = generate_color_refs(tmp_path, count=3, size=(128, 128))
    assert len(paths) == 3
    for p in paths:
        assert p.suffix == ".png"
        assert p.exists()
        with Image.open(p) as img:
            assert img.size == (128, 128)


def test_generate_color_refs_distinct_colors(tmp_path: Path):
    paths = generate_color_refs(tmp_path, count=4)
    pixels = []
    for p in paths:
        with Image.open(p) as img:
            pixels.append(img.getpixel((0, 0)))
    assert len(set(pixels)) == 4  # 每张颜色不同
```

- [ ] **Step 2：运行测试确认失败**

```bash
uv run pytest tests/scripts/test_fixture_generator.py -v
```

Expected: FAIL，ModuleNotFoundError 或函数未定义。

- [ ] **Step 3：实现 fixture 生成器**

创建 `scripts/fixtures/reference_video/generate_fixtures.py`：

```python
"""生成 SDK 验证用的纯色参考图（跨平台、无外部资产依赖）。"""

from __future__ import annotations

import colorsys
from pathlib import Path

from PIL import Image


def generate_color_refs(
    out_dir: Path,
    *,
    count: int,
    size: tuple[int, int] = (512, 512),
) -> list[Path]:
    """在 out_dir 下生成 count 张等间距色相的 PNG，返回路径列表。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i in range(count):
        hue = i / max(count, 1)
        rgb = tuple(int(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.7, 0.95))
        img = Image.new("RGB", size, rgb)
        out = out_dir / f"ref_{i + 1}.png"
        img.save(out, format="PNG")
        paths.append(out)
    return paths
```

创建空 `scripts/fixtures/reference_video/__init__.py` 和 `tests/scripts/__init__.py`。

- [ ] **Step 4：运行测试确认通过**

```bash
uv run pytest tests/scripts/test_fixture_generator.py -v
```

Expected: 2 PASS。

- [ ] **Step 5：Commit**

```bash
git add scripts/fixtures/reference_video/__init__.py scripts/fixtures/reference_video/generate_fixtures.py tests/scripts/__init__.py tests/scripts/test_fixture_generator.py
git commit -m "feat(sdk-verify): add color ref image fixture generator"
```

---

## Task 2：Provider 枚举 + CLI 骨架

**Files:**
- Create: `scripts/verify_reference_video_sdks.py`
- Test: `tests/scripts/test_verify_reference_video_sdks.py`

- [ ] **Step 1：写失败测试（argparse）**

创建 `tests/scripts/test_verify_reference_video_sdks.py`：

```python
import pytest

from scripts.verify_reference_video_sdks import Provider, parse_args


def test_parse_args_provider_required():
    with pytest.raises(SystemExit):
        parse_args([])


def test_parse_args_provider_accepts_all_four():
    for name in ("ark", "grok", "veo", "sora"):
        args = parse_args(["--provider", name])
        assert args.provider == Provider(name)


def test_parse_args_rejects_unknown_provider():
    with pytest.raises(SystemExit):
        parse_args(["--provider", "unknown"])


def test_parse_args_defaults():
    args = parse_args(["--provider", "ark"])
    assert args.refs == 3
    assert args.duration == 5
    assert args.multi_shot is False
    assert args.report_dir.name == "verification-reports"


def test_parse_args_override():
    args = parse_args([
        "--provider", "grok",
        "--refs", "7",
        "--duration", "10",
        "--multi-shot",
    ])
    assert args.refs == 7
    assert args.duration == 10
    assert args.multi_shot is True
```

- [ ] **Step 2：运行测试确认失败**

```bash
uv run pytest tests/scripts/test_verify_reference_video_sdks.py -v
```

Expected: FAIL，ModuleNotFoundError。

- [ ] **Step 3：实现 CLI 骨架**

创建 `scripts/verify_reference_video_sdks.py`：

```python
"""SDK 验证脚本：跑四家供应商的参考生视频真实能力矩阵。

用法:
    python scripts/verify_reference_video_sdks.py --provider ark --refs 9 --duration 10 --multi-shot
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
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


def main() -> int:
    args = parse_args()
    print(f"[verify] provider={args.provider} refs={args.refs} duration={args.duration}s multi_shot={args.multi_shot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4：运行测试确认通过**

```bash
uv run pytest tests/scripts/test_verify_reference_video_sdks.py -v
```

Expected: 5 PASS。

- [ ] **Step 5：Commit**

```bash
git add scripts/verify_reference_video_sdks.py tests/scripts/test_verify_reference_video_sdks.py
git commit -m "feat(sdk-verify): add CLI skeleton with Provider enum"
```

---

## Task 3：单次运行结果数据类 + Markdown 报告渲染器

**Files:**
- Modify: `scripts/verify_reference_video_sdks.py`
- Test: `tests/scripts/test_verify_reference_video_sdks.py`

- [ ] **Step 1：加测试（报告渲染）**

追加到 `tests/scripts/test_verify_reference_video_sdks.py`：

```python
from datetime import datetime
from pathlib import Path

from scripts.verify_reference_video_sdks import (
    Provider,
    RunResult,
    render_report,
)


def _ok(provider: Provider, refs: int, duration: int, note: str = "") -> RunResult:
    return RunResult(
        provider=provider,
        model="test-model",
        refs=refs,
        duration=duration,
        multi_shot=False,
        success=True,
        elapsed_sec=12.3,
        request_bytes=1024,
        error=None,
        video_path=None,
        note=note,
    )


def _fail(provider: Provider, error: str) -> RunResult:
    return RunResult(
        provider=provider,
        model="test-model",
        refs=3,
        duration=5,
        multi_shot=False,
        success=False,
        elapsed_sec=0.0,
        request_bytes=0,
        error=error,
        video_path=None,
        note="",
    )


def test_render_report_contains_header_and_rows():
    results = [_ok(Provider.ARK, 9, 10, note="fast mode"), _fail(Provider.SORA, "422 Unprocessable")]
    md = render_report(results, generated_at=datetime(2026, 4, 20, 12, 0))

    assert "# Reference-to-Video SDK 验证报告" in md
    assert "2026-04-20" in md
    # Table header
    assert "| Provider | Model | Refs | Duration | Multi-shot | Result | Elapsed | Bytes | Note |" in md
    # Rows
    assert "| ark |" in md
    assert "| sora |" in md
    assert "FAIL" in md
    assert "422 Unprocessable" in md


def test_render_report_empty_results_still_emits_header():
    md = render_report([], generated_at=datetime(2026, 4, 20, 12, 0))
    assert "# Reference-to-Video SDK 验证报告" in md
    assert "_no results_" in md
```

- [ ] **Step 2：运行测试确认失败**

```bash
uv run pytest tests/scripts/test_verify_reference_video_sdks.py -v
```

Expected: FAIL（`RunResult` / `render_report` 未定义）。

- [ ] **Step 3：实现 RunResult + render_report**

编辑 `scripts/verify_reference_video_sdks.py`，在 `parse_args` 之后插入：

```python
from datetime import datetime


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

    lines.extend([
        "| Provider | Model | Refs | Duration | Multi-shot | Result | Elapsed | Bytes | Note |",
        "|---|---|---|---|---|---|---|---|---|",
    ])
    for r in results:
        outcome = "PASS" if r.success else f"FAIL: {r.error or ''}".strip()
        lines.append(
            f"| {r.provider} | {r.model} | {r.refs} | {r.duration}s "
            f"| {'yes' if r.multi_shot else 'no'} | {outcome} "
            f"| {r.elapsed_sec:.1f}s | {r.request_bytes} | {r.note} |"
        )
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4：运行测试确认通过**

```bash
uv run pytest tests/scripts/test_verify_reference_video_sdks.py -v
```

Expected: 7 PASS。

- [ ] **Step 5：Commit**

```bash
git add scripts/verify_reference_video_sdks.py tests/scripts/test_verify_reference_video_sdks.py
git commit -m "feat(sdk-verify): add RunResult and Markdown report renderer"
```

---

## Task 4：Provider → Backend 解析器（Fake 驱动测试）

**Files:**
- Modify: `scripts/verify_reference_video_sdks.py`
- Test: `tests/scripts/test_verify_reference_video_sdks.py`

本任务设计：`resolve_backend(provider, *, config)` 返回 `VideoBackend`。测试用 Fake backend 验证 orchestration，不真实调 API。

- [ ] **Step 1：写失败测试（resolve + run_once Fake）**

追加到 `tests/scripts/test_verify_reference_video_sdks.py`：

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from lib.video_backends.base import (
    VideoBackend,
    VideoCapabilities,
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
)
from scripts.verify_reference_video_sdks import Provider, RunResult, run_once


class _FakeBackend:
    name = "fake"
    model = "fake-v1"
    capabilities = {VideoCapability.TEXT_TO_VIDEO, VideoCapability.IMAGE_TO_VIDEO}
    video_capabilities = VideoCapabilities(reference_images=True, max_reference_images=9)
    _calls: list[VideoGenerationRequest] = []

    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        type(self)._calls.append(request)
        return VideoGenerationResult(
            video_path=request.output_path,
            provider=self.name,
            model=self.model,
            duration_seconds=request.duration_seconds,
        )


class _FailBackend(_FakeBackend):
    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult:
        raise RuntimeError("boom: payload too large")


@pytest.mark.asyncio
async def test_run_once_success(tmp_path: Path):
    _FakeBackend._calls.clear()
    result = await run_once(
        provider=Provider.ARK,
        backend=_FakeBackend(),
        refs=3,
        duration=5,
        multi_shot=False,
        work_dir=tmp_path,
    )
    assert isinstance(result, RunResult)
    assert result.success is True
    assert result.refs == 3
    assert result.duration == 5
    assert result.error is None
    # Fake backend 收到 3 张 ref
    req = _FakeBackend._calls[-1]
    assert req.reference_images is not None
    assert len(req.reference_images) == 3


@pytest.mark.asyncio
async def test_run_once_multi_shot_prompt(tmp_path: Path):
    _FakeBackend._calls.clear()
    await run_once(
        provider=Provider.ARK,
        backend=_FakeBackend(),
        refs=2,
        duration=8,
        multi_shot=True,
        work_dir=tmp_path,
    )
    req = _FakeBackend._calls[-1]
    assert "Shot 1" in req.prompt
    assert "Shot 2" in req.prompt


@pytest.mark.asyncio
async def test_run_once_failure_captures_error(tmp_path: Path):
    result = await run_once(
        provider=Provider.ARK,
        backend=_FailBackend(),
        refs=3,
        duration=5,
        multi_shot=False,
        work_dir=tmp_path,
    )
    assert result.success is False
    assert "payload too large" in (result.error or "")
```

- [ ] **Step 2：运行测试确认失败**

```bash
uv run pytest tests/scripts/test_verify_reference_video_sdks.py -v
```

Expected: FAIL（`run_once` 未定义）。

- [ ] **Step 3：实现 run_once**

编辑 `scripts/verify_reference_video_sdks.py`，添加：

```python
import time
from lib.video_backends.base import VideoBackend, VideoGenerationRequest
from scripts.fixtures.reference_video.generate_fixtures import generate_color_refs


DEFAULT_PROMPT_SINGLE = "A cinematic establishing shot of [图1]."
DEFAULT_PROMPT_MULTI = (
    "Shot 1 (3s): medium shot of [图1] walking into the room.\n"
    "Shot 2 (5s): close-up of [图1] reacting to [图2]."
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
```

- [ ] **Step 4：运行测试确认通过**

```bash
uv run pytest tests/scripts/test_verify_reference_video_sdks.py -v
```

Expected: 10 PASS（含前面的）。

- [ ] **Step 5：Commit**

```bash
git add scripts/verify_reference_video_sdks.py tests/scripts/test_verify_reference_video_sdks.py
git commit -m "feat(sdk-verify): add run_once orchestrator with Fake backend tests"
```

---

## Task 5：Backend 解析（读配置 → 实例化真实 backend）

**Files:**
- Modify: `scripts/verify_reference_video_sdks.py`
- Test: `tests/scripts/test_verify_reference_video_sdks.py`

本任务把 `Provider` 绑定到具体后端工厂。设计：`resolve_backend(provider, refs)` 读配置 + 返回 backend 实例；当 `refs > max_reference_images` 时 clamp 并在 `RunResult.note` 标注。

- [ ] **Step 1：写失败测试（resolve_backend 的能力检查）**

追加到 `tests/scripts/test_verify_reference_video_sdks.py`：

```python
import pytest

from scripts.verify_reference_video_sdks import (
    Provider,
    clamp_refs_for_backend,
)
from lib.video_backends.base import VideoCapabilities


def test_clamp_refs_respects_backend_max():
    caps = VideoCapabilities(reference_images=True, max_reference_images=3)
    clamped, note = clamp_refs_for_backend(requested=7, caps=caps)
    assert clamped == 3
    assert "clamped" in note.lower()


def test_clamp_refs_under_limit_passthrough():
    caps = VideoCapabilities(reference_images=True, max_reference_images=9)
    clamped, note = clamp_refs_for_backend(requested=3, caps=caps)
    assert clamped == 3
    assert note == ""


def test_clamp_refs_backend_without_reference_support():
    caps = VideoCapabilities(reference_images=False, max_reference_images=0)
    with pytest.raises(ValueError, match="does not support reference_images"):
        clamp_refs_for_backend(requested=1, caps=caps)
```

- [ ] **Step 2：运行测试确认失败**

```bash
uv run pytest tests/scripts/test_verify_reference_video_sdks.py::test_clamp_refs_respects_backend_max -v
```

Expected: FAIL。

- [ ] **Step 3：实现 clamp_refs_for_backend**

编辑 `scripts/verify_reference_video_sdks.py`：

```python
from lib.video_backends.base import VideoCapabilities


def clamp_refs_for_backend(*, requested: int, caps: VideoCapabilities) -> tuple[int, str]:
    if not caps.reference_images:
        raise ValueError("Backend does not support reference_images")
    if requested <= caps.max_reference_images:
        return requested, ""
    note = f"clamped {requested} → {caps.max_reference_images} (backend max)"
    return caps.max_reference_images, note
```

- [ ] **Step 4：运行测试确认通过**

```bash
uv run pytest tests/scripts/test_verify_reference_video_sdks.py -v
```

Expected: 13 PASS。

- [ ] **Step 5：加 `resolve_backend` + 集成测试（skip by default）**

追加：

```python
import os
from typing import Callable

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
        _register_factory(Provider.ARK, lambda: ArkVideoBackend.from_env())
    except Exception:  # noqa: BLE001
        pass
    try:
        from lib.video_backends.grok import GrokVideoBackend
        _register_factory(Provider.GROK, lambda: GrokVideoBackend.from_env())
    except Exception:
        pass
    try:
        from lib.video_backends.gemini import GeminiVideoBackend
        _register_factory(Provider.VEO, lambda: GeminiVideoBackend.from_env())
    except Exception:
        pass
    try:
        from lib.video_backends.openai import OpenAIVideoBackend
        _register_factory(Provider.SORA, lambda: OpenAIVideoBackend.from_env())
    except Exception:
        pass
```

（注意：`ArkVideoBackend.from_env()` 等工厂方法需核实真实名字——若不是 `from_env`，要用现有 backend 的构造方式。实施时先跑一次 `uv run python -c "from lib.video_backends.ark import ArkVideoBackend; help(ArkVideoBackend)"` 确认。）

追加测试（确认注册表可用）：

```python
def test_lazy_register_factories_smoke():
    # 每家 try/except 容错；至少不应抛出异常
    from scripts.verify_reference_video_sdks import _lazy_register_factories
    _lazy_register_factories()
```

- [ ] **Step 6：确认测试通过**

```bash
uv run pytest tests/scripts/test_verify_reference_video_sdks.py -v
```

Expected: 14 PASS。

- [ ] **Step 7：Commit**

```bash
git add scripts/verify_reference_video_sdks.py tests/scripts/test_verify_reference_video_sdks.py
git commit -m "feat(sdk-verify): add clamp_refs and lazy backend resolver"
```

---

## Task 6：main() 连通 + 报告落盘

**Files:**
- Modify: `scripts/verify_reference_video_sdks.py`
- Test: `tests/scripts/test_verify_reference_video_sdks.py`

- [ ] **Step 1：写失败测试（整体流程，用 Fake）**

追加到 `tests/scripts/test_verify_reference_video_sdks.py`：

```python
from pathlib import Path

import pytest

import scripts.verify_reference_video_sdks as mod


@pytest.mark.asyncio
async def test_run_with_backend_writes_report(tmp_path: Path, monkeypatch):
    fake = _FakeBackend()
    report_dir = tmp_path / "reports"
    work_dir = tmp_path / "work"

    monkeypatch.setattr(mod, "resolve_backend", lambda p: fake)

    code = await mod.run_with_backend(
        provider=mod.Provider.ARK,
        refs=3,
        duration=5,
        multi_shot=True,
        report_dir=report_dir,
        work_dir=work_dir,
    )
    assert code == 0
    reports = list(report_dir.glob("reference-video-sdks-*.md"))
    assert len(reports) == 1
    content = reports[0].read_text(encoding="utf-8")
    assert "| ark |" in content
    assert "PASS" in content
```

- [ ] **Step 2：运行测试确认失败**

```bash
uv run pytest tests/scripts/test_verify_reference_video_sdks.py::test_run_with_backend_writes_report -v
```

Expected: FAIL（`run_with_backend` 未定义）。

- [ ] **Step 3：实现 run_with_backend + main 接线**

编辑 `scripts/verify_reference_video_sdks.py`：

```python
import asyncio
from datetime import date


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
        existing_rows = [ln for ln in existing if ln.startswith("| ") and "Provider" not in ln and "---" not in ln]
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
    return asyncio.run(run_with_backend(
        provider=args.provider,
        refs=args.refs,
        duration=args.duration,
        multi_shot=args.multi_shot,
        report_dir=args.report_dir,
        work_dir=work_dir,
    ))
```

并删除旧的占位 `main`（原 Step 2 写的那个）。

- [ ] **Step 4：运行测试确认通过**

```bash
uv run pytest tests/scripts/test_verify_reference_video_sdks.py -v
```

Expected: 15 PASS。

- [ ] **Step 5：占位 `docs/verification-reports/` + Commit**

```bash
mkdir -p docs/verification-reports
touch docs/verification-reports/.gitkeep
git add scripts/verify_reference_video_sdks.py tests/scripts/test_verify_reference_video_sdks.py docs/verification-reports/.gitkeep
git commit -m "feat(sdk-verify): wire main() end-to-end with append-mode report"
```

---

## Task 7：手动跑一次（非 CI 验证）+ 更新 spec 能力矩阵

**Files:**
- Modify: `docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md`（附录 B）
- Create: `docs/verification-reports/reference-video-sdks-YYYY-MM-DD.md`（脚本产出）

⚠️ **此任务需要真实 API Key + 网络**。在 CI/自动化环境中跳过；由开发者手动跑。

- [ ] **Step 1：Ark Seedance 2.0 基础验证**

```bash
uv run python scripts/verify_reference_video_sdks.py --provider ark --refs 3 --duration 5
uv run python scripts/verify_reference_video_sdks.py --provider ark --refs 9 --duration 10 --multi-shot
```

Expected：两次 PASS；报告表格新增两行。

- [ ] **Step 2：Ark Seedance 2.0 fast（如 backend 支持切换模型）**

需确认 `ArkVideoBackend` 是否可通过环境变量/构造参数切 fast 模型。若可，跑同样的组合并在报告 note 标 "fast mode"。

- [ ] **Step 3：Grok + Veo + Sora 依次验证**

```bash
uv run python scripts/verify_reference_video_sdks.py --provider grok --refs 7 --duration 5 --multi-shot
uv run python scripts/verify_reference_video_sdks.py --provider veo --refs 3 --duration 8
uv run python scripts/verify_reference_video_sdks.py --provider sora --refs 3 --duration 5
uv run python scripts/verify_reference_video_sdks.py --provider sora --refs 1 --duration 5  # 单图降级验证
```

Expected：报告表格总计 ≥ 5 行。记录每家 PASS/FAIL + elapsed + bytes。

- [ ] **Step 4：回填 spec 附录 B**

编辑 `docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md` 附录 B，用实际验证值替换"待验证"：

```markdown
| 供应商 | 最大参考图 | 最大时长 | multi-shot 可靠性 | generate_audio | 备注 |
|---|---|---|---|---|---|
| Ark Seedance 2.0 | 9 | 15s | 已验证（real） | ✅ | 首推 |
| Ark Seedance 2.0 fast | 9 | 15s | 已验证（real） | ✅ | fast 模式约快 XX% |
| Grok grok-imagine-video | 7 | YYs | 已验证 / 失败 | ✅（默认） | 请求体 ZZ KB；gRPC 上限 YY MB |
| Gemini Veo | 3 | 8s | 受限（实测最多 N shot） | ✅（Vertex） | clamp duration 至 8s |
| OpenAI Sora | X | 12s | 单图 / 多图 | - | 实测结论 |
```

- [ ] **Step 5：Commit**

```bash
git add docs/verification-reports/ docs/superpowers/specs/2026-04-15-reference-to-video-mode-design.md
git commit -m "docs(sdk-verify): fill provider capability matrix with real verification results"
```

---

## Task 8：PR 收尾 + 自检

- [ ] **Step 1：跑全量 lint + 测试**

```bash
uv run ruff check scripts/verify_reference_video_sdks.py scripts/fixtures/reference_video/ tests/scripts/
uv run ruff format scripts/verify_reference_video_sdks.py scripts/fixtures/reference_video/ tests/scripts/
uv run pytest tests/scripts/ --cov=scripts.verify_reference_video_sdks --cov=scripts.fixtures.reference_video --cov-report=term-missing
```

Expected：lint 干净；覆盖率 ≥ 90%（主要路径全覆盖，真实 backend 分支在 CI 跳过）。

- [ ] **Step 2：更新 roadmap 里 PR1 状态**

编辑 `docs/superpowers/plans/2026-04-17-reference-to-video-roadmap.md`，在"里程碑追踪"章节勾选 `- [x] PR1 合并`（合并时再勾）。

- [ ] **Step 3：开 PR**

```bash
git push -u origin feature/seedance2-reference-to-video
gh pr create --title "feat(sdk-verify): reference-to-video SDK verifier + capability matrix" --body "..."
```

PR 描述模板：

```markdown
## Summary
- 新增 `scripts/verify_reference_video_sdks.py`：CLI 对 Ark/Grok/Veo/Sora 的 reference_images 模式做真实调用
- 新增 `scripts/fixtures/reference_video/generate_fixtures.py`：自生成纯色 PNG，避免提交二进制
- 填充 Spec 附录 B 的能力矩阵

## Test plan
- [ ] `uv run pytest tests/scripts/ -v` 全绿
- [ ] 手动跑 `--provider ark --refs 9 --multi-shot` 产出报告
- [ ] Spec 附录 B 已回填

## Out of scope
- 后端/前端/Agent 改动留给 PR2-7
```

---

## Self-Review

1. **Spec 覆盖**：
   - §8.1 验证脚本 ✅（Task 2-6）
   - 附录 B 能力矩阵 ✅（Task 7）
2. **Placeholder scan**：
   - Task 5 提到"需核实真实名字 `from_env`"——这是已知的运行时待确认点，已加清晰的 verify 命令；不是 placeholder
3. **Type 一致性**：
   - `RunResult` 字段在 Task 3 定义后，Task 4/6 复用未改名
   - `Provider` 枚举从 Task 2 起稳定
   - `clamp_refs_for_backend` 签名从 Task 5 起稳定

## 验收清单

- [ ] 8 个 task 全部 commit
- [ ] `uv run pytest tests/scripts/ -v` 全绿
- [ ] 覆盖率 ≥ 90%
- [ ] Spec 附录 B 已填真实数据
- [ ] 至少 3 家供应商（Ark + Grok + Veo）有 PASS 结果
- [ ] PR 已开
