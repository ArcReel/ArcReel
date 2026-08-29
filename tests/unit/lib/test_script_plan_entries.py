"""``lib.script_plan_entries``：条目内容指纹的构造、条目级时效判定与增量装配。"""

from __future__ import annotations

import pytest

from lib.script_plan_entries import (
    CONTENT_FIELDS,
    PLAN_ITEMS_KEY,
    PRESERVED_ON_UNCHANGED_FIELDS,
    SCRIPT_PLAN_ENTRY_REVISION_FIELD,
    ScriptPlanEntryError,
    entry_revision,
    evaluate_entry_currency,
    plan_entries_from_document,
    plan_entry_revisions,
    resolve_rewrite_ids,
    script_entries_by_id,
    splice_entries,
)


def drama_plan_entry(scene_id: str = "E1S01", **overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "scene_id": scene_id,
        "duration_seconds": 8,
        "segment_break": False,
        "characters_in_scene": ["主角"],
        "scenes": ["酒馆"],
        "props": [],
        "scene_description": "主角推门而入",
        "utterances": [{"kind": "dialogue", "speaker": "主角", "text": "来一杯"}],
        "source_text": "他推开了酒馆的门。",
    }
    entry.update(overrides)
    return entry


def narration_plan_entry(segment_id: str = "E1S01", **overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "segment_id": segment_id,
        "novel_text": "他推开了酒馆的门。",
        "duration_seconds": 6,
        "segment_break": False,
        "characters_in_segment": ["主角"],
        "scenes": ["酒馆"],
        "props": [],
    }
    entry.update(overrides)
    return entry


def reference_plan_entry(unit_id: str = "E1U01", **overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "unit_id": unit_id,
        "text": "@[主角] 推门而入",
        "duration_seconds": 4,
        "source_text": "他推开了酒馆的门。",
    }
    entry.update(overrides)
    return entry


PLAN_FACTORIES = {
    "drama": drama_plan_entry,
    "narration": narration_plan_entry,
    "reference_video": reference_plan_entry,
}


class TestEntryRevision:
    @pytest.mark.parametrize("kind", sorted(PLAN_FACTORIES))
    def test_digest_is_prefixed_and_stable_across_key_order(self, kind: str) -> None:
        entry = PLAN_FACTORIES[kind]()
        reordered = dict(reversed(list(entry.items())))
        revision = entry_revision(kind, entry)
        assert revision.startswith("sha256-v1:")
        assert entry_revision(kind, reordered) == revision

    @pytest.mark.parametrize("kind", sorted(PLAN_FACTORIES))
    def test_every_content_field_moves_the_digest(self, kind: str) -> None:
        """内容字段清单是契约：任何一项改动都必须让该条目失配，否则改它不会触发重写。"""
        entry = PLAN_FACTORIES[kind]()
        baseline = entry_revision(kind, entry)
        for field in CONTENT_FIELDS[kind]:
            value = entry[field]
            mutated = {
                **entry,
                field: (value + 1) if isinstance(value, int) and not isinstance(value, bool) else _flip(value),
            }
            assert entry_revision(kind, mutated) != baseline, field

    @pytest.mark.parametrize("kind", sorted(PLAN_FACTORIES))
    def test_id_and_runtime_state_stay_out_of_the_digest(self, kind: str) -> None:
        entry = PLAN_FACTORIES[kind]()
        baseline = entry_revision(kind, entry)
        assert entry_revision(kind, {**entry, "generated_assets": {"status": "completed"}}) == baseline
        assert entry_revision(kind, {**entry, "needs_replan": True}) == baseline

    def test_unknown_plan_kind_fails_loud(self) -> None:
        with pytest.raises(ScriptPlanEntryError):
            entry_revision("ad", {})


def _flip(value: object) -> object:
    if isinstance(value, bool):
        return not value
    if isinstance(value, str):
        return value + "改"
    if isinstance(value, list):
        return [*value, "新增"]
    return value


class TestPlanEntryRevisions:
    def test_keys_use_the_rewritten_episode_prefix(self) -> None:
        revisions = plan_entry_revisions("drama", [drama_plan_entry("E1S01")], episode=3)
        assert list(revisions) == ["E3S01"]

    def test_duplicate_ids_after_rewrite_fail_loud(self) -> None:
        with pytest.raises(ScriptPlanEntryError, match="重复"):
            plan_entry_revisions("drama", [drama_plan_entry("E1S01"), drama_plan_entry("E2S01")], episode=2)

    def test_missing_id_fails_loud(self) -> None:
        entry = drama_plan_entry()
        del entry["scene_id"]
        with pytest.raises(ScriptPlanEntryError, match="scene_id"):
            plan_entry_revisions("drama", [entry], episode=1)


