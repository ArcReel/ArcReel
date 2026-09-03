"""角色衍生资产图任务用例共享的项目 fixture 与出站请求解码器。

生成侧的判据落在「发往图像供应商的那次请求」上，因此这里搭的是一条真实通路：真
``ProjectManager`` + 真 ``MediaGenerator`` + 真图像后端，只把记账账本换成不碰数据库的
替身，出站流量交给 respx 在 transport 层拦截。
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

from lib.artifact_manifest import ArtifactKey
from lib.asset_types import DERIVATIVES_FIELD
from lib.media_generator import MediaGenerator
from lib.project_manager import ProjectManager
from tests.fakes import FakeConfigResolver
from tests.integration.server.services.generation_tasks_support import register_stale_visual_claim

#: 本体资产图的可辨识内容：左右两半各一个纯色块，压缩后仍能按像素认出来。
OWNER_SHEET_SIZE = (96, 48)
OWNER_LEFT_RGB = (220, 30, 40)
OWNER_RIGHT_RGB = (30, 60, 220)

#: 供应商回图的内容，与本体图刻意不同，便于区分「送进去的」和「收回来的」。
RESULT_IMAGE_RGB = (20, 200, 90)

#: DashScope 多模态生成端点与它回给的产物地址（respx 在 transport 层应答这两条）。
GENERATION_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
RESULT_URL = "https://dashscope.example/out.png"


def write_two_tone_png(path: Path, *, left: tuple[int, int, int], right: tuple[int, int, int]) -> bytes:
    """写一张左右双色 PNG 并返回它的字节。"""
    width, height = OWNER_SHEET_SIZE
    image = Image.new("RGB", OWNER_SHEET_SIZE, left)
    for x in range(width // 2, width):
        for y in range(height):
            image.putpixel((x, y), right)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PNG")
    return path.read_bytes()


def solid_png_bytes(rgb: tuple[int, int, int], size: tuple[int, int] = (32, 32)) -> bytes:
    """返回一张纯色 PNG 的字节，用作供应商回图。"""
    buffer = BytesIO()
    Image.new("RGB", size, rgb).save(buffer, format="PNG")
    return buffer.getvalue()


def decode_data_url_image(data_url: str) -> Image.Image:
    """把请求体里的 ``data:image/...;base64,`` 解回可按像素比对的图片。"""
    _header, _, encoded = data_url.partition(",")
    return Image.open(BytesIO(base64.b64decode(encoded)))


def close_to(pixel: tuple[int, ...], expected: tuple[int, int, int], *, tolerance: int = 24) -> bool:
    """有损压缩后的像素与期望色的逐通道距离判定。"""
    return all(abs(int(actual) - int(want)) <= tolerance for actual, want in zip(pixel[:3], expected, strict=True))


def seed_derivative_project(
    tmp_path: Path,
    *,
    owner: str = "阿岚",
    derivative: str = "战斗装",
    description: str = "换上黑色重甲",
    with_owner_sheet: bool = True,
) -> tuple[ProjectManager, Path]:
    """建一个只含「一个角色 + 一个衍生登记」的真实项目，返回管理器与项目目录。

    ``with_owner_sheet`` 为假时本体没有资产图，也没有对应的清单登记——那正是生成侧应当
    拒绝的形态。
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


class FakeLedger:
    """记账括号替身：语义与真账本一致，但不落库。

    取消穿透、异常记 failed 后重抛、正常退出未声明成功即报错——生成路径依赖这三条，
    替身不复刻就会把用例的失败挪到别处。
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
    """真 ``MediaGenerator``（真版本管理器 + 真图像后端），账本换成不落库的替身。"""
    generator = MediaGenerator(
        project_path,
        image_backend=backend,
        image_provider_id=provider_id,
        config_resolver=FakeConfigResolver(),
    )
    generator.ledger = FakeLedger()
    return generator


def run_derivative_generation(
    pm: ProjectManager,
    project_path: Path,
    monkeypatch: Any,
    *,
    result_rgb: tuple[int, int, int] = RESULT_IMAGE_RGB,
    owner: str = "阿岚",
    derivative: str = "战斗装",
) -> bytes:
    """跑一次真实的衍生资产图生成（出站请求由 respx 应答），返回落盘的图片字节。"""
    from lib.image_backends.dashscope import DashScopeImageBackend
    from server.services import derivative_sheet_tasks, generation_tasks
    from tests.http_capture import capture_http
    from tests.integration.server.services.generation_tasks_support import fake_resolve_ctx

    generator = build_generator(project_path, DashScopeImageBackend(api_key="sk", model="qwen-image-2.0"))
    monkeypatch.setattr(derivative_sheet_tasks, "get_project_manager", lambda: pm)
    monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
    monkeypatch.setattr(generation_tasks, "resolve_generation_context", fake_resolve_ctx(generator))

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
