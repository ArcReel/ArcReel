"""One submitted ComfyUI execution: outbound destination validation on remote interruption.

Backend cases cover polling, retrieval, and interruption through their respective channels; this file covers otherwise unreachable cases.
Generation and interruption share a base URL: a denied network blocks submission first, preventing interruption from being reached.
"""

from __future__ import annotations

import httpx

from lib.custom_provider.comfyui.comfyui_client import ComfyuiClient
from lib.custom_provider.comfyui.comfyui_execution import ComfyuiExecution
from tests.factories import comfyui_endpoint_definition
from tests.http_capture import capture_http

METADATA_BASE_URL = "http://169.254.169.254"


async def test_a_stop_toward_a_metadata_address_is_never_sent():
    """Interruption shares generation destination validation; best-effort denial logs only and does not propagate an exception."""
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
