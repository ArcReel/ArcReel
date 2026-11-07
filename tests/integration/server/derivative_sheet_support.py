"""Shared project fixtures/outbound decoders for character-derivative image-task cases.

Generation assertions target the actual image-provider request, so use a real path: real ``ProjectManager`` +
real ``MediaGenerator`` + real image backend. Only the accounting ledger becomes a database-free substitute;
respx intercepts outbound traffic at the transport layer.
"""

from __future__ import annotations

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from lib.artifacts.artifact_manifest import ArtifactKey
from lib.generation.media_generator import MediaGenerator
from lib.project.asset_types import DERIVATIVES_FIELD
from lib.project.project_manager import ProjectManager
from tests.fakes import FakeConfigResolver, use_generation_context, use_project_manager
from tests.integration.server.services.tasks.generation_tasks_support import register_stale_visual_claim

#: Recognizable base-image contents: two solid-color halves, still identifiable by pixels after compression.
OWNER_SHEET_SIZE = (96, 48)
OWNER_LEFT_RGB = (220, 30, 40)
OWNER_RIGHT_RGB = (30, 60, 220)

#: Provider-returned image contents intentionally differ from the base image to distinguish sent and received images.
RESULT_IMAGE_RGB = (20, 200, 90)

#: DashScope multimodal endpoint and returned artifact URL (respx answers both at the transport layer).
GENERATION_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
RESULT_URL = "https://dashscope.example/out.png"


def write_two_tone_png(path: Path, *, left: tuple[int, int, int], right: tuple[int, int, int]) -> bytes:
    """Write a two-color left/right PNG and return its bytes."""
    width, height = OWNER_SHEET_SIZE
    image = Image.new("RGB", OWNER_SHEET_SIZE, left)
    for x in range(width // 2, width):
        for y in range(height):
            image.putpixel((x, y), right)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")
    return path.read_bytes()


def solid_png_bytes(rgb: tuple[int, int, int], size: tuple[int, int] = (32, 32)) -> bytes:
    """Return solid-color PNG bytes for the provider response."""
    buffer = BytesIO()
    Image.new("RGB", size, rgb).save(buffer, format="PNG")
    return buffer.getvalue()


def decode_data_url_image(data_url: str) -> Image.Image:
    """Decode request-body ``data:image/...;base64,`` into an image for pixel comparisons."""
    _header, _, encoded = data_url.partition(",")
    return Image.open(BytesIO(base64.b64decode(encoded)))


def close_to(pixel: tuple[int, ...], expected: tuple[int, int, int], *, tolerance: int = 24) -> bool:
    """Compare per-channel distances between lossy-compressed pixels and expected colors."""
    return all(abs(int(actual) - int(want)) <= tolerance for actual, want in zip(pixel[:3], expected, strict=True))


def seed_derivative_project(
    tmp_path: Path,
    *,
    owner: str = "阿岚",
    derivative: str = "战斗装",
    description: str = "换上黑色重甲",
    with_owner_sheet: bool = True,
) -> tuple[ProjectManager, Path]:
    """Create a real project with one character + one derivative registration; return manager and project directory.

    With ``with_owner_sheet`` false, the owner lacks both image and manifest registration, precisely
    the state generation should reject.
    """
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.add_character("demo", owner, "少女")

    def _register(entry: dict[str, Any]) -> None:
        entry.setdefault(DERIVATIVES_FIELD, {})[derivative] = {"description": description, "character_sheet": ""}

    pm.update_asset_entry("character", "demo", owner, _register)

    project_path = pm.get_project_path("demo")
    if with_owner_sheet:
        sheet_path = f"characters/{owner}.png"
        write_two_tone_png(project_path / sheet_path, left=OWNER_LEFT_RGB, right=OWNER_RIGHT_RGB)
        pm.update_project_character_sheet("demo", owner, sheet_path)
        register_stale_visual_claim(project_path, ArtifactKey.asset_sheet("character", owner), sheet_path)
    return pm, project_path


def read_project_json(project_path: Path) -> dict[str, Any]:
    return json.loads((project_path / "project.json").read_text(encoding="utf-8"))


class _FakeLedgerCall:
    def __init__(self, call_id: int):
        self.call_id = call_id
        self.declared = False
        self.result: Any = None

    def success(self, result: Any) -> None:
        self.declared = True
        self.result = result


class _FakeLedger:
    """Accounting-bracket substitute matching real-ledger semantics without database writes.

    Cancellation propagates; exceptions record failed then re-raise; normal exit without declared success
    raises. Generation depends on all three; omitting them moves failures elsewhere.
    """

    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.outcomes: list[dict[str, Any]] = []
        self._n = 0

    async def record_provider_response(self, *, call_id: int, body: Any) -> None:
        del call_id, body

    @asynccontextmanager
    async def record(self, **kwargs: Any):
        self._n += 1
        self.started.append(kwargs)
        call = _FakeLedgerCall(self._n)
        try:
            yield call
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.outcomes.append({"status": "failed", "error": exc})
            raise
        else:
            if not call.declared:
                raise RuntimeError("ledger.record exited without success()")
            self.outcomes.append({"status": "success", "result": call.result})


def build_generator(project_path: Path, backend: Any, *, provider_id: str = "dashscope") -> MediaGenerator:
    """Real ``MediaGenerator`` (real version manager + image backend) with database-free accounting substitute."""
    generator = MediaGenerator(
        project_path,
        image_backend=backend,
        image_provider_id=provider_id,
        config_resolver=FakeConfigResolver(),
    )
    generator.ledger = _FakeLedger()
    return generator


def wire_derivative_generation(monkeypatch: Any, pm: ProjectManager, generator: MediaGenerator) -> None:
    """Connect derivative-image task project management/generation context to the case's own real project/generator."""
    from server.services.tasks import derivative_sheet_tasks
    from tests.integration.server.services.tasks.generation_tasks_support import fake_resolve_ctx

    monkeypatch.setattr(derivative_sheet_tasks, "get_project_manager", lambda: pm)
    monkeypatch.setattr(derivative_sheet_tasks, "resolve_generation_context", fake_resolve_ctx(generator))
    use_generation_context(monkeypatch, fake_resolve_ctx(generator))


def run_derivative_generation(
    pm: ProjectManager,
    project_path: Path,
    monkeypatch: Any,
    *,
    result_rgb: tuple[int, int, int] = RESULT_IMAGE_RGB,
    owner: str = "阿岚",
    derivative: str = "战斗装",
) -> bytes:
    """Run real derivative-image generation (respx answers outbound requests) and return persisted image bytes."""
    from lib.backends.image_backends.dashscope import DashScopeImageBackend
    from server.services.tasks import derivative_sheet_tasks
    from tests.http_capture import capture_http

    generator = build_generator(project_path, DashScopeImageBackend(api_key="sk", model="qwen-image-2.0"))
    use_project_manager(monkeypatch, pm)
    wire_derivative_generation(monkeypatch, pm, generator)

    result_bytes = solid_png_bytes(result_rgb)
    with capture_http() as router:
        router.post(GENERATION_URL).mock(
            return_value=httpx.Response(
                200, json={"output": {"choices": [{"message": {"content": [{"image": RESULT_URL}]}}]}}
            )
        )
        router.get(RESULT_URL).mock(return_value=httpx.Response(200, content=result_bytes))
        asyncio.run(
            derivative_sheet_tasks.execute_character_derivative_task(
                "demo", f"{owner}/{derivative}", {}, task_id="task-1"
            )
        )
    return result_bytes
