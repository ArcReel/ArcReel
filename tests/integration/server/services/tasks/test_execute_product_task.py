"""Tests for execute_product_task."""

import pytest

from lib.infra.api_errors import BadRequestError
from server.services.tasks import generation_tasks
from tests.integration.server.services.tasks.generation_tasks_support import (
    FakeGenerator,
    _FakePM,
    fake_resolve_ctx,
    prepare_files,
)


class TestGenerationTasks:
    async def test_execute_product_task_injects_reference_images(self, tmp_path, monkeypatch):
        """product sheet 生成把用户上传原图作为参考注入（标准化整理的输入），描述取自存储的条目；
        完成后回写 product_sheet。"""
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = FakeGenerator(project_path)

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", fake_resolve_ctx(fake_generator))

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
        assert "商品「保温杯」的标准资产图。" in call["prompt"]
        assert "不锈钢保温杯" in call["prompt"]
        assert "入队时的旧描述" not in call["prompt"]
        assert call["prompt"].endswith("Avoid: 出镜人物、水印、多余文字、Logo")
        assert "Style:" not in call["prompt"]

    async def test_execute_product_task_without_refs_is_t2i(self, tmp_path, monkeypatch):
        project_path = prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["products"]["保温杯"]["reference_images"] = []
        fake_generator = FakeGenerator(project_path)

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", fake_resolve_ctx(fake_generator))

        await generation_tasks.execute_product_task("demo", "保温杯", {})
        assert fake_generator.image_calls[0]["reference_images"] is None

    @pytest.mark.parametrize(
        "unreadable",
        ["products/refs/缺失.jpg", "../outside.jpg", "products/refs/../../../outside.jpg", "products/refs"],
    )
    async def test_unreadable_original_is_refused_before_any_provider_call(self, tmp_path, monkeypatch, unreadable):
        """声明了却读不到的原图（缺失、越出项目目录、是目录）在提交供应商前拒绝，不静默少图。"""
        project_path = prepare_files(tmp_path)
        (tmp_path / "outside.jpg").write_bytes(b"jpg")
        fake_pm = _FakePM(project_path)
        fake_pm.project["products"]["保温杯"]["reference_images"] = ["products/refs/保温杯_1.jpg", unreadable]
        fake_generator = FakeGenerator(project_path)
        lanes: list[dict] = []

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            fake_resolve_ctx(fake_generator, seen_lane_requests=lanes),
        )

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

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            fake_resolve_ctx(fake_generator, image_max_reference_images=1),
        )

        result = await generation_tasks.execute_product_task("demo", "保温杯", {})

        assert fake_generator.image_reference_bytes[0] == [b"jpg"]
        assert [warning["key"] for warning in result["warnings"]] == ["ref_too_many_images"]
