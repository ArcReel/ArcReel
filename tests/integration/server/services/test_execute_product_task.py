"""Tests for execute_product_task."""

from server.services import generation_tasks
from tests.integration.server.services.generation_tasks_support import (
    _fake_resolve_ctx,
    _FakeGenerator,
    _FakePM,
    _prepare_files,
)


class TestGenerationTasks:
    async def test_execute_product_task_injects_reference_images(self, tmp_path, monkeypatch):
        """product sheet 生成把用户上传原图作为参考注入（标准化整理的输入），缺失文件跳过；
        完成后回写 product_sheet。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator(project_path)

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        result = await generation_tasks.execute_product_task(
            "demo",
            "保温杯",
            {"prompt": "不锈钢保温杯，银色磨砂"},
        )
        assert result["resource_type"] == "products"
        assert result["file_path"] == "products/保温杯.png"
        assert fake_pm.project["products"]["保温杯"]["product_sheet"] == "products/保温杯.png"

        call = fake_generator.image_calls[0]
        # 仅存在的原图进入参考；缺失文件跳过
        assert len(call["reference_images"]) == 1
        assert call["reference_images"][0].name == "0000-保温杯_1.jpg"
        assert not call["reference_images"][0].is_relative_to(project_path)
        assert fake_generator.image_reference_bytes[0] == [b"jpg"]
        assert "保温杯" in call["prompt"]

    async def test_execute_product_task_without_refs_is_t2i(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["products"]["保温杯"]["reference_images"] = []
        fake_generator = _FakeGenerator(project_path)

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        await generation_tasks.execute_product_task("demo", "保温杯", {"prompt": "保温杯"})
        assert fake_generator.image_calls[0]["reference_images"] is None

    def test_collect_product_reference_images_rejects_path_escape(self, tmp_path):
        """reference_images 中的绝对路径与 `..` 穿越值不得越出项目目录读取宿主机文件；目录路径同样跳过。"""
        project_path = _prepare_files(tmp_path)
        outside = tmp_path / "outside.jpg"
        outside.write_bytes(b"jpg")
        project = {
            "products": {
                "保温杯": {
                    "reference_images": [
                        str(outside),
                        "../outside.jpg",
                        "products/refs/../../../outside.jpg",
                        "products/refs",
                        "products/refs/保温杯_1.jpg",
                    ],
                }
            }
        }

        result = generation_tasks._collect_product_reference_images(project, project_path, "保温杯")

        assert result == [project_path / "products" / "refs" / "保温杯_1.jpg"]
