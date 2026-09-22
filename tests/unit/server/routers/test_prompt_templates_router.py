from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.prompts.prompt_templates import PromptTemplates
from lib.script.script_models import ImagePrompt
from server.error_handlers import register_error_handlers
from server.routers import prompt_templates as prompt_templates_router
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth


def write_template(directory: Path, name: str, body: str, **metadata: object) -> None:
    path = directory / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("---\n" + yaml.safe_dump(metadata, allow_unicode=True) + "---\n" + body, encoding="utf-8")


def write_partial(directory: Path, name: str, text: str) -> None:
    path = directory / "partials" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def templates(tmp_path: Path) -> PromptTemplates:
    write_template(
        tmp_path,
        "asset/sheet",
        '{{ variant("asset/sheet/title", asset_type) }}\n\n{{ description }}\n\n{{ partial("shared/avoid") }}',
        id="asset/sheet",
        category="asset",
        title="资产图",
        description="资产设定图",
        applies_to={"asset_type": ["character", "scene"]},
        slots={"asset_type": "资产类型", "description": "外观描述"},
        protected=False,
    )
    write_template(
        tmp_path,
        "storyboard/image",
        "{{ scene }}",
        id="storyboard/image",
        category="storyboard",
        title="分镜图",
        description="分镜画面",
        applies_to={},
        slots={"scene": "画面描述"},
        protected=True,
        output_schema="lib.script.script_models:ImagePrompt",
    )
    write_partial(tmp_path, "asset/sheet/title/character", "角色设定图")
    write_partial(tmp_path, "asset/sheet/title/scene", "")
    write_partial(tmp_path, "shared/avoid", "Avoid: 水印")
    return PromptTemplates(tmp_path)


def make_app(templates: PromptTemplates | None = None) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(prompt_templates_router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    if templates is not None:
        app.dependency_overrides[prompt_templates_router.get_prompt_templates] = lambda: templates
    return app


@pytest.mark.parametrize("path", ["/api/v1/prompt-templates", "/api/v1/prompt-templates/asset/sheet"])
def test_unauthenticated_requests_rejected(monkeypatch, templates, path):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    with TestClient(make_app(templates), raise_server_exceptions=False) as client:
        assert client.get(path).status_code == 401


def test_list_returns_metadata_of_every_template_in_registry_order(templates):
    app = make_app(templates)
    override_auth(app)
    with TestClient(app) as client:
        response = client.get("/api/v1/prompt-templates")
    assert response.status_code == 200
    assert response.json() == {
        "templates": [
            {
                "id": "asset/sheet",
                "category": "asset",
                "title": "资产图",
                "description": "资产设定图",
                "applies_to": {"asset_type": ["character", "scene"]},
                "slots": {"asset_type": "资产类型", "description": "外观描述"},
                "protected": False,
                "output_schema": None,
            },
            {
                "id": "storyboard/image",
                "category": "storyboard",
                "title": "分镜图",
                "description": "分镜画面",
                "applies_to": {},
                "slots": {"scene": "画面描述"},
                "protected": True,
                "output_schema": "lib.script.script_models:ImagePrompt",
            },
        ]
    }


def test_detail_returns_source_slots_and_partials_with_every_axis_value(templates):
    app = make_app(templates)
    override_auth(app)
    with TestClient(app) as client:
        response = client.get("/api/v1/prompt-templates/asset/sheet")
    assert response.status_code == 200
    body = response.json()
    assert body["template"]["slots"] == {"asset_type": "资产类型", "description": "外观描述"}
    assert body["source"] == (
        '{{ variant("asset/sheet/title", asset_type) }}\n\n{{ description }}\n\n{{ partial("shared/avoid") }}'
    )
    assert body["partials"] == [
        {"name": "asset/sheet/title/character", "source": "角色设定图"},
        {"name": "asset/sheet/title/scene", "source": ""},
        {"name": "shared/avoid", "source": "Avoid: 水印"},
    ]
    assert "output_schema" not in body


def test_detail_resolves_output_schema_to_json_schema(templates):
    app = make_app(templates)
    override_auth(app)
    with TestClient(app) as client:
        response = client.get("/api/v1/prompt-templates/storyboard/image")
    assert response.status_code == 200
    assert response.json()["output_schema"] == ImagePrompt.model_json_schema()


def test_unknown_template_returns_404(templates):
    app = make_app(templates)
    override_auth(app)
    with TestClient(app) as client:
        response = client.get("/api/v1/prompt-templates/asset/missing")
    assert response.status_code == 404


def test_default_dependency_serves_builtin_templates():
    app = make_app()
    override_auth(app)
    with TestClient(app) as client:
        listed = client.get("/api/v1/prompt-templates").json()["templates"]
        detail = client.get("/api/v1/prompt-templates/asset/sheet").json()
    assert "asset/sheet" in {item["id"] for item in listed}
    assert "shared/image_avoid" in {partial["name"] for partial in detail["partials"]}


def test_builtin_style_templates_listed_as_one_group_with_whole_prompt_as_source():
    app = make_app()
    override_auth(app)
    with TestClient(app) as client:
        listed = client.get("/api/v1/prompt-templates").json()["templates"]
        detail = client.get("/api/v1/prompt-templates/style/live_premium_drama").json()
    style_positions = [index for index, item in enumerate(listed) if item["category"] == "style"]
    assert len(style_positions) == 36
    assert style_positions == list(range(style_positions[0], style_positions[0] + 36))
    assert listed[style_positions[0]]["id"] == "style/live_cinematic_ancient"
    assert detail["source"].strip() == "真人电视剧风格，精品短剧画风，大师级构图"
    assert detail["partials"] == []
