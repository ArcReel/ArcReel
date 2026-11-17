"""OpenAI Images 调用通道的显式画质：请求带画质时 ``quality`` 取它，``size`` 只由比例和分辨率档位决定（docs/adr/0104）。

未带画质时的档位映射由 ``test_openai_image_backend.py::test_quality_mapping`` 与 ``test_quality_propagation.py`` 锁住。
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from arcreel_catalog_core import CatalogEntry, ImageFacts
from lib.backends.image_backends.base import ImageGenerationRequest, ImageRequestFacts, ReferenceImage
from lib.backends.image_backends.openai import OpenAIImageBackend
from tests.http_capture import capture_http, only_request, request_form_fields, request_json

_BASES = {"low": 16, "medium": 48, "high": 96}


def _entry() -> CatalogEntry:
    return CatalogEntry(
        maker="openai",
        name="gpt-image-x",
        media_type="image",
        display_name="gpt-image-x",
        facts=ImageFacts(
            text_to_image=True, image_to_image=True, output_token_bases=_BASES, qualities=("low", "medium", "high")
        ),
    )


async def _sent(tmp_path: Path, *, image_size: str | None, quality: str | None, edit: bool) -> dict:
    """真实 SDK 序列化后发出的 ``size`` 与 ``quality``（没发时不在结果里）。"""
    refs: list[ReferenceImage] = []
    if edit:
        ref = tmp_path / "ref.png"
        ref.write_bytes(b"\x89PNG\r\n\x1a\n")
        refs = [ReferenceImage(path=str(ref))]
    with capture_http() as router:
        path = "edits" if edit else "generations"
        route = router.post(f"https://api.openai.com/v1/images/{path}").respond(
            json={"created": 0, "data": [{"b64_json": base64.b64encode(b"img").decode()}]}
        )
        backend = OpenAIImageBackend(api_key="k")
        await backend.generate(
            ImageGenerationRequest(
                prompt="p",
                output_path=tmp_path / "out.png",
                reference_images=refs,
                aspect_ratio="9:16",
                image_size=image_size,
                quality=quality,
            )
        )
        request = only_request(route)
        fields = request_form_fields(request) if edit else request_json(request)
    return {key: fields[key] for key in ("size", "quality") if key in fields}


@pytest.mark.parametrize("edit", [False, True], ids=["generate", "edit"])
class TestExplicitQualityInRequest:
    async def test_quality_follows_the_choice_and_size_follows_the_tier(self, tmp_path: Path, edit: bool) -> None:
        assert await _sent(tmp_path, image_size="1K", quality="low", edit=edit) == {
            "size": "1008x1792",
            "quality": "low",
        }
        # 同一档位换画质，size 不变
        assert await _sent(tmp_path, image_size="1K", quality="high", edit=edit) == {
            "size": "1008x1792",
            "quality": "high",
        }
        # 同一画质换档位，quality 不变
        assert await _sent(tmp_path, image_size="2K", quality="low", edit=edit) == {
            "size": "1440x2560",
            "quality": "low",
        }

    async def test_choice_outside_the_catalog_values_is_sent_verbatim(self, tmp_path: Path, edit: bool) -> None:
        assert (await _sent(tmp_path, image_size="1K", quality="LOW", edit=edit))["quality"] == "LOW"

    async def test_stored_512px_still_reaches_the_request(self, tmp_path: Path, edit: bool) -> None:
        assert await _sent(tmp_path, image_size="512px", quality="high", edit=edit) == {
            "size": "576x1024",
            "quality": "high",
        }

    async def test_choice_is_sent_without_a_tier(self, tmp_path: Path, edit: bool) -> None:
        assert await _sent(tmp_path, image_size=None, quality="medium", edit=edit) == {
            "size": "720x1280",
            "quality": "medium",
        }


class TestExplicitQualityInEstimate:
    @pytest.mark.parametrize(("image_size", "size", "tokens"), [("1K", "1008x1792", 138), ("2K", "1440x2560", 205)])
    def test_low_is_estimated_at_each_tier(self, image_size: str, size: str, tokens: int) -> None:
        usage = OpenAIImageBackend.estimate_usage(
            ImageRequestFacts(
                model="gpt-image-x", aspect_ratio="9:16", image_size=image_size, quality="low", entry=_entry()
            )
        )
        assert usage.dimensions == {"resolution": size, "quality": "low"}
        assert usage.quantities["image_output_tokens"] == tokens

    def test_case_differing_choice_has_no_output_estimate(self) -> None:
        usage = OpenAIImageBackend.estimate_usage(
            ImageRequestFacts(model="gpt-image-x", aspect_ratio="9:16", image_size="1K", quality="LOW", entry=_entry())
        )
        assert usage.dimensions == {"resolution": "1008x1792", "quality": "LOW"}
        assert "image_output_tokens" not in usage.quantities


class TestTierSizesStayWithinTheOfficialPixelRange:
    """1K、2K 在产品比例（9:16、16:9）、存量宫格比例（4:3、3:4）与常见比例下，总像素在 655,360～8,294,400 之间。"""

    @pytest.mark.parametrize("image_size", ["1K", "2K"])
    @pytest.mark.parametrize(
        "aspect_ratio",
        ["9:16", "16:9", "3:4", "4:3", "1:1", "2:3", "3:2", "4:5", "5:4", "9:21", "21:9", "1:2", "2:1", "1:3", "3:1"],
    )
    def test_total_pixels_in_range(self, image_size: str, aspect_ratio: str) -> None:
        usage = OpenAIImageBackend.estimate_usage(
            ImageRequestFacts(model="gpt-image-x", aspect_ratio=aspect_ratio, image_size=image_size)
        )
        size = usage.dimensions["resolution"]
        assert isinstance(size, str)
        width, height = (int(part) for part in size.split("x"))
        assert 655_360 <= width * height <= 8_294_400
        assert width % 16 == 0
        assert height % 16 == 0
        assert max(width, height) <= 3840
