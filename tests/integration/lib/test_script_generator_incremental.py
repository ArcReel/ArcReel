"""ScriptGenerator 的条目级增量合并：只重写失效条目，其余条目原样保留。

三种脚本规划变体（drama / narration / reference_video）各自走一遍同一组判据：改一条内容后
只有那一条被重写、其余条目逐字节不变；条目增删改序跟随脚本规划；``scope`` 的三种取值；
内容确认门禁与存量剧本的兼容口径。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.artifact_activation import activate_artifact_target_state
from lib.config.resolver import ConfigResolver
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script_generator import ScriptGenerator
from lib.script_plan_entries import (
    SCRIPT_PLAN_ENTRY_REVISION_FIELD,
    ScriptPlanEntryError,
    plan_entries_from_document,
    plan_entry_revisions,
)
from tests.fakes import FakeConfigResolver

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# 项目与脚本规划装配
# ---------------------------------------------------------------------------


def _activate(project_dir: Path, episode: int = 1) -> None:
    source = project_dir / "source" / f"episode_{episode}.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("原文", encoding="utf-8")
    activate_artifact_target_state(project_dir, bump_schema=False)


def _write_project(tmp_path: Path, **overrides: Any) -> Path:
    project_dir = tmp_path / "proj"
    project_dir.mkdir(exist_ok=True)
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
    """按调用次序吐出预置响应；多调一次即耗尽，用例据此断言「本轮没有调用模型」。"""
    generator = MagicMock()
    generator.model = "mock"
    generator.generate = AsyncMock(side_effect=[MagicMock(text=text) for text in responses])
    return generator


def _script(project_dir: Path) -> dict[str, Any]:
    return json.loads((project_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 三种变体的装配器：造项目 + 脚本规划 + 视觉层响应
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
    """一种脚本规划变体在本组用例里的全部差异点。"""

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
        #: 该变体脚本规划条目上带默认值、可以整个缺席的内容字段（drama 无草稿模型故为 None）。
        self.omissible_field = omissible_field
        #: 两个条目各自在 prompt 里的可辨认串。参考生视频的 prompt 不渲染 unit_id（视觉层按位对齐），
        #: 只能认正文。
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
    entry_ids=("E1U01", "E1U02"),
    omissible_field="source_text",
    prompt_needles=("第1镜", "第2镜"),
)

VARIANTS = [NARRATION, DRAMA, REFERENCE]


@pytest.fixture(params=VARIANTS, ids=lambda variant: variant.name)
def variant(request) -> _Variant:
    return request.param


#: 各变体在**脚本规划中间文件**里的条目数组键（剧本侧的键见 ``_Variant.items_key``）。
_PLAN_ENTRIES_KEY = {"narration": "segments", "drama": "scenes", "reference_video": "units"}

#: 用户手工成果与已付费产物引用：未变条目必须原样保留这些字段（见 issue 验收判据）。
_USER_FIELDS: dict[str, Any] = {
    "note": "用户备注",
    "transition_to_next": "fade",
    "generated_assets": {"storyboard_image": "storyboards/scene.png", "status": "storyboard_ready"},
}


def _entries(script: dict[str, Any], variant: _Variant) -> dict[str, dict[str, Any]]:
    return {entry[variant.id_field]: entry for entry in script[variant.items_key]}


def _stamp_user_fields(project_dir: Path, variant: _Variant, entry_id: str) -> None:
    """把用户字段直接写进磁盘上的剧本——它们由用户编辑与产物生成写入，不经提示词编写产生。"""
    path = project_dir / "scripts" / "episode_1.json"
    script = json.loads(path.read_text(encoding="utf-8"))
    for entry in script[variant.items_key]:
        if entry[variant.id_field] == entry_id:
            entry.update(_USER_FIELDS)
            if variant is not REFERENCE:
                # 参考生视频单元没有尾帧字段。
                entry["end_frame_image"] = "end_frames/scene.png"
    path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------


class TestIncrementalMerge:
    async def test_only_the_changed_entry_is_rewritten(self, tmp_path: Path, variant: _Variant) -> None:
        """改一条 source_text：只有该条目在重写名单里，其余条目逐字节不变。"""
        first, second = variant.entry_ids
        project_dir, plan_path = variant.build(tmp_path)
        generator = variant.generator(project_dir, [variant.visual_factory(first, second, mark="首轮")])
        await generator.generate(1)

        # 在第一条上留下用户手工成果与已付费产物引用：增量重写第二条时它们必须逐字节存活。
        _stamp_user_fields(project_dir, variant, first)
        before = _entries(_script(project_dir), variant)
        before_first_json = json.dumps(before[first], ensure_ascii=False, sort_keys=True)

        document = json.loads(plan_path.read_text(encoding="utf-8"))
        entries_key = _PLAN_ENTRIES_KEY[variant.name]
        document[entries_key][1]["novel_text" if variant is NARRATION else "source_text"] = "改了一个错别字。"
        plan_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        _activate(project_dir)

        rewritten: list[str] = []
        rerun = variant.generator(project_dir, [variant.visual_factory(second, mark="二轮")])
        await rerun.generate(1, rewritten_entry_ids=rewritten)

        assert rewritten == [second]
        after = _entries(_script(project_dir), variant)
        assert json.dumps(after[first], ensure_ascii=False, sort_keys=True) == before_first_json
        assert after[second] != before[second]

    async def test_unchanged_plan_skips_the_model_entirely(self, tmp_path: Path, variant: _Variant) -> None:
        first, second = variant.entry_ids
        project_dir, _plan_path = variant.build(tmp_path)
        await variant.generator(project_dir, [variant.visual_factory(first, second)]).generate(1)
        before = _script(project_dir)[variant.items_key]

        # 只备一份响应且已被首轮耗尽：本轮若调用模型，AsyncMock 会 StopIteration。
        rewritten: list[str] = []
        await variant.generator(project_dir, []).generate(1, rewritten_entry_ids=rewritten)

        assert rewritten == []
        assert _script(project_dir)[variant.items_key] == before

    async def test_added_removed_and_reordered_entries_follow_the_plan(self, tmp_path: Path, variant: _Variant) -> None:
        first, second = variant.entry_ids
        project_dir, plan_path = variant.build(tmp_path)
        await variant.generator(project_dir, [variant.visual_factory(first, second)]).generate(1)
        before = _entries(_script(project_dir), variant)
        before_second_json = json.dumps(before[second], ensure_ascii=False, sort_keys=True)

        entries_key = _PLAN_ENTRIES_KEY[variant.name]
        document = json.loads(plan_path.read_text(encoding="utf-8"))
        # 删掉第一条、把第二条排到新增的第三条之后：集合、顺序都变，未变条目仍须保留。
        third = dict(document[entries_key][0])
        third[variant.id_field] = variant.entry_ids[0].replace("01", "03")
        document[entries_key] = [third, document[entries_key][1]]
        plan_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        _activate(project_dir)

        third_id = third[variant.id_field]
        rewritten: list[str] = []
        rerun = variant.generator(project_dir, [variant.visual_factory(third_id, mark="三轮")])
        await rerun.generate(1, rewritten_entry_ids=rewritten)

        script = _script(project_dir)
        assert [entry[variant.id_field] for entry in script[variant.items_key]] == [third_id, second]
        assert rewritten == [third_id]
        after = _entries(script, variant)
        assert json.dumps(after[second], ensure_ascii=False, sort_keys=True) == before_second_json

    async def test_scope_all_rewrites_every_entry(self, tmp_path: Path, variant: _Variant) -> None:
        first, second = variant.entry_ids
        project_dir, _plan_path = variant.build(tmp_path)
        await variant.generator(project_dir, [variant.visual_factory(first, second, mark="首轮")]).generate(1)

        rewritten: list[str] = []
        rerun = variant.generator(project_dir, [variant.visual_factory(first, second, mark="整集")])
        await rerun.generate(1, scope="all", rewritten_entry_ids=rewritten)

        assert rewritten == [first, second]
        assert all(entry[SCRIPT_PLAN_ENTRY_REVISION_FIELD] for entry in _script(project_dir)[variant.items_key])

    async def test_scope_entry_ids_rewrites_only_those(self, tmp_path: Path, variant: _Variant) -> None:
        first, second = variant.entry_ids
        project_dir, _plan_path = variant.build(tmp_path)
        await variant.generator(project_dir, [variant.visual_factory(first, second, mark="首轮")]).generate(1)
        before = _entries(_script(project_dir), variant)
        before_second_json = json.dumps(before[second], ensure_ascii=False, sort_keys=True)

        rewritten: list[str] = []
        rerun = variant.generator(project_dir, [variant.visual_factory(first, mark="点名")])
        await rerun.generate(1, scope=[first], rewritten_entry_ids=rewritten)

        assert rewritten == [first]
        after = _entries(_script(project_dir), variant)
        assert json.dumps(after[second], ensure_ascii=False, sort_keys=True) == before_second_json

    async def test_entry_ids_missing_a_new_entry_fails_before_the_model(
        self, tmp_path: Path, variant: _Variant
    ) -> None:
        """点名重写时漏了脚本规划新增的条目：在调用文本模型之前拒绝，不留下缺条目的剧本。"""
        first, second = variant.entry_ids
        project_dir, plan_path = variant.build(tmp_path)
        await variant.generator(project_dir, [variant.visual_factory(first, second)]).generate(1)
        before = (project_dir / "scripts" / "episode_1.json").read_bytes()

        entries_key = _PLAN_ENTRIES_KEY[variant.name]
        document = json.loads(plan_path.read_text(encoding="utf-8"))
        third = dict(document[entries_key][0])
        third[variant.id_field] = first.replace("01", "03")
        document[entries_key].append(third)
        plan_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        _activate(project_dir)

        # 只备一份响应：本轮若调用模型再失败，说明拦截发生在付费之后。
        with pytest.raises(ScriptPlanEntryError, match="新增的条目不在本次重写范围内"):
            await variant.generator(project_dir, []).generate(1, scope=[first])

        assert (project_dir / "scripts" / "episode_1.json").read_bytes() == before

    async def test_unknown_scope_entry_id_fails_without_writing(self, tmp_path: Path, variant: _Variant) -> None:
        first, second = variant.entry_ids
        project_dir, _plan_path = variant.build(tmp_path)
        await variant.generator(project_dir, [variant.visual_factory(first, second)]).generate(1)
        before = (project_dir / "scripts" / "episode_1.json").read_bytes()

        with pytest.raises(ScriptPlanEntryError, match="不在当前脚本规划内"):
            await variant.generator(project_dir, []).generate(1, scope=["E9U99"])

        assert (project_dir / "scripts" / "episode_1.json").read_bytes() == before

    async def test_first_generation_stamps_every_entry_revision(self, tmp_path: Path, variant: _Variant) -> None:
        first, second = variant.entry_ids
        project_dir, _plan_path = variant.build(tmp_path)
        rewritten: list[str] = []
        generator = variant.generator(project_dir, [variant.visual_factory(first, second)])
        await generator.generate(1, rewritten_entry_ids=rewritten)

        assert rewritten == [first, second]
        entries = _entries(_script(project_dir), variant)
        assert {entry_id: bool(entry[SCRIPT_PLAN_ENTRY_REVISION_FIELD]) for entry_id, entry in entries.items()} == {
            first: True,
            second: True,
        }

    async def test_legacy_script_without_entry_revisions_is_not_reported_stale(
        self, tmp_path: Path, variant: _Variant
    ) -> None:
        """存量剧本：条目上没有指纹，但整集指纹仍匹配——不重跑模型，只补齐条目指纹。"""
        first, second = variant.entry_ids
        project_dir, _plan_path = variant.build(tmp_path)
        await variant.generator(project_dir, [variant.visual_factory(first, second)]).generate(1)

        script_path = project_dir / "scripts" / "episode_1.json"
        script = json.loads(script_path.read_text(encoding="utf-8"))
        for entry in script[variant.items_key]:
            entry.pop(SCRIPT_PLAN_ENTRY_REVISION_FIELD, None)
        script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

        rewritten: list[str] = []
        await variant.generator(project_dir, []).generate(1, rewritten_entry_ids=rewritten)

        assert rewritten == []
        assert all(entry[SCRIPT_PLAN_ENTRY_REVISION_FIELD] for entry in _script(project_dir)[variant.items_key])


class TestWorkflowSeesTheSameRevisions:
    """登记（生成落盘）与比对（工作流状态）必须摘出同一个指纹。

    两侧读的是同一份脚本规划文件，但生成侧经草稿模型归一、工作流侧读磁盘原文：口径一旦分叉，
    刚落盘的剧本会被工作流立刻判成整集失效，下一次提示词编写便整集重写、覆盖用户精修的提示词。
    """

    async def test_stamped_revisions_match_the_plan_document(self, tmp_path: Path, variant: _Variant) -> None:
        first, second = variant.entry_ids
        project_dir, plan_path = variant.build(tmp_path)
        await variant.generator(project_dir, [variant.visual_factory(first, second)]).generate(1)

        document = json.loads(plan_path.read_text(encoding="utf-8"))
        expected = plan_entry_revisions(variant.name, plan_entries_from_document(variant.name, document), episode=1)
        stamped = {
            entry_id: entry[SCRIPT_PLAN_ENTRY_REVISION_FIELD]
            for entry_id, entry in _entries(_script(project_dir), variant).items()
        }
        assert stamped == expected

    async def test_plan_omitting_defaulted_fields_still_matches(self, tmp_path: Path, variant: _Variant) -> None:
        """脚本规划省略带默认值的内容字段（存量文件的常态）时两侧仍同源。"""
        if variant.omissible_field is None:
            pytest.skip("drama 的脚本规划没有草稿模型，两侧消费的都是磁盘原文")
        first, second = variant.entry_ids
        project_dir, plan_path = variant.build(tmp_path)
        document = json.loads(plan_path.read_text(encoding="utf-8"))
        for entry in document[_PLAN_ENTRIES_KEY[variant.name]]:
            entry.pop(variant.omissible_field, None)
        plan_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        _activate(project_dir)

        await variant.generator(project_dir, [variant.visual_factory(first, second)]).generate(1)

        expected = plan_entry_revisions(variant.name, plan_entries_from_document(variant.name, document), episode=1)
        stamped = {
            entry_id: entry[SCRIPT_PLAN_ENTRY_REVISION_FIELD]
            for entry_id, entry in _entries(_script(project_dir), variant).items()
        }
        assert stamped == expected


@pytest.fixture(params=[NARRATION, DRAMA], ids=lambda variant: variant.name)
def prompt_variant(request) -> _Variant:
    """带 image_prompt / video_prompt 的两个变体；参考生视频的视觉层是 unit 正文，无提示词字段。"""
    return request.param


class TestTextShapedPrompts:
    """文本形态提示词（``lib.script_models.PromptText``）在增量合并两条路径下的行为。"""

    @staticmethod
    def _write_text_prompts(project_dir: Path, variant: _Variant, entry_id: str) -> None:
        path = project_dir / "scripts" / "episode_1.json"
        script = json.loads(path.read_text(encoding="utf-8"))
        for entry in script[variant.items_key]:
            if entry[variant.id_field] == entry_id:
                entry["image_prompt"] = "手写的分镜图提示词正文"
                entry["video_prompt"] = "手写的视频提示词正文"
        path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    async def test_unchanged_entry_keeps_its_text_shaped_prompts(
        self, tmp_path: Path, prompt_variant: _Variant
    ) -> None:
        first, second = prompt_variant.entry_ids
        project_dir, plan_path = prompt_variant.build(tmp_path)
        await prompt_variant.generator(project_dir, [prompt_variant.visual_factory(first, second)]).generate(1)
        self._write_text_prompts(project_dir, prompt_variant, first)
        before_first = json.dumps(_entries(_script(project_dir), prompt_variant)[first], sort_keys=True)

        document = json.loads(plan_path.read_text(encoding="utf-8"))
        entries_key = _PLAN_ENTRIES_KEY[prompt_variant.name]
        document[entries_key][1]["novel_text" if prompt_variant is NARRATION else "source_text"] = "改了一句。"
        plan_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        _activate(project_dir)

        rerun = prompt_variant.generator(project_dir, [prompt_variant.visual_factory(second, mark="二轮")])
        await rerun.generate(1)

        after_first = _entries(_script(project_dir), prompt_variant)[first]
        assert json.dumps(after_first, sort_keys=True) == before_first
        assert after_first["image_prompt"] == "手写的分镜图提示词正文"

    async def test_rewritten_entry_drops_the_text_shape(self, tmp_path: Path, prompt_variant: _Variant) -> None:
        """点名重写一个文本形态条目：视觉层由 LLM 重出，回到结构形态、不留旧正文。"""
        first, second = prompt_variant.entry_ids
        project_dir, _plan_path = prompt_variant.build(tmp_path)
        await prompt_variant.generator(project_dir, [prompt_variant.visual_factory(first, second)]).generate(1)
        self._write_text_prompts(project_dir, prompt_variant, first)

        rerun = prompt_variant.generator(project_dir, [prompt_variant.visual_factory(first, mark="点名")])
        await rerun.generate(1, scope=[first])

        entry = _entries(_script(project_dir), prompt_variant)[first]
        assert entry["image_prompt"]["scene"] == f"点名-{first}"
        assert isinstance(entry["video_prompt"], dict)


class TestDryRunPrompt:
    """dry-run 的 prompt 与真实运行同一个重写范围：它要回答「这次会发出什么」。"""

    async def test_prompt_covers_only_the_outdated_entry(self, tmp_path: Path, variant: _Variant) -> None:
        first, second = variant.entry_ids
        project_dir, plan_path = variant.build(tmp_path, texts=("原文甲甲甲。", "原文乙乙乙。"))
        await variant.generator(project_dir, [variant.visual_factory(first, second)]).generate(1)

        document = json.loads(plan_path.read_text(encoding="utf-8"))
        entries_key = _PLAN_ENTRIES_KEY[variant.name]
        text_field = "novel_text" if variant is NARRATION else "source_text"
        document[entries_key][1][text_field] = "改过的原文丙丙丙。"
        plan_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        _activate(project_dir)

        prompt = await variant.generator(project_dir, []).build_prompt(1)

        first_needle, second_needle = variant.prompt_needles
        assert second_needle in prompt
        assert first_needle not in prompt

    async def test_prompt_says_so_when_nothing_is_outdated(self, tmp_path: Path, variant: _Variant) -> None:
        first, second = variant.entry_ids
        project_dir, _plan_path = variant.build(tmp_path)
        await variant.generator(project_dir, [variant.visual_factory(first, second)]).generate(1)

        prompt = await variant.generator(project_dir, []).build_prompt(1)

        assert "没有需要重写的条目" in prompt

    async def test_scope_all_covers_every_entry(self, tmp_path: Path, variant: _Variant) -> None:
        first, second = variant.entry_ids
        project_dir, _plan_path = variant.build(tmp_path)
        await variant.generator(project_dir, [variant.visual_factory(first, second)]).generate(1)

        prompt = await variant.generator(project_dir, []).build_prompt(1, scope="all")

        assert all(needle in prompt for needle in variant.prompt_needles)
