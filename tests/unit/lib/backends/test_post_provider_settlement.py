"""供应商返回之后的落盘不被取消打断（docs/standards/backend.md「供应商返回之后的落账段不可中断」）。

公共落盘助手与各后端共用的落盘函数：取消落在写盘期间时，已计费的产物照常写完，调用方后续的
转码与产物提交照常执行。各后端自己的入口（Gemini 图片、OpenAI 音频与视频、DashScope 音频）
的同类用例在各自的测试文件里。
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from arcreel_market_core.video_backend_contract import ProviderJobStatus
from lib.backends import artifact_download_guard
from lib.backends.artifact_download_guard import artifact_http_client, stream_body_to_file
from lib.backends.image_backends import agnes, minimax
from lib.backends.image_backends.base import save_image_from_response_item
from lib.custom_provider.declarative_image_backend import ImageJobState, _write_inline_image
from tests.fakes import PNG_BYTES
from tests.http_capture import capture_http
from tests.unit.lib.backends.write_cancel_gate import GatedWritePath, WriteGate, cancel_during_write

PNG_B64 = base64.b64encode(PNG_BYTES).decode("ascii")


def _gated_output(tmp_path: Path, gate: WriteGate) -> GatedWritePath:
    return GatedWritePath(tmp_path / "out.png").attach(gate)


def _written(tmp_path: Path) -> bytes:
    return (tmp_path / "out.png").read_bytes()


async def test_response_item_b64_write_settles_through_cancellation(tmp_path: Path):
    gate = WriteGate()
    output = _gated_output(tmp_path, gate)
    item = SimpleNamespace(b64_json=PNG_B64, url=None)

    steps = await cancel_during_write(lambda: save_image_from_response_item(item, output), gate)

    assert steps == ["follow-up"]
    assert _written(tmp_path) == PNG_BYTES


@pytest.mark.parametrize("module", [agnes, minimax], ids=["agnes", "minimax"])
async def test_inline_b64_image_write_settles_through_cancellation(module, tmp_path: Path):
    gate = WriteGate()
    output = _gated_output(tmp_path, gate)

    steps = await cancel_during_write(lambda: module._write_base64_image(PNG_B64, output), gate)

    assert steps == ["follow-up"]
    assert _written(tmp_path) == PNG_BYTES


async def test_declarative_inline_image_write_settles_through_cancellation(tmp_path: Path):
    gate = WriteGate()
    output = _gated_output(tmp_path, gate)
    state = ImageJobState(
        body={},
        status=ProviderJobStatus.SUCCEEDED,
        provider_status=None,
        error=None,
        result_id=None,
        image_url=None,
        image_b64=PNG_B64,
    )

    steps = await cancel_during_write(lambda: _write_inline_image(state, output), gate)

    assert steps == ["follow-up"]
    assert _written(tmp_path) == PNG_BYTES


async def test_downloaded_artifact_rename_settles_through_cancellation(tmp_path: Path, monkeypatch):
    # 下载的产物成形在 .part 改名为成品这一步：取消落在改名期间时，成品就位、取消随之消失，后续步骤继续
    gate = WriteGate()
    real_replace = os.replace

    def gated_replace(source, destination):
        gate.block()
        real_replace(source, destination)

    monkeypatch.setattr(artifact_download_guard.os, "replace", gated_replace)
    output = tmp_path / "a.png"

    async def download() -> None:
        with capture_http() as router:
            router.get("https://cdn.example/a.png").mock(return_value=httpx.Response(200, content=PNG_BYTES))
            async with (
                artifact_http_client(resolver=_resolver("93.184.216.34")) as client,
                client.stream("GET", "https://cdn.example/a.png") as resp,
            ):
                await stream_body_to_file(resp, output, max_bytes=1024)

    steps = await cancel_during_write(download, gate)

    assert steps == ["follow-up"]
    assert output.read_bytes() == PNG_BYTES
    assert not output.with_name(f"{output.name}.part").exists()


def _resolver(address: str):
    async def resolve(host: str, port: int):
        return (address,)

    return resolve
