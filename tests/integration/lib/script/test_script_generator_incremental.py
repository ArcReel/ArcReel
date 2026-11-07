"""Entry-level formal-script maintenance: confirmation materializes whole pending scripts; authoring fills gaps by default and rewrites explicitly.

Drama/narration/reference_video share criteria: author pending entries only by default, scope via ``entry_ids``, explicit ``rewrite``, byte-identical other entries, no planning reads. Materialization projects whole content layers and rejects unconfirmed plans/scripts."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.artifacts.artifact_activation import activate_artifact_target_state
from lib.config.resolver import ConfigResolver
from lib.project import project_trash
from lib.project.project_manager import ProjectManager, ScriptWriteConflict
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script import script_generator as script_generator_module
from lib.script import script_review
from lib.script.item_ids import format_item_id
from lib.script.prompt_authoring_scope import PromptOverwriteRequired
from lib.script.script_batch_edit import ScriptBatchEditCommand, ScriptBatchEditor, blank_item_after, script_revision
from lib.script.script_document import SCRIPT_PLAN_CONVERSION_GENERATOR
from lib.script.script_generator import PromptAuthoringTargetError, ScriptGenerator
from lib.script.script_models import PENDING_AUTHORING_FIELD
from tests.fakes import FakeConfigResolver

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("video_request_facts")]


# ---------------------------------------------------------------------------
# Project/script-plan assembly
# ---------------------------------------------------------------------------


def _activate(project_dir: Path, episode: int = 1) -> None:
    source = project_dir / "source" / f"episode_{episode}.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("原文", encoding="utf-8")
    activate_artifact_target_state(project_dir, bump_schema=False)


def _write_project(tmp_path: Path, **overrides: Any) -> Path:
    project_dir = tmp_path / "projects" / "proj"
    project_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "title": "项目",
        "content_mode": "narration",
        "generation_mode": "storyboard",
        "overview": {"synopsis": "s", "genre": "g", "theme": "th", "world_setting": "w"},
        "style": "国漫",
        "style_description": "水墨",
        "characters": {"主角": {"description": "d"}},
        "scenes": {"酒馆": {"description": "d"}},
        "props": {},
        "episodes": [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
    }
    payload.update(overrides)
    (project_dir / "project.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return project_dir


def _write_plan(project_dir: Path, filename: str, document: dict[str, Any]) -> Path:
    path = project_dir / "drafts" / "episode_1" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    _activate(project_dir)
    return path


def _fake_generator(responses: list[str]) -> MagicMock:
    """Yield prepared responses in call order, exhausting extra calls to assert no model call this round."""
    generator = MagicMock()
    generator.model = "mock"
    generator.generate = AsyncMock(side_effect=[MagicMock(text=text) for text in responses])
    return generator


def _script(project_dir: Path) -> dict[str, Any]:
    return json.loads((project_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Three variant assemblers create project + planning + visual responses.
# ---------------------------------------------------------------------------


def _narration_plan(*, texts: tuple[str, ...] = ("原文甲。", "原文乙。")) -> dict[str, Any]:
    return {
        "episode": 1,
        "segments": [
            {
                "segment_id": f"E1S{index + 1:02d}",
                "novel_text": text,
                "duration_seconds": 4,
                "segment_break": False,
                "characters_in_segment": ["主角"],
                "scenes": ["酒馆"],
                "props": [],
            }
            for index, text in enumerate(texts)
        ],
    }


def _narration_visual(*ids: str, mark: str = "画面") -> str:
    return json.dumps(
        {
            "title": "第一集",
            "segments": [
                {
                    "segment_id": entry_id,
                    "image_prompt": {
                        "scene": f"{mark}-{entry_id}",
                        "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
                    },
                    "video_prompt": {
                        "action": "动作",
                        "camera_motion": "Static",
                        "ambiance_audio": "风声",
                        "dialogue": [],
                    },
                }
                for entry_id in ids
            ],
        },
        ensure_ascii=False,
    )


def _drama_plan(*, texts: tuple[str, ...] = ("原文甲。", "原文乙。")) -> dict[str, Any]:
    return {
        "title": "第一集",
        "scenes": [
            {
                "scene_id": f"E1S{index + 1:02d}",
                "duration_seconds": 8,
                "segment_break": False,
                "characters_in_scene": ["主角"],
                "scenes": ["酒馆"],
                "props": [],
                "scene_description": "主角推门而入",
                "utterances": [{"kind": "dialogue", "speaker": "主角", "text": "来一杯"}],
                "source_text": text,
            }
            for index, text in enumerate(texts)
        ],
    }


def _drama_visual(*ids: str, mark: str = "画面") -> str:
    return json.dumps(
        {
            "scenes": [
                {
                    "scene_id": entry_id,
                    "image_prompt": {
                        "scene": f"{mark}-{entry_id}",
                        "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
                    },
                    "video_prompt": {"action": "动作", "camera_motion": "Static", "ambiance_audio": "风声"},
                }
                for entry_id in ids
            ]
        },
        ensure_ascii=False,
    )


def _reference_plan(*, texts: tuple[str, ...] = ("原文甲。", "原文乙。")) -> dict[str, Any]:
    return {
        "units": [
            {
                "unit_id": f"E1U{index + 1:02d}",
                "text": f"@[主角] 推开 @[酒馆] 的门（第{index + 1}镜）",
                "duration_seconds": 4,
                "source_text": text,
            }
            for index, text in enumerate(texts)
        ]
    }


def _reference_visual(*ids: str, mark: str = "镜头1") -> str:
    return json.dumps(
        {
            "title": "第一集",
            "units": [{"text": f"{mark}：中景，平视。@[主角] 推开 @[酒馆] 的门。"} for _ in ids],
        },
        ensure_ascii=False,
    )


class _Variant:
    """All differences for one script-planning variant in this case group."""

    def __init__(
        self,
        name: str,
        *,
        project_overrides: dict[str, Any],
        plan_filename: str,
        items_key: str,
        id_field: str,
        plan_factory,
        visual_factory,
        entry_ids: tuple[str, str],
        omissible_field: str | None = None,
        prompt_needles: tuple[str, str] | None = None,
    ) -> None:
        self.name = name
        self.project_overrides = project_overrides
        self.plan_filename = plan_filename
        self.items_key = items_key
        self.id_field = id_field
        self.plan_factory = plan_factory
        self.visual_factory = visual_factory
        self.entry_ids = entry_ids
        #: Optional defaulted plan-content field that may be wholly absent; drama has no draft model, so None.
        self.omissible_field = omissible_field
        #: Recognizable prompt strings for each of two entries; reference-video authoring does not render unit_id,
        #: so identify bodies only.
        self.prompt_needles = prompt_needles or entry_ids

    def build(self, tmp_path: Path, *, texts: tuple[str, ...] | None = None) -> tuple[Path, Path]:
        project_dir = _write_project(tmp_path, **self.project_overrides)
        document = self.plan_factory() if texts is None else self.plan_factory(texts=texts)
        return project_dir, _write_plan(project_dir, self.plan_filename, document)

    def generator(self, project_dir: Path, responses: list[str]) -> ScriptGenerator:
        return ScriptGenerator(
            project_dir,
            generator=_fake_generator(responses),
            config_resolver=cast(ConfigResolver, FakeConfigResolver(supported_durations=(4, 6, 8))),
        )


NARRATION = _Variant(
    "narration",
    project_overrides={"content_mode": "narration", "generation_mode": "storyboard"},
    plan_filename="script_plan_segments.json",
    items_key="segments",
    id_field="segment_id",
    plan_factory=_narration_plan,
    visual_factory=_narration_visual,
    entry_ids=("E1S01", "E1S02"),
    omissible_field="segment_break",
)

DRAMA = _Variant(
    "drama",
    project_overrides={"content_mode": "drama", "generation_mode": "storyboard"},
    plan_filename="script_plan_normalized_script.json",
    items_key="scenes",
    id_field="scene_id",
    plan_factory=_drama_plan,
    visual_factory=_drama_visual,
    entry_ids=("E1S01", "E1S02"),
)

REFERENCE = _Variant(
    "reference_video",
    project_overrides={
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "video_backend": "vidu/vidu2.0",
        "episodes": [
            {
                "episode": 1,
                "title": "第一集",
                "script_file": "scripts/episode_1.json",
                "generation_mode": "reference_video",
            }
        ],
    },
    plan_filename="script_plan_reference_units.json",
    items_key="video_units",
    id_field="unit_id",
    plan_factory=_reference_plan,
    visual_factory=_reference_visual,
    entry_ids=("E1U1", "E1U2"),
    omissible_field="source_text",
    prompt_needles=("第1镜", "第2镜"),
)

VARIANTS = [NARRATION, DRAMA, REFERENCE]


@pytest.fixture(params=VARIANTS, ids=lambda variant: variant.name)
def variant(request) -> _Variant:
    return request.param


#: Entry-array keys in intermediate script-plan files; script-side keys are ``_Variant.items_key``.
_PLAN_ENTRIES_KEY = {"narration": "segments", "drama": "scenes", "reference_video": "units"}

#: User edits and paid-artifact references must remain unchanged on unaffected entries, per issue acceptance.
_USER_FIELDS: dict[str, Any] = {
    "note": "用户备注",
    "generated_assets": {"storyboard_image": "storyboards/scene.png", "status": "storyboard_ready"},
}


def _entries(script: dict[str, Any], variant: _Variant) -> dict[str, dict[str, Any]]:
    return {entry[variant.id_field]: entry for entry in script[variant.items_key]}


def _stamp_user_fields(project_dir: Path, variant: _Variant, entry_id: str) -> None:
    """Write user fields directly into on-disk scripts; user edits/artifact generation create them, never prompt authoring."""
    path = project_dir / "scripts" / "episode_1.json"
    script = json.loads(path.read_text(encoding="utf-8"))
    for entry in script[variant.items_key]:
        if entry[variant.id_field] == entry_id:
            entry.update(_USER_FIELDS)
            if variant is not REFERENCE:
                # Reference-video units have no end-frame field.
                entry["end_frame_image"] = "end_frames/scene.png"
    path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")


def _rewrite_script(project_dir: Path, mutate: Callable[[dict[str, Any]], None]) -> None:
    path = project_dir / "scripts" / "episode_1.json"
    script = json.loads(path.read_text(encoding="utf-8"))
    mutate(script)
    path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")


def _revision(project_dir: Path) -> str | None:
    return script_review.content_fingerprint(project_dir / "scripts" / "episode_1.json")


def _entry_json(project_dir: Path, variant: _Variant, entry_id: str) -> str:
    return json.dumps(_entries(_script(project_dir), variant)[entry_id], ensure_ascii=False, sort_keys=True)


def _converter(project_dir: Path) -> ScriptGenerator:
    """Content-confirmation materialization calls no text model; construct without generator like dry-run."""
    return ScriptGenerator(
        project_dir,
        config_resolver=cast(ConfigResolver, FakeConfigResolver(supported_durations=(4, 6, 8))),
    )


async def _materialize(project_dir: Path, plan_path: Path):
    """Materialize complete planning into formal scripts against current planning and accepted formal-script overwrite."""
    plan_revision = script_review.content_fingerprint(plan_path)
    assert plan_revision is not None
    return await _converter(project_dir).materialize_script_plan(
        1,
        expected_plan_revision=plan_revision,
        expected_script_fingerprint=script_review.content_fingerprint(project_dir / "scripts" / "episode_1.json"),
        project_update=lambda _project: None,
    )


async def _converted_and_authored(
    tmp_path: Path, variant: _Variant, *, mark: str = "首轮", texts: tuple[str, ...] | None = None
) -> tuple[Path, Path]:
    """Materialize planning and author all pending entries, yielding visual layers everywhere without pending markers."""
    first, second = variant.entry_ids
    project_dir, plan_path = variant.build(tmp_path, texts=texts)
    await _materialize(project_dir, plan_path)
    await variant.generator(project_dir, [variant.visual_factory(first, second, mark=mark)]).generate(1)
    return project_dir, plan_path


#: Variant visual fields: authoring writes only these (reference-video rewrites unit bodies).
_VISUAL_FIELDS = {
    "narration": ("image_prompt", "video_prompt"),
    "drama": ("image_prompt", "video_prompt"),
    "reference_video": ("text",),
}


def _without_visual_layer(entry: dict[str, Any], variant: _Variant) -> dict[str, Any]:
    hidden = {*_VISUAL_FIELDS[variant.name], PENDING_AUTHORING_FIELD}
    return {key: value for key, value in entry.items() if key not in hidden}


def _authored_with(entry: dict[str, Any], variant: _Variant, mark: str) -> bool:
    """Entry visual layers come from the response carrying ``mark``."""
    if variant is REFERENCE:
        return entry["text"].startswith(f"{mark}：")
    return entry["image_prompt"]["scene"] == f"{mark}-{entry[variant.id_field]}"


def _plan_text_field(variant: _Variant) -> str:
    return "novel_text" if variant is NARRATION else "source_text"


@pytest.fixture(params=[NARRATION, DRAMA], ids=lambda variant: variant.name)
def prompt_variant(request) -> _Variant:
    """Two variants carry image_prompt / video_prompt; reference-video visual layers are unit bodies without prompt fields."""
    return request.param


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


class TestPromptAuthoring:
    """Author only formal-script entries: pending by default, ``entry_ids`` scoping, explicit ``rewrite``, byte-identical others."""

    async def test_manually_added_entry_is_the_only_one_authored(self, tmp_path: Path, variant: _Variant) -> None:
        first, second = variant.entry_ids
        project_dir, _plan_path = await _converted_and_authored(tmp_path, variant)
        _stamp_user_fields(project_dir, variant, first)
        third = format_item_id(1, variant.items_key, 3)

        def add_pending_entry(script: dict[str, Any]) -> None:
            entry = copy.deepcopy(script[variant.items_key][1])
            entry[variant.id_field] = third
            if variant is REFERENCE:
                entry["text"] = "@[主角] 推开 @[酒馆] 的门（手动新增）"
            else:
                entry["image_prompt"] = None
                entry["video_prompt"] = None
            entry[PENDING_AUTHORING_FIELD] = True
            script[variant.items_key].append(entry)

        _rewrite_script(project_dir, add_pending_entry)
        before = {entry_id: _entry_json(project_dir, variant, entry_id) for entry_id in (first, second)}
        added = _entries(_script(project_dir), variant)[third]

        rewritten: list[str] = []
        rerun = variant.generator(project_dir, [variant.visual_factory(third, mark="补写")])
        await rerun.generate(1, rewritten_entry_ids=rewritten)

        assert rewritten == [third]
        assert {entry_id: _entry_json(project_dir, variant, entry_id) for entry_id in (first, second)} == before
        after = _entries(_script(project_dir), variant)[third]
        assert _authored_with(after, variant, "补写")
        assert PENDING_AUTHORING_FIELD not in after
        assert _without_visual_layer(after, variant) == _without_visual_layer(added, variant)

    async def test_timeline_insert_and_remove_survive_default_authoring(
        self, tmp_path: Path, prompt_variant: _Variant
    ) -> None:
        """After timeline additions/removals, fill new blanks without resurrecting removed storyboards."""
        variant = prompt_variant
        first, second = variant.entry_ids
        project_dir, _plan_path = await _converted_and_authored(tmp_path, variant)
        pm = ProjectManager.for_project_dir(project_dir)
        editor = ScriptBatchEditor(pm)

        def edit(operation: dict[str, Any]) -> None:
            current = pm.load_script(project_dir.name, "episode_1.json")
            command = ScriptBatchEditCommand.model_validate(
                {"script": "episode_1.json", "expected_revision": script_revision(current), "operations": [operation]}
            )
            result = editor.execute(project_dir.name, command)
            assert result.success is True, result.problems

        item = blank_item_after(pm.load_script(project_dir.name, "episode_1.json"), first)
        if variant is NARRATION:
            item["novel_text"] = "手动新增的旁白。"
        edit({"op": "insert_after", "after_id": first, "item": item})
        edit({"op": "remove", "id": second})
        added = item[variant.id_field]
        before_first = _entry_json(project_dir, variant, first)

        rewritten: list[str] = []
        await variant.generator(project_dir, [variant.visual_factory(added, mark="补写")]).generate(
            1, rewritten_entry_ids=rewritten
        )

        assert rewritten == [added]
        after = _script(project_dir)[variant.items_key]
        assert [entry[variant.id_field] for entry in after] == [first, added]
        assert _authored_with(after[1], variant, "补写")
        assert PENDING_AUTHORING_FIELD not in after[1]
        assert _entry_json(project_dir, variant, first) == before_first

    async def test_nothing_pending_leaves_the_script_untouched(self, tmp_path: Path, variant: _Variant) -> None:
        project_dir, _plan_path = await _converted_and_authored(tmp_path, variant)
        before = (project_dir / "scripts" / "episode_1.json").read_bytes()

        # Prepare no responses: any model call raises AsyncMock StopIteration.
        rewritten: list[str] = []
        await variant.generator(project_dir, []).generate(1, rewritten_entry_ids=rewritten)

        assert rewritten == []
        assert (project_dir / "scripts" / "episode_1.json").read_bytes() == before

    @pytest.mark.parametrize("spelling", ["prefixed", "bare"])
    async def test_entry_ids_rewrite_keeps_content_and_user_fields(
        self, tmp_path: Path, variant: _Variant, spelling: str
    ) -> None:
        """Explicitly rewrite existing visual layers only for selected entries; preserve content/user fields."""
        first, second = variant.entry_ids
        project_dir, _plan_path = await _converted_and_authored(tmp_path, variant)
        _stamp_user_fields(project_dir, variant, first)
        before_first = _entries(_script(project_dir), variant)[first]
        before_second = _entry_json(project_dir, variant, second)

        rewritten: list[str] = []
        rerun = variant.generator(project_dir, [variant.visual_factory(first, mark="点名")])
        revision = _revision(project_dir)
        if spelling == "prefixed":
            revision = "sha256-v1:" + revision
        await rerun.generate(
            1, entry_ids=[first], rewrite=True, overwrite_revision=revision, rewritten_entry_ids=rewritten
        )

        assert rewritten == [first]
        after_first = _entries(_script(project_dir), variant)[first]
        assert _authored_with(after_first, variant, "点名")
        assert _without_visual_layer(after_first, variant) == _without_visual_layer(before_first, variant)
        assert _entry_json(project_dir, variant, second) == before_second

    async def test_unknown_entry_id_fails_without_writing(self, tmp_path: Path, variant: _Variant) -> None:
        project_dir, _plan_path = await _converted_and_authored(tmp_path, variant)
        before = (project_dir / "scripts" / "episode_1.json").read_bytes()

        with pytest.raises(PromptAuthoringTargetError, match="不在集（id=1）正式脚本内"):
            await variant.generator(project_dir, []).generate(1, entry_ids=["E9U99"])

        assert (project_dir / "scripts" / "episode_1.json").read_bytes() == before

    @pytest.mark.parametrize("plan_state", ["missing", "diverged"])
    async def test_authoring_does_not_read_the_script_plan(
        self, tmp_path: Path, variant: _Variant, plan_state: str
    ) -> None:
        """Missing or inconsistent script planning does not block authoring; input consists only of formal entries."""
        first, second = variant.entry_ids
        project_dir, plan_path = variant.build(tmp_path)
        await _materialize(project_dir, plan_path)
        before = _entries(_script(project_dir), variant)
        if plan_state == "missing":
            plan_path.unlink()
        else:
            entries_key = _PLAN_ENTRIES_KEY[variant.name]
            document = json.loads(plan_path.read_text(encoding="utf-8"))
            document[entries_key] = document[entries_key][:1]
            document[entries_key][0][_plan_text_field(variant)] = "规划里改过的原文。"
            plan_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            _activate(project_dir)

        rewritten: list[str] = []
        await variant.generator(project_dir, [variant.visual_factory(first, second)]).generate(
            1, rewritten_entry_ids=rewritten
        )

        assert rewritten == [first, second]
        after = _entries(_script(project_dir), variant)
        for entry_id in (first, second):
            assert PENDING_AUTHORING_FIELD not in after[entry_id]
            assert _without_visual_layer(after[entry_id], variant) == _without_visual_layer(before[entry_id], variant)

    async def test_missing_formal_script_is_refused(self, tmp_path: Path, variant: _Variant) -> None:
        project_dir, _plan_path = variant.build(tmp_path)

        with pytest.raises(PromptAuthoringTargetError, match="尚无正式脚本"):
            await variant.generator(project_dir, []).generate(1)

        assert not (project_dir / "scripts" / "episode_1.json").exists()


class TestFillAndRewrite:
    """Gap filling preserves existing complete image/video prompt units; overwrites require explicit rewrite and overwrite tokens."""

    @staticmethod
    async def _materialized(tmp_path: Path, variant: _Variant) -> Path:
        project_dir, plan_path = variant.build(tmp_path)
        await _materialize(project_dir, plan_path)
        return project_dir

    @staticmethod
    def _set_entry(project_dir: Path, variant: _Variant, entry_id: str, **fields: Any) -> None:
        def mutate(script: dict[str, Any]) -> None:
            for entry in script[variant.items_key]:
                if entry[variant.id_field] == entry_id:
                    entry.update(fields)

        _rewrite_script(project_dir, mutate)

    async def test_fill_keeps_the_existing_image_prompt_and_adds_the_video_prompt(
        self, tmp_path: Path, prompt_variant: _Variant
    ) -> None:
        variant = prompt_variant
        first, second = variant.entry_ids
        project_dir = await self._materialized(tmp_path, variant)
        self._set_entry(project_dir, variant, first, image_prompt="手写的分镜图提示词")

        rewritten: list[str] = []
        await variant.generator(project_dir, [variant.visual_factory(first, second, mark="补缺")]).generate(
            1, rewritten_entry_ids=rewritten
        )

        assert rewritten == [first, second]
        after = _entries(_script(project_dir), variant)
        assert after[first]["image_prompt"] == "手写的分镜图提示词"
        assert isinstance(after[first]["video_prompt"], dict)
        assert PENDING_AUTHORING_FIELD not in after[first]
        assert _authored_with(after[second], variant, "补缺")

    async def test_selected_entry_with_a_complete_visual_layer_is_skipped_when_filling(
        self, tmp_path: Path, variant: _Variant
    ) -> None:
        """Selection defines scope only: gap filling neither writes complete visual layers nor calls models for them."""
        first, _second = variant.entry_ids
        project_dir, _plan_path = await _converted_and_authored(tmp_path, variant)
        before = (project_dir / "scripts" / "episode_1.json").read_bytes()

        rewritten: list[str] = []
        skipped: list[str] = []
        await variant.generator(project_dir, []).generate(
            1, entry_ids=[first], rewritten_entry_ids=rewritten, skipped_entry_ids=skipped
        )

        assert (rewritten, skipped) == ([], [first])
        assert (project_dir / "scripts" / "episode_1.json").read_bytes() == before

    async def test_rewrite_over_existing_content_requires_the_overwrite_token(
        self, tmp_path: Path, variant: _Variant
    ) -> None:
        first, _second = variant.entry_ids
        project_dir, _plan_path = await _converted_and_authored(tmp_path, variant)
        before = (project_dir / "scripts" / "episode_1.json").read_bytes()

        with pytest.raises(PromptOverwriteRequired) as caught:
            await variant.generator(project_dir, []).generate(1, entry_ids=[first], rewrite=True)

        expected_fields = list(_VISUAL_FIELDS[variant.name])
        assert caught.value.overwrite.to_dict() == {
            "revision": script_review.published_revision(_revision(project_dir)),
            "entries": [{"id": first, "fields": expected_fields}],
        }
        assert (project_dir / "scripts" / "episode_1.json").read_bytes() == before

    async def test_a_token_for_an_older_script_is_refused(self, tmp_path: Path, variant: _Variant) -> None:
        """Formal edits after acceptance invalidate old overwrite tokens; reject again with updated lists."""
        first, _second = variant.entry_ids
        project_dir, _plan_path = await _converted_and_authored(tmp_path, variant)
        stale = _revision(project_dir)
        _rewrite_script(project_dir, lambda script: script.__setitem__("title", "认可之后又改了"))

        with pytest.raises(PromptOverwriteRequired) as caught:
            await variant.generator(project_dir, []).generate(
                1, entry_ids=[first], rewrite=True, overwrite_revision=stale
            )

        assert caught.value.overwrite.fingerprint == _revision(project_dir) != stale

    async def test_rewrite_of_entries_without_content_needs_no_token(
        self, tmp_path: Path, prompt_variant: _Variant
    ) -> None:
        variant = prompt_variant
        first, _second = variant.entry_ids
        project_dir = await self._materialized(tmp_path, variant)

        rewritten: list[str] = []
        await variant.generator(project_dir, [variant.visual_factory(first, mark="重写")]).generate(
            1, entry_ids=[first], rewrite=True, rewritten_entry_ids=rewritten
        )

        assert rewritten == [first]
        assert _authored_with(_entries(_script(project_dir), variant)[first], variant, "重写")

    async def test_reference_units_marked_pending_are_expanded_despite_their_text(self, tmp_path: Path) -> None:
        """Reference-video bodies transferred from planning may be nonempty but pending; expand gaps without overwrite tokens."""
        first, second = REFERENCE.entry_ids
        project_dir = await self._materialized(tmp_path, REFERENCE)
        assert _entries(_script(project_dir), REFERENCE)[first]["text"]

        rewritten: list[str] = []
        await REFERENCE.generator(project_dir, [REFERENCE.visual_factory(first, second, mark="展开")]).generate(
            1, entry_ids=[first, second], rewritten_entry_ids=rewritten
        )

        assert rewritten == [first, second]
        after = _entries(_script(project_dir), REFERENCE)
        assert all(_authored_with(after[entry_id], REFERENCE, "展开") for entry_id in (first, second))


class TestTextShapedPrompts:
    """Plain-text prompts (``lib.script.script_models.PromptText``) across both authoring paths."""

    @staticmethod
    def _write_text_prompts(project_dir: Path, variant: _Variant, entry_id: str) -> None:
        def mutate(script: dict[str, Any]) -> None:
            for entry in script[variant.items_key]:
                if entry[variant.id_field] == entry_id:
                    entry["image_prompt"] = "手写的分镜图提示词正文"
                    entry["video_prompt"] = "手写的视频提示词正文"

        _rewrite_script(project_dir, mutate)

    async def test_unmarked_entry_keeps_its_text_shaped_prompts(self, tmp_path: Path, prompt_variant: _Variant) -> None:
        first, second = prompt_variant.entry_ids
        project_dir, _plan_path = await _converted_and_authored(tmp_path, prompt_variant)
        self._write_text_prompts(project_dir, prompt_variant, first)
        _rewrite_script(
            project_dir,
            lambda script: script[prompt_variant.items_key][1].__setitem__(PENDING_AUTHORING_FIELD, True),
        )
        before_first = _entry_json(project_dir, prompt_variant, first)

        rerun = prompt_variant.generator(project_dir, [prompt_variant.visual_factory(second, mark="二轮")])
        await rerun.generate(1)

        assert _entry_json(project_dir, prompt_variant, first) == before_first
        assert _entries(_script(project_dir), prompt_variant)[first]["image_prompt"] == "手写的分镜图提示词正文"

    async def test_rewritten_entry_drops_the_text_shape(self, tmp_path: Path, prompt_variant: _Variant) -> None:
        """Explicitly rewriting one plain-text entry regenerates visuals through LLM into structured shape, retaining no old body."""
        first, _second = prompt_variant.entry_ids
        project_dir, _plan_path = await _converted_and_authored(tmp_path, prompt_variant)
        self._write_text_prompts(project_dir, prompt_variant, first)

        rerun = prompt_variant.generator(project_dir, [prompt_variant.visual_factory(first, mark="点名")])
        await rerun.generate(1, entry_ids=[first], rewrite=True, overwrite_revision=_revision(project_dir))

        entry = _entries(_script(project_dir), prompt_variant)[first]
        assert entry["image_prompt"]["scene"] == f"点名-{first}"
        assert isinstance(entry["video_prompt"], dict)


class TestDryRunPrompt:
    """Dry-run prompts target the same entries as real execution, answering what would actually be sent."""

    @staticmethod
    async def _converted_with_pending(tmp_path: Path, variant: _Variant, *pending_ids: str) -> Path:
        """After materialization, only ``pending_ids`` keep pending markers."""
        project_dir, plan_path = variant.build(tmp_path, texts=("原文甲甲甲。", "原文乙乙乙。"))
        await _materialize(project_dir, plan_path)

        def keep_markers(script: dict[str, Any]) -> None:
            for entry in script[variant.items_key]:
                if entry[variant.id_field] not in pending_ids:
                    entry.pop(PENDING_AUTHORING_FIELD, None)

        _rewrite_script(project_dir, keep_markers)
        return project_dir

    async def test_prompt_covers_only_the_pending_entry(self, tmp_path: Path, variant: _Variant) -> None:
        _first, second = variant.entry_ids
        project_dir = await self._converted_with_pending(tmp_path, variant, second)

        prompt = await variant.generator(project_dir, []).build_prompt(1)

        first_needle, second_needle = variant.prompt_needles
        assert second_needle in prompt
        assert first_needle not in prompt

    async def test_prompt_says_so_when_nothing_is_pending(self, tmp_path: Path, variant: _Variant) -> None:
        project_dir, _plan_path = await _converted_and_authored(tmp_path, variant)

        prompt = await variant.generator(project_dir, []).build_prompt(1)

        assert "没有要编写的条目" in prompt

    async def test_entry_ids_cover_those_entries(self, tmp_path: Path, variant: _Variant) -> None:
        first, second = variant.entry_ids
        project_dir = await self._converted_with_pending(tmp_path, variant)

        prompt = await variant.generator(project_dir, []).build_prompt(1, entry_ids=[first, second], rewrite=True)

        assert all(needle in prompt for needle in variant.prompt_needles)


class TestScriptPlanMaterialization:
    """Confirmation projects the entire content layer with all entries pending, using only confirmed plans and caller-accepted script overwrites."""

    async def test_narration_plan_ids_past_99_still_materialize(self, tmp_path: Path) -> None:
        """IDs rise monotonically: server-issued E1S100 and later plan IDs materialize normally."""
        project_dir, plan_path = NARRATION.build(tmp_path)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        for number, segment in enumerate(plan["segments"], start=100):
            segment["segment_id"] = format_item_id(1, "segments", number)
        plan_path = _write_plan(project_dir, NARRATION.plan_filename, plan)

        receipt = await _materialize(project_dir, plan_path)

        assert len(receipt.entry_ids) == len(plan["segments"])

    async def test_reconfirm_after_purging_deleted_entries_uses_new_ids_and_keeps_confirmation(
        self, tmp_path: Path, variant: _Variant
    ) -> None:
        project_dir, plan_path = variant.build(tmp_path)
        pm = ProjectManager.for_project_dir(project_dir)
        pm.reserve_item_ids(project_dir.name, 1, variant.items_key, 2)
        plan_before = plan_path.read_bytes()
        revision = script_review.content_fingerprint(plan_path)
        assert revision is not None

        def record_confirmation(project: dict[str, Any]) -> None:
            assert script_review.apply_confirmation(project, 1, revision, "2026-01-01T00:00:00Z")

        async def confirm():
            return await _converter(project_dir).materialize_script_plan(
                1,
                expected_plan_revision=revision,
                expected_script_fingerprint=_revision(project_dir),
                project_update=record_confirmation,
            )

        first = await confirm()
        assert first.entry_ids == tuple(format_item_id(1, variant.items_key, n) for n in (3, 4))
        current = pm.load_script(project_dir.name, "episode_1.json")
        current[variant.items_key] = []
        pm.save_script(project_dir.name, current, "episode_1.json")
        records = project_trash.list_records(project_dir)
        assert len(records) == 2
        for record in records:
            project_trash.purge(project_dir, record.id)

        second = await confirm()
        assert second.entry_ids == tuple(format_item_id(1, variant.items_key, n) for n in (5, 6))
        assert set(first.entry_ids).isdisjoint(second.entry_ids)
        await variant.generator(project_dir, [variant.visual_factory(*second.entry_ids)]).generate(1)
        project = pm.load_project(project_dir.name)
        assert script_review.formal_script_plan_confirmed(project_dir, project, 1)
        assert plan_path.read_bytes() == plan_before
        assert all(PENDING_AUTHORING_FIELD not in item for item in _script(project_dir)[variant.items_key])

    async def test_materialization_projects_every_entry_with_pending_prompts(
        self, tmp_path: Path, variant: _Variant
    ) -> None:
        first, second = variant.entry_ids
        project_dir, plan_path = variant.build(tmp_path)

        receipt = await _materialize(project_dir, plan_path)

        assert (receipt.entry_ids, receipt.removed) == ((first, second), ())
        script = _script(project_dir)
        entries = _entries(script, variant)
        assert list(entries) == [first, second]
        assert script["metadata"]["generator"] == SCRIPT_PLAN_CONVERSION_GENERATOR
        document = json.loads(plan_path.read_text(encoding="utf-8"))
        plan_entries = dict(zip(entries, document[_PLAN_ENTRIES_KEY[variant.name]], strict=True))
        for entry_id, entry in entries.items():
            assert entry[PENDING_AUTHORING_FIELD] is True
            if variant is REFERENCE:
                assert entry["text"] == plan_entries[entry_id]["text"]
                assert "image_prompt" not in entry
            else:
                assert entry["image_prompt"] is None
                assert entry["video_prompt"] is None
                assert "needs_replan" not in entry
                if variant is DRAMA:
                    assert entry["scene_description"] == plan_entries[entry_id]["scene_description"]
                assert entry[_plan_text_field(variant)] == plan_entries[entry_id][_plan_text_field(variant)]

    async def test_generate_with_entry_ids_fills_only_that_entry(
        self, tmp_path: Path, prompt_variant: _Variant
    ) -> None:
        """After materialization, explicitly author one entry while other pending entries remain byte-identical."""
        variant = prompt_variant
        first, second = variant.entry_ids
        project_dir, plan_path = variant.build(tmp_path)
        await _materialize(project_dir, plan_path)
        before_second = json.dumps(_entries(_script(project_dir), variant)[second], ensure_ascii=False, sort_keys=True)

        rewritten: list[str] = []
        await variant.generator(project_dir, [variant.visual_factory(first, mark="补写")]).generate(
            1, entry_ids=[first], rewritten_entry_ids=rewritten
        )

        assert rewritten == [first]
        after = _entries(_script(project_dir), variant)
        assert after[first]["image_prompt"] is not None
        assert "pending_authoring" not in after[first]
        assert json.dumps(after[second], ensure_ascii=False, sort_keys=True) == before_second
        assert after[second]["pending_authoring"] is True

    async def test_default_generate_after_materialization_fills_every_pending_entry(
        self, tmp_path: Path, variant: _Variant
    ) -> None:
        """All materialized entries are pending; default authoring fills all, clears markers and preserves content layers."""
        first, second = variant.entry_ids
        project_dir, plan_path = variant.build(tmp_path)
        await _materialize(project_dir, plan_path)
        before = _entries(_script(project_dir), variant)

        rewritten: list[str] = []
        await variant.generator(project_dir, [variant.visual_factory(first, second, mark="首轮")]).generate(
            1, rewritten_entry_ids=rewritten
        )

        assert rewritten == [first, second]
        after = _entries(_script(project_dir), variant)
        for entry_id in (first, second):
            assert _authored_with(after[entry_id], variant, "首轮")
            assert PENDING_AUTHORING_FIELD not in after[entry_id]
            assert _without_visual_layer(after[entry_id], variant) == _without_visual_layer(before[entry_id], variant)

    def _edit_plan(self, plan_path: Path, project_dir: Path, variant: _Variant) -> str:
        """Edit second body, delete first, add third at the front and return third ID."""
        entries_key = _PLAN_ENTRIES_KEY[variant.name]
        document = json.loads(plan_path.read_text(encoding="utf-8"))
        third = dict(document[entries_key][0])
        third[variant.id_field] = format_item_id(1, variant.items_key, 3)
        changed = dict(document[entries_key][1])
        changed[_plan_text_field(variant)] = "改了一个错别字。"
        document[entries_key] = [third, changed]
        plan_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        _activate(project_dir)
        return third[variant.id_field]

    async def test_materialization_over_an_authored_script_replaces_it_wholesale(
        self, tmp_path: Path, variant: _Variant
    ) -> None:
        """Replace an authored formal script wholly from new planning without reusing prompts/user fields."""
        first, second = variant.entry_ids
        project_dir, plan_path = await _converted_and_authored(tmp_path, variant)
        _stamp_user_fields(project_dir, variant, second)
        self._edit_plan(plan_path, project_dir, variant)

        receipt = await _materialize(project_dir, plan_path)

        # Always issue formal IDs above high-water marks; treat both old-script entries as removed.
        third = format_item_id(1, variant.items_key, 3)
        renewed = format_item_id(1, variant.items_key, 4)
        assert (receipt.entry_ids, receipt.removed) == ((third, renewed), (first, second))
        script = _script(project_dir)
        assert [entry[variant.id_field] for entry in script[variant.items_key]] == [third, renewed]
        after = _entries(script, variant)
        for entry in after.values():
            assert entry[PENDING_AUTHORING_FIELD] is True
            assert "note" not in entry
            if variant is not REFERENCE:
                assert entry["image_prompt"] is None
        if variant is not REFERENCE:
            assert after[renewed][_plan_text_field(variant)] == "改了一个错别字。"

    async def test_materialization_rejects_a_plan_edit_that_landed_after_loading(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: _Variant
    ) -> None:
        """If planning changes after loading but before persistence, locked fingerprint mismatch rejects and preserves scripts."""
        project_dir, plan_path = variant.build(tmp_path)
        await _materialize(project_dir, plan_path)
        before = (project_dir / "scripts" / "episode_1.json").read_bytes()
        original = script_generator_module.formal_script_overwrite

        def overwrite_then_concurrent_plan_edit(project_path: Path, project: dict, episode: int) -> Any:
            document = json.loads(plan_path.read_text(encoding="utf-8"))
            document[_PLAN_ENTRIES_KEY[variant.name]][0][_plan_text_field(variant)] = "加载之后又改了一遍。"
            plan_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            return original(project_path, project, episode)

        monkeypatch.setattr(script_generator_module, "formal_script_overwrite", overwrite_then_concurrent_plan_edit)

        with pytest.raises(script_review.ScriptPlanWriteConflict):
            await _materialize(project_dir, plan_path)
        assert (project_dir / "scripts" / "episode_1.json").read_bytes() == before

    async def test_materialization_refuses_a_plan_other_than_the_confirmed_one(
        self, tmp_path: Path, variant: _Variant
    ) -> None:
        """Confirmations identify the validated plan; loaded fingerprint mismatch rejects without script/project.json writes."""
        project_dir, _plan_path = variant.build(tmp_path)
        project_before = (project_dir / "project.json").read_bytes()

        with pytest.raises(script_review.ScriptPlanWriteConflict):
            await _converter(project_dir).materialize_script_plan(
                1,
                expected_plan_revision="sha256-v1:" + "0" * 64,
                expected_script_fingerprint=None,
                project_update=lambda project: project.__setitem__("touched", True),
            )

        assert not (project_dir / "scripts" / "episode_1.json").exists()
        assert (project_dir / "project.json").read_bytes() == project_before

    async def test_materialization_refuses_a_script_other_than_the_acknowledged_one(
        self, tmp_path: Path, variant: _Variant
    ) -> None:
        """Overwrite acceptance names the caller-seen formal script; later edits conflict and preserve script/project.json."""
        project_dir, plan_path = variant.build(tmp_path)
        await _materialize(project_dir, plan_path)
        script_path = project_dir / "scripts" / "episode_1.json"
        acknowledged = script_review.content_fingerprint(script_path)
        document = json.loads(script_path.read_text(encoding="utf-8"))
        document["title"] = "认可之后又改了"
        script_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        before = script_path.read_bytes()
        project_before = (project_dir / "project.json").read_bytes()
        plan_revision = script_review.content_fingerprint(plan_path)
        assert plan_revision is not None

        with pytest.raises(ScriptWriteConflict):
            await _converter(project_dir).materialize_script_plan(
                1,
                expected_plan_revision=plan_revision,
                expected_script_fingerprint=acknowledged,
                project_update=lambda project: project.__setitem__("touched", True),
            )

        assert script_path.read_bytes() == before
        assert (project_dir / "project.json").read_bytes() == project_before

    @pytest.mark.parametrize("ledger_title", ["", "用户起的名字"])
    async def test_materialization_keeps_the_ledger_title_over_the_plan_title(
        self, tmp_path: Path, ledger_title: str
    ) -> None:
        """Do not adopt model-generated drama plan titles; script/ledger retain ledger values including empty titles."""
        project_dir, plan_path = DRAMA.build(tmp_path)
        project = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
        project["episodes"][0]["title"] = ledger_title
        (project_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
        document = json.loads(plan_path.read_text(encoding="utf-8"))
        document["title"] = "模型起的名字"
        plan_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        _activate(project_dir)

        await _materialize(project_dir, plan_path)

        assert _script(project_dir)["title"] == ledger_title
        ledger = json.loads((project_dir / "project.json").read_text(encoding="utf-8"))["episodes"][0]
        assert ledger["title"] == ledger_title
