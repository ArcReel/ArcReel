"""草稿路由：视频模型配置或正式剧本单元阻断手修保存时，返回真实原因并保留草稿。"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.artifacts.artifact_activation import activate_artifact_target_state
from lib.generation.video_request_facts import VideoRequestFactsFailure
from lib.project.project_manager import ProjectManager
from lib.script.draft_quarantine import (
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    quarantine_path,
    write_quarantine,
)
from server.error_handlers import register_error_handlers
from server.routers import episode_drafts
from tests.fakes import FakeTextGenerator, use_project_manager, use_text_generator


def test_web_save_blocked_by_the_video_model_reports_the_real_reason(
    tmp_path: Path, video_request_facts, set_video_request_facts, monkeypatch
) -> None:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.add_character("demo", "张三", "村民")
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    project_path = pm.get_project_path("demo")
    novel = "张三在村口等人。"
    (project_path / "source" / "episode_1.txt").write_text(novel, encoding="utf-8")
    segment = {
        "segment_id": "E1S01",
        "novel_text": novel,
        "duration_seconds": 4,
        "segment_break": False,
        "characters_in_segment": ["张三"],
        "scenes": [],
        "props": [],
    }
    write_quarantine(
        project_path,
        1,
        QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
        content={"segments": [{**segment, "characters_in_segment": ["王五"]}]},
        violations=[],
        meta={"source": None},
    )
    use_project_manager(monkeypatch, pm)
    app = FastAPI()
    app.include_router(episode_drafts.router)
    register_error_handlers(app)
    with TestClient(app) as client:
        path = "/projects/demo/episodes/1/drafts/narration_script_plan"
        revision = client.get(path).json()["revision"]
        set_video_request_facts(
            VideoRequestFactsFailure("video_capability_missing_i2v", (("provider", "gemini"), ("model", "veo-x")))
        )

        response = client.put(
            path,
            json={"content": {"segments": [segment]}, "base_revision": revision},
            headers={"Accept-Language": "zh"},
        )

    assert response.status_code == 422
    body = response.json()
    assert body["diagnostic"] == {
        "code": "video_capability_missing_i2v",
        "params": {"provider": "gemini", "model": "veo-x"},
    }
    assert "「图生视频」" in body["detail"]
    assert "video_capability_missing_i2v" not in body["detail"]
    assert quarantine_path(project_path, 1, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN).exists()


@pytest.mark.parametrize(
    ("unit", "phrase"),
    [
        (
            {"text": "@[张三] 起身\n@[张三]：{" + "这是一段非常长的台词" * 6 + "}", "duration_seconds": 4},
            "视频单元 1 的台词念完约需",
        ),
        (
            {"text": "@[张三] 起身", "duration_seconds": 5},
            "视频单元 1 的时长 5 秒不在当前视频模型支持的档位内（可选 4 / 6 / 8 秒）",
        ),
    ],
    ids=["dialogue_overload", "duration_off_tier"],
)
@pytest.mark.parametrize(
    ("locale", "unit_label"), [("zh", "视频单元 1"), ("en", "video unit 1"), ("vi", "đơn vị video 1")]
)
def test_web_save_blocked_by_a_formal_unit_names_it_by_position(
    tmp_path: Path, video_request_facts, monkeypatch, unit: dict, phrase: str, locale: str, unit_label: str
) -> None:
    """提示词编写草稿手修保存后，晋升被正式剧本单元挡下：按「视频单元 N」说明原因，不含内部单元 ID。"""
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.update_project("demo", lambda p: p.__setitem__("generation_mode", "reference_video"))
    pm.add_character("demo", "张三", "村民")
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    project_path = pm.get_project_path("demo")
    (project_path / "source" / "episode_1.txt").write_text("张三起身。", encoding="utf-8")
    pm.save_script(
        "demo",
        {"title": "第一集", "episode": 1, "content_mode": "narration", "video_units": [{"unit_id": "E1U01", **unit}]},
        "episode_1.json",
    )
    activate_artifact_target_state(project_path, bump_schema=False)
    write_quarantine(
        project_path,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "第一集", "units": [{"text": "@[张三] 坐下"}]},
        violations=[],
        meta={"unit_ids": ["E1U01"]},
    )
    # 预判在调用文本模型之前：替身只为晋升装配 ScriptGenerator，不会被调用。
    use_text_generator(monkeypatch, FakeTextGenerator("").create)
    use_project_manager(monkeypatch, pm)
    app = FastAPI()
    app.include_router(episode_drafts.router)
    register_error_handlers(app)
    with TestClient(app) as client:
        path = "/projects/demo/episodes/1/drafts/reference_prompt_authoring"
        revision = client.get(path).json()["revision"]

        response = client.put(
            path,
            json={"content": {"title": "第一集", "units": [{"text": "@[张三] 坐好"}]}, "base_revision": revision},
            headers={"Accept-Language": locale},
        )

    assert response.status_code == 422
    body = response.json()
    assert body["diagnostic"] == {"code": "draft_invalid"}
    assert unit_label in body["detail"]
    assert "{" not in body["detail"]
    if locale == "zh":
        assert phrase in body["detail"]
    assert "E1U01" not in body["detail"]
    assert quarantine_path(project_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING).exists()
