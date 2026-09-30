"""一集草稿的 Web 读写：视图的条目定位、手修保存清零即采用、可编辑草稿归 Agent、丢弃回到正式内容，
以及 AI 修复（``DraftRepair``）的范围、合并与重判。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.backends.text_generator import TextGenerator
from lib.project.project_manager import ProjectManager
from lib.script.draft_quarantine import (
    FORMAL_EDIT_META_KEY,
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_SCRIPT_PLAN,
    quarantine_path,
    write_quarantine,
)
from lib.script.draft_violation import DraftViolation
from server.draft_repair import DraftRepair
from server.draft_workflow import DraftContext, DraftWorkflowError
from server.services.project.episode_drafts import EpisodeDraftService
from server.services.project.script_review import ScriptReviewService
from tests.factories import make_video_request_facts
from tests.fakes import FakeTextGenerator

pytestmark = pytest.mark.usefixtures("video_request_facts")

_NOVEL = "张三在村口等人。"


def _segment(**overrides) -> dict:
    segment = {
        "segment_id": "E1S01",
        "novel_text": _NOVEL,
        "duration_seconds": 4,
        "segment_break": False,
        "characters_in_segment": ["张三"],
        "scenes": [],
        "props": [],
    }
    segment.update(overrides)
    return segment


@pytest.fixture
def narration(tmp_path: Path) -> tuple[ProjectManager, Path]:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.add_character("demo", "张三", "村民")
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    project_path = pm.get_project_path("demo")
    (project_path / "source").mkdir(exist_ok=True)
    (project_path / "source" / "episode_1.txt").write_text(_NOVEL, encoding="utf-8")
    return pm, project_path


def _write_narration_draft(project_path: Path, segments: list[dict], **meta) -> None:
    write_quarantine(
        project_path,
        1,
        QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
        content={"segments": segments},
        violations=[],
        meta={"source": None, **meta},
    )


def _formal_segments(project_path: Path) -> list[dict]:
    path = project_path / "drafts" / "episode_1" / "script_plan_segments.json"
    return json.loads(path.read_text(encoding="utf-8"))["segments"]


async def test_draft_view_locates_violations_on_their_items(narration) -> None:
    pm, project_path = narration
    _write_narration_draft(project_path, [_segment(), _segment(segment_id="E1S02", characters_in_segment=["王五"])])

    view = await EpisodeDraftService(pm).get_draft("demo", 1, "narration_script_plan")

    assert view["editable_by"] == "user"
    assert view["content"]["segments"][1]["characters_in_segment"] == ["王五"]
    located = {(v["code"], v.get("item_index"), v.get("item_id")) for v in view["violations"]}
    assert ("unregistered_asset", 1, "E1S02") in located
    # 两段各带全文，覆盖违约落在整集层面，没有条目定位。
    assert ("novel_text_coverage", None, None) in located


@pytest.mark.parametrize("kind", ["drama", "narration", "reference_video"])
async def test_schema_field_violations_locate_the_invalid_item(narration, kind) -> None:
    pm, project_path = narration
    if kind == "drama":
        pm.update_project("demo", lambda p: p.__setitem__("content_mode", "drama"))
        quarantine_kind, doc_type = QUARANTINE_KIND_DRAMA_SCRIPT_PLAN, "drama_script_plan"
        content = {
            "title": "第一集",
            "scenes": [
                {
                    "scene_id": "E1S01",
                    "duration_seconds": 5,
                    "segment_break": False,
                    "characters_in_scene": [],
                    "scenes": [],
                    "props": [],
                    "scene_description": "村口",
                    "utterances": [],
                    "source_text": _NOVEL,
                }
            ],
        }
    elif kind == "reference_video":
        pm.update_project("demo", lambda p: p.__setitem__("generation_mode", "reference_video"))
        quarantine_kind, doc_type = QUARANTINE_KIND_SCRIPT_PLAN, "reference_script_plan"
        content = {"units": [{"text": "村口", "source_text": _NOVEL, "duration_seconds": 5}]}
    else:
        quarantine_kind, doc_type = QUARANTINE_KIND_NARRATION_SCRIPT_PLAN, "narration_script_plan"
        content = {"segments": [_segment(duration_seconds="invalid")]}
    write_quarantine(project_path, 1, quarantine_kind, content=content, violations=[], meta={"source": None})

    view = await EpisodeDraftService(pm).get_draft("demo", 1, doc_type)

    assert [(v["code"], v.get("item_index"), v.get("item_id")) for v in view["violations"]] == [
        ("schema_invalid", 0, None if kind == "reference_video" else "E1S01")
    ]


async def test_adopted_reference_plan_keeps_its_soft_violations(narration, set_video_request_facts) -> None:
    pm, project_path = narration
    pm.update_project("demo", lambda p: p.__setitem__("generation_mode", "reference_video"))
    set_video_request_facts(
        make_video_request_facts(route="reference_video", voice_consistency="native", max_reference_audio_count=3)
    )
    content = {"units": [{"text": "张三在村口等人。\n@[张三]{你好。}", "source_text": _NOVEL, "duration_seconds": 4}]}
    write_quarantine(
        project_path, 1, QUARANTINE_KIND_SCRIPT_PLAN, content=content, violations=[], meta={"source": None}
    )
    service = EpisodeDraftService(pm)
    draft = await service.get_draft("demo", 1, "reference_script_plan")

    result = await service.save_draft("demo", 1, "reference_script_plan", content, draft["revision"])
    assert result["adopted"] is True, result
    state = await ScriptReviewService(pm).get_state("demo", 1)

    assert ("ref_warn_speaker_without_audio", 0, "E1U01") in {
        (soft["code"], soft["item_index"], soft["item_id"]) for soft in state["soft_violations"]
    }
    assert state["soft_violations"] == draft["soft_violations"]


async def test_save_that_still_violates_keeps_the_draft_with_refreshed_violations(narration) -> None:
    pm, project_path = narration
    _write_narration_draft(project_path, [_segment(characters_in_segment=["王五"])])
    service = EpisodeDraftService(pm)
    view = await service.get_draft("demo", 1, "narration_script_plan")

    edited = {"segments": [_segment(characters_in_segment=["王五"], duration_seconds=5)]}
    result = await service.save_draft("demo", 1, "narration_script_plan", edited, view["revision"])

    assert result["adopted"] is False
    draft = result["draft"]
    assert draft["content"]["segments"][0]["duration_seconds"] == 5
    assert {(v["code"], v.get("item_index")) for v in draft["violations"]} == {
        ("unregistered_asset", 0),
        ("duration_off_tier", 0),
    }
    assert quarantine_path(project_path, 1, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN).exists()


async def test_save_that_clears_every_violation_adopts_the_draft(narration) -> None:
    pm, project_path = narration
    _write_narration_draft(project_path, [_segment(characters_in_segment=["王五"])])
    service = EpisodeDraftService(pm)
    view = await service.get_draft("demo", 1, "narration_script_plan")

    result = await service.save_draft("demo", 1, "narration_script_plan", {"segments": [_segment()]}, view["revision"])

    assert result == {"episode": 1, "doc_type": "narration_script_plan", "adopted": True, "draft": None}
    assert not quarantine_path(project_path, 1, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN).exists()
    assert _formal_segments(project_path)[0]["characters_in_segment"] == ["张三"]
    assert await service.list_drafts("demo", 1) == []


async def test_save_with_a_stale_revision_is_rejected(narration) -> None:
    pm, project_path = narration
    _write_narration_draft(project_path, [_segment(characters_in_segment=["王五"])])

    with pytest.raises(DraftWorkflowError) as exc_info:
        await EpisodeDraftService(pm).save_draft(
            "demo", 1, "narration_script_plan", {"segments": [_segment()]}, "stale"
        )

    assert exc_info.value.code == "revision_conflict"


async def test_agent_editable_draft_is_status_only_and_rejects_web_saves(narration) -> None:
    pm, project_path = narration
    _write_narration_draft(project_path, [_segment()], **{FORMAL_EDIT_META_KEY: True})
    service = EpisodeDraftService(pm)

    view = await service.get_draft("demo", 1, "narration_script_plan")
    assert view["editable_by"] == "agent"
    assert view["content"] is None
    assert await service.list_drafts("demo", 1) == [
        {"doc_type": "narration_script_plan", "editable_by": "agent", "violation_count": 0}
    ]

    with pytest.raises(DraftWorkflowError) as exc_info:
        await service.save_draft("demo", 1, "narration_script_plan", {"segments": [_segment()]}, view["revision"])
    assert exc_info.value.code == "draft_agent_owned"


async def test_discard_returns_to_the_formal_script_plan(narration) -> None:
    pm, project_path = narration
    formal = project_path / "drafts" / "episode_1" / "script_plan_segments.json"
    formal.parent.mkdir(parents=True, exist_ok=True)
    formal.write_text(json.dumps({"segments": [_segment()]}, ensure_ascii=False), encoding="utf-8")
    _write_narration_draft(project_path, [_segment(characters_in_segment=["王五"])])
    service = EpisodeDraftService(pm)
    view = await service.get_draft("demo", 1, "narration_script_plan")
    assert view["formal_exists"] is True

    result = await service.discard_draft("demo", 1, "narration_script_plan", view["revision"])

    assert result["discarded"] is True
    assert _formal_segments(project_path) == [_segment()]
    with pytest.raises(DraftWorkflowError) as exc_info:
        await service.get_draft("demo", 1, "narration_script_plan")
    assert exc_info.value.code == "draft_not_found"


def _repair(pm: ProjectManager) -> DraftRepair:
    return DraftRepair(DraftContext(project_name="demo", data_root=pm.data_root, pm=pm))


async def test_ai_repair_rewrites_only_the_violating_item_and_adopts_when_clean(narration, monkeypatch) -> None:
    pm, project_path = narration
    kept = _segment(novel_text="张三在村口", segment_break=True)
    broken = _segment(segment_id="E1S02", novel_text="等人。", characters_in_segment=["王五"])
    _write_narration_draft(project_path, [kept, broken])
    view = await EpisodeDraftService(pm).get_draft("demo", 1, "narration_script_plan")
    assert [(v["code"], v.get("item_index")) for v in view["violations"]] == [("unregistered_asset", 1)]
    repaired = _segment(segment_id="E1S02", novel_text="等人。", characters_in_segment=["张三"])
    model = FakeTextGenerator(json.dumps({"segments": [repaired]}, ensure_ascii=False))
    monkeypatch.setattr(TextGenerator, "create", model.create)

    result = await _repair(pm).repair(1, "narration_script_plan", view["revision"], "保持口语化")

    assert result["adopted"] is True
    assert _formal_segments(project_path) == [kept, repaired]
    # 交给模型改的只有违约条目：完整草稿只作上下文，修改要求里只列出 segments[1]。
    (request,) = model.requests
    requirements = request.prompt.split("# 修改要求", 1)[1]
    assert "## `segments[1]`" in requirements
    assert '"segment_id": "E1S02"' in requirements
    assert '"segment_id": "E1S01"' not in requirements
    assert "保持口语化" in requirements


async def test_ai_repair_rewrites_the_whole_draft_for_episode_level_violations(narration, monkeypatch) -> None:
    pm, project_path = narration
    _write_narration_draft(project_path, [_segment(), _segment(segment_id="E1S02")])
    view = await EpisodeDraftService(pm).get_draft("demo", 1, "narration_script_plan")
    assert ("novel_text_coverage", None) in {(v["code"], v.get("item_index")) for v in view["violations"]}
    model = FakeTextGenerator(json.dumps({"segments": [_segment()]}, ensure_ascii=False))
    monkeypatch.setattr(TextGenerator, "create", model.create)

    result = await _repair(pm).repair(1, "narration_script_plan", view["revision"], None)

    assert result["adopted"] is True
    assert _formal_segments(project_path) == [_segment()]


async def test_ai_repair_that_still_violates_updates_the_draft(narration, monkeypatch) -> None:
    pm, project_path = narration
    _write_narration_draft(project_path, [_segment(characters_in_segment=["王五"])])
    service = EpisodeDraftService(pm)
    view = await service.get_draft("demo", 1, "narration_script_plan")
    still_broken = _segment(characters_in_segment=["王五"], duration_seconds=5)
    model = FakeTextGenerator(json.dumps({"segments": [still_broken]}, ensure_ascii=False))
    monkeypatch.setattr(TextGenerator, "create", model.create)

    result = await _repair(pm).repair(1, "narration_script_plan", view["revision"], None)

    assert result["adopted"] is False
    after = await service.get_draft("demo", 1, "narration_script_plan")
    assert after["content"] == {"segments": [still_broken]}
    assert {(v["code"], v.get("item_index")) for v in after["violations"]} == {
        ("unregistered_asset", 0),
        ("duration_off_tier", 0),
    }


@pytest.mark.parametrize(
    "reply",
    [RuntimeError("provider down"), "not json", json.dumps({"segments": []})],
    ids=["provider_error", "not_json", "wrong_item_count"],
)
async def test_failed_ai_repair_leaves_the_draft_untouched(narration, monkeypatch, reply) -> None:
    pm, project_path = narration
    _write_narration_draft(project_path, [_segment(characters_in_segment=["王五"])])
    service = EpisodeDraftService(pm)
    view = await service.get_draft("demo", 1, "narration_script_plan")
    monkeypatch.setattr(TextGenerator, "create", FakeTextGenerator(reply).create)

    with pytest.raises(DraftWorkflowError) as exc_info:
        await _repair(pm).repair(1, "narration_script_plan", view["revision"], None)

    assert exc_info.value.code == "draft_repair_failed"
    after = await service.get_draft("demo", 1, "narration_script_plan")
    assert (after["revision"], after["content"]) == (view["revision"], view["content"])


async def test_prompt_authoring_draft_view_maps_items_to_formal_units(tmp_path: Path) -> None:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.update_project("demo", lambda p: p.__setitem__("generation_mode", "reference_video"))
    pm.add_episode("demo", 1, "第一集", "scripts/episode_1.json")
    project_path = pm.get_project_path("demo")
    (project_path / "scripts").mkdir(exist_ok=True)
    (project_path / "scripts" / "episode_1.json").write_text(
        json.dumps(
            {
                "episode": 1,
                "title": "第一集",
                "video_units": [
                    {"unit_id": "E1U01", "text": "村口", "duration_seconds": 4},
                    {"unit_id": "E1U03", "text": "@[王五] 出场", "duration_seconds": 4},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    write_quarantine(
        project_path,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "第一集", "units": [{"text": "@[王五] 出场"}]},
        violations=[DraftViolation("未登记", code="unregistered_mention", item_index=0, item_id="E1U03")],
        meta={"base_fingerprint": None, "unit_ids": ["E1U03"]},
    )
    service = EpisodeDraftService(pm)

    view = await service.get_draft("demo", 1, "reference_prompt_authoring")

    assert view["editable_by"] == "user"
    assert view["item_ids"] == ["E1U03"]
    assert view["content"]["units"] == [{"text": "@[王五] 出场"}]
    assert [(v["code"], v["item_index"], v["item_id"]) for v in view["violations"]] == [
        ("unregistered_mention", 0, "E1U03")
    ]
    assert await service.list_drafts("demo", 1) == [
        {"doc_type": "reference_prompt_authoring", "editable_by": "user", "violation_count": 1}
    ]
