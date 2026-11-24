"""ScriptGenerator reference_video 分支测试。

提示词编写只读正式剧本的 video_units：默认改写带待编写标记的单元正文，``entry_ids`` 划定范围、``rewrite`` 显式重写；
脚本规划只在内容确认转换（``materialize_script_plan``）里被读取。
"""

import json as _json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.artifacts.artifact_activation import activate_artifact_target_state
from lib.config.resolver import ConfigResolver
from lib.episode.episode_paths import (
    REFERENCE_VIDEO_PROMPT_AUTHORING_QUARANTINE_FILENAME,
    REFERENCE_VIDEO_SCRIPT_PLAN_QUARANTINE_FILENAME,
    episode_drafts_dir,
)
from lib.generation.video_request_facts import VideoRequestFactsError, VideoRequestFactsFailure
from lib.project.project_manager import ProjectManager
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script import script_review
from lib.script.reference_video.draft_validation import DraftViolation
from lib.script.reference_video.request_projection import configured_reference_request_facts
from lib.script.reference_video.text_parser import extract_mentions
from lib.script.reference_video.unit_capabilities import evaluate_reference_unit_capabilities
from lib.script.script_generator import (
    PlanningVideoFacts,
    PromptAuthoringUnitRejected,
    ScriptGenerator,
    UnitDurationOffTier,
)
from tests.factories import make_video_request_facts

SCRIPT_PLAN_UNIT = {"unit_id": "E1U01", "text": "@[主角] 推开 @[酒馆] 的门", "duration_seconds": 4}
SCRIPT_PLAN_UNITS_JSON = _json.dumps({"units": [SCRIPT_PLAN_UNIT]}, ensure_ascii=False)


def _prompt_authoring_response(*texts: str, title: str = "t") -> str:

    return _json.dumps(
        {"title": title, "units": [{"unit_id": f"E1U{i:02}", "text": t} for i, t in enumerate(texts, 1)]},
        ensure_ascii=False,
    )


def _fake_prompt_authoring_generator(*texts: str, first_id: str = "E1U01") -> MagicMock:
    generator = MagicMock()
    generator.model = "mock"
    data = _json.loads(_prompt_authoring_response(*texts))
    data["units"][0]["unit_id"] = first_id
    generator.generate = AsyncMock(return_value=MagicMock(text=_json.dumps(data)))
    return generator


def _idle_generator() -> MagicMock:
    """被调用即可断言出来的文本生成替身：用例据此判断拦截发生在付费调用之前。"""
    generator = MagicMock()
    generator.model = "mock"
    generator.generate = AsyncMock()
    return generator


PROMPT_AUTHORING_UNIT_TEXT = "镜头1：中景，平视。@[主角] 推开 @[酒馆] 的门，侧身跨过门槛。"


def _activate_project_artifacts(project_dir: Path, episode: int = 1) -> None:
    """补齐该集的溯源输入后，对项目做一次全量产物激活。

    产物清单是读取已生成产物的唯一口径：落盘本身不代表已登记，未登记的 script_plan 进不了转换。
    ``episode`` 只决定补写哪一集的 ``source/episode_{episode}.txt``；登记范围是整个项目。
    """
    source = project_dir / "source" / f"episode_{episode}.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("原文", encoding="utf-8")
    activate_artifact_target_state(project_dir, bump_schema=False)


def _claim_character_sheet(project_dir: Path, name: str) -> None:
    """给已登记角色落一张资产图并经产物激活认领：正文提及它的单元据此才按 r2v 定桶。"""
    sheet = project_dir / "characters" / f"{name}.png"
    sheet.parent.mkdir(parents=True, exist_ok=True)
    sheet.write_bytes(b"png")
    project_file = project_dir / "project.json"
    project = _json.loads(project_file.read_text(encoding="utf-8"))
    project["characters"].setdefault(name, {"description": "d"})["character_sheet"] = f"characters/{name}.png"
    project_file.write_text(_json.dumps(project, ensure_ascii=False), encoding="utf-8")
    _activate_project_artifacts(project_dir)


def _write_script_plan(project_dir: Path, payload: str, episode: int = 1) -> None:
    """写正式 script_plan 并登记进产物清单。"""
    path = project_dir / "drafts" / f"episode_{episode}" / "script_plan_reference_units.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    _activate_project_artifacts(project_dir, episode)


def _script_path(project: Path) -> Path:
    return project / "scripts" / "episode_1.json"


def _write_formal_units(project_dir: Path, units: list[dict[str, Any]], *, title: str = "第1集") -> Path:
    """经写盘统一入口落一份正式剧本；单元默认带待编写标记，显式给出 ``pending_authoring`` 的除外。"""
    project = _json.loads((project_dir / "project.json").read_text(encoding="utf-8"))
    script = {
        "title": title,
        "episode": 1,
        "content_mode": project["content_mode"],
        "novel": {"title": project["title"], "chapter": "第1集"},
        "video_units": [{"pending_authoring": True, **unit} for unit in units],
    }
    return ProjectManager.for_project_dir(project_dir).save_script(project_dir.name, script, "episode_1.json")


def _formal_units(project_dir: Path) -> dict[str, dict[str, Any]]:
    script = _json.loads(_script_path(project_dir).read_text(encoding="utf-8"))
    return {unit["unit_id"]: unit for unit in script["video_units"]}


@pytest.fixture(autouse=True)
def reference_request_facts(set_video_request_facts):
    """本模块的档位一律来自视频请求事实：缺省两桶同一份 (4, 6, 8)，需要两桶不同或失败的用例就地覆盖。"""
    set_video_request_facts(make_video_request_facts(route="reference_video", generation_type="i2v"))


async def _materialize(generator: ScriptGenerator, episode: int = 1):
    """以当前规划与当前正式剧本为认可对象做内容确认转换；规划缺席时由加载器先行报错。"""
    plan_path = generator.project_path / "drafts" / f"episode_{episode}" / "script_plan_reference_units.json"
    script_path = generator.project_path / "scripts" / f"episode_{episode}.json"
    return await generator.materialize_script_plan(
        episode,
        expected_plan_revision=script_review.content_fingerprint(plan_path) or "",
        expected_script_fingerprint=script_review.content_fingerprint(script_path),
        project_update=lambda _project: None,
    )


