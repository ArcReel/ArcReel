"""Tests for ad_product_fidelity."""

from server.services.tasks import generation_tasks
from tests.fakes import use_generation_context, use_project_manager, use_video_thumbnail_extractor
from tests.integration.server.services.tasks.generation_tasks_support import (
    FakeGenerator,
    ad_pm,
    async_return,
    fake_resolve_ctx,
    prepare_files,
    seed_current_storyboard,
    skip_artifact_registration,
)


def _ref_paths(refs: list) -> list:
    return [r["image"] if isinstance(r, dict) else r for r in refs]


class TestAdProductFidelityStoryboard:
    """Binary product-fidelity injection at storyboard level."""

    def _patch(self, monkeypatch, pm, generator):
        use_project_manager(monkeypatch, pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(generator))
        skip_artifact_registration(monkeypatch)

    async def test_product_shot_injects_sheet_then_originals_before_other_sheets(self, tmp_path, monkeypatch):
        """Confirmed product sheets inject multi-angle sheet plus originals with absolute priority over character/scene sheets."""
        project_path = prepare_files(tmp_path)
        (project_path / "products" / "保温杯.png").write_bytes(b"png")
        pm = ad_pm(project_path, with_sheet=True)
        generator = FakeGenerator()
        self._patch(monkeypatch, pm, generator)

        await generation_tasks.execute_storyboard_task(
            "demo", "E1S02", {"script_file": "episode_1.json", "prompt": "商品特写"}
        )

        refs = generator.image_calls[0]["reference_images"]
        paths = _ref_paths(refs)
        # Inject all product references first: sheet then original images, before character/scene sheets.
        assert [path.name for path in paths[:2]] == ["0000-保温杯.png", "0001-保温杯_1.jpg"]
        assert generator.image_reference_bytes[0][:2] == [b"png", b"jpg"]
        # Existing character/scene assembly follows product references normally.
        assert [path.name for path in paths[2:]] == ["0002-Alice.png", "0003-祠堂.png"]
        # Declaration lines number product references by position and demand exact fidelity; images themselves carry no labels.
        prompt = generator.image_calls[0]["prompt"]
        assert prompt.startswith(
            "Style: Anime\nVisual style: cinematic\nReference_Images: '图1, 图2: product reference images; the product in the frame must match exactly; 图3: character reference images; 图4: scene reference images.'\n"
        )
        assert "\n\n商品特写\n\n" in prompt
        assert "保温杯" not in prompt

    async def test_product_shot_without_sheet_injects_originals_directly(self, tmp_path, monkeypatch):
        """Without product sheets, inject originals directly, still first."""
        project_path = prepare_files(tmp_path)
        pm = ad_pm(project_path, with_sheet=False)
        generator = FakeGenerator()
        self._patch(monkeypatch, pm, generator)

        await generation_tasks.execute_storyboard_task(
            "demo", "E1S02", {"script_file": "episode_1.json", "prompt": "商品特写"}
        )

        refs = generator.image_calls[0]["reference_images"]
        paths = _ref_paths(refs)
        assert paths[0].name == "0000-保温杯_1.jpg"
        assert generator.image_reference_bytes[0][0] == b"jpg"
        assert (
            "Reference_Images: '图1: product reference images; the product in the frame must match exactly; "
            in generator.image_calls[0]["prompt"]
        )

    async def test_declaration_only_numbers_products_with_injected_references(self, tmp_path, monkeypatch):
        """Declaration lines number only products with injected reference images; products lacking both sheets/originals use text without consuming positions."""
        project_path = prepare_files(tmp_path)
        pm = ad_pm(project_path, with_sheet=False)
        pm.project["products"]["杯刷"] = {
            "description": "配套杯刷",
            "product_sheet": "",
            "brand": "",
            "reference_images": [],
            "selling_points": [],
        }
        pm.script["shots"][1]["products_in_shot"] = ["保温杯", "杯刷"]
        generator = FakeGenerator()
        self._patch(monkeypatch, pm, generator)

        await generation_tasks.execute_storyboard_task(
            "demo", "E1S02", {"script_file": "episode_1.json", "prompt": "双商品同框"}
        )

        prompt = generator.image_calls[0]["prompt"]
        assert (
            "Reference_Images: '图1: product reference images; the product in the frame must match exactly; 图2: character reference images; 图3: scene reference images.'\n"
            in prompt
        )
        assert len(generator.image_calls[0]["reference_images"]) == 3

    async def test_atmosphere_shot_zero_product_images(self, tmp_path, monkeypatch):
        """Atmosphere storyboards (empty products_in_shot) inject no product images; retain character/scene sheets and omit fidelity instructions."""
        project_path = prepare_files(tmp_path)
        (project_path / "products" / "保温杯.png").write_bytes(b"png")
        pm = ad_pm(project_path, with_sheet=True)
        generator = FakeGenerator()
        self._patch(monkeypatch, pm, generator)

        await generation_tasks.execute_storyboard_task(
            "demo", "E1S01", {"script_file": "episode_1.json", "prompt": "氛围开场"}
        )

        refs = generator.image_calls[0]["reference_images"]
        assert [reference["image"].name for reference in refs] == ["0000-Alice.png", "0001-祠堂.png"]
        assert generator.image_reference_bytes[0] == [b"png", b"png"]
        prompt = generator.image_calls[0]["prompt"]
        assert prompt.startswith(
            "Style: Anime\nVisual style: cinematic\nReference_Images: '图1: character reference images; 图2: scene reference images.'\n"
        )
        assert "\n\n氛围开场\n\n" in prompt
        assert "product reference images" not in prompt


def _patch_video_path(monkeypatch, pm, generator):
    use_project_manager(monkeypatch, pm)
    use_generation_context(monkeypatch, fake_resolve_ctx(generator))
    skip_artifact_registration(monkeypatch)
    use_video_thumbnail_extractor(monkeypatch, async_return(None))


class TestAdProductVideoRequest:
    """Product storyboard video requests use pure image-to-video: storyboard image as first frame, no reference images."""

    async def test_product_shot_video_request_carries_no_reference_images(self, tmp_path, monkeypatch):
        project_path = prepare_files(tmp_path)
        (project_path / "products" / "保温杯.png").write_bytes(b"png")
        (project_path / "storyboards" / "scene_E1S02.png").write_bytes(b"png")
        pm = ad_pm(project_path, with_sheet=True)
        seed_current_storyboard(pm, "E1S02")
        generator = FakeGenerator()
        _patch_video_path(monkeypatch, pm, generator)

        result = await generation_tasks.execute_video_task(
            "demo",
            "E1S02",
            {
                "script_file": "episode_1.json",
                "prompt": {"action": "举起保温杯", "camera_motion": "Static", "dialogue": []},
                "duration_seconds": 4,
            },
        )

        assert result["resource_type"] == "videos"
        call = generator.video_calls[0]
        assert "reference_images" not in call
        assert call["start_image"] == project_path / "storyboards" / "scene_E1S02.png"
        # Without references, omit product-fidelity instructions because pointing to absent images would mislead models.
        assert "High-fidelity" not in call["prompt"]
