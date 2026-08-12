"""Assistant image attachment request-size contract."""

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from server.routers import assistant
from tests.test_assistant_routes import PREFIX, _build_client

pytestmark = pytest.mark.unit


def _image(data: str) -> dict[str, str]:
    return {"data": data, "media_type": "image/png"}


def test_send_request_accepts_single_image_at_base64_character_limit():
    request = assistant.SendRequest(images=[_image("A" * assistant.MAX_IMAGE_BASE64_CHARS)])

    assert len(request.images[0].data) == 6_990_508


def test_send_request_rejects_single_image_above_base64_character_limit():
    with pytest.raises(ValidationError) as exc_info:
        assistant.SendRequest(images=[_image("A" * (assistant.MAX_IMAGE_BASE64_CHARS + 1))])

    assert exc_info.value.errors()[0]["type"] == "assistant_image_too_large"


def test_rewrite_request_accepts_five_images_at_total_base64_character_limit():
    request = assistant.RewriteRequest(
        anchor_entry_uuid="entry-1",
        images=[_image("A" * assistant.MAX_IMAGE_BASE64_CHARS) for _ in range(assistant.MAX_IMAGES_PER_REQUEST)],
    )

    assert sum(len(image.data) for image in request.images) == 34_952_540


def test_rewrite_request_rejects_images_above_total_base64_character_limit():
    images = [_image("A" * assistant.MAX_IMAGE_BASE64_CHARS) for _ in range(assistant.MAX_IMAGES_PER_REQUEST)]
    images[-1]["data"] += "A"

    with pytest.raises(ValidationError) as exc_info:
        assistant.RewriteRequest(anchor_entry_uuid="entry-1", images=images)

    assert exc_info.value.errors()[0]["type"] == "assistant_images_total_too_large"


@pytest.mark.parametrize(
    ("path", "body", "service_method"),
    [
        (f"{PREFIX}/sessions/send", {"content": "hello"}, "send_or_create"),
        (
            f"{PREFIX}/sessions/session-1/rewrite",
            {"anchor_entry_uuid": "entry-1", "content": "hello"},
            "rewrite_message",
        ),
    ],
)
@pytest.mark.parametrize(
    ("locale", "expected_detail"),
    [
        ("zh", "每张图片的原图大小不能超过 5 MB"),
        ("en", "Each original image must be no larger than 5 MB"),
        ("vi", "Mỗi ảnh gốc không được lớn hơn 5 MB"),
    ],
)
def test_send_and_rewrite_return_same_localized_422_for_oversized_image(
    path: str,
    body: dict[str, object],
    service_method: str,
    locale: str,
    expected_detail: str,
):
    body["images"] = [_image("A" * (assistant.MAX_IMAGE_BASE64_CHARS + 1))]

    with patch.object(assistant.assistant_service, service_method, new=AsyncMock()) as service_call:
        with _build_client() as client:
            response = client.post(path, json=body, headers={"Accept-Language": locale})

    assert response.status_code == 422
    assert response.json() == {"detail": expected_detail}
    service_call.assert_not_awaited()


@pytest.mark.parametrize(
    ("path", "body", "service_method"),
    [
        (f"{PREFIX}/sessions/send", {"content": "hello"}, "send_or_create"),
        (
            f"{PREFIX}/sessions/session-1/rewrite",
            {"anchor_entry_uuid": "entry-1", "content": "hello"},
            "rewrite_message",
        ),
    ],
)
def test_send_and_rewrite_return_same_localized_422_for_oversized_image_total(
    path: str,
    body: dict[str, object],
    service_method: str,
):
    images = [_image("A" * assistant.MAX_IMAGE_BASE64_CHARS) for _ in range(assistant.MAX_IMAGES_PER_REQUEST)]
    images[-1]["data"] += "A"
    body["images"] = images

    with patch.object(assistant.assistant_service, service_method, new=AsyncMock()) as service_call:
        with _build_client() as client:
            response = client.post(path, json=body, headers={"Accept-Language": "en"})

    assert response.status_code == 422
    assert response.json() == {"detail": "The original images must be no larger than 25 MB in total"}
    service_call.assert_not_awaited()