def _write_reference_project(
    tmp_path: Path, *, video_backend: str, content_mode: str = "narration", resolution: str | None = None
) -> Path:
    """造一个带脚本规划的参考生视频最小项目。

    ``video_backend`` / ``resolution`` 只是项目自报身份；规划读到的档位来自各用例提供的视频请求事实。
    """
    project_dir = tmp_path / "projects" / "proj"
    project_dir.mkdir(parents=True)
    (project_dir / "project.json").write_text(
        """{
          "schema_version": __SCHEMA__,
          "title": "t",
          "content_mode": "__MODE__",
          "generation_mode": "reference_video",
          "video_backend": "__BACKEND__",
          "overview": {"synopsis": "s", "genre": "g", "theme": "th", "world_setting": "w"},
          "style": "国漫",
          "style_description": "水墨",
          "characters": {"主角": {"description": "d"}},
          "scenes": {"酒馆": {"description": "d"}},
          "props": {},
          "episodes": [
            {"episode": 1, "title": "t1", "script_file": "scripts/episode_1.json",
             "generation_mode": "reference_video"}
          ]
        }""".replace("__SCHEMA__", str(CURRENT_PROJECT_SCHEMA_VERSION))
        .replace("__BACKEND__", video_backend)
        .replace("__MODE__", content_mode),
        encoding="utf-8",
    )
    if resolution is not None:
        project_file = project_dir / "project.json"
        project = _json.loads(project_file.read_text(encoding="utf-8"))
        project["model_settings"] = {video_backend: {"resolution": resolution}}
        project_file.write_text(_json.dumps(project, ensure_ascii=False), encoding="utf-8")
    _write_script_plan(project_dir, SCRIPT_PLAN_UNITS_JSON)
    return project_dir


@pytest.fixture
def plan_only_reference_project(tmp_path: Path) -> Path:
    """只有脚本规划、尚无正式剧本的项目：内容确认转换的输入。"""
    return _write_reference_project(tmp_path, video_backend="vidu/vidu2.0", resolution="1080p")


@pytest.fixture
def reference_project(plan_only_reference_project: Path) -> Path:
    """正式剧本里有一个待编写单元（``SCRIPT_PLAN_UNIT``）的项目。"""
    _write_formal_units(plan_only_reference_project, [SCRIPT_PLAN_UNIT])
    return plan_only_reference_project


@pytest.fixture
def wide_tier_reference_project(tmp_path: Path) -> Path:
    """两桶档位不同的用例用的项目：r2v 3–16 秒、i2v 1–16 秒由各用例按桶提供事实。

    参考图约束只做收窄，故「带图档位严于不带图」是唯一有意义的方向。正式剧本由各用例自行写入。
    """
    return _write_reference_project(tmp_path, video_backend="vidu/viduq3-turbo")


@pytest.mark.asyncio
async def test_build_prompt_renders_pending_formal_units(reference_project: Path):
    gen = ScriptGenerator(reference_project)
    prompt = await gen.build_prompt(episode=1)
    assert "@[主角] 推开 @[酒馆] 的门" in prompt
    assert "（时长 4s）" in prompt
    assert "E1U01" in prompt


@pytest.mark.asyncio
async def test_prompt_authoring_ignores_the_script_plan(reference_project: Path):
    """编写只读正式剧本：脚本规划缺失、或与正式剧本不一致，编写照常进行。"""
    plan = reference_project / "drafts" / "episode_1" / "script_plan_reference_units.json"
    plan.write_text(
        _json.dumps({"units": [{"unit_id": "E1U09", "text": "规划里另一段正文", "duration_seconds": 8}]}),
        encoding="utf-8",
    )
    prompt = await ScriptGenerator(reference_project).build_prompt(episode=1)
    assert "@[主角] 推开 @[酒馆] 的门" in prompt
    assert "规划里另一段正文" not in prompt

    plan.unlink()
    out = await ScriptGenerator(
        reference_project, generator=_fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT)
    ).generate(episode=1)
    assert _formal_units(reference_project)["E1U01"]["text"] == PROMPT_AUTHORING_UNIT_TEXT
    assert out == _script_path(reference_project)


@pytest.mark.asyncio
async def test_reference_prompt_authoring_ends_with_instructions_on_both_paths(reference_project: Path):
    gen = ScriptGenerator(reference_project)
    assert "# 附加指令" not in await gen.build_prompt(episode=1)
    preview = await gen.build_prompt(episode=1, instructions="多给人物面部特写")
    assert preview.endswith("逐 unit 产出。\n\n# 附加指令\n多给人物面部特写")

    fake_generator = _fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT)
    await ScriptGenerator(reference_project, generator=fake_generator).generate(
        episode=1, instructions="多给人物面部特写"
    )
    assert fake_generator.generate.await_args.args[0].prompt == preview


@pytest.mark.asyncio
async def test_script_generator_uses_reference_schema_on_generate(reference_project: Path):

    fake_generator = _fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT)

    out = await ScriptGenerator(reference_project, generator=fake_generator).generate(episode=1)

    data = _json.loads(out.read_text(encoding="utf-8"))
    assert data["content_mode"] == "narration"
    assert "generation_mode" not in data
    assert "duration_seconds" not in data
    assert len(data["video_units"]) == 1
    unit = data["video_units"][0]
    assert unit["unit_id"] == "E1U01"
    assert unit["duration_seconds"] == 4
    assert unit["text"].startswith("镜头1：中景，平视。")
    assert "pending_authoring" not in unit
    assert extract_mentions(unit["text"]) == ["主角", "酒馆"]

    schema = fake_generator.generate.await_args.args[0].response_schema
    assert "unit_id" in str(schema.model_json_schema())
    assert "duration_seconds" not in _json.dumps(schema.model_json_schema())


@pytest.mark.asyncio
async def test_prompt_authoring_rewrites_only_pending_units(reference_project: Path):
    """已编写、无标记的单元逐字节不变；只有待编写单元的正文被改写，其余字段原样保留。"""
    authored = {
        "unit_id": "E1U01",
        "text": "镜头1：远景。@[主角] 站在 @[酒馆] 门口。",
        "duration_seconds": 4,
        "note": "用户备注",
        "pending_authoring": False,
    }
    pending = {
        "unit_id": "E1U02",
        "text": "@[主角] 走进 @[酒馆]",
        "duration_seconds": 4,
        "note": "新增单元",
        "source_text": "他推门走进酒馆。",
    }
    _write_formal_units(reference_project, [authored, pending])
    before = _formal_units(reference_project)

    rewritten: list[str] = []
    new_text = "镜头1：中景。@[主角] 走进 @[酒馆]，环顾四周。"
    fake_generator = _fake_prompt_authoring_generator(new_text, first_id="E1U02")
    await ScriptGenerator(reference_project, generator=fake_generator).generate(
        episode=1, rewritten_entry_ids=rewritten
    )

    assert rewritten == ["E1U02"]
    prompt = fake_generator.generate.await_args.args[0].prompt
    assert "@[主角] 走进 @[酒馆]" in prompt
    after = _formal_units(reference_project)
    assert _json.dumps(after["E1U01"], sort_keys=True) == _json.dumps(before["E1U01"], sort_keys=True)
    assert after["E1U02"] == {
        "unit_id": "E1U02",
        "text": new_text,
        "duration_seconds": 4,
        "note": "新增单元",
        "source_text": "他推门走进酒馆。",
    }


