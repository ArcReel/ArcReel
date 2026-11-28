import asyncio
from types import SimpleNamespace

import pytest

from lib.project.project_manager import ProjectManager, ProjectOverview
from tests.fakes import use_text_generator


@pytest.mark.asyncio
async def test_overview_preserves_language_edited_during_generation(tmp_path, monkeypatch):
    manager = ProjectManager(tmp_path)
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo")
    (manager.get_project_path("demo") / "source" / "story.txt").write_text("Story", encoding="utf-8")
    manager.update_project("demo", lambda project: project.update(source_language="zh"))

    async def generate(request, *, project_name):
        await asyncio.to_thread(
            manager.update_project, project_name, lambda project: project.update(source_language="vi")
        )
        overview = ProjectOverview(synopsis="Story", genre="Drama", theme="Love", world_setting="Today", language="en")
        return SimpleNamespace(text=overview.model_dump_json())

    async def create(*args, **kwargs):
        return SimpleNamespace(generate=generate)

    use_text_generator(monkeypatch, create)
    result = await manager.generate_overview("demo")
    saved = manager.load_project("demo")
    assert saved["overview"] == result
    assert result["language"] == "en"
    assert saved["source_language"] == "vi"
