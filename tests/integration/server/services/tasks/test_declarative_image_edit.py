"""图片编辑选用声明了图生图的声明式图片端点：编辑指令随 prompt 下发，当前图作为参考图，产出新版本。

真 ``ProjectManager`` + 真 ``MediaGenerator`` + 由端点投影装出的声明式图片 backend，出站请求由
respx 在 transport 层应答。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import httpx

from lib.custom_provider.endpoints import declarative_endpoint_spec
from server.services.tasks import formal_image_commit, image_edit_tasks
from server.services.tasks.image_edit_tasks import execute_image_edit_task
from tests.factories import image_endpoint_definition
from tests.fakes import bounded_poll_clock
from tests.http_capture import capture_http, only_request, request_json
from tests.integration.server.derivative_sheet_support import (
    OWNER_LEFT_RGB,
    OWNER_RIGHT_RGB,
    OWNER_SHEET_SIZE,
    build_generator,
    close_to,
    decode_data_url_image,
    seed_derivative_project,
    solid_png_bytes,
)
from tests.integration.server.services.tasks.generation_tasks_support import fake_resolve_ctx

_RESULT_URL = "https://cdn.test/img/edited.png"


def _image_to_image_backend():
    definition = image_endpoint_definition(
        capabilities={"text_to_image": True, "image_to_image": True, "max_reference_images": 4},
        inputs={"refs": {"source": "reference_images", "encoding": "data_uri"}},
    )
    definition["submit"]["body"]["image_urls"] = [
        {"$each": {"in": "inputs.refs", "as": "image", "item": "{{ image }}"}}
    ]
    spec = declarative_endpoint_spec("ce-7", definition, source="custom")
    provider = SimpleNamespace(provider_id="custom-1", base_url="https://relay.test", api_key="sk")
    return spec.build_backend(cast("Any", provider), "gpt-image-2")


async def test_image_edit_on_a_declarative_image_to_image_endpoint_produces_a_new_version(tmp_path, monkeypatch):
    pm, project_path = seed_derivative_project(tmp_path)
    generator = build_generator(project_path, _image_to_image_backend(), provider_id="custom-1")
    monkeypatch.setattr(image_edit_tasks, "get_project_manager", lambda: pm)
    monkeypatch.setattr(
        formal_image_commit, "resolve_generation_context", fake_resolve_ctx(generator, image_max_reference_images=4)
    )

    edited = solid_png_bytes((240, 120, 20))
    with capture_http() as router, bounded_poll_clock():
        submit = router.post("https://relay.test/v1/images/generations").mock(
            return_value=httpx.Response(200, json={"data": [{"task_id": "task_9"}]})
        )
        router.get("https://relay.test/v1/tasks/task_9").mock(
            return_value=httpx.Response(
                200, json={"data": {"status": "completed", "result": {"images": [{"url": [_RESULT_URL]}]}}}
            )
        )
        router.get(_RESULT_URL).mock(return_value=httpx.Response(200, content=edited))

        result = await execute_image_edit_task(
            "demo", "阿岚", {"resource_type": "character", "prompt": "把头发改成红色"}, task_id="task-1"
        )

    body = request_json(only_request(submit))
    assert body["prompt"] == "把头发改成红色"
    assert len(body["image_urls"]) == 1
    sent = decode_data_url_image(body["image_urls"][0]).convert("RGB")
    width, height = OWNER_SHEET_SIZE
    assert close_to(sent.getpixel((4, height // 2)), OWNER_LEFT_RGB)
    assert close_to(sent.getpixel((width - 4, height // 2)), OWNER_RIGHT_RGB)

    assert result["version"] == 2
    assert (project_path / result["file_path"]).read_bytes() == edited