@pytest.mark.asyncio
async def test_prompt_authoring_without_pending_units_skips_the_model(reference_project: Path):
    _write_formal_units(reference_project, [{**SCRIPT_PLAN_UNIT, "pending_authoring": False}])
    before = _script_path(reference_project).read_bytes()
    generator = _idle_generator()

    rewritten: list[str] = ["stale"]
    out = await ScriptGenerator(reference_project, generator=generator).generate(
        episode=1, rewritten_entry_ids=rewritten
    )

    assert out == _script_path(reference_project)
    assert rewritten == []
    generator.generate.assert_not_awaited()
    assert _script_path(reference_project).read_bytes() == before


@pytest.mark.asyncio
async def test_entry_ids_rewrite_an_authored_unit_keeping_other_fields(reference_project: Path):
    authored = {**SCRIPT_PLAN_UNIT, "text": "镜头1：远景。@[主角] 推开 @[酒馆] 的门。", "note": "保留"}
    _write_formal_units(reference_project, [{**authored, "pending_authoring": False}])

    await ScriptGenerator(
        reference_project, generator=_fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT)
    ).generate(
        episode=1,
        entry_ids=["E1U01"],
        rewrite=True,
        overwrite_revision=script_review.content_fingerprint(reference_project / "scripts" / "episode_1.json"),
    )

    unit = _formal_units(reference_project)["E1U01"]
    assert unit == {"unit_id": "E1U01", "text": PROMPT_AUTHORING_UNIT_TEXT, "duration_seconds": 4, "note": "保留"}


@pytest.mark.asyncio
async def test_prompt_authoring_rejects_formal_duration_outside_effective_tiers(
    reference_project: Path, set_video_request_facts
):
    """档位全集 [4, 6, 8] 被分辨率约束收窄到 [4]：正式剧本里 8 秒的单元不再是合法值。
    不能静默取档改写落盘，须 fail-loud 并指向时间线。

    拦截须发生在 TextBackend 调用之前：带引用与不带引用两种生效档位都不接受该时长时，本次编写
    必然失败；放到输出解析阶段才拦，用户已经为它付了费。
    """
    set_video_request_facts(
        make_video_request_facts(
            route="reference_video", generation_type="i2v", supported_durations=(4, 6, 8), allowed_durations=(4,)
        )
    )
    _write_formal_units(
        reference_project,
        [
            {**SCRIPT_PLAN_UNIT, "pending_authoring": False},
            {**SCRIPT_PLAN_UNIT, "unit_id": "E1U04", "duration_seconds": 8},
        ],
    )
    generator = _idle_generator()

    gen = ScriptGenerator(reference_project, generator=generator)
    with pytest.raises(PromptAuthoringUnitRejected, match=r"不在当前生效档位.*时间线") as exc_info:
        await gen.generate(episode=1)
    generator.generate.assert_not_awaited()
    assert (exc_info.value.code, exc_info.value.params) == (
        "prompt_authoring_duration_off_tier",
        {"unit_id": "E1U04", "position": 2, "duration_seconds": 8, "tiers": "4"},
    )


@pytest.mark.asyncio
async def test_no_image_five_second_unit_uses_i2v_facts_through_conversion_and_authoring(
    tmp_path: Path, set_video_request_facts
):
    project = _write_reference_project(tmp_path, video_backend="gemini-aistudio/veo-3.1-generate-preview")
    _write_script_plan(
        project,
        _json.dumps(
            {
                "units": [
                    {
                        "unit_id": "E1U01",
                        "text": "镜头1：空镜，街道渐亮。",
                        "duration_seconds": 5,
                        "source_text": "原文",
                    }
                ]
            },
            ensure_ascii=False,
        ),
    )
    set_video_request_facts(
        make_video_request_facts(
            route="reference_video",
            generation_type="i2v",
            provider_id="ark",
            model_id="doubao-seedance-2-0-260128",
            supported_durations=tuple(range(4, 16)),
            allowed_durations=tuple(range(4, 16)),
        )
    )

    generator = ScriptGenerator(
        project,
        generator=_fake_prompt_authoring_generator("镜头1：远景，晨光照亮空旷街道。", first_id="E1U1"),
    )

    await _materialize(generator)
    await generator.generate(episode=1)

    assert _formal_units(project)["E1U1"]["duration_seconds"] == 5


@pytest.mark.asyncio
async def test_no_image_conversion_rejects_unavailable_i2v_even_when_r2v_accepts_duration(
    tmp_path: Path, set_video_request_facts
):
    project = _write_reference_project(tmp_path, video_backend="gemini-aistudio/veo-3.1-generate-preview")
    _write_script_plan(
        project,
        _json.dumps({"units": [{"unit_id": "E1U01", "text": "镜头1：空镜，街道渐亮。", "duration_seconds": 8}]}),
    )
    set_video_request_facts(
        {
            "r2v": make_video_request_facts(route="reference_video", generation_type="r2v"),
            "i2v": VideoRequestFactsFailure("reference_capability_unavailable", (("capability", "i2v"),)),
        }
    )

    with pytest.raises(VideoRequestFactsError) as exc:
        await _materialize(ScriptGenerator(project))

    assert exc.value.code == "reference_capability_unavailable"
    assert not _script_path(project).exists()


