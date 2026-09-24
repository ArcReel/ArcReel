"""一次已提交的 ComfyUI 执行：叫停远端那一路的出站目的地校验。

轮询、取件与叫停的行为由两个 backend 的用例经各自通道覆盖；这里只放经 backend 触发不到的格子——
生成路径与叫停共用同一个 base URL，base URL 落在被拒网段时提交就先被拦下，走不到叫停。
"""

from __future__ import annotations

import httpx

from lib.custom_provider.comfyui.comfyui_client import ComfyuiClient
from lib.custom_provider.comfyui.comfyui_execution import ComfyuiExecution
from tests.factories import comfyui_endpoint_definition
from tests.http_capture import capture_http

METADATA_BASE_URL = "http://169.254.169.254"


async def test_a_stop_toward_a_metadata_address_is_never_sent():
    """叫停与生成路径同一道目的地校验；被拒时按 best-effort 口径只记日志，不向外抛。"""
    definition = comfyui_endpoint_definition()
    execution = ComfyuiExecution(
        client=ComfyuiClient(base_url=METADATA_BASE_URL, api_key="", definition=definition),
        definition=definition,
        media_type="video",
    )
    with capture_http() as router:
        version = router.get(f"{METADATA_BASE_URL}/system_stats").mock(
            return_value=httpx.Response(200, json={"system": {"comfyui_version": "0.26.0"}})
        )
        cancel = router.post(f"{METADATA_BASE_URL}/api/jobs/p-1/cancel").mock(return_value=httpx.Response(200, json={}))
        queue = router.get(f"{METADATA_BASE_URL}/queue").mock(return_value=httpx.Response(200, json={}))

        await execution.stop_remote("p-1")

    assert version.call_count == 0
    assert cancel.call_count == 0
    assert queue.call_count == 0
