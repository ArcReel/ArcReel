"""正式脚本的条目级维护：内容确认整份转出待编写条目，提示词编写只填待编写条目。

三种变体（drama / narration / reference_video）各自走一遍同一组判据：提示词编写默认只编写待编写
条目、``entry_ids`` 显式重写、其余条目逐字节不变、不读脚本规划；转换整份投影内容层并拒绝确认之外的
规划或剧本。
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib import script_generator as script_generator_module
from lib import script_review
from lib.artifact_activation import activate_artifact_target_state
from lib.config.resolver import ConfigResolver
from lib.project_manager import ProjectManager, ScriptWriteConflict
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script_batch_edit import ScriptBatchEditCommand, ScriptBatchEditor, blank_item_after, script_revision
from lib.script_document import SCRIPT_PLAN_CONVERSION_GENERATOR
from lib.script_generator import PromptAuthoringTargetError, ScriptGenerator
from lib.script_models import PENDING_AUTHORING_FIELD
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


def _rewrite_script(project_dir: Path, mutate: Callable[[dict[str, Any]], None]) -> None:
    path = project_dir / "scripts" / "episode_1.json"
    script = json.loads(path.read_text(encoding="utf-8"))
    mutate(script)
    path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")


def _entry_json(project_dir: Path, variant: _Variant, entry_id: str) -> str:
    return json.dumps(_entries(_script(project_dir), variant)[entry_id], ensure_ascii=False, sort_keys=True)


def _converter(project_dir: Path) -> ScriptGenerator:
    """内容确认转换不调用文本模型：不注入 generator，走与 dry-run 相同的裸构造。"""
    return ScriptGenerator(
        project_dir,
        config_resolver=cast(ConfigResolver, FakeConfigResolver(supported_durations=(4, 6, 8))),
    )


async def _materialize(project_dir: Path, plan_path: Path):
    """以当前规划与当前正式剧本为认可对象，把脚本规划整份转为正式剧本。"""
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
    """转换脚本规划并编写全部待编写条目：得到一份条目都有视觉层、无待编写标记的正式脚本。"""
    first, second = variant.entry_ids
    project_dir, plan_path = variant.build(tmp_path, texts=texts)
    await _materialize(project_dir, plan_path)
    await variant.generator(project_dir, [variant.visual_factory(first, second, mark=mark)]).generate(1)
    return project_dir, plan_path


#: 各变体的视觉层字段：提示词编写只写这些字段（参考生视频改写单元正文）。
_VISUAL_FIELDS = {
    "narration": ("image_prompt", "video_prompt"),
    "drama": ("image_prompt", "video_prompt"),
    "reference_video": ("text",),
}


def _without_visual_layer(entry: dict[str, Any], variant: _Variant) -> dict[str, Any]:
    hidden = {*_VISUAL_FIELDS[variant.name], PENDING_AUTHORING_FIELD}
    return {key: value for key, value in entry.items() if key not in hidden}


def _authored_with(entry: dict[str, Any], variant: _Variant, mark: str) -> bool:
    """条目的视觉层出自带 ``mark`` 的那次模型响应。"""
    if variant is REFERENCE:
        return entry["text"].startswith(f"{mark}：")
    return entry["image_prompt"]["scene"] == f"{mark}-{entry[variant.id_field]}"


def _plan_text_field(variant: _Variant) -> str:
    return "novel_text" if variant is NARRATION else "source_text"


@pytest.fixture(params=[NARRATION, DRAMA], ids=lambda variant: variant.name)
def prompt_variant(request) -> _Variant:
    """带 image_prompt / video_prompt 的两个变体；参考生视频的视觉层是 unit 正文，无提示词字段。"""
    return request.param


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------


class TestPromptAuthoring:
    """提示词编写只读正式脚本：默认编写全部待编写条目，``entry_ids`` 显式重写，其余条目逐字节不变。"""

    async def test_manually_added_entry_is_the_only_one_authored(self, tmp_path: Path, variant: _Variant) -> None:
        first, second = variant.entry_ids
        project_dir, _plan_path = await _converted_and_authored(tmp_path, variant)
        _stamp_user_fields(project_dir, variant, first)
        third = first.replace("01", "03")

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
        """时间线新增 / 移除分镜之后编写：新增的空分镜被填充，移除的分镜不会被生成回来。"""
        variant = prompt_variant
        first, second = variant.entry_ids
        project_dir, _plan_path = await _converted_and_authored(tmp_path, variant)
        pm = ProjectManager(str(project_dir.parent))
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

        # 不备响应：本轮若调用模型，AsyncMock 会 StopIteration。
        rewritten: list[str] = []
        await variant.generator(project_dir, []).generate(1, rewritten_entry_ids=rewritten)

        assert rewritten == []
        assert (project_dir / "scripts" / "episode_1.json").read_bytes() == before

    async def test_entry_ids_rewrite_keeps_content_and_user_fields(self, tmp_path: Path, variant: _Variant) -> None:
        """点名已有视觉层的条目：只重写这些条目的视觉层，内容字段与用户字段原样保留。"""
        first, second = variant.entry_ids
        project_dir, _plan_path = await _converted_and_authored(tmp_path, variant)
        _stamp_user_fields(project_dir, variant, first)
        before_first = _entries(_script(project_dir), variant)[first]
        before_second = _entry_json(project_dir, variant, second)

        rewritten: list[str] = []
        rerun = variant.generator(project_dir, [variant.visual_factory(first, mark="点名")])
        await rerun.generate(1, entry_ids=[first], rewritten_entry_ids=rewritten)

        assert rewritten == [first]
        after_first = _entries(_script(project_dir), variant)[first]
        assert _authored_with(after_first, variant, "点名")
        assert _without_visual_layer(after_first, variant) == _without_visual_layer(before_first, variant)
        assert _entry_json(project_dir, variant, second) == before_second

    async def test_unknown_entry_id_fails_without_writing(self, tmp_path: Path, variant: _Variant) -> None:
        project_dir, _plan_path = await _converted_and_authored(tmp_path, variant)
        before = (project_dir / "scripts" / "episode_1.json").read_bytes()

        with pytest.raises(PromptAuthoringTargetError, match="不在第 1 集正式脚本内"):
            await variant.generator(project_dir, []).generate(1, entry_ids=["E9U99"])

        assert (project_dir / "scripts" / "episode_1.json").read_bytes() == before

    @pytest.mark.parametrize("plan_state", ["missing", "diverged"])
    async def test_authoring_does_not_read_the_script_plan(
        self, tmp_path: Path, variant: _Variant, plan_state: str
    ) -> None:
        """脚本规划缺失或与正式脚本不一致，编写照常进行：输入只有正式脚本里的条目。"""
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


class TestTextShapedPrompts:
    """文本形态提示词（``lib.script_models.PromptText``）在编写两条路径下的行为。"""

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
        """点名重写一个文本形态条目：视觉层由 LLM 重出，回到结构形态、不留旧正文。"""
        first, _second = prompt_variant.entry_ids
        project_dir, _plan_path = await _converted_and_authored(tmp_path, prompt_variant)
        self._write_text_prompts(project_dir, prompt_variant, first)

        rerun = prompt_variant.generator(project_dir, [prompt_variant.visual_factory(first, mark="点名")])
        await rerun.generate(1, entry_ids=[first])

        entry = _entries(_script(project_dir), prompt_variant)[first]
        assert entry["image_prompt"]["scene"] == f"点名-{first}"
        assert isinstance(entry["video_prompt"], dict)


class TestDryRunPrompt:
    """dry-run 的 prompt 与真实运行同一批编写条目：它要回答「这次会发出什么」。"""

    @staticmethod
    async def _converted_with_pending(tmp_path: Path, variant: _Variant, *pending_ids: str) -> Path:
        """转换后只让 ``pending_ids`` 保留待编写标记。"""
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

        assert "没有待编写的条目" in prompt

    async def test_entry_ids_cover_those_entries(self, tmp_path: Path, variant: _Variant) -> None:
        first, second = variant.entry_ids
        project_dir = await self._converted_with_pending(tmp_path, variant)

        prompt = await variant.generator(project_dir, []).build_prompt(1, entry_ids=[first, second])

        assert all(needle in prompt for needle in variant.prompt_needles)


class TestScriptPlanMaterialization:
    """内容确认转换：整份投影内容层、全部条目待编写，只认确认过的规划与调用方认可覆盖的剧本。"""

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
        plan_entries = {e[variant.id_field]: e for e in document[_PLAN_ENTRIES_KEY[variant.name]]}
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
        """转换后点名让模型补一条提示词：其余待生成条目逐字节不变。"""
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
        """转换出的条目都待编写：默认编写全部待编写条目，写回后标记清除、内容层不变。"""
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
        """改第二条正文、删第一条、新增第三条并排在最前，返回第三条 id。"""
        entries_key = _PLAN_ENTRIES_KEY[variant.name]
        document = json.loads(plan_path.read_text(encoding="utf-8"))
        third = dict(document[entries_key][0])
        third[variant.id_field] = variant.entry_ids[0].replace("01", "03")
        changed = dict(document[entries_key][1])
        changed[_plan_text_field(variant)] = "改了一个错别字。"
        document[entries_key] = [third, changed]
        plan_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        _activate(project_dir)
        return third[variant.id_field]

    async def test_materialization_over_an_authored_script_replaces_it_wholesale(
        self, tmp_path: Path, variant: _Variant
    ) -> None:
        """已有编写过的正式剧本：整份按新规划替换，不沿用任何条目的提示词或用户字段。"""
        first, second = variant.entry_ids
        project_dir, plan_path = await _converted_and_authored(tmp_path, variant)
        _stamp_user_fields(project_dir, variant, second)
        third = self._edit_plan(plan_path, project_dir, variant)

        receipt = await _materialize(project_dir, plan_path)

        assert (receipt.entry_ids, receipt.removed) == ((third, second), (first,))
        script = _script(project_dir)
        assert [entry[variant.id_field] for entry in script[variant.items_key]] == [third, second]
        after = _entries(script, variant)
        for entry in after.values():
            assert entry[PENDING_AUTHORING_FIELD] is True
            assert "note" not in entry
            if variant is not REFERENCE:
                assert entry["image_prompt"] is None
        if variant is not REFERENCE:
            assert after[second][_plan_text_field(variant)] == "改了一个错别字。"

    async def test_materialization_rejects_a_plan_edit_that_landed_after_loading(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: _Variant
    ) -> None:
        """加载规划之后、落盘之前规划又被改写：持锁复核指纹失配即拒绝，剧本保持原样。"""
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
        """确认记录的是校验过的那份规划：加载到的规划指纹不同即拒绝，不写剧本、不改 project.json。"""
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
        """覆盖认可对应调用方看到的那份正式剧本：剧本之后又被改过即按冲突拒绝，剧本与 project.json 原样。"""
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

    async def test_blank_plan_title_falls_back_to_the_episode_title(self, tmp_path: Path) -> None:
        """drama 规划标题只有空白：正式剧本取分集账本标题。"""
        project_dir, plan_path = DRAMA.build(tmp_path)
        document = json.loads(plan_path.read_text(encoding="utf-8"))
        document["title"] = "   "
        plan_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        _activate(project_dir)

        await _materialize(project_dir, plan_path)

        assert _script(project_dir)["title"] == "第一集"