@pytest.mark.asyncio
async def test_script_generator_narrows_duration_tiers_per_unit_not_episode_wide(
    wide_tier_reference_project: Path, set_video_request_facts
):
    """同集内一个 unit 带参考图（收窄到 3–16 秒）、另一个不带（仍是 1–16 秒）：后者本已合法的
    2 秒不应因前者的收窄被连带改成 3——取档须按每个 unit 自己的参考图状态重算生效档位，
    不套用 episode 级 any(...) 收窄出的粗粒度集合。
    """
    set_video_request_facts(
        make_video_request_facts(
            route="reference_video",
            generation_type="i2v",
            supported_durations=tuple(range(1, 17)),
            allowed_durations=tuple(range(1, 17)),
        )
    )
    project = wide_tier_reference_project
    _write_formal_units(
        project,
        [
            {"unit_id": "E1U01", "text": "@[主角] 推门", "duration_seconds": 3},
            {"unit_id": "E1U02", "text": "空镜", "duration_seconds": 2},
        ],
    )

    fake_generator = _fake_prompt_authoring_generator("镜头1：中景。@[主角] 推门", "镜头1：空镜，风吹过门廊")
    gen = ScriptGenerator(project, generator=fake_generator)
    await gen.generate(episode=1)

    units_by_id = _formal_units(project)
    assert units_by_id["E1U01"]["duration_seconds"] == 3
    assert units_by_id["E1U02"]["duration_seconds"] == 2


@pytest.mark.asyncio
async def test_script_generator_takes_duration_tier_from_final_output_references(
    wide_tier_reference_project: Path, set_video_request_facts
):
    """单元现有正文带引用（带图档位最短 3 秒，2 秒不在其中），编写输出去掉了引用（回落到纯文本档位
    1–16 秒，2 秒合法）：取档须按最终落地的 references 状态重算，不能沿用改写前的正文状态。
    """
    set_video_request_facts(
        make_video_request_facts(
            route="reference_video",
            generation_type="i2v",
            supported_durations=tuple(range(1, 17)),
            allowed_durations=tuple(range(1, 17)),
        )
    )
    project = wide_tier_reference_project
    _write_formal_units(project, [{"unit_id": "E1U01", "text": "@[主角] 推门", "duration_seconds": 2}])

    fake_generator = _fake_prompt_authoring_generator("镜头1：空镜，门廊在风里轻响")
    gen = ScriptGenerator(project, generator=fake_generator)
    await gen.generate(episode=1)

    unit = _formal_units(project)["E1U01"]
    assert extract_mentions(unit["text"]) == []
    assert unit["duration_seconds"] == 2


@pytest.mark.asyncio
async def test_prompt_authoring_takes_the_i2v_tier_for_a_registered_reference_without_image(
    wide_tier_reference_project: Path, set_video_request_facts, db_factory
):
    """提示词编写的取档按可用参考图定桶：已登记但缺图的引用让单元与内容确认面板、执行一样落 i2v，
    2 秒在 i2v 档位（1–16 秒）内合法，不因正文带 `@` 就按 r2v 档位（3–16 秒）判越档落草稿。
    """
    set_video_request_facts(
        make_video_request_facts(
            route="reference_video",
            generation_type="i2v",
            supported_durations=tuple(range(1, 17)),
            allowed_durations=tuple(range(1, 17)),
        )
    )
    project = wide_tier_reference_project
    _write_formal_units(project, [{"unit_id": "E1U01", "text": "@[主角] 推门", "duration_seconds": 2}])
    resolver = ConfigResolver(db_factory)
    gen = ScriptGenerator(
        project, generator=_fake_prompt_authoring_generator("镜头1：中景。@[主角] 推门"), config_resolver=resolver
    )

    await gen.generate(episode=1)

    unit = _formal_units(project)["E1U01"]
    assert "@[主角]" in unit["text"]
    assert unit["duration_seconds"] == 2
    (capability,) = await evaluate_reference_unit_capabilities(
        gen.project_json, project, [unit], request_facts=configured_reference_request_facts(gen.project_json, resolver)
    )
    assert capability.generation_type == "i2v"


@pytest.mark.asyncio
async def test_conversion_requires_i2v_facts_for_a_registered_reference_without_image(
    plan_only_reference_project: Path, set_video_request_facts
):
    """内容确认转换对已登记但缺图的单元按 i2v 定桶：i2v 事实不可解析时拒绝，不借 r2v 档位放行一份执行不了的方案。"""
    set_video_request_facts(VideoRequestFactsFailure("reference_capability_unavailable", (("capability", "i2v"),)))

    with pytest.raises(VideoRequestFactsError) as exc:
        await _materialize(ScriptGenerator(plan_only_reference_project))

    assert exc.value.code == "reference_capability_unavailable"
    assert not _script_path(plan_only_reference_project).exists()


@pytest.mark.asyncio
async def test_prompt_authoring_reads_the_r2v_tier_for_a_unit_with_a_usable_reference(
    reference_project: Path, set_video_request_facts
):
    """带可用参考图的单元按 r2v 桶的事实取档：r2v 收窄到 [4]、i2v 仍是 [4, 8] 时，8 秒在付费调用前不拦
    （去掉引用就能合法落地），编写输出保留引用后按 r2v 档位判越档，保留原正文与时长。
    """
    set_video_request_facts(
        {
            "r2v": make_video_request_facts(
                route="reference_video", generation_type="r2v", supported_durations=(4, 8), allowed_durations=(4,)
            ),
            "i2v": make_video_request_facts(
                route="reference_video", generation_type="i2v", supported_durations=(4, 8), allowed_durations=(4, 8)
            ),
        }
    )
    _claim_character_sheet(reference_project, "主角")
    _write_formal_units(reference_project, [{**SCRIPT_PLAN_UNIT, "duration_seconds": 8}])
    generator = _fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT)

    gen = ScriptGenerator(reference_project, generator=generator)
    with pytest.raises(ValueError, match="不在当前生效档位"):
        await gen.generate(episode=1)

    assert generator.generate.await_count == 2
    assert _formal_units(reference_project)["E1U01"]["duration_seconds"] == 8
    assert not (
        episode_drafts_dir(reference_project, 1) / REFERENCE_VIDEO_PROMPT_AUTHORING_QUARANTINE_FILENAME
    ).exists()


@pytest.mark.asyncio
async def test_script_generator_ignores_unknown_authoring_units(reference_project: Path):

    gen = ScriptGenerator(
        reference_project, generator=_fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT, "镜头1：多出来的一段")
    )
    await gen.generate(episode=1)
    assert list(_formal_units(reference_project)) == ["E1U01"]


