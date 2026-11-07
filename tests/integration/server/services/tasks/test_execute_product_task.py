"""Tests for execute_product_task."""

import pytest

from lib.infra.api_errors import BadRequestError
from server.services.tasks import generation_tasks
from tests.fakes import use_generation_context, use_project_manager
from tests.integration.server.services.tasks.generation_tasks_support import (
    FakeGenerator,
    _FakePM,
    fake_resolve_ctx,
    prepare_files,
)


class TestGenerationTasks:
    async def test_execute_product_task_injects_reference_images(self, tmp_path, monkeypatch):
        """Product sheets inject user-uploaded originals as standardization references and use stored descriptions; write product_sheet on completion."""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = FakeGenerator(project_path)

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))

        result = await generation_tasks.execute_product_task("demo", "保温杯", {"prompt": "入队时的旧描述"})
        assert result["resource_type"] == "products"
        assert result["file_path"] == "products/保温杯.png"
        assert fake_pm.project["products"]["保温杯"]["product_sheet"] == "products/保温杯.png"

        call = fake_generator.image_calls[0]
        assert len(call["reference_images"]) == 1
        [sent] = [reference["image"] for reference in call["reference_images"]]
        assert sent.name == "0000-保温杯_1.jpg"
        assert not sent.is_relative_to(project_path)
        assert fake_generator.image_reference_bytes[0] == [b"jpg"]
        assert "Standard asset sheet for product “保温杯”." in call["prompt"]
        assert "不锈钢保温杯" in call["prompt"]
        assert "入队时的旧描述" not in call["prompt"]
        assert call["prompt"].endswith("Avoid: people appearing in the shot, watermarks, extra text, logos")
        assert "Style:" not in call["prompt"]

    async def test_execute_product_task_without_refs_is_t2i(self, tmp_path, monkeypatch):
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["products"]["保温杯"]["reference_images"] = []
        fake_generator = FakeGenerator(project_path)

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator))

        await generation_tasks.execute_product_task("demo", "保温杯", {})
        assert fake_generator.image_calls[0]["reference_images"] is None

    @pytest.mark.parametrize(
        "unreadable",
        ["products/refs/缺失.jpg", "../outside.jpg", "products/refs/../../../outside.jpg", "products/refs"],
    )
    async def test_unreadable_original_is_refused_before_any_provider_call(self, tmp_path, monkeypatch, unreadable):
        """Declared originals that are missing, outside projects or directories fail before provider submission, never silently reducing image count."""
        project_path = prepare_files(tmp_path)
        (tmp_path / "outside.jpg").write_bytes(b"jpg")
        fake_pm = _FakePM(project_path)
        fake_pm.project["products"]["保温杯"]["reference_images"] = ["products/refs/保温杯_1.jpg", unreadable]
        fake_generator = FakeGenerator(project_path)
        lanes: list[dict] = []

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator, seen_lane_requests=lanes))

        with pytest.raises(BadRequestError) as excinfo:
            await generation_tasks.execute_product_task("demo", "保温杯", {})

        assert excinfo.value.key == "asset_original_missing"
        assert excinfo.value.params["gaps"] == [
            {"code": "asset_original_missing", "asset_type": "product", "name": "保温杯"}
        ]
        assert lanes == []
        assert fake_generator.image_calls == []
        assert fake_pm.project["products"]["保温杯"]["product_sheet"] == ""

    async def test_product_references_are_clamped_to_the_provider_limit(self, tmp_path, monkeypatch):
        project_path = prepare_files(tmp_path)
        (project_path / "products" / "refs" / "保温杯_2.jpg").write_bytes(b"jpg2")
        fake_pm = _FakePM(project_path)
        fake_pm.project["products"]["保温杯"]["reference_images"] = [
            "products/refs/保温杯_1.jpg",
            "products/refs/保温杯_2.jpg",
        ]
        fake_generator = FakeGenerator(project_path)

        use_project_manager(monkeypatch, fake_pm)
        use_generation_context(monkeypatch, fake_resolve_ctx(fake_generator, image_max_reference_images=1))

        result = await generation_tasks.execute_product_task("demo", "保温杯", {})

        assert fake_generator.image_reference_bytes[0] == [b"jpg"]
        assert [warning["key"] for warning in result["warnings"]] == ["ref_too_many_images"]
