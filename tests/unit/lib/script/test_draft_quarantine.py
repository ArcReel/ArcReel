"""Unit tests for draft envelopes and violation collection.

Cover the foundation of preserving outputs: envelope round-trips, malformed-JSON degradation, collecting multiple violations, and
report rendering. Higher-level split/promotion/gate-blocking tests live in ``tests/integration/server/
agent_toolset/test_draft_tools.py``, ``tests/integration/lib/script/test_script_generator_reference_branch.py``,
and ``tests/integration/server/services/project/test_script_review.py``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lib.project.project_activity import ACTIVITY_FILENAME, recorded_project_activity
from lib.script.draft_quarantine import (
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
    QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_SCRIPT_PLAN,
    QUARANTINE_SCHEMA_VERSION,
    clear_quarantine,
    draft_revision,
    quarantine_exists,
    quarantine_path,
    read_quarantine,
    render_report,
    resolve_schema_version,
    violation_entries,
    write_quarantine,
)
from lib.script.draft_violation import (
    DraftViolation,
    DraftViolations,
    collect_violations,
    render_violation_report,
    violation_items,
)


def _violation(code: str = "unregistered_asset", label: str = "unit E1U01") -> DraftViolation:
    return DraftViolation(f"{label} 引用了未登记的资产名", code=code, label=label)


class TestEnvelope:
    def test_write_read_roundtrip_preserves_content_violations_and_meta(self, tmp_path: Path):
        path = write_quarantine(
            tmp_path,
            3,
            QUARANTINE_KIND_SCRIPT_PLAN,
            content={"units": [{"text": "镜头1：门开了"}]},
            violations=[_violation()],
            meta={"source": "source/episode_3.txt"},
        )
        assert path == quarantine_path(tmp_path, 3, QUARANTINE_KIND_SCRIPT_PLAN)

        draft = read_quarantine(tmp_path, 3, QUARANTINE_KIND_SCRIPT_PLAN)
        assert draft is not None
        assert draft.kind == QUARANTINE_KIND_SCRIPT_PLAN
        assert draft.episode == 3
        assert draft.content == {"units": [{"text": "镜头1：门开了"}]}
        assert draft.violations == [
            {
                "code": "unregistered_asset",
                "label": "unit E1U01",
                "message": "unit E1U01 引用了未登记的资产名",
                "line": None,
            }
        ]
        assert draft.meta == {"source": "source/episode_3.txt"}

    def test_write_creates_missing_drafts_dir(self, tmp_path: Path):
        """An episode without prior script_plan output has no directory; initial splitting violations are normal and must not prevent writing."""
        assert not (tmp_path / "drafts").exists()
        write_quarantine(
            tmp_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING, content={"units": []}, violations=[_violation()]
        )
        assert quarantine_exists(tmp_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING)

    def test_script_plan_and_prompt_authoring_drafts_are_separate_files(self, tmp_path: Path):
        write_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN, content={"units": []}, violations=[])
        write_quarantine(tmp_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING, content={"units": []}, violations=[])
        assert quarantine_path(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN) != quarantine_path(
            tmp_path, 1, QUARANTINE_KIND_PROMPT_AUTHORING
        )

    def test_broken_json_reads_as_none_but_still_counts_as_present(self, tmp_path: Path):
        """Agent-edited malformed JSON is an expected intermediate state; unreadable content must not count as no draft,
        allowing gates and prompt_authoring to pass."""
        path = quarantine_path(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        path.parent.mkdir(parents=True)
        path.write_text("{不是 JSON", encoding="utf-8")

        assert read_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN) is None
        assert quarantine_exists(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN) is True

    def test_envelope_without_content_object_reads_as_none(self, tmp_path: Path):
        path = quarantine_path(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"kind": QUARANTINE_KIND_SCRIPT_PLAN, "content": []}), encoding="utf-8")
        assert read_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN) is None

    def test_envelope_with_non_numeric_episode_reads_as_none(self, tmp_path: Path):
        """Nonnumeric hand-edited episode matches malformed content semantics: return None without raising, while exists remains true."""
        path = quarantine_path(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {"kind": QUARANTINE_KIND_SCRIPT_PLAN, "episode": "一", "content": {"units": []}}, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        assert read_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN) is None
        assert quarantine_exists(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN) is True

    @pytest.mark.parametrize(
        "envelope",
        [
            {"kind": QUARANTINE_KIND_PROMPT_AUTHORING, "episode": 1, "content": {"units": []}},
            {"kind": QUARANTINE_KIND_SCRIPT_PLAN, "episode": 2, "content": {"units": []}},
            {"episode": 1, "content": {"units": []}},
            {"kind": QUARANTINE_KIND_SCRIPT_PLAN, "content": {"units": []}},
        ],
        ids=["kind_mismatch", "episode_mismatch", "kind_missing", "episode_missing"],
    )
    def test_envelope_identity_must_match_requested_draft(self, tmp_path: Path, envelope: dict):
        """Mismatched/missing kind or episode is malformed shape; do not fall back to requested values.

        Without validation, both parsed fields are effectively discarded: an envelope copied from another episode could validate
        its own meta.source anchors, rebuild under this episode's unit_id values, and overwrite this episode's formal script_plan.
        """
        path = quarantine_path(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
        assert read_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN) is None
        assert quarantine_exists(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN) is True

    def test_clear_is_idempotent(self, tmp_path: Path):
        write_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN, content={"units": []}, violations=[])
        clear_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        clear_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        assert not quarantine_exists(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)


class TestSchemaVersion:
    """Envelope version: writes stamp a fixed value, absent read values mean v1, and existing-key semantics stay unchanged."""

    def _envelope_with_meta(self, tmp_path: Path, meta: dict) -> Path:
        """Write a draft envelope with exact supplied meta, bypassing write-side stamping."""
        path = quarantine_path(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "kind": QUARANTINE_KIND_SCRIPT_PLAN,
                    "episode": 1,
                    "meta": meta,
                    "violations": [],
                    "content": {"units": [{"text": "镜头1：门开了"}]},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def test_written_draft_carries_current_schema_version_on_disk(self, tmp_path: Path):
        path = write_quarantine(
            tmp_path,
            1,
            QUARANTINE_KIND_SCRIPT_PLAN,
            content={"units": []},
            violations=[],
            meta={"source": "source/episode_1.txt"},
        )
        envelope = json.loads(path.read_text(encoding="utf-8"))
        assert envelope["meta"]["schema_version"] == QUARANTINE_SCHEMA_VERSION
        assert envelope["meta"]["source"] == "source/episode_1.txt"

    def test_write_stamps_current_version_over_a_stale_one_carried_in_meta(self, tmp_path: Path):
        """Editing and rewriting a read draft produces this code version's envelope; preserving old version values would assign new semantics to old versions."""
        path = write_quarantine(
            tmp_path,
            1,
            QUARANTINE_KIND_SCRIPT_PLAN,
            content={"units": []},
            violations=[],
            meta={"schema_version": 99},
        )
        assert json.loads(path.read_text(encoding="utf-8"))["meta"]["schema_version"] == QUARANTINE_SCHEMA_VERSION

    def test_roundtrip_exposes_version_as_field_not_as_meta_key(self, tmp_path: Path):
        """Consumers read parsed results, not raw meta keys; remove the version from meta, retaining only reevaluation context."""
        write_quarantine(
            tmp_path,
            1,
            QUARANTINE_KIND_SCRIPT_PLAN,
            content={"units": []},
            violations=[],
            meta={"source": "source/episode_1.txt"},
        )
        draft = read_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        assert draft is not None
        assert draft.schema_version == QUARANTINE_SCHEMA_VERSION
        assert draft.meta == {"source": "source/episode_1.txt"}

    def test_legacy_envelope_without_version_reads_as_v1_unchanged(self, tmp_path: Path):
        """Unversioned envelopes parse as v1 and read other fields normally, following the same read path as stamped envelopes."""
        self._envelope_with_meta(tmp_path, {"source": "source/episode_1.txt"})
        draft = read_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        assert draft is not None
        assert draft.schema_version == 1
        assert draft.meta == {"source": "source/episode_1.txt"}
        assert draft.content == {"units": [{"text": "镜头1：门开了"}]}
        assert draft.violations == []

    def test_version_bit_does_not_shift_the_optimistic_concurrency_token(self, tmp_path: Path):
        """Identical content has identical revision before/after stamping; including version in tokens would reject Agents holding unstamped draft revisions
        with revision_conflict."""
        self._envelope_with_meta(tmp_path, {"source": "source/episode_1.txt"})
        legacy = read_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        write_quarantine(
            tmp_path,
            1,
            QUARANTINE_KIND_SCRIPT_PLAN,
            content={"units": [{"text": "镜头1：门开了"}]},
            violations=[],
            meta={"source": "source/episode_1.txt"},
        )
        stamped = read_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        assert legacy is not None
        assert stamped is not None
        assert draft_revision(legacy) == draft_revision(stamped)

    @pytest.mark.parametrize(
        "raw",
        ["1", 1.0, True, None, [1], {"v": 1}, 0, -3],
        ids=["str", "float", "bool", "null", "list", "dict", "zero", "negative"],
    )
    def test_unusable_version_reads_as_v1(self, tmp_path: Path, raw: object):
        """An untrusted version establishes only that this code did not write it, with only v1 semantics available;
        rejecting reads would turn an Agent-repairable/promotable draft into no draft."""
        self._envelope_with_meta(tmp_path, {"source": "source/episode_1.txt", "schema_version": raw})
        draft = read_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        assert draft is not None
        assert draft.schema_version == 1
        assert resolve_schema_version({"schema_version": raw}) == 1

    def test_future_version_is_returned_as_is_and_still_readable(self, tmp_path: Path):
        """Return future versions unchanged, without clamping or rejecting reads; this is not a migration framework, and clamping would disguise newer drafts as current."""
        future = QUARANTINE_SCHEMA_VERSION + 7
        self._envelope_with_meta(tmp_path, {"source": "source/episode_1.txt", "schema_version": future})
        draft = read_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        assert draft is not None
        assert draft.schema_version == future
        assert draft.content == {"units": [{"text": "镜头1：门开了"}]}

    def test_missing_meta_object_reads_as_v1_with_empty_context(self, tmp_path: Path):
        """Absent or nonobject meta follows missing-version semantics without raising."""
        path = quarantine_path(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"kind": QUARANTINE_KIND_SCRIPT_PLAN, "episode": 1, "meta": [], "content": {"units": []}}),
            encoding="utf-8",
        )
        draft = read_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)
        assert draft is not None
        assert draft.schema_version == 1
        assert draft.meta == {}