@pytest.mark.asyncio
async def test_script_generator_rejects_prompt_authoring_dialogue_rewrite(reference_project: Path):
    """台词规范行逐字不变：prompt_authoring 改词即失败，不静默接受被改成「好配画面」的台词。"""
    _write_formal_units(reference_project, [{**SCRIPT_PLAN_UNIT, "text": "@[主角] 推门\n@[主角]：{我来了。}"}])
    gen = ScriptGenerator(
        reference_project,
        generator=_fake_prompt_authoring_generator("镜头1：中景。@[主角] 推门跨入\n@[主角]：{我到了。}"),
    )
    with pytest.raises(ValueError, match="台词"):
        await gen.generate(episode=1)


@pytest.mark.asyncio
async def test_script_generator_accepts_prompt_authoring_expansion_keeping_dialogue(reference_project: Path):
    """画面描述自由展开、台词逐字保留 → 放行，并把台词说话人排除在参考图之外。"""
    _write_formal_units(reference_project, [{**SCRIPT_PLAN_UNIT, "text": "@[主角] 推门\n@[主角]：{我来了。}"}])
    gen = ScriptGenerator(
        reference_project,
        generator=_fake_prompt_authoring_generator(
            "镜头1：中景，平视。@[主角] 推开 @[酒馆] 的门，跨过门槛\n@[主角]：{我来了。}"
        ),
    )
    await gen.generate(episode=1)
    assert extract_mentions(_formal_units(reference_project)["E1U01"]["text"]) == ["主角", "酒馆"]


@pytest.mark.asyncio
async def test_script_generator_rejects_prompt_authoring_unregistered_mention(reference_project: Path):
    """prompt_authoring 新增的 mention 同样过登记校验：未登记资产名不得混进正文。"""
    gen = ScriptGenerator(
        reference_project, generator=_fake_prompt_authoring_generator("镜头1：@[主角] 与 @[路人乙] 对视")
    )
    with pytest.raises(ValueError, match="未登记"):
        await gen.generate(episode=1)


@pytest.mark.asyncio
async def test_prompt_authoring_keeps_an_unregistered_speaker_from_the_formal_unit(reference_project: Path):
    """确认时选为「不登记」的说话人留在正式正文里：提示词编写逐字保留它，不因未登记而拒绝。"""
    _write_formal_units(reference_project, [{**SCRIPT_PLAN_UNIT, "text": "路人 拦住 @[主角]\n@[路人]{借过。}"}])
    gen = ScriptGenerator(
        reference_project,
        generator=_fake_prompt_authoring_generator("镜头1：中景。路人 拦住 @[主角] 的去路\n@[路人]{借过。}"),
    )

    await gen.generate(episode=1)

    assert _formal_units(reference_project)["E1U01"]["text"].endswith("@[路人]{借过。}")


@pytest.mark.asyncio
async def test_prompt_authoring_refuses_a_speaker_the_formal_unit_did_not_have(reference_project: Path):
    _write_formal_units(reference_project, [{**SCRIPT_PLAN_UNIT, "text": "@[主角] 推门\n@[主角]{我来了。}"}])
    gen = ScriptGenerator(
        reference_project,
        generator=_fake_prompt_authoring_generator("镜头1：@[主角] 推门\n@[主角]{我来了。}\n@[路人乙]{谁？}"),
    )

    with pytest.raises(ValueError, match="说话人"):
        await gen.generate(episode=1)


@pytest.mark.parametrize("i2v_limit", [1, 2])
async def test_prompt_authoring_only_retries_a_speaker_limit_when_another_bucket_can_fit(
    reference_project: Path, set_video_request_facts, i2v_limit: int
) -> None:
    set_video_request_facts(
        {
            bucket: make_video_request_facts(
                route="reference_video",
                generation_type=bucket,
                voice_consistency="native",
                max_reference_audio_count=limit,
            )
            for bucket, limit in (("r2v", 1), ("i2v", i2v_limit))
        }
    )
    _claim_character_sheet(reference_project, "主角")
    text = "@[主角] 推门\n@[主角]{来了。}\n@[路人]{好。}"
    _write_formal_units(reference_project, [{**SCRIPT_PLAN_UNIT, "text": text}])
    generator = _fake_prompt_authoring_generator(text)
    gen = ScriptGenerator(reference_project, generator=generator)
    with pytest.raises(ValueError, match="说话人"):
        await gen.generate(episode=1)
    assert generator.generate.await_count == i2v_limit
    assert _formal_units(reference_project)["E1U01"]["text"] == text


@pytest.mark.asyncio
async def test_script_plan_conversion_inherits_drama_content_mode(tmp_path: Path):
    """drama 项目下转换出的参考生视频剧本 content_mode 必须为 drama。

    Pydantic 的 ReferenceVideoScript.content_mode 默认 "narration"，model_dump 会
    把该默认值写入 dict；_add_metadata 必须显式覆盖而非 setdefault，否则 drama 项目
    的参考生视频剧本会被错误标记成 narration。
    """
    project_dir = _write_reference_project(tmp_path, video_backend="vidu/vidu2.0", content_mode="drama")

    await _materialize(ScriptGenerator(project_dir))

    data = _json.loads(_script_path(project_dir).read_text(encoding="utf-8"))
    assert data["content_mode"] == "drama"
    assert "generation_mode" not in data


@pytest.mark.parametrize(
    ("with_references", "expected"),
    [
        (make_video_request_facts(route="reference_video", generation_type="r2v", max_reference_images=3), 3),
        (make_video_request_facts(route="reference_video", generation_type="r2v", max_reference_images=1), 1),
        (make_video_request_facts(route="reference_video", generation_type="r2v", max_reference_images=0), 0),
        (make_video_request_facts(route="reference_video", generation_type="r2v", max_reference_images=None), None),
        (VideoRequestFactsFailure("reference_capability_unavailable", (("capability", "r2v"),)), None),
    ],
)
def test_resolve_max_refs_reads_the_r2v_request_facts(with_references, expected):
    """参考图数量上限只来自 r2v 桶的视频请求事实，不再回退到 project.json 自报身份查 registry。"""
    facts = PlanningVideoFacts(
        by_bucket={
            "r2v": with_references,
            "i2v": make_video_request_facts(route="reference_video", generation_type="i2v", max_reference_images=9),
        },
    )

    assert ScriptGenerator._resolve_max_refs(facts) == expected


def _write_minimal_reference_project(tmp_path: Path, **overrides: Any) -> Path:
    project_dir = tmp_path / "projects" / "proj"
    project_dir.mkdir(parents=True)
    project = {
        "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "title": "t",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "overview": {"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
        "style": "s",
        "style_description": "d",
        "characters": {},
        "scenes": {},
        "props": {},
        "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        **overrides,
    }
    (project_dir / "project.json").write_text(_json.dumps(project, ensure_ascii=False), encoding="utf-8")
    _write_script_plan(project_dir, SCRIPT_PLAN_UNITS_JSON)
    _write_formal_units(project_dir, [SCRIPT_PLAN_UNIT])
    return project_dir