class TestPlanEntriesFromDocument:
    @pytest.mark.parametrize("kind", ["drama", "narration", "reference_video"])
    def test_reads_each_variant_items_key(self, kind: str) -> None:
        entry = PLAN_FACTORIES[kind]()
        document = {PLAN_ITEMS_KEY[kind]: [entry]}
        assert len(plan_entries_from_document(kind, document)) == 1

    @pytest.mark.parametrize("kind", ["narration", "reference_video"])
    def test_defaulted_fields_are_filled_like_the_generator_sees_them(self, kind: str) -> None:
        """带草稿模型的两个变体经模型归一：生成侧消费的正是归一后的条目，两侧指纹才同源。"""
        omitted = {"narration": "segment_break", "reference_video": "source_text"}[kind]
        entry = PLAN_FACTORIES[kind]()
        entry.pop(omitted)
        [normalized] = plan_entries_from_document(kind, {PLAN_ITEMS_KEY[kind]: [entry]})
        assert omitted in normalized

    @pytest.mark.parametrize("kind", ["narration", "reference_video"])
    def test_entries_the_draft_model_rejects_yield_nothing(self, kind: str) -> None:
        """归一失败即「没有可比对的条目」，判定退回整集口径，不另造一类错误。"""
        assert plan_entries_from_document(kind, {PLAN_ITEMS_KEY[kind]: [{"乱写": 1}]}) == []

    @pytest.mark.parametrize("document", [None, [], {"scenes": "坏形状"}, {}])
    def test_malformed_document_yields_no_entries(self, document: object) -> None:
        assert plan_entries_from_document("drama", document) == []


class TestEvaluateEntryCurrency:
    def _script(self, *entries: dict[str, object]) -> dict[str, object]:
        return {"scenes": list(entries)}

    def test_matching_entries_are_current(self) -> None:
        revisions = plan_entry_revisions("drama", [drama_plan_entry()], episode=1)
        script = self._script({"scene_id": "E1S01", SCRIPT_PLAN_ENTRY_REVISION_FIELD: revisions["E1S01"]})
        currency = evaluate_entry_currency(
            "drama", script=script, plan_revisions=revisions, legacy_entries_current=False
        )
        assert not currency.is_stale
        assert currency.outdated_ids == ()

    def test_only_the_changed_entry_goes_stale(self) -> None:
        plan = [drama_plan_entry("E1S01"), drama_plan_entry("E1S02")]
        before = plan_entry_revisions("drama", plan, episode=1)
        script = self._script(
            {"scene_id": "E1S01", SCRIPT_PLAN_ENTRY_REVISION_FIELD: before["E1S01"]},
            {"scene_id": "E1S02", SCRIPT_PLAN_ENTRY_REVISION_FIELD: before["E1S02"]},
        )
        plan[1]["source_text"] = "改了一个错别字。"
        after = plan_entry_revisions("drama", plan, episode=1)
        currency = evaluate_entry_currency("drama", script=script, plan_revisions=after, legacy_entries_current=False)
        assert currency.stale_ids == ("E1S02",)
        assert currency.outdated_ids == ("E1S02",)
        assert currency.is_stale

    def test_added_removed_and_reordered_entries_are_reported(self) -> None:
        plan = [drama_plan_entry("E1S02"), drama_plan_entry("E1S01"), drama_plan_entry("E1S03")]
        revisions = plan_entry_revisions("drama", plan, episode=1)
        script = self._script(
            {"scene_id": "E1S01", SCRIPT_PLAN_ENTRY_REVISION_FIELD: revisions["E1S01"]},
            {"scene_id": "E1S02", SCRIPT_PLAN_ENTRY_REVISION_FIELD: revisions["E1S02"]},
            {"scene_id": "E1S09", SCRIPT_PLAN_ENTRY_REVISION_FIELD: "sha256-v1:" + "0" * 64},
        )
        currency = evaluate_entry_currency(
            "drama", script=script, plan_revisions=revisions, legacy_entries_current=False
        )
        assert currency.new_ids == ("E1S03",)
        assert currency.removed_ids == ("E1S09",)
        assert currency.order_changed is True
        assert currency.outdated_ids == ("E1S03",)

    def test_legacy_entries_follow_the_whole_plan_revision(self) -> None:
        """存量剧本没有条目指纹：整集指纹仍相等时不误报，不相等时全部失配。"""
        revisions = plan_entry_revisions("drama", [drama_plan_entry()], episode=1)
        script = self._script({"scene_id": "E1S01"})
        assert not evaluate_entry_currency(
            "drama", script=script, plan_revisions=revisions, legacy_entries_current=True
        ).is_stale
        assert evaluate_entry_currency(
            "drama", script=script, plan_revisions=revisions, legacy_entries_current=False
        ).stale_ids == ("E1S01",)


