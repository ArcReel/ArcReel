"""资产图与衍生资产图任务执行：生成后判 current；生成输入不成立时在解析供应商通道与付费前失败。"""

import pytest

from lib.artifacts.artifact_activation import active_artifact_currency_resolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactStatus
from lib.generation.task_failure import parse_failure, render_failure
from lib.generation.task_failure_encoding import encode_task_failure_message
from lib.i18n import _
from lib.infra.api_errors import BadRequestError
from lib.project.project_manager import ProjectManager
from server.services.tasks import derivative_sheet_tasks, generation_tasks
from tests.integration.server.services.tasks.generation_tasks_support import (
    FakeGenerator,
    _FakePM,
    fake_resolve_ctx,
    persist_active_fake_project,
    prepare_files,
)

_DERIVATIVE_SHEET = "characters/derivatives/Alice/劲装.png"


def _project(tmp_path):
    project_path = prepare_files(tmp_path)
    fixture = _FakePM(project_path)
    fixture.project["characters"]["Alice"]["derivatives"] = {"劲装": {"description": "换上劲装", "character_sheet": ""}}
    persist_active_fake_project(fixture)
    return project_path


def _patch(monkeypatch, pm, generator, lanes=None):
    resolve = fake_resolve_ctx(generator, seen_lane_requests=lanes)
    for module in (generation_tasks, derivative_sheet_tasks):
        monkeypatch.setattr(module, "get_project_manager", lambda: pm)
        monkeypatch.setattr(module, "resolve_generation_context", resolve)


def _status(pm, project_path, key, artifact_path):
    resolver = active_artifact_currency_resolver(project_path, pm.load_project("demo"))
    return resolver.compare(key, artifact_path=artifact_path).status


def _zh(key, **params):
    return _(key, locale="zh", **params)


async def test_generated_asset_sheets_and_derivative_are_current(tmp_path, monkeypatch):
    project_path = _project(tmp_path)
    pm = ProjectManager(tmp_path / "projects")
    generator = FakeGenerator(project_path)
    _patch(monkeypatch, pm, generator)

    targets = [
        ("character", "Alice", "characters/Alice.png"),
        ("scene", "祠堂", "scenes/祠堂.png"),
        ("prop", "玉佩", "props/玉佩.png"),
        ("product", "保温杯", "products/保温杯.png"),
    ]
    for kind, name, _path in targets:
        await generation_tasks.execute_asset_sheet_task(kind, "demo", name, {})
    await derivative_sheet_tasks.execute_character_derivative_task("demo", "Alice/劲装", {})

    for kind, name, path in [*targets, ("character", "Alice/劲装", _DERIVATIVE_SHEET)]:
        assert _status(pm, project_path, ArtifactKey.asset_sheet(kind, name), path) is ArtifactStatus.CURRENT
    sent = [[reference["image"].name for reference in call["reference_images"] or []] for call in generator.image_calls]
    assert sent == [["0000-Alice-ref.png"], [], [], ["0000-保温杯_1.jpg"], ["0000-Alice.png"]]


async def test_missing_original_is_refused_before_the_provider_is_resolved(tmp_path, monkeypatch):
    project_path = _project(tmp_path)
    (project_path / "characters" / "refs" / "Alice-ref.png").unlink()
    pm = ProjectManager(tmp_path / "projects")
    generator = FakeGenerator(project_path)
    lanes: list[dict] = []
    _patch(monkeypatch, pm, generator, lanes)

    with pytest.raises(BadRequestError) as refused:
        await generation_tasks.execute_character_task("demo", "Alice", {})

    assert refused.value.key == "asset_original_missing"
    stored = encode_task_failure_message(refused.value)
    assert parse_failure(stored)[0] == "asset_original_missing"
    assert render_failure(stored, _zh) == "声明的原图读不到：character: Alice；请重新上传原图，或清除原图字段"
    assert lanes == []
    assert generator.image_calls == []


async def test_derivative_without_a_usable_owner_sheet_is_refused_with_every_gap(tmp_path, monkeypatch):
    project_path = _project(tmp_path)
    (project_path / "characters" / "Alice.png").unlink()
    pm = ProjectManager(tmp_path / "projects")
    project = pm.load_project("demo")
    project["characters"]["Alice"]["derivatives"]["劲装"]["description"] = ""
    pm.save_project("demo", project)
    generator = FakeGenerator(project_path)
    lanes: list[dict] = []
    _patch(monkeypatch, pm, generator, lanes)

    with pytest.raises(BadRequestError) as refused:
        await derivative_sheet_tasks.execute_character_derivative_task("demo", "Alice/劲装", {})

    assert refused.value.key == "derivative_description_required"
    assert refused.value.params["gaps"] == [
        {"code": "derivative_description_required", "asset_type": "character", "name": "劲装"},
        {"code": "derivative_owner_sheet_missing", "asset_type": "character", "name": "Alice"},
    ]
    stored = encode_task_failure_message(refused.value)
    assert parse_failure(stored)[0] == "derivative_description_required"
    detail = render_failure(stored, _zh)
    assert "衍生「劲装」还没有填写外观变化" in detail
    assert "角色「Alice」还没有资产图" in detail
    assert lanes == []
    assert generator.image_calls == []
