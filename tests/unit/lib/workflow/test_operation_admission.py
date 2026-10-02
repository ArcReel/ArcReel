"""AI 操作结构准入谓词：每个操作只看自己的输入，结论带稳定理由码。"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.episode.episode_ledger import SourceDoc
from lib.workflow.operation_admission import (
    AdmissionReason,
    AdmissionState,
    OperationAdmission,
    ad_inputs_present,
    admit_ad_script,
    admit_author_prompts,
    admit_edit_timeline,
    admit_plan_episodes,
    admit_script_plan,
    episode_source_present,
    pending_authoring_entry_ids,
    whole_source_present,
)


def _conclusion(admission: OperationAdmission) -> tuple[str, str | None]:
    return admission.state.value, admission.reason.value if admission.reason else None


@pytest.mark.parametrize(
    ("docs", "expected"),
    [
        ([], False),
        ([SourceDoc(rel_path="source/novel.txt", text=" \n\t")], False),
        ([SourceDoc(rel_path="source/novel.txt", text=" "), SourceDoc(rel_path="source/b.txt", text="正文")], True),
        ([SourceDoc(rel_path="source/novel.txt", text="正文")], True),
    ],
)
def test_whole_source_is_any_non_blank_registered_file(docs: list[SourceDoc], expected: bool) -> None:
    assert whole_source_present(docs) is expected


@pytest.mark.parametrize("origin", ["own", "whole_source"])
def test_episode_source_is_a_non_blank_regular_file(tmp_path: Path, origin: str) -> None:
    source = tmp_path / "source"
    source.mkdir()
    entry = {"episode": 1, "source_origin": origin}
    assert episode_source_present(tmp_path, 1, entry) is False
    (source / "episode_1.txt").write_text("  ", encoding="utf-8")
    assert episode_source_present(tmp_path, 1, entry) is False
    (source / "episode_1.txt").write_text("原文", encoding="utf-8")
    assert episode_source_present(tmp_path, 1, entry) is True
    (source / "episode_2.txt").symlink_to(source / "episode_1.txt")
    assert episode_source_present(tmp_path, 2, {"episode": 2, "source_origin": origin}) is False


def test_no_source_episode_has_no_episode_source_even_with_a_file_on_disk(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "episode_1.txt").write_text("没有登记的原文", encoding="utf-8")

    assert episode_source_present(tmp_path, 1, {"episode": 1, "source_origin": "none"}) is False
    assert episode_source_present(tmp_path, 1, None) is False


@pytest.mark.parametrize(
    ("project", "expected"),
    [
        ({}, False),
        ({"brief": "  ", "products": {}}, False),
        ({"brief": "夏季新品"}, True),
        ({"products": {"杯子": {}}}, True),
    ],
)
def test_ad_inputs_are_a_brief_or_any_product(project: dict, expected: bool) -> None:
    assert ad_inputs_present(project) is expected


def test_pending_authoring_ids_follow_script_order_and_the_skeleton_id_field() -> None:
    items = [
        {"segment_id": "E1S01", "pending_authoring": True},
        {"segment_id": "E1S02"},
        {"segment_id": "E1S03", "pending_authoring": True},
        "not-an-item",
    ]
    assert pending_authoring_entry_ids(items, "segments") == ["E1S01", "E1S03"]
    assert pending_authoring_entry_ids(items, None) == []


@pytest.mark.parametrize(
    ("content_mode", "whole_source", "expected"),
    [
        ("narration", True, ("admitted", None)),
        ("drama", False, ("refused", "whole_source_missing")),
        ("ad", True, ("not_applicable", "operation_not_applicable")),
    ],
)
def test_plan_episodes_needs_the_whole_source(content_mode: str, whole_source: bool, expected: tuple) -> None:
    assert _conclusion(admit_plan_episodes(content_mode, whole_source=whole_source)) == expected


@pytest.mark.parametrize(
    ("content_mode", "episode_source", "expected"),
    [
        ("narration", True, ("admitted", None)),
        ("drama", False, ("refused", "episode_source_missing")),
        ("ad", True, ("not_applicable", "operation_not_applicable")),
    ],
)
def test_script_plan_needs_the_episode_source(content_mode: str, episode_source: bool, expected: tuple) -> None:
    assert _conclusion(admit_script_plan(content_mode, episode_source=episode_source)) == expected


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"formal_script": True, "pending_ids": ["E1S01"], "draft_pending": False}, ("admitted", None)),
        (
            {"formal_script": True, "pending_ids": [], "draft_pending": False, "explicit_ids": ["E1S01"]},
            ("admitted", None),
        ),
        ({"formal_script": True, "pending_ids": [], "draft_pending": False}, ("refused", "no_pending_authoring")),
        ({"formal_script": False, "pending_ids": [], "draft_pending": False}, ("refused", "formal_script_missing")),
        (
            {"formal_script": False, "pending_ids": [], "draft_pending": True},
            ("refused", "prompt_authoring_draft_pending"),
        ),
    ],
)
def test_prompt_authoring_needs_pending_entries_and_no_pending_draft(kwargs: dict, expected: tuple) -> None:
    assert _conclusion(admit_author_prompts(**kwargs)) == expected


@pytest.mark.parametrize(
    ("content_mode", "formal_script", "ad_inputs", "regenerate", "expected"),
    [
        ("ad", False, True, False, ("admitted", None)),
        ("ad", False, False, False, ("refused", "ad_brief_and_products_missing")),
        ("ad", True, True, False, ("refused", "formal_script_exists")),
        ("ad", True, False, False, ("refused", "ad_brief_and_products_missing")),
        ("ad", True, True, True, ("admitted", None)),
        ("ad", True, False, True, ("refused", "ad_brief_and_products_missing")),
        ("narration", False, True, False, ("not_applicable", "operation_not_applicable")),
    ],
)
def test_ad_script_needs_a_brief_or_products_and_regenerates_an_existing_script_only_on_request(
    content_mode: str, formal_script: bool, ad_inputs: bool, regenerate: bool, expected: tuple
) -> None:
    admission = admit_ad_script(content_mode, formal_script=formal_script, ad_inputs=ad_inputs, regenerate=regenerate)
    assert _conclusion(admission) == expected


@pytest.mark.parametrize(
    ("available_videos", "expected"),
    [(1, ("admitted", None)), (0, ("refused", "no_available_video"))],
)
def test_edit_timeline_needs_at_least_one_available_video(available_videos: int, expected: tuple) -> None:
    assert _conclusion(admit_edit_timeline(available_videos=available_videos)) == expected


def test_refusals_always_carry_a_reason_and_admission_never_does() -> None:
    for state in AdmissionState:
        reason = None if state is AdmissionState.ADMITTED else AdmissionReason.NOT_APPLICABLE
        assert OperationAdmission(state=state, reason=reason).admitted is (state is AdmissionState.ADMITTED)