class TestResolveRewriteIds:
    def _currency(self):
        plan = [drama_plan_entry("E1S01"), drama_plan_entry("E1S02")]
        revisions = plan_entry_revisions("drama", plan, episode=1)
        script = {
            "scenes": [
                {"scene_id": "E1S01", SCRIPT_PLAN_ENTRY_REVISION_FIELD: revisions["E1S01"]},
                {"scene_id": "E1S02", SCRIPT_PLAN_ENTRY_REVISION_FIELD: "sha256-v1:" + "0" * 64},
            ]
        }
        return evaluate_entry_currency("drama", script=script, plan_revisions=revisions, legacy_entries_current=False)

    def test_default_and_stale_select_outdated_entries(self) -> None:
        currency = self._currency()
        assert resolve_rewrite_ids(None, currency) == ("E1S02",)
        assert resolve_rewrite_ids("stale", currency) == ("E1S02",)

    def test_all_selects_every_plan_entry(self) -> None:
        assert resolve_rewrite_ids("all", self._currency()) == ("E1S01", "E1S02")

    def test_explicit_ids_keep_plan_order(self) -> None:
        assert resolve_rewrite_ids(["E1S02", "E1S01"], self._currency()) == ("E1S01", "E1S02")

    def test_unknown_explicit_id_fails_loud(self) -> None:
        with pytest.raises(ScriptPlanEntryError, match="E9S99"):
            resolve_rewrite_ids(["E9S99"], self._currency())

    def test_unknown_scope_string_fails_loud(self) -> None:
        with pytest.raises(ScriptPlanEntryError, match="scope"):
            resolve_rewrite_ids("everything", self._currency())


class TestSpliceEntries:
    def test_unchanged_entries_pass_through_verbatim_and_get_stamped(self) -> None:
        revisions = plan_entry_revisions("drama", [drama_plan_entry("E1S01"), drama_plan_entry("E1S02")], episode=1)
        kept = {
            "scene_id": "E1S01",
            "image_prompt": {"scene": "旧图"},
            "video_prompt": {"action": "旧动作"},
            "note": "用户备注",
            "end_frame_image": "end_frames/scene_E1S01.png",
            "transition_to_next": "fade",
            "generated_assets": {"status": "completed"},
            "needs_replan": False,
            SCRIPT_PLAN_ENTRY_REVISION_FIELD: revisions["E1S01"],
        }
        entries = splice_entries(
            "drama",
            plan_revisions=revisions,
            rewritten=[{"scene_id": "E1S02", "image_prompt": {"scene": "新图"}}],
            existing={"E1S01": kept, "E1S02": {"scene_id": "E1S02"}},
        )
        assert entries[0] == kept
        assert [entry["scene_id"] for entry in entries] == ["E1S01", "E1S02"]
        assert entries[1][SCRIPT_PLAN_ENTRY_REVISION_FIELD] == revisions["E1S02"]

    def test_every_preserved_field_survives(self) -> None:
        revisions = plan_entry_revisions("drama", [drama_plan_entry("E1S01")], episode=1)
        kept = {"scene_id": "E1S01", SCRIPT_PLAN_ENTRY_REVISION_FIELD: revisions["E1S01"]} | {
            field: f"值-{field}" for field in PRESERVED_ON_UNCHANGED_FIELDS
        }
        entries = splice_entries("drama", plan_revisions=revisions, rewritten=[], existing={"E1S01": kept})
        assert all(entries[0][field] == f"值-{field}" for field in PRESERVED_ON_UNCHANGED_FIELDS)

    def test_rewritten_entry_keeps_note_and_transition_but_not_assets(self) -> None:
        revisions = plan_entry_revisions("drama", [drama_plan_entry("E1S01")], episode=1)
        previous = {
            "scene_id": "E1S01",
            "note": "用户备注",
            "transition_to_next": "fade",
            "generated_assets": {"status": "completed"},
            "end_frame_image": "end_frames/scene_E1S01.png",
        }
        entries = splice_entries(
            "drama",
            plan_revisions=revisions,
            rewritten=[{"scene_id": "E1S01", "image_prompt": {"scene": "新图"}}],
            existing={"E1S01": previous},
        )
        assert entries[0]["note"] == "用户备注"
        assert entries[0]["transition_to_next"] == "fade"
        assert "generated_assets" not in entries[0]
        assert "end_frame_image" not in entries[0]

    def test_entry_neither_rewritten_nor_previously_present_fails_loud(self) -> None:
        revisions = plan_entry_revisions("drama", [drama_plan_entry("E1S01")], episode=1)
        with pytest.raises(ScriptPlanEntryError, match="E1S01"):
            splice_entries("drama", plan_revisions=revisions, rewritten=[], existing={})

    def test_rewritten_entry_outside_the_plan_fails_loud(self) -> None:
        revisions = plan_entry_revisions("drama", [drama_plan_entry("E1S01")], episode=1)
        with pytest.raises(ScriptPlanEntryError, match="E1S09"):
            splice_entries(
                "drama",
                plan_revisions=revisions,
                rewritten=[{"scene_id": "E1S09"}],
                existing={"E1S01": {"scene_id": "E1S01"}},
            )


class TestScriptEntriesById:
    def test_skips_malformed_items_instead_of_raising(self) -> None:
        script = {"scenes": ["坏条目", {"no_id": 1}, {"scene_id": "E1S01"}]}
        assert list(script_entries_by_id("drama", script)) == ["E1S01"]