@pytest.mark.asyncio
async def test_generate_without_r2v_facts_fails_with_the_facts_code_before_the_paid_call(
    tmp_path: Path, set_video_request_facts
):
    """带可用参考图的单元落 r2v：该桶事实解析不出时编写在调用模型前以同族问题码失败。

    档位只来自视频请求事实，不回退到 project.json 自报身份查 registry；i2v 桶可解析也不借用。
    """
    set_video_request_facts(
        {
            "r2v": VideoRequestFactsFailure("reference_capability_unavailable", (("capability", "r2v"),)),
            "i2v": make_video_request_facts(route="reference_video", generation_type="i2v"),
        }
    )
    project_dir = _write_minimal_reference_project(tmp_path)
    _claim_character_sheet(project_dir, "主角")
    generator = _idle_generator()

    gen = ScriptGenerator(project_dir, generator=generator)
    with pytest.raises(VideoRequestFactsError) as exc:
        await gen.generate(episode=1)

    assert exc.value.code == "reference_capability_unavailable"
    generator.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_prompt_follows_project_reference_route(tmp_path: Path):
    """项目生成模式为 reference_video 时 build_prompt 必须走 reference 分支。

    生成模式取自 ``project.json`` 顶层 ``generation_mode``，全项目相同、不随集号变化。
    """
    project_dir = _write_minimal_reference_project(
        tmp_path, video_backend="vidu/vidu2.0", characters={"主角": {"description": "d"}}
    )

    prompt = await ScriptGenerator(project_dir).build_prompt(episode=1)
    assert "@[主角] 推开 @[酒馆] 的门" in prompt
    assert "（时长 4s）" in prompt


@pytest.mark.asyncio
async def test_conversion_reads_legacy_script_plan_draft_without_source_text(plan_only_reference_project: Path):
    """存量 script_plan 草稿（无 source_text，per-shot 时长已由迁移收编）仍能被校验器读取并转换。

    ``source_text`` 是拆分工具产出时校验后落盘的原文锚，不带该字段的草稿一律视为存量：
    默认空串使读取照常通过，不要求用户重跑拆分。
    """
    project = plan_only_reference_project
    saved = _json.loads((project / "drafts" / "episode_1" / "script_plan_reference_units.json").read_text("utf-8"))
    assert "source_text" not in saved["units"][0]

    receipt = await _materialize(ScriptGenerator(project))

    assert receipt.entry_ids == ("E1U1",)
    assert list(_formal_units(project)) == ["E1U1"]


@pytest.mark.asyncio
async def test_reference_script_plan_legacy_md_prompts_resplit(plan_only_reference_project: Path):
    """仅存在结构化前的旧 .md 拆分表时，给出明确的「重跑拆分」提示而非笼统缺文件错误。"""
    drafts = plan_only_reference_project / "drafts" / "episode_1"
    (drafts / "script_plan_reference_units.json").unlink()
    (drafts / "script_plan_reference_units.md").write_text("| E1U1 | Shot1(4s) |", encoding="utf-8")

    gen = ScriptGenerator(plan_only_reference_project)
    with pytest.raises(FileNotFoundError, match="generate_script_plan"):
        await _materialize(gen)


@pytest.mark.asyncio
async def test_reference_script_plan_missing_raises(plan_only_reference_project: Path):
    drafts = plan_only_reference_project / "drafts" / "episode_1"
    (drafts / "script_plan_reference_units.json").unlink()

    gen = ScriptGenerator(plan_only_reference_project)
    with pytest.raises(FileNotFoundError, match="video_unit 拆分"):
        await _materialize(gen)


@pytest.mark.asyncio
async def test_reference_script_plan_rejects_out_of_enum_duration(
    plan_only_reference_project: Path, set_video_request_facts
):
    """转换时复验 unit 时长落在两桶生效档位内：仍是全集成员、但已被收窄出局的时长也拒绝，防手工编辑漂移。"""
    set_video_request_facts(
        make_video_request_facts(
            route="reference_video", generation_type="r2v", supported_durations=(4, 6, 8), allowed_durations=(4, 8)
        )
    )
    drafts = plan_only_reference_project / "drafts" / "episode_1"
    (drafts / "script_plan_reference_units.json").write_text(
        _json.dumps({"units": [{"unit_id": "E1U01", "text": "@[主角] 转身", "duration_seconds": 6}]}),
        encoding="utf-8",
    )
    _claim_character_sheet(plan_only_reference_project, "主角")

    gen = ScriptGenerator(plan_only_reference_project)
    with pytest.raises(UnitDurationOffTier, match="视频单元 1 已确认时长 6s 不在当前生效档位") as exc_info:
        await _materialize(gen)
    assert (exc_info.value.position, exc_info.value.duration_seconds, exc_info.value.tiers) == (1, 6, [4, 8])
    assert "E1U01" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_reference_script_plan_rejects_duplicate_unit_ids(plan_only_reference_project: Path):
    drafts = plan_only_reference_project / "drafts" / "episode_1"
    unit = {"unit_id": "E1U01", "text": "@[主角] 转身", "duration_seconds": 4}
    (drafts / "script_plan_reference_units.json").write_text(
        _json.dumps({"units": [unit, dict(unit)]}), encoding="utf-8"
    )

    gen = ScriptGenerator(plan_only_reference_project)
    with pytest.raises(ValueError, match="unit_id 重复"):
        await _materialize(gen)