class TestReport:
    def test_report_names_draft_field_and_promote_tool(self, tmp_path: Path):
        """Recovery guidance states which file/field to edit and what to call afterward; Agents unaware outputs remain on disk would regenerate."""
        path = quarantine_path(tmp_path, 2, QUARANTINE_KIND_SCRIPT_PLAN)
        text = render_report(path, QUARANTINE_KIND_SCRIPT_PLAN, [_violation()], episode=2)
        assert str(path) in text
        assert "按报告字段路径修复" in text
        assert "content.units[i]" in text
        assert 'promote_draft({"episode_id": 2, "doc_type": "reference_script_plan", "base_revision":' in text
        assert "无轮次上限" in text

    def test_report_numbers_each_violation_with_its_class(self):
        text = render_violation_report([_violation("unregistered_asset"), _violation("too_many_shots", "unit E1U02")])
        assert text.splitlines()[0].startswith("1. [unregistered_asset] ")
        assert text.splitlines()[1].startswith("2. [too_many_shots] ")

    def test_drama_script_plan_report_points_at_scene_fields(self, tmp_path: Path):
        """Drama drafts edit storyboard content tables, not reference-to-video units; wrong field paths in guidance
        make Agents edit nonexistent fields and attempt promotion, wasting a round."""
        path = quarantine_path(tmp_path, 3, QUARANTINE_KIND_DRAMA_SCRIPT_PLAN)
        text = render_report(path, QUARANTINE_KIND_DRAMA_SCRIPT_PLAN, [_violation()], episode=3)

        assert path.name == "script_plan_normalized_script.invalid.json"
        assert "content.scenes[i]" in text
        assert "units[i]" not in text
        assert 'promote_draft({"episode_id": 3, "doc_type": "drama_script_plan", "base_revision":' in text

    def test_narration_script_plan_report_points_at_segment_fields(self, tmp_path: Path):
        """Narration drafts edit storyboard tables; incorrect guidance paths make Agents edit nonexistent fields
        and attempt promotion, wasting a round."""
        path = quarantine_path(tmp_path, 4, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN)
        text = render_report(path, QUARANTINE_KIND_NARRATION_SCRIPT_PLAN, [_violation()], episode=4)

        assert path.name == "script_plan_segments.invalid.json"
        assert "content.segments[i]" in text
        assert "units[i]" not in text
        assert "scenes[i]" not in text
        assert 'promote_draft({"episode_id": 4, "doc_type": "narration_script_plan", "base_revision":' in text

    def test_each_script_plan_variant_has_its_own_draft_file(self, tmp_path: Path):
        """All three script_plan draft routes coexist in one directory without overwriting; a shared filename could make route-switched projects
        read leftover drafts as pending work for their current route."""
        names = {
            quarantine_path(tmp_path, 1, kind).name
            for kind in (
                QUARANTINE_KIND_SCRIPT_PLAN,
                QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
                QUARANTINE_KIND_NARRATION_SCRIPT_PLAN,
            )
        }
        assert len(names) == 3

    def test_prompt_authoring_report_only_points_at_text(self, tmp_path: Path):
        """Only prompt_authoring unit bodies are editable; duration/source anchors are script_plan-confirmed contracts, outside repairs at this layer."""
        text = render_report(tmp_path / "d.json", QUARANTINE_KIND_PROMPT_AUTHORING, [_violation()], episode=1)
        assert "content.units[i].text" in text
        assert "source_text" not in text

    def test_entries_carry_class_and_locator(self):
        assert violation_entries([_violation("blank_shot", "unit E2U07")]) == [
            {"code": "blank_shot", "label": "unit E2U07", "message": "unit E2U07 引用了未登记的资产名", "line": None}
        ]


