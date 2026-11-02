"""ComfyUI 专用 httpx 客户端：按目标 host 决定是否绕系统代理，且这个判定只落在传入的 base_url。

ComfyUI 走内网 / Tailscale（如 ``http://192.168.5.149:8188``），httpx 默认 ``trust_env=True`` 会读
macOS 系统代理把内网请求劫持成 502（本次 ``comfyui_upload_failed`` 的根因）。本文件钉住：封装把
``bypass_proxy_for`` 等 kwargs 原样透传给 ``artifact_http_client``；内网 base_url 时该 host 的 transport
不走代理、公网 base_url 时仍走代理；以及一条护栏——走公网的非 ComfyUI 供应商（这里取 Kling）对其
公网 host 的 transport 仍走系统代理。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from lib.backends.video_backends.kling import KlingVideoBackend
from lib.custom_provider.comfyui import comfyui_http_client as module
from tests.fakes import bounded_poll_clock, captured_provider_job_ids
from tests.http_capture import capture_http

_KLING_BASE_URL = "https://api-beijing.klingai.com/v1"
_DOWNLOAD_URL = "https://x/final.mp4"


def _proxy_url(transport: object) -> object | None:
    return getattr(getattr(transport, "_pool", None), "_proxy_url", None)


class TestWrapper:
    """封装层：参数原样透传，内网 / 公网 base_url 分别直连 / 走代理。"""

    def test_it_passes_kwargs_through(self):
        captured: dict[str, Any] = {}

        def _fake(**kwargs: Any) -> Any:
            captured.update(kwargs)
            return "client"

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(module, "artifact_http_client", _fake)
            module.comfyui_http_client(timeout=1, follow_redirects=True, bypass_proxy_for="http://192.168.5.149:8188")

        # 不再硬写 trust_env：代理绕过改由 bypass_proxy_for 驱动。
        assert "trust_env" not in captured
        assert captured["timeout"] == 1
        assert captured["follow_redirects"] is True
        assert captured["bypass_proxy_for"] == "http://192.168.5.149:8188"

    def test_private_base_url_bypasses_proxy(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
        client = module.comfyui_http_client(bypass_proxy_for="http://192.168.5.149:8188")
        # 内网 host 的 transport 不走代理（等价于原先对该 host 的 trust_env=False）。
        assert _proxy_url(client._transport_for_url(httpx.URL("http://192.168.5.149:8188/view"))) is None

    def test_public_base_url_keeps_proxy(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
        client = module.comfyui_http_client(bypass_proxy_for="https://comfy.example.com")
        # 万一 ComfyUI 挂在公网：仍按系统代理出网。
        assert _proxy_url(client._transport_for_url(httpx.URL("https://comfy.example.com/view"))) is not None


class TestOtherProvidersAreUntouched:
    """护栏：走公网的非 ComfyUI 供应商（取 Kling）对其公网 host 的 transport 仍走系统代理。"""

    @staticmethod
    def _resp(json_body: dict[str, Any], status_code: int = 200) -> httpx.Response:
        return httpx.Response(status_code, json=json_body)

    def _submit(self, task_id: str = "t-1") -> dict[str, Any]:
        return {"code": 0, "message": "SUCCEED", "data": {"task_id": task_id, "task_status": "submitted"}}

    def _query(self, status: str, url: str = "") -> dict[str, Any]:
        data: dict[str, Any] = {"task_id": "t-1", "task_status": status, "task_status_msg": ""}
        if url:
            data["task_result"] = {"videos": [{"id": "v1", "url": url}]}
        return {"code": 0, "message": "SUCCEED", "data": data}

    def _request(self, tmp_path: Path):
        from arcreel_market_core.video_backend_contract import VideoGenerationRequest

        return VideoGenerationRequest(
            prompt="a cat walking",
            output_path=tmp_path / "out.mp4",
            duration_seconds=5,
            aspect_ratio="9:16",
        )

    async def test_kling_client_keeps_proxy_for_its_public_host(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
        from lib.backends.video_backends import kling

        clients: list[httpx.AsyncClient] = []
        with (
            capture_http() as router,
            bounded_poll_clock(),
            captured_provider_job_ids(),
            pytest.MonkeyPatch.context() as patch,
        ):
            real = kling.artifact_http_client

            def _record(**kwargs: Any) -> Any:
                client = real(**kwargs)
                clients.append(client)
                return client

            patch.setattr(kling, "artifact_http_client", _record)
            router.post(f"{_KLING_BASE_URL}/videos/text2video").mock(return_value=self._resp(self._submit("task-9")))
            router.get(f"{_KLING_BASE_URL}/videos/text2video/task-9").mock(
                return_value=self._resp(self._query("succeed", url=_DOWNLOAD_URL))
            )
            router.get(_DOWNLOAD_URL).mock(return_value=httpx.Response(200, content=b""))

            backend = KlingVideoBackend(auth_mode="bearer", api_key="static-key")
            await backend.generate(self._request(tmp_path))

        assert clients, "Kling 的 artifact_http_client 应被调用过"
        # 公网供应商对其公网 host 的 transport 仍走系统代理：这一改动只对内网目标直连。
        for client in clients:
            transport = client._transport_for_url(httpx.URL(f"{_KLING_BASE_URL}/videos/text2video"))
            assert _proxy_url(transport) is not None