def test_reference_script_plan_migration_carries_confirmation_forward(plan_only_reference_project: Path):
    """迁移回写让 script_plan 内容指纹漂移；若该集已确认（指纹恰是迁移前内容），须把确认指纹
    平移到迁移后的值，否则仅加载一次规划就会让已确认分集重新等待确认。
    """
    drafts = plan_only_reference_project / "drafts" / "episode_1"
    legacy = {"units": [{"unit_id": "E1U01", "text": "@[主角] 转身", "duration_seconds": 4, "duration_override": True}]}
    script_plan_path = drafts / "script_plan_reference_units.json"
    script_plan_path.write_text(_json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    before = script_review.content_fingerprint(script_plan_path)

    project_path = plan_only_reference_project / "project.json"
    project = _json.loads(project_path.read_text(encoding="utf-8"))
    project["episodes"][0]["script_plan_review"] = {"fingerprint": before, "confirmed_at": "2026-01-01T00:00:00Z"}
    project_path.write_text(_json.dumps(project, ensure_ascii=False), encoding="utf-8")

    gen = ScriptGenerator(plan_only_reference_project)
    gen._load_reference_script_plan(episode=1, supported_durations=[4, 8])

    after_project = _json.loads(project_path.read_text(encoding="utf-8"))
    review = after_project["episodes"][0]["script_plan_review"]
    after = script_review.content_fingerprint(script_plan_path)
    assert review["fingerprint"] == after
    assert review["fingerprint"] != before


def test_reference_script_plan_migration_carries_confirmation_confirmed_after_construction(
    plan_only_reference_project: Path,
):
    """确认发生在 ScriptGenerator 构造之后（如 generate() 内 await _fetch_video_request_facts()
    期间用户经 ScriptReviewService.confirm() 并发确认）：self.project_json 是构造时的旧快照，
    看不到这次确认，但迁移写回仍须正确搬移它——不能用这份旧快照做前置短路。
    """
    drafts = plan_only_reference_project / "drafts" / "episode_1"
    legacy = {"units": [{"unit_id": "E1U01", "text": "@[主角] 转身", "duration_seconds": 4, "duration_override": True}]}
    script_plan_path = drafts / "script_plan_reference_units.json"
    script_plan_path.write_text(_json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    before = script_review.content_fingerprint(script_plan_path)

    gen = ScriptGenerator(plan_only_reference_project)

    project_path = plan_only_reference_project / "project.json"
    project = _json.loads(project_path.read_text(encoding="utf-8"))
    project["episodes"][0]["script_plan_review"] = {"fingerprint": before, "confirmed_at": "2026-01-01T00:00:00Z"}
    project_path.write_text(_json.dumps(project, ensure_ascii=False), encoding="utf-8")
    assert "script_plan_review" not in gen.project_json["episodes"][0]

    gen._load_reference_script_plan(episode=1, supported_durations=[4, 8])

    after_project = _json.loads(project_path.read_text(encoding="utf-8"))
    review = after_project["episodes"][0]["script_plan_review"]
    after = script_review.content_fingerprint(script_plan_path)
    assert review["fingerprint"] == after
    assert review["confirmed_at"] == "2026-01-01T00:00:00Z"
    assert review["confirmed_at"] == "2026-01-01T00:00:00Z"


def test_reference_script_plan_migration_does_not_carry_confirmation_when_duration_is_clamped(
    plan_only_reference_project: Path,
):
    """迁移带 warnings（求和时长不在模型档位内，被取档改写）不是纯格式收编：已确认分集
    须重新等待确认，不能平移确认——取档后的秒数不是用户确认时看到的值。
    """
    drafts = plan_only_reference_project / "drafts" / "episode_1"
    legacy = {"units": [{"unit_id": "E1U01", "text": "@[主角] 转身", "duration_seconds": 4, "duration_override": True}]}
    script_plan_path = drafts / "script_plan_reference_units.json"
    script_plan_path.write_text(_json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    before = script_review.content_fingerprint(script_plan_path)

    project_path = plan_only_reference_project / "project.json"
    project = _json.loads(project_path.read_text(encoding="utf-8"))
    project["episodes"][0]["script_plan_review"] = {"fingerprint": before, "confirmed_at": "2026-01-01T00:00:00Z"}
    project_path.write_text(_json.dumps(project, ensure_ascii=False), encoding="utf-8")

    gen = ScriptGenerator(plan_only_reference_project)
    gen._load_reference_script_plan(episode=1, supported_durations=[8])

    assert _json.loads(script_plan_path.read_text(encoding="utf-8"))["units"][0]["duration_seconds"] == 8

    after_project = _json.loads(project_path.read_text(encoding="utf-8"))
    review = after_project["episodes"][0]["script_plan_review"]
    assert review["fingerprint"] == before


@pytest.mark.asyncio
async def test_formal_text_violation_is_caught_before_the_paid_prompt_authoring_call(reference_project: Path):
    """待编写单元正文的语法违约在调用文本模型之前就被拦下，且错误指回正式剧本与时间线。

    prompt_authoring 会逐字保留这段正文，违约必然原样复现——不在调用前判，就要付完 prompt_authoring
    的钱才失败，且错误指向 prompt_authoring「改坏了」。
    """
    _write_formal_units(reference_project, [{**SCRIPT_PLAN_UNIT, "text": "@[查无此人} 推门"}])
    generator = _idle_generator()

    with pytest.raises(PromptAuthoringUnitRejected, match=r"来自正式脚本.*时间线") as exc_info:
        await ScriptGenerator(reference_project, generator=generator).generate(episode=1)
    generator.generate.assert_not_awaited()
    assert (exc_info.value.code, exc_info.value.params) == (
        "prompt_authoring_unit_invalid",
        {"unit_id": "E1U01", "position": 1},
    )


def test_speech_violation_preserves_canonical_unit_and_locations(reference_project: Path):
    gen = ScriptGenerator(reference_project)
    units = [
        {
            "unit_id": "E1U01",
            "duration_seconds": 4,
            "text": "门被推开\n@[主角]：{快走。}\n{风吹过旷野。}",
        }
    ]

    with pytest.raises(DraftViolation) as exc_info:
        gen._assert_reference_unit_text_valid(units, max_refs=None)

    problem = exc_info.value
    assert problem.code == "mixed_speech"
    assert "unit E1U01 发声准入未通过" in str(problem)
    assert "unit 正式脚本 的 unit" not in str(problem)
    assert problem.locations == (
        {"path": ["text"], "line": 1},
        {"path": ["text"], "line": 2},
    )
    assert problem.reason == "character_and_narrator_mixed"
    assert problem.action == "replan_unit"


async def test_dialogue_overload_is_caught_before_the_paid_prompt_authoring_call(reference_project: Path):
    """在时间线上改短时长 / 补写台词会绕开拆分时的口播量校验，编写前复判把它拦下。

    prompt_authoring 逐字保留台词、之后再无口播量校验：不在这里复判，念不完的 unit 会一路落盘成片。
    """
    long_line = "他站在门口足足看了半晌才缓缓开口说出这句迟到了整整十年的道歉与告别" * 2
    _write_formal_units(
        reference_project,
        [
            {**SCRIPT_PLAN_UNIT, "pending_authoring": False},
            {**SCRIPT_PLAN_UNIT, "unit_id": "E1U04", "text": f"@[主角] 推开 @[酒馆] 的门\n@[主角]：{{{long_line}}}"},
        ],
    )
    generator = _idle_generator()

    with pytest.raises(PromptAuthoringUnitRejected, match="台词念完约需") as exc_info:
        await ScriptGenerator(reference_project, generator=generator).generate(episode=1)
    generator.generate.assert_not_awaited()
    rejection = exc_info.value
    assert rejection.code == "prompt_authoring_dialogue_overload"
    assert rejection.params["position"] == 2
    assert rejection.params["unit_id"] == "E1U04"
    assert rejection.params["duration_seconds"] == 4
    assert float(rejection.params["spoken_seconds"]) > 4


async def test_prompt_authoring_missing_title_does_not_fail_the_paid_call(reference_project: Path):
    """非约束解码通道漏写 title 不让已付费的展开失败：title 不参与写回，正式剧本标题原样保留。"""
    generator = MagicMock()
    generator.model = "mock"
    generator.generate = AsyncMock(
        return_value=MagicMock(
            text=_json.dumps({"units": [{"unit_id": "E1U01", "text": PROMPT_AUTHORING_UNIT_TEXT}]}, ensure_ascii=False)
        )
    )

    out = await ScriptGenerator(reference_project, generator=generator).generate(episode=1)

    data = _json.loads(out.read_text(encoding="utf-8"))
    assert data["title"] == "第1集"
    assert data["video_units"][0]["text"] == PROMPT_AUTHORING_UNIT_TEXT


@pytest.mark.asyncio
async def test_prompt_authoring_ignores_legacy_invalid_files_on_disk(reference_project: Path):
    """编写只读正式剧本：盘上遗留的旧 .invalid.json 文件既不阻断编写也不被清理。"""
    legacy_path = episode_drafts_dir(reference_project, 1) / REFERENCE_VIDEO_SCRIPT_PLAN_QUARANTINE_FILENAME
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text('{"legacy": true}', encoding="utf-8")
    gen = ScriptGenerator(reference_project, generator=_fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT))

    await gen.generate(episode=1)

    assert _formal_units(reference_project)["E1U01"]["text"] == PROMPT_AUTHORING_UNIT_TEXT
    assert legacy_path.read_text(encoding="utf-8") == '{"legacy": true}'


@pytest.mark.asyncio
async def test_prompt_authoring_duration_off_tier_after_merge_preserves_script(
    wide_tier_reference_project: Path, set_video_request_facts
):
    set_video_request_facts(
        {
            "r2v": make_video_request_facts(
                route="reference_video",
                generation_type="r2v",
                supported_durations=tuple(range(1, 17)),
                allowed_durations=tuple(range(3, 17)),
            ),
            "i2v": make_video_request_facts(
                route="reference_video",
                generation_type="i2v",
                supported_durations=tuple(range(1, 17)),
                allowed_durations=tuple(range(1, 17)),
            ),
        }
    )
    project = wide_tier_reference_project
    _claim_character_sheet(project, "主角")
    _write_formal_units(project, [{"unit_id": "E1U01", "text": "他推门", "duration_seconds": 2}])
    formal_before = _script_path(project).read_bytes()
    with_reference_text = "镜头1：中景，平视。@[主角] 推开门，侧身跨过门槛。"
    gen = ScriptGenerator(project, generator=_fake_prompt_authoring_generator(with_reference_text))

    with pytest.raises(ValueError, match="生效档位"):
        await gen.generate(episode=1)
    assert _script_path(project).read_bytes() == formal_before
    assert not (episode_drafts_dir(project, 1) / REFERENCE_VIDEO_PROMPT_AUTHORING_QUARANTINE_FILENAME).exists()


@pytest.mark.parametrize("failure", ["truncated", "network", "provider", "malformed"])
async def test_reference_nonrepairable_response_leaves_script_untouched(reference_project, failure):
    from lib.backends.text_backends.base import TextOutputTruncatedError

    before = _script_path(reference_project).read_bytes()
    generator = _idle_generator()
    if failure == "malformed":
        generator.generate.return_value = MagicMock(text='{"units":false}')
        expected = ValueError
    else:
        error = {
            "truncated": TextOutputTruncatedError(provider="fake", model="m"),
            "network": ConnectionError("network"),
            "provider": RuntimeError("provider"),
        }[failure]
        generator.generate.side_effect = error
        expected = type(error)
    with pytest.raises(expected):
        await ScriptGenerator(reference_project, generator=generator).generate(1)
    generator.generate.assert_awaited_once()
    assert _script_path(reference_project).read_bytes() == before
    assert not (
        episode_drafts_dir(reference_project, 1) / REFERENCE_VIDEO_PROMPT_AUTHORING_QUARANTINE_FILENAME
    ).exists()


async def test_reference_repairs_single_bad_row_from_original_baseline(reference_project):
    generator = _idle_generator()
    generator.generate.side_effect = [
        MagicMock(text='{"units":[{"unit_id":"E1U01","text":13}]}'),
        MagicMock(text=_prompt_authoring_response(PROMPT_AUTHORING_UNIT_TEXT)),
    ]
    await ScriptGenerator(reference_project, generator=generator).generate(1)
    assert generator.generate.await_count == 2
    assert _formal_units(reference_project)["E1U01"]["text"] == PROMPT_AUTHORING_UNIT_TEXT


@pytest.mark.parametrize(
    ("invalid", "reason"),
    [("duplicate_id", "duplicate resource identity"), ("wrong_episode", "unit_id"), ("extra_body", "references")],
)
async def test_reference_authoring_rejects_invalid_unselected_structure(reference_project, invalid, reason):
    path = _script_path(reference_project)
    script = _json.loads(path.read_text())
    other = {**SCRIPT_PLAN_UNIT, "unit_id": "E1U02", "pending_authoring": False}
    if invalid == "duplicate_id":
        other["unit_id"] = "E1U01"
    elif invalid == "wrong_episode":
        other["unit_id"] = "E2U02"
    else:
        other["references"] = ["主角"]
    script["video_units"].append(other)
    path.write_text(_json.dumps(script))
    before = path.read_bytes()
    generator = _idle_generator()
    with pytest.raises(ValueError, match=reason):
        await ScriptGenerator(reference_project, generator=generator).generate(1, entry_ids=["E1U01"])
    assert path.read_bytes() == before
    generator.generate.assert_not_awaited()