class TestCollectViolations:
    def test_collects_all_instead_of_stopping_at_first(self):
        def bad(code: str):
            def _check():
                raise _violation(code)

            return _check

        found = collect_violations([bad("a"), lambda: None, bad("b")])
        assert [v.code for v in found] == ["a", "b"]

    def test_non_violation_errors_are_not_swallowed(self):
        """Internal parser errors / corruption-triggered type errors propagate normally, without masquerading as content violations."""

        def boom():
            raise TypeError("脏数据")

        with pytest.raises(TypeError):
            collect_violations([boom])

    def test_aggregate_flattens_and_renders_as_report(self):
        aggregate = DraftViolations([_violation("a"), _violation("b")])
        assert [v.code for v in violation_items(aggregate)] == ["a", "b"]
        assert "[a]" in str(aggregate)
        assert "[b]" in str(aggregate)
        # Aggregates remain DraftViolation; callers need no separate single/multiple handling paths.
        assert isinstance(aggregate, DraftViolation)

    def test_single_violation_flattens_to_itself(self):
        single = _violation("a")
        assert violation_items(single) == [single]


class TestProjectActivity:
    """Draft writes/clears advance project activity; draft JSON mtime does not count toward activity."""

    STALE = datetime(2026, 3, 1, 8, 0, tzinfo=UTC)

    def _stale_ledger(self, project_path: Path) -> None:
        (project_path / ACTIVITY_FILENAME).write_text(self.STALE.isoformat(), encoding="utf-8")

    def _assert_moved_forward(self, project_path: Path) -> None:
        recorded = recorded_project_activity(project_path)
        assert recorded is not None
        assert recorded > self.STALE

    def test_write_moves_activity_forward(self, tmp_path: Path):
        self._stale_ledger(tmp_path)

        write_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN, content={"units": []}, violations=[])

        self._assert_moved_forward(tmp_path)

    def test_clear_moves_activity_forward(self, tmp_path: Path):
        write_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN, content={"units": []}, violations=[])
        self._stale_ledger(tmp_path)

        clear_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)

        self._assert_moved_forward(tmp_path)

    def test_clearing_an_absent_draft_records_nothing(self, tmp_path: Path):
        self._stale_ledger(tmp_path)

        clear_quarantine(tmp_path, 1, QUARANTINE_KIND_SCRIPT_PLAN)

        assert recorded_project_activity(tmp_path) == self.STALE
