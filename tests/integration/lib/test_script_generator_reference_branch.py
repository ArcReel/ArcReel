"""ScriptGenerator reference_video 分支测试。"""

import asyncio
import json as _json
import threading
from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from lib import project_manager as project_manager_module
from lib import script_review
from lib.artifact_activation import activate_artifact_target_state
from lib.config.resolver import ConfigResolver
from lib.draft_quarantine import (
    QUARANTINE_KIND_PROMPT_AUTHORING,
    QUARANTINE_KIND_SCRIPT_PLAN,
    quarantine_path,
    write_quarantine,
)
from lib.project_manager import ProjectManager, ScriptWriteConflict
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.reference_video.draft_validation import DraftViolation
from lib.reference_video.text_parser import extract_mentions
from lib.script_generator import ScriptGenerator

SCRIPT_PLAN_UNITS_JSON = _json.dumps(
    {
        "units": [
            {
                "unit_id": "E1U01",
                "text": "@[主角] 推开 @[酒馆] 的门",
                "duration_seconds": 4,
            }
        ]
    },
    ensure_ascii=False,
)


def _prompt_authoring_response(*texts: str, title: str = "t") -> str:
    """prompt_authoring 的 LLM 产出：扁平 ``{title, units: [{text}]}``——unit_id 与时长不进输出。"""
    return _json.dumps({"title": title, "units": [{"text": t} for t in texts]}, ensure_ascii=False)


def _fake_prompt_authoring_generator(*texts: str) -> MagicMock:
    generator = MagicMock()
    generator.model = "mock"
    generator.generate = AsyncMock(return_value=MagicMock(text=_prompt_authoring_response(*texts)))
    return generator


#: 与 ``SCRIPT_PLAN_UNITS_JSON`` 单 unit 对应的合法提示词编写：无台词可改。
PROMPT_AUTHORING_UNIT_TEXT = "镜头1：中景，平视。@[主角] 推开 @[酒馆] 的门，侧身跨过门槛。"


def _activate_project_artifacts(project_dir: Path, episode: int = 1) -> None:
    """补齐该集的溯源输入后，对项目做一次全量产物激活。

    产物清单是读取已生成产物的唯一口径：落盘本身不代表已登记，未登记的 script_plan 进不了付费调用。
    ``episode`` 只决定补写哪一集的 ``source/episode_{episode}.txt``；登记范围是整个项目。
    """
    source = project_dir / "source" / f"episode_{episode}.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("原文", encoding="utf-8")
    activate_artifact_target_state(project_dir, bump_schema=False)


def _write_script_plan(project_dir: Path, payload: str, episode: int = 1) -> None:
    """写正式 script_plan 并登记进产物清单。"""
    path = project_dir / "drafts" / f"episode_{episode}" / "script_plan_reference_units.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    _activate_project_artifacts(project_dir, episode)


class _StubConfigResolver:
    """能力解析替身：``ScriptGenerator`` 只消费 ``video_capabilities_for_project`` 这一个读点。

    ``caps=None`` 表达「解析不可用」，按生产上的真实形态抛 DB 错误，让
    ``_fetch_video_capabilities`` 走它自己那条吞异常回退，而不是绕过回退直接喂进一个 None。
    """

    def __init__(self, caps: dict | None = None) -> None:
        self._caps = caps

    async def video_capabilities_for_project(self, project: dict, *, capability: object = None) -> dict:
        if self._caps is None:
            raise OperationalError("SELECT ...", {}, Exception("no such table: system_setting"))
        return self._caps


def _stub_resolver(caps: dict | None = None) -> ConfigResolver:
    return cast(ConfigResolver, _StubConfigResolver(caps))


def _write_reference_project(tmp_path: Path, *, video_backend: str) -> Path:
    """造一个参考生视频的最小项目；``video_backend`` 决定 registry 侧的真实时长档位。"""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "project.json").write_text(
        """{
          "schema_version": __SCHEMA__,
          "title": "t",
          "content_mode": "narration",
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
        }""".replace("__SCHEMA__", str(CURRENT_PROJECT_SCHEMA_VERSION)).replace("__BACKEND__", video_backend),
        encoding="utf-8",
    )
    _write_script_plan(project_dir, SCRIPT_PLAN_UNITS_JSON)
    return project_dir


@pytest.fixture
def reference_project(tmp_path: Path) -> Path:
    """vidu2.0：raw 档位 [4, 8]，参考生视频下被参考图与分辨率两条约束收窄到 [4]。"""
    return _write_reference_project(tmp_path, video_backend="vidu/vidu2.0")


@pytest.fixture
def wide_tier_reference_project(tmp_path: Path) -> Path:
    """viduq3-turbo：raw 档位 1–16 秒，带参考图的 unit 收窄到 3–16 秒。

    参考图约束只做收窄，故「带图档位严于不带图」是真实型号唯一能表达的方向；两档之间需要
    差异的用例都取这个型号，不再拿替身编造反向的档位。
    """
    return _write_reference_project(tmp_path, video_backend="vidu/viduq3-turbo")


@pytest.mark.asyncio
async def test_script_generator_reads_script_plan_reference_units(reference_project: Path):
    gen = ScriptGenerator(reference_project)
    prompt = await gen.build_prompt(episode=1)
    # script_plan 正文逐字进入 prompt，与 unit 时长一起
    assert "@[主角] 推开 @[酒馆] 的门" in prompt
    assert "（时长 4s）" in prompt
    # unit_id 由序号机械派生，不下发给 prompt_authoring
    assert "E1U01" not in prompt


@pytest.mark.asyncio
async def test_script_generator_uses_reference_schema_on_generate(reference_project: Path):
    """prompt_authoring 用扁平 schema 出正文，落盘结构由 script_plan + 正文机械合成。"""
    from lib.script_models import ReferencePromptAuthoringFlatScript

    fake_generator = _fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT)

    gen = ScriptGenerator(reference_project, generator=fake_generator)

    out = await gen.generate(episode=1)
    assert out.exists()
    import json as _j

    data = _j.loads(out.read_text(encoding="utf-8"))
    # 参考生视频剧本 content_mode 继承项目级 narration/drama；生成模式是项目级事实，
    # 剧本不落盘任何生成模式标记。
    assert data["content_mode"] == "narration"
    assert "generation_mode" not in data
    assert len(data["video_units"]) == 1
    unit = data["video_units"][0]
    # unit_id / 时长沿用 script_plan；正文是 prompt_authoring 展开后的整段文本，参考图执行期才从中派生
    assert unit["unit_id"] == "E1U01"
    assert unit["duration_seconds"] == 4
    assert unit["text"].startswith("镜头1：中景，平视。")
    assert extract_mentions(unit["text"]) == ["主角", "酒馆"]

    # prompt_authoring 的 response_schema 是扁平形状，且不含 duration_seconds——时长没让 LLM 写
    schema = fake_generator.generate.await_args.args[0].response_schema
    assert schema is ReferencePromptAuthoringFlatScript
    assert "duration_seconds" not in _json.dumps(schema.model_json_schema())


@pytest.mark.asyncio
async def test_script_generator_overrides_llm_duration_with_script_plan_confirmed_value(reference_project: Path):
    """unit 时长的单一真相是 script_plan 完成内容确认时的值：prompt_authoring 根本不产出该字段，落盘值机械取自
    script_plan（时长即计费，不给 LLM 留任何改写入口）。
    """
    gen = ScriptGenerator(reference_project, generator=_fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT))
    out = await gen.generate(episode=1)

    data = _json.loads(out.read_text(encoding="utf-8"))
    assert data["video_units"][0]["duration_seconds"] == 4  # script_plan 确认值
    # 集总时长不落盘：它是逐 unit 求和的派生值，由项目摘要读时计算
    assert "duration_seconds" not in data


@pytest.mark.asyncio
async def test_script_generator_rejects_confirmed_duration_outside_effective_tiers(reference_project: Path):
    """script_plan 校验用未收窄的 raw 档位（vidu2.0 的 [4, 8]），但参考生视频下的生效档位被参考图与
    分辨率两条约束收窄到 [4]：确认时合法的 8 秒不再是收窄后的合法值。这种情况下不能静默取档
    改写落盘——用户审阅通过的时长/费用会被换成一个从未过目的值，须 fail-loud 要求重新审阅确认。

    拦截须发生在 TextBackend 调用之前：带引用与不带引用两种生效档位都不接受该确认时长时，
    本次生成必然失败；放到输出解析阶段才拦，用户已经为它付了费。
    """
    _write_script_plan(
        reference_project,
        _json.dumps(
            {"units": [{"unit_id": "E1U01", "text": "@[主角] 推开 @[酒馆] 的门", "duration_seconds": 8}]},
            ensure_ascii=False,
        ),
    )
    fake_generator = MagicMock()
    fake_generator.model = "mock"
    fake_generator.generate = AsyncMock()

    # caps 给空字典：档位一律回落到 project.json 自报身份查 registry，不受 DB 全局默认干扰。
    gen = ScriptGenerator(reference_project, generator=fake_generator, config_resolver=_stub_resolver({}))
    with pytest.raises(ValueError, match="不在当前生效档位"):
        await gen.generate(episode=1)
    fake_generator.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_script_generator_narrows_duration_tiers_per_unit_not_episode_wide(
    wide_tier_reference_project: Path,
):
    """同集内一个 unit 带参考图（收窄到 3–16 秒）、另一个不带（仍是 1–16 秒）：后者本已合法的
    确认值 2 秒不应因前者的收窄被连带改成 3——取档须按每个 unit 自己的参考图状态重算
    生效档位，不套用 episode 级 any(...) 收窄出的粗粒度集合。
    """
    project = wide_tier_reference_project
    _write_script_plan(
        project,
        _json.dumps(
            {
                "units": [
                    {"unit_id": "E1U01", "text": "@[主角] 推门", "duration_seconds": 3},
                    {"unit_id": "E1U02", "text": "空镜", "duration_seconds": 2},
                ]
            },
            ensure_ascii=False,
        ),
    )

    fake_generator = _fake_prompt_authoring_generator("镜头1：中景。@[主角] 推门", "镜头1：空镜，风吹过门廊")
    gen = ScriptGenerator(project, generator=fake_generator, config_resolver=_stub_resolver({}))
    out = await gen.generate(episode=1)

    data = _json.loads(out.read_text(encoding="utf-8"))
    units_by_id = {u["unit_id"]: u for u in data["video_units"]}
    assert units_by_id["E1U01"]["duration_seconds"] == 3
    assert units_by_id["E1U02"]["duration_seconds"] == 2  # 未被另一个带图 unit 的收窄连带改动


@pytest.mark.asyncio
async def test_script_generator_takes_duration_tier_from_final_output_references_not_script_plan(
    wide_tier_reference_project: Path,
):
    """script_plan 拆分时某 unit 带引用（带图档位最短 3 秒，2 秒只在未收窄的 raw 档位上过了校验），
    但 prompt_authoring 输出给这个 unit 去掉了引用（回落到纯文本档位 1–16 秒，2 秒合法）：取档须按最终
    落地的 references 状态重算，不能沿用 script_plan 的旧状态——按 script_plan 状态取档会把本已合法的
    确认值改写成 3 秒。
    """
    project = wide_tier_reference_project
    _write_script_plan(
        project,
        _json.dumps(
            {"units": [{"unit_id": "E1U01", "text": "@[主角] 推门", "duration_seconds": 2}]},
            ensure_ascii=False,
        ),
    )

    fake_generator = _fake_prompt_authoring_generator("镜头1：空镜，门廊在风里轻响")
    gen = ScriptGenerator(project, generator=fake_generator, config_resolver=_stub_resolver({}))
    out = await gen.generate(episode=1)

    data = _json.loads(out.read_text(encoding="utf-8"))
    unit = data["video_units"][0]
    assert extract_mentions(unit["text"]) == []
    assert unit["duration_seconds"] == 2  # 按最终正文（无引用）取档合法，不因 script_plan 的带图状态被误判


@pytest.mark.asyncio
async def test_script_generator_reclamps_duration_even_when_caps_unavailable(reference_project: Path):
    """caps 解析失败（DB 不可用，``_fetch_video_capabilities`` 按其文档吞掉异常返回 None）不代表
    取不到任何档位——``_resolve_supported_durations`` 自带 caps → registry 两级回退，
    project.json 自报的 vidu2.0 仍能兜底出 raw [4, 8] 并收窄到 [4]。回填逻辑须无条件取档，
    不能因为 caps 是 None 就保留一个未经取档的值：确认值 8 秒落在收窄后的生效档位外，取档
    执行了就必抛错，不执行则会静默用未取档的 8 落盘成功。
    """
    _write_script_plan(
        reference_project,
        _json.dumps(
            {"units": [{"unit_id": "E1U01", "text": "@[主角] 推开 @[酒馆] 的门", "duration_seconds": 8}]},
            ensure_ascii=False,
        ),
    )
    fake_generator = MagicMock()
    fake_generator.model = "mock"
    fake_generator.generate = AsyncMock()

    gen = ScriptGenerator(reference_project, generator=fake_generator, config_resolver=_stub_resolver(None))
    with pytest.raises(ValueError, match="不在当前生效档位"):
        await gen.generate(episode=1)


@pytest.mark.asyncio
async def test_script_generator_rejects_prompt_authoring_unit_count_change(reference_project: Path):
    """prompt_authoring 合并 / 拆分 / 增删 unit：unit 数是 script_plan 已确认的内容契约，改动即响亮失败。"""
    gen = ScriptGenerator(
        reference_project, generator=_fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT, "镜头1：多出来的一段")
    )
    with pytest.raises(ValueError, match="unit 数"):
        await gen.generate(episode=1)


@pytest.mark.asyncio
async def test_script_generator_rejects_prompt_authoring_dialogue_rewrite(reference_project: Path):
    """台词规范行逐字不变：prompt_authoring 改词即失败，不静默接受被改成「好配画面」的台词。"""
    drafts = reference_project / "drafts" / "episode_1"
    (drafts / "script_plan_reference_units.json").write_text(
        _json.dumps(
            {
                "units": [
                    {
                        "unit_id": "E1U01",
                        "text": "@[主角] 推门\n@[主角]：{我来了。}",
                        "duration_seconds": 4,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    gen = ScriptGenerator(
        reference_project,
        generator=_fake_prompt_authoring_generator("镜头1：中景。@[主角] 推门跨入\n@[主角]：{我到了。}"),
    )
    with pytest.raises(ValueError, match="台词"):
        await gen.generate(episode=1)


@pytest.mark.asyncio
async def test_script_generator_accepts_prompt_authoring_expansion_keeping_dialogue(reference_project: Path):
    """画面描述自由展开、台词逐字保留 → 放行，并把台词说话人排除在参考图之外。"""
    drafts = reference_project / "drafts" / "episode_1"
    (drafts / "script_plan_reference_units.json").write_text(
        _json.dumps(
            {
                "units": [
                    {
                        "unit_id": "E1U01",
                        "text": "@[主角] 推门\n@[主角]：{我来了。}",
                        "duration_seconds": 4,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    gen = ScriptGenerator(
        reference_project,
        generator=_fake_prompt_authoring_generator(
            "镜头1：中景，平视。@[主角] 推开 @[酒馆] 的门，跨过门槛\n@[主角]：{我来了。}"
        ),
    )
    out = await gen.generate(episode=1)
    unit = _json.loads(out.read_text(encoding="utf-8"))["video_units"][0]
    assert extract_mentions(unit["text"]) == ["主角", "酒馆"]


@pytest.mark.asyncio
async def test_script_generator_rejects_prompt_authoring_unregistered_mention(reference_project: Path):
    """prompt_authoring 新增的 mention 同样过登记校验：未登记资产名不得混进正文。"""
    gen = ScriptGenerator(
        reference_project, generator=_fake_prompt_authoring_generator("镜头1：@[主角] 与 @[路人乙] 对视")
    )
    with pytest.raises(ValueError, match="未登记"):
        await gen.generate(episode=1)


@pytest.mark.asyncio
async def test_script_generator_reference_branch_inherits_drama_content_mode(tmp_path: Path):
    """drama 项目下生成的参考生视频剧本 content_mode 必须为 drama。

    Pydantic 的 ReferenceVideoScript.content_mode 默认 "narration"，model_dump 会
    把该默认值写入 dict；_add_metadata 必须显式覆盖而非 setdefault，否则 drama 项目
    的参考生视频剧本会被错误标记成 narration。
    """
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "project.json").write_text(
        """{
          "schema_version": __SCHEMA__,
          "title": "t",
          "content_mode": "drama",
          "generation_mode": "reference_video",
          "video_backend": "vidu/vidu2.0",
          "overview": {"synopsis": "s", "genre": "g", "theme": "th", "world_setting": "w"},
          "style": "国漫", "style_description": "水墨",
          "characters": {"主角": {"description": "d"}},
          "scenes": {"酒馆": {"description": "d"}}, "props": {},
          "episodes": [
            {"episode": 1, "title": "t1", "script_file": "scripts/episode_1.json",
             "generation_mode": "reference_video"}
          ]
        }""".replace("__SCHEMA__", str(CURRENT_PROJECT_SCHEMA_VERSION)),
        encoding="utf-8",
    )
    _write_script_plan(project_dir, SCRIPT_PLAN_UNITS_JSON)

    gen = ScriptGenerator(project_dir, generator=_fake_prompt_authoring_generator("镜头1：中景。@[主角] 推门"))
    out = await gen.generate(episode=1)

    import json as _j

    data = _j.loads(out.read_text(encoding="utf-8"))
    assert data["content_mode"] == "drama"
    assert "generation_mode" not in data


@pytest.mark.parametrize(
    ("caps", "expected"),
    [
        ({"max_reference_images": 3}, 3),
        ({"max_reference_images": 1}, 1),
        ({"max_reference_images": 0}, 0),
        # caps 缺该键 → 无法确定上限 → None
        ({}, None),
        # caps 整体缺失 → None
        (None, None),
    ],
)
def test_resolve_max_refs_from_caps(tmp_path: Path, caps, expected):
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    import json as _j

    project = {
        "title": "t",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "overview": {},
        "style": "",
        "style_description": "",
        "characters": {},
        "scenes": {},
        "props": {},
    }
    (project_dir / "project.json").write_text(_j.dumps(project), encoding="utf-8")

    gen = ScriptGenerator(project_dir)
    assert gen._resolve_max_refs(caps) == expected


@pytest.mark.parametrize(
    ("video_backend", "expected"),
    [
        ("grok/grok-imagine-video", 7),
        ("gemini-aistudio/veo-3.1-generate-preview", 3),
        ("ark/doubao-seedance-2-0-260128", 9),
        # registry 里 max_reference_images=0（字段默认/未声明）→ truthy 守卫当未声明 → None
        ("ark/doubao-seedream-4-0-250828", None),
        # registry 不存在该 provider → None
        ("nonexistent/whatever", None),
    ],
)
def test_resolve_max_refs_from_registry_fallback(tmp_path: Path, video_backend, expected):
    """caps 缺失时退到 project.json.video_backend → registry，与 _resolve_supported_durations 同构。"""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    import json as _j

    project = {
        "title": "t",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "video_backend": video_backend,
        "overview": {},
        "style": "",
        "style_description": "",
        "characters": {},
        "scenes": {},
        "props": {},
    }
    (project_dir / "project.json").write_text(_j.dumps(project), encoding="utf-8")

    gen = ScriptGenerator(project_dir)
    assert gen._resolve_max_refs(None) == expected


@pytest.mark.asyncio
async def test_build_prompt_no_video_backend_raises_value_error(tmp_path: Path):
    """project.json 缺 video_backend 且 caps 不可解析时，build_prompt 应抛 ValueError。

    设计意图：supported_durations 是单一真相源，必须由 caps（DB 全局默认）或 project.json 自报身份查 registry 提供；
    都拿不到才 fail loud，避免向 LLM 注入兜底 [4, 8] 误导生成。
    经 config_resolver seam 注入一个解析不可用的替身，模拟无任何 model 配置的环境。
    """
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    import json as _j

    (project_dir / "project.json").write_text(
        _j.dumps(
            {
                "title": "t",
                "content_mode": "narration",
                "generation_mode": "reference_video",
                "overview": {"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
                "style": "s",
                "style_description": "d",
                "characters": {},
                "scenes": {},
                "props": {},
                "episodes": [{"episode": 1, "generation_mode": "reference_video"}],
            }
        ),
        encoding="utf-8",
    )
    drafts = project_dir / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "script_plan_reference_units.json").write_text(SCRIPT_PLAN_UNITS_JSON, encoding="utf-8")

    gen = ScriptGenerator(project_dir, config_resolver=_stub_resolver(None))
    with pytest.raises(ValueError, match="supported_durations"):
        await gen.build_prompt(episode=1)


@pytest.mark.asyncio
async def test_fetch_video_capabilities_swallows_db_errors(reference_project: Path):
    """CI 回归：裸测试容器缺 migration 时 ConfigResolver 会抛 OperationalError；
    _fetch_video_capabilities 必须 fallback 返 None，不让 generate() 崩溃。
    """
    gen = ScriptGenerator(reference_project, config_resolver=_stub_resolver(None))
    caps = await gen._fetch_video_capabilities()
    assert caps is None


@pytest.mark.asyncio
async def test_build_prompt_follows_project_reference_route(tmp_path: Path):
    """项目生成模式为 reference_video 时 build_prompt 必须走 reference 分支。

    生成模式取自 ``project.json`` 顶层 ``generation_mode``，全项目相同、不随集号变化。
    """
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    import json as _j

    (project_dir / "project.json").write_text(
        _j.dumps(
            {
                "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
                "title": "t",
                "content_mode": "narration",
                "generation_mode": "reference_video",
                "video_backend": "vidu/vidu2.0",
                "overview": {"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
                "style": "s",
                "style_description": "d",
                "characters": {"A": {"description": "d"}},
                "scenes": {},
                "props": {},
                "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
            }
        ),
        encoding="utf-8",
    )
    _write_script_plan(project_dir, SCRIPT_PLAN_UNITS_JSON)

    gen = ScriptGenerator(project_dir)
    prompt = await gen.build_prompt(episode=1)
    assert "@[主角] 推开 @[酒馆] 的门" in prompt


@pytest.mark.asyncio
async def test_script_generator_reads_legacy_script_plan_draft_without_source_text(reference_project: Path):
    """存量 script_plan 草稿（无 source_text，per-shot 时长已由迁移收编）仍能被新校验器读取并跑完 prompt_authoring。

    ``source_text`` 是拆分工具产出时校验后落盘的原文锚，不带该字段的草稿一律视为存量：
    默认空串使读取照常通过，不要求用户重跑拆分。
    """
    saved = _json.loads(
        (reference_project / "drafts" / "episode_1" / "script_plan_reference_units.json").read_text("utf-8")
    )
    assert "source_text" not in saved["units"][0]

    gen = ScriptGenerator(reference_project, generator=_fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT))
    out = await gen.generate(episode=1)
    assert _json.loads(out.read_text(encoding="utf-8"))["video_units"][0]["unit_id"] == "E1U01"


@pytest.mark.asyncio
async def test_reference_script_plan_legacy_md_prompts_resplit(reference_project: Path):
    """仅存在结构化前的旧 .md 拆分表时，给出明确的「重跑拆分」提示而非笼统缺文件错误。"""
    drafts = reference_project / "drafts" / "episode_1"
    (drafts / "script_plan_reference_units.json").unlink()
    (drafts / "script_plan_reference_units.md").write_text("| E1U1 | Shot1(4s) |", encoding="utf-8")

    gen = ScriptGenerator(reference_project)
    with pytest.raises(FileNotFoundError, match="generate_script_plan"):
        await gen.build_prompt(episode=1)


@pytest.mark.asyncio
async def test_reference_script_plan_missing_raises(reference_project: Path):
    drafts = reference_project / "drafts" / "episode_1"
    (drafts / "script_plan_reference_units.json").unlink()

    gen = ScriptGenerator(reference_project)
    with pytest.raises(FileNotFoundError, match="video_unit 拆分"):
        await gen.build_prompt(episode=1)


@pytest.mark.asyncio
async def test_reference_script_plan_rejects_out_of_enum_duration(reference_project: Path):
    """读取侧复验 unit 时长 ∈ supported_durations，防手工编辑漂移出非法时长。"""
    drafts = reference_project / "drafts" / "episode_1"
    (drafts / "script_plan_reference_units.json").write_text(
        _json.dumps({"units": [{"unit_id": "E1U01", "text": "@[主角] 转身", "duration_seconds": 5}]}),
        encoding="utf-8",
    )

    # 固定能力来源为 project.json 自报身份查 registry（vidu2.0 → [4, 8]），隔离 DB 全局默认干扰
    gen = ScriptGenerator(reference_project, config_resolver=_stub_resolver(None))
    with pytest.raises(ValueError, match="时长非法"):
        await gen.build_prompt(episode=1)


@pytest.mark.asyncio
async def test_reference_script_plan_rejects_duplicate_unit_ids(reference_project: Path):
    drafts = reference_project / "drafts" / "episode_1"
    unit = {"unit_id": "E1U01", "text": "@[主角] 转身", "duration_seconds": 4}
    (drafts / "script_plan_reference_units.json").write_text(
        _json.dumps({"units": [unit, dict(unit)]}), encoding="utf-8"
    )

    gen = ScriptGenerator(reference_project)
    with pytest.raises(ValueError, match="unit_id 重复"):
        await gen.build_prompt(episode=1)


def test_reference_script_plan_migration_carries_confirmation_forward(reference_project: Path):
    """迁移回写让 script_plan 内容指纹漂移；若该集已确认（指纹恰是迁移前内容），须把确认指纹
    平移到迁移后的值，否则仅 build_prompt/dry-run 预览一次就会让已确认分集重新等待确认。
    """
    drafts = reference_project / "drafts" / "episode_1"
    # duration_override 是随 per-shot 时长一同退役的标记，加载时被收编迁移剥掉。
    legacy = {"units": [{"unit_id": "E1U01", "text": "@[主角] 转身", "duration_seconds": 4, "duration_override": True}]}
    script_plan_path = drafts / "script_plan_reference_units.json"
    script_plan_path.write_text(_json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    before = script_review.content_fingerprint(script_plan_path)

    project_path = reference_project / "project.json"
    project = _json.loads(project_path.read_text(encoding="utf-8"))
    project["episodes"][0]["script_plan_review"] = {"fingerprint": before, "confirmed_at": "2026-01-01T00:00:00Z"}
    project_path.write_text(_json.dumps(project, ensure_ascii=False), encoding="utf-8")

    gen = ScriptGenerator(reference_project)
    gen._load_reference_script_plan(episode=1, supported_durations=[4, 8])

    after_project = _json.loads(project_path.read_text(encoding="utf-8"))
    review = after_project["episodes"][0]["script_plan_review"]
    after = script_review.content_fingerprint(script_plan_path)
    assert review["fingerprint"] == after
    assert review["fingerprint"] != before


@pytest.mark.asyncio
async def test_reference_script_plan_migration_waits_for_prompt_authoring_draft_lock(
    reference_project: Path, monkeypatch
) -> None:
    script_plan_path = reference_project / "drafts" / "episode_1" / "script_plan_reference_units.json"
    script_plan_path.write_text(
        _json.dumps(
            {
                "units": [
                    {
                        "unit_id": "E1U01",
                        "text": "@[主角] 转身",
                        "duration_seconds": 4,
                        "duration_override": True,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    prompt_authoring_path = quarantine_path(reference_project, 1, QUARANTINE_KIND_PROMPT_AUTHORING)
    pm = ProjectManager(reference_project.parent)
    held = threading.Event()
    release = threading.Event()

    def hold_prompt_authoring_lock() -> None:
        with pm.file_lock(prompt_authoring_path):
            held.set()
            _ = release.wait()

    holder = asyncio.create_task(asyncio.to_thread(hold_prompt_authoring_lock))
    try:
        assert await asyncio.to_thread(held.wait, 1)
        attempted = threading.Event()
        target_lock = prompt_authoring_path.parent / f".{prompt_authoring_path.name}.lock"
        original_acquire = project_manager_module.portalocker.Lock.acquire

        def tracked_acquire(lock, *args, **kwargs):
            if Path(lock.filename) == target_lock:
                attempted.set()
            return original_acquire(lock, *args, **kwargs)

        monkeypatch.setattr(project_manager_module.portalocker.Lock, "acquire", tracked_acquire)
        prompt = asyncio.create_task(ScriptGenerator(reference_project).build_prompt(episode=1))
        attempted_before_release = await asyncio.to_thread(attempted.wait, 1)
        if attempted_before_release:
            assert not prompt.done()
            assert "duration_override" in script_plan_path.read_text(encoding="utf-8")
            ticked = asyncio.Event()
            asyncio.get_running_loop().call_soon(ticked.set)
            await asyncio.wait_for(ticked.wait(), timeout=1)
    finally:
        release.set()
        await asyncio.wait_for(holder, timeout=1)

    await asyncio.wait_for(prompt, timeout=1)
    assert attempted_before_release
    assert "duration_override" not in script_plan_path.read_text(encoding="utf-8")


def test_reference_script_plan_migration_carries_confirmation_confirmed_after_construction(reference_project: Path):
    """确认发生在 ScriptGenerator 构造之后（如 generate() 内 await _fetch_video_capabilities()
    期间用户经 ScriptReviewService.confirm() 并发确认）：self.project_json 是构造时的旧快照，
    看不到这次确认，但迁移写回仍须正确搬移它——不能用这份旧快照做前置短路。
    """
    drafts = reference_project / "drafts" / "episode_1"
    # duration_override 是随 per-shot 时长一同退役的标记，加载时被收编迁移剥掉。
    legacy = {"units": [{"unit_id": "E1U01", "text": "@[主角] 转身", "duration_seconds": 4, "duration_override": True}]}
    script_plan_path = drafts / "script_plan_reference_units.json"
    script_plan_path.write_text(_json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    before = script_review.content_fingerprint(script_plan_path)

    # 构造时 project.json 尚无确认记录。
    gen = ScriptGenerator(reference_project)

    # 构造之后才发生确认（模拟并发的 ScriptReviewService.confirm()），self.project_json 不刷新。
    project_path = reference_project / "project.json"
    project = _json.loads(project_path.read_text(encoding="utf-8"))
    project["episodes"][0]["script_plan_review"] = {"fingerprint": before, "confirmed_at": "2026-01-01T00:00:00Z"}
    project_path.write_text(_json.dumps(project, ensure_ascii=False), encoding="utf-8")
    assert "script_plan_review" not in gen.project_json["episodes"][0]  # 构造时的快照确实还没有它

    gen._load_reference_script_plan(episode=1, supported_durations=[4, 8])

    after_project = _json.loads(project_path.read_text(encoding="utf-8"))
    review = after_project["episodes"][0]["script_plan_review"]
    after = script_review.content_fingerprint(script_plan_path)
    assert review["fingerprint"] == after
    assert review["confirmed_at"] == "2026-01-01T00:00:00Z"
    assert review["confirmed_at"] == "2026-01-01T00:00:00Z"


def test_reference_script_plan_migration_does_not_carry_confirmation_when_duration_is_clamped(reference_project: Path):
    """迁移带 warnings（求和时长不在模型档位内，被取档改写）不是纯格式收编：已确认分集
    须重新等待确认，不能平移确认——取档后的秒数不是用户确认时看到的值。

    重新等待确认的同时本次调用也须中止：内容确认判的是迁移前状态、已按「已确认」放行，
    改写发生在放行之后，继续下去就会按用户从未过目的秒数走完付费的 prompt_authoring。
    """
    drafts = reference_project / "drafts" / "episode_1"
    # duration_override 是随 per-shot 时长一同退役的标记，加载时被收编迁移剥掉。
    legacy = {"units": [{"unit_id": "E1U01", "text": "@[主角] 转身", "duration_seconds": 4, "duration_override": True}]}
    script_plan_path = drafts / "script_plan_reference_units.json"
    script_plan_path.write_text(_json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    before = script_review.content_fingerprint(script_plan_path)

    project_path = reference_project / "project.json"
    project = _json.loads(project_path.read_text(encoding="utf-8"))
    project["episodes"][0]["script_plan_review"] = {"fingerprint": before, "confirmed_at": "2026-01-01T00:00:00Z"}
    project_path.write_text(_json.dumps(project, ensure_ascii=False), encoding="utf-8")

    gen = ScriptGenerator(reference_project)
    # 求和 4s 不是模型档位成员，取档改写为 8s——这一步产生 warning。
    with pytest.raises(ValueError, match="尚未完成内容确认"):
        gen._load_reference_script_plan(episode=1, supported_durations=[8])

    # 迁移本身已幂等落盘（中止的是本次生成，不是迁移）。
    assert _json.loads(script_plan_path.read_text(encoding="utf-8"))["units"][0]["duration_seconds"] == 8

    after_project = _json.loads(project_path.read_text(encoding="utf-8"))
    review = after_project["episodes"][0]["script_plan_review"]
    assert review["fingerprint"] == before  # 未被平移，仍是迁移前的旧指纹——照常判定为待确认


async def test_script_plan_text_violation_is_caught_before_the_paid_prompt_authoring_call(reference_project: Path):
    """script_plan 正文的语法违约在调用文本模型之前就被拦下，且错误指名 script_plan。

    编辑器侧保存只做结构校验（人写的文本有作者意图要保护，语法问题仅出 warning），手工编辑
    过的 script_plan 因而可能带着未登记的 `@[名称]` 进到生成。prompt_authoring 会逐字保留这段正文，违约必然
    原样复现——不在调用前判，就要付完 prompt_authoring 的钱才失败，且错误指向 prompt_authoring「改坏了」。
    """
    drafts = reference_project / "drafts" / "episode_1"
    (drafts / "script_plan_reference_units.json").write_text(
        _json.dumps(
            {"units": [{"unit_id": "E1U01", "duration_seconds": 4, "text": "@[查无此人} 推门"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    fake_generator = _fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT)

    gen = ScriptGenerator(reference_project, generator=fake_generator)

    with pytest.raises(DraftViolation, match="来自 script_plan"):
        await gen.generate(episode=1)
    fake_generator.generate.assert_not_awaited()


def test_script_plan_speech_violation_preserves_canonical_unit_and_locations(reference_project: Path):
    gen = ScriptGenerator(reference_project)
    units = [
        {
            "unit_id": "E1U01",
            "duration_seconds": 4,
            "text": "门被推开\n@[主角]：{快走。}\n{风吹过旷野。}",
        }
    ]

    with pytest.raises(DraftViolation) as exc_info:
        gen._assert_reference_script_plan_text_valid(units, max_refs=None)

    problem = exc_info.value
    assert problem.code == "mixed_speech"
    assert "unit E1U01 发声准入未通过" in str(problem)
    assert "unit script_plan 的 unit" not in str(problem)
    assert problem.locations == (
        {"path": ["text"], "line": 1},
        {"path": ["text"], "line": 2},
    )
    assert problem.reason == "character_and_narrator_mixed"
    assert problem.action == "replan_unit"


async def test_script_plan_dialogue_overload_is_caught_before_the_paid_prompt_authoring_call(reference_project: Path):
    """内容确认时改短时长 / 补写台词绕开了拆分时的口播量校验，生成前复判把它拦下。

    prompt_authoring 逐字保留台词、之后再无口播量校验：不在这里复判，念不完的 unit 会一路落盘成片。
    """
    drafts = reference_project / "drafts" / "episode_1"
    long_line = "他站在门口足足看了半晌才缓缓开口说出这句迟到了整整十年的道歉与告别" * 2
    (drafts / "script_plan_reference_units.json").write_text(
        _json.dumps(
            {
                "units": [
                    {
                        "unit_id": "E1U01",
                        "duration_seconds": 4,
                        "text": f"@[主角] 推开 @[酒馆] 的门\n@[主角]：{{{long_line}}}",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    fake_generator = _fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT)

    gen = ScriptGenerator(reference_project, generator=fake_generator)

    with pytest.raises(DraftViolation, match="台词念完约需"):
        await gen.generate(episode=1)
    fake_generator.generate.assert_not_awaited()


async def test_prompt_authoring_missing_title_falls_back_instead_of_failing_the_paid_call(reference_project: Path):
    """非约束解码通道漏写 title 时兜底为「第N集」：title 仅展示用，不值得让已付费的展开失败。"""
    drafts = reference_project / "drafts" / "episode_1"
    (drafts / "script_plan_reference_units.json").write_text(SCRIPT_PLAN_UNITS_JSON, encoding="utf-8")
    generator = MagicMock()
    generator.model = "mock"
    generator.generate = AsyncMock(
        return_value=MagicMock(text=_json.dumps({"units": [{"text": PROMPT_AUTHORING_UNIT_TEXT}]}, ensure_ascii=False))
    )

    gen = ScriptGenerator(reference_project, generator=generator)
    out = await gen.generate(episode=1)

    assert _json.loads(out.read_text(encoding="utf-8"))["title"] == "第1集"


# ---------------------------------------------------------------------------
# prompt_authoring 违约的待修复草稿与修复晋升闭环
# ---------------------------------------------------------------------------

#: 违约的 prompt_authoring 展开：引用了未登记的资产名（script_plan 正文里没有的 @[路人甲]）。
BAD_PROMPT_AUTHORING_UNIT_TEXT = "镜头1：中景。@[路人甲] 推开 @[酒馆] 的门。"


def _prompt_authoring_quarantine(project: Path):
    return quarantine_path(project, 1, QUARANTINE_KIND_PROMPT_AUTHORING)


def _script_path(project: Path) -> Path:
    return project / "scripts" / "episode_1.json"


@pytest.mark.asyncio
async def test_prompt_authoring_violation_quarantines_instead_of_discarding(reference_project: Path):
    """prompt_authoring 违约不丢弃这次已付费的展开：产物落待修复草稿、正式剧本不被写出、报告带处置指引。"""
    gen = ScriptGenerator(reference_project, generator=_fake_prompt_authoring_generator(BAD_PROMPT_AUTHORING_UNIT_TEXT))

    with pytest.raises(DraftViolation) as excinfo:
        await gen.generate(episode=1)

    report = str(excinfo.value)
    assert "unregistered_asset" in report
    assert "promote_draft" in report
    assert not _script_path(reference_project).exists()

    envelope = _json.loads(_prompt_authoring_quarantine(reference_project).read_text(encoding="utf-8"))
    assert envelope["kind"] == QUARANTINE_KIND_PROMPT_AUTHORING
    assert envelope["meta"]["base_fingerprint"] is None
    assert [v["code"] for v in envelope["violations"]] == ["unregistered_asset"]
    # 草稿装的是扁平草稿结构（Agent 要改的是其中的正文 / 原文锚 / 时长）
    assert envelope["content"]["units"][0]["text"] == BAD_PROMPT_AUTHORING_UNIT_TEXT


@pytest.mark.asyncio
async def test_cancelled_prompt_authoring_quarantine_finishes_without_blocking_event_loop(reference_project: Path):
    generator = ScriptGenerator(
        reference_project, generator=_fake_prompt_authoring_generator(BAD_PROMPT_AUTHORING_UNIT_TEXT)
    )
    started = threading.Event()
    release = threading.Event()
    caller_thread = threading.get_ident()
    worker_threads: list[int] = []

    def before_quarantine_commit() -> None:
        worker_threads.append(threading.get_ident())
        started.set()
        release.wait()

    generation = asyncio.create_task(generator.generate(episode=1, before_quarantine_commit=before_quarantine_commit))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        ticked = asyncio.Event()
        asyncio.get_running_loop().call_soon(ticked.set)
        await asyncio.wait_for(ticked.wait(), timeout=1)
        generation.cancel()
        await asyncio.sleep(0)
        assert not generation.done()
    finally:
        release.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(generation, timeout=1)
    assert _prompt_authoring_quarantine(reference_project).exists()
    assert worker_threads
    assert all(thread != caller_thread for thread in worker_threads)


@pytest.mark.asyncio
async def test_prompt_authoring_generation_preserves_draft_edited_during_model_call(reference_project: Path):
    started = asyncio.Event()
    release = asyncio.Event()
    generator = MagicMock()
    generator.model = "mock"

    async def generate(_request, **_kwargs):
        started.set()
        await release.wait()
        return MagicMock(text=_prompt_authoring_response(BAD_PROMPT_AUTHORING_UNIT_TEXT))

    generator.generate = AsyncMock(side_effect=generate)
    generation = asyncio.create_task(ScriptGenerator(reference_project, generator=generator).generate(episode=1))
    await started.wait()
    write_quarantine(
        reference_project,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "并发草稿", "units": [{"text": "并发编辑内容"}]},
        violations=[],
    )
    release.set()

    with pytest.raises(DraftViolation) as excinfo:
        await asyncio.wait_for(generation, timeout=1)
    assert excinfo.value.code == "draft_revision_conflict"
    envelope = _json.loads(_prompt_authoring_quarantine(reference_project).read_text(encoding="utf-8"))
    assert envelope["content"]["title"] == "并发草稿"
    assert envelope["content"]["units"][0]["text"] == "并发编辑内容"


@pytest.mark.asyncio
async def test_successful_prompt_authoring_generation_rejects_draft_created_during_model_call(reference_project: Path):
    started = asyncio.Event()
    release = asyncio.Event()
    generator = MagicMock()
    generator.model = "mock"

    async def generate(_request, **_kwargs):
        started.set()
        await release.wait()
        return MagicMock(text=_prompt_authoring_response(PROMPT_AUTHORING_UNIT_TEXT))

    generator.generate = AsyncMock(side_effect=generate)
    generation = asyncio.create_task(ScriptGenerator(reference_project, generator=generator).generate(episode=1))
    await asyncio.wait_for(started.wait(), timeout=1)
    write_quarantine(
        reference_project,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "并发草稿", "units": [{"text": "并发编辑内容"}]},
        violations=[],
        meta={"base_fingerprint": None},
    )
    release.set()

    with pytest.raises(DraftViolation) as excinfo:
        await asyncio.wait_for(generation, timeout=1)
    assert excinfo.value.code == "draft_revision_conflict"
    assert not _script_path(reference_project).exists()
    envelope = _json.loads(_prompt_authoring_quarantine(reference_project).read_text(encoding="utf-8"))
    assert envelope["content"]["title"] == "并发草稿"


@pytest.mark.asyncio
async def test_prompt_authoring_generation_rejects_existing_draft_before_model_call(reference_project: Path):
    write_quarantine(
        reference_project,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "现有草稿", "units": [{"text": PROMPT_AUTHORING_UNIT_TEXT}]},
        violations=[],
        meta={"base_fingerprint": None},
    )
    generator = _fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT)

    with pytest.raises(DraftViolation) as excinfo:
        await ScriptGenerator(reference_project, generator=generator).generate(episode=1)

    assert excinfo.value.code == "draft_revision_conflict"
    generator.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_prompt_authoring_preserves_output_when_formal_script_changes_during_generation(reference_project: Path):
    formal = await ScriptGenerator(
        reference_project, generator=_fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT)
    ).generate(episode=1)
    concurrent = _json.loads(formal.read_text(encoding="utf-8"))
    concurrent["title"] = "并发正式标题"
    generator = MagicMock()
    generator.model = "mock"

    async def generate(_request, **_kwargs):
        formal.write_text(_json.dumps(concurrent, ensure_ascii=False), encoding="utf-8")
        return MagicMock(text=_prompt_authoring_response(PROMPT_AUTHORING_UNIT_TEXT, title="本次生成标题"))

    generator.generate = AsyncMock(side_effect=generate)

    with pytest.raises(DraftViolation) as excinfo:
        # scope="all"：脚本规划未变，默认的失配范围为空、不会调用模型；本用例测的是整集重写
        # 期间正式剧本被并发改写的处置。
        await ScriptGenerator(reference_project, generator=generator).generate(episode=1, scope="all")

    assert "formal_revision_conflict" in str(excinfo.value)
    assert _json.loads(formal.read_text(encoding="utf-8"))["title"] == "并发正式标题"
    envelope = _json.loads(_prompt_authoring_quarantine(reference_project).read_text(encoding="utf-8"))
    assert envelope["content"]["title"] == "本次生成标题"
    assert envelope["meta"]["base_fingerprint"] is not None


@pytest.mark.asyncio
async def test_prompt_authoring_violation_keeps_generation_start_formal_baseline(reference_project: Path):
    formal = await ScriptGenerator(
        reference_project, generator=_fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT)
    ).generate(episode=1)
    baseline = script_review.content_fingerprint(formal)
    assert baseline is not None
    concurrent = _json.loads(formal.read_text(encoding="utf-8"))
    concurrent["title"] = "并发正式标题"
    generator = MagicMock()
    generator.model = "mock"

    async def generate(_request, **_kwargs):
        formal.write_text(_json.dumps(concurrent, ensure_ascii=False), encoding="utf-8")
        return MagicMock(text=_prompt_authoring_response(BAD_PROMPT_AUTHORING_UNIT_TEXT, title="本次违约输出"))

    generator.generate = AsyncMock(side_effect=generate)

    with pytest.raises(DraftViolation):
        # 同上：脚本规划未变，整集重写须显式声明 scope。
        await ScriptGenerator(reference_project, generator=generator).generate(episode=1, scope="all")

    assert _json.loads(formal.read_text(encoding="utf-8"))["title"] == "并发正式标题"
    envelope = _json.loads(_prompt_authoring_quarantine(reference_project).read_text(encoding="utf-8"))
    assert envelope["content"]["title"] == "本次违约输出"
    assert envelope["meta"]["base_fingerprint"] == baseline
    assert envelope["meta"]["base_fingerprint"] != script_review.content_fingerprint(formal)


@pytest.mark.asyncio
async def test_promote_prompt_authoring_draft_after_repair(reference_project: Path, monkeypatch):
    """修好待修复草稿后晋升：正式剧本落盘、草稿清除，结构仍由 script_plan + 正文机械合成。"""
    gen = ScriptGenerator(reference_project, generator=_fake_prompt_authoring_generator(BAD_PROMPT_AUTHORING_UNIT_TEXT))
    with pytest.raises(DraftViolation):
        await gen.generate(episode=1)

    path = _prompt_authoring_quarantine(reference_project)
    envelope = _json.loads(path.read_text(encoding="utf-8"))
    envelope["content"]["units"][0]["text"] = PROMPT_AUTHORING_UNIT_TEXT
    path.write_text(_json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    caller_thread = threading.get_ident()
    worker_threads: list[int] = []
    original_save = ProjectManager.save_script

    def tracked_save(self, *args, **kwargs):
        worker_threads.append(threading.get_ident())
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(ProjectManager, "save_script", tracked_save)

    out = await ScriptGenerator(reference_project).promote_reference_prompt_authoring_draft(episode=1)

    assert out.exists()
    assert worker_threads
    assert all(thread != caller_thread for thread in worker_threads)
    assert not path.exists()
    data = _json.loads(out.read_text(encoding="utf-8"))
    unit = data["video_units"][0]
    assert unit["unit_id"] == "E1U01"
    assert unit["duration_seconds"] == 4
    assert extract_mentions(unit["text"]) == ["主角", "酒馆"]


@pytest.mark.asyncio
async def test_promote_prompt_authoring_draft_rejects_stale_formal_baseline(reference_project: Path):
    formal = await ScriptGenerator(
        reference_project, generator=_fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT)
    ).generate(episode=1)
    baseline = script_review.content_fingerprint(formal)
    assert baseline is not None
    write_quarantine(
        reference_project,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "修复稿", "units": [{"text": PROMPT_AUTHORING_UNIT_TEXT}]},
        violations=[],
        meta={"base_fingerprint": baseline},
    )

    pm = ProjectManager(str(reference_project.parent))
    concurrent = pm.load_script(reference_project.name, formal.name)
    concurrent["title"] = "并发修改"
    pm.save_script(reference_project.name, concurrent, formal.name)

    with pytest.raises(ScriptWriteConflict):
        await ScriptGenerator(reference_project).promote_reference_prompt_authoring_draft(
            episode=1,
            expected_fingerprint=baseline,
        )

    assert pm.load_script(reference_project.name, formal.name)["title"] == "并发修改"
    assert _prompt_authoring_quarantine(reference_project).exists()


@pytest.mark.asyncio
async def test_promote_prompt_authoring_draft_requires_formal_baseline(reference_project: Path):
    write_quarantine(
        reference_project,
        1,
        QUARANTINE_KIND_PROMPT_AUTHORING,
        content={"title": "修复稿", "units": [{"text": PROMPT_AUTHORING_UNIT_TEXT}]},
        violations=[],
    )

    with pytest.raises(DraftViolation) as excinfo:
        await ScriptGenerator(reference_project).promote_reference_prompt_authoring_draft(episode=1)

    assert excinfo.value.code == "formal_revision_missing"
    assert _prompt_authoring_quarantine(reference_project).exists()
    assert not _script_path(reference_project).exists()


@pytest.mark.asyncio
async def test_promote_prompt_authoring_draft_reports_again_without_round_limit(reference_project: Path):
    """再违约则刷新报告、草稿留在原地——可反复晋升，无收敛轮次上限。"""
    gen = ScriptGenerator(reference_project, generator=_fake_prompt_authoring_generator(BAD_PROMPT_AUTHORING_UNIT_TEXT))
    with pytest.raises(DraftViolation):
        await gen.generate(episode=1)

    path = _prompt_authoring_quarantine(reference_project)
    for _round in range(3):
        with pytest.raises(DraftViolation, match="unregistered_asset"):
            await ScriptGenerator(reference_project).promote_reference_prompt_authoring_draft(episode=1)
        assert path.exists()
        assert not _script_path(reference_project).exists()

    envelope = _json.loads(path.read_text(encoding="utf-8"))
    envelope["content"]["units"][0]["text"] = "门开了\n@[主角]：{我来了"
    path.write_text(_json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(DraftViolation):
        await ScriptGenerator(reference_project).promote_reference_prompt_authoring_draft(episode=1)
    refreshed = _json.loads(path.read_text(encoding="utf-8"))
    assert [v["code"] for v in refreshed["violations"]] == ["dialogue_line_syntax"]


@pytest.mark.asyncio
async def test_promote_prompt_authoring_draft_rejects_schema_breach_with_report(reference_project: Path):
    """草稿的 content 被改坏 schema 层同样只回报告：与 script_plan 晋升同口径，正式剧本不被污染。

    这条路上没有 backend 可重试（content 是 Agent 手写的），走 ValueError 直抛的话草稿里的
    violations 快照不会刷新，Agent 只能从工具文本里看到一段 pydantic 报错。
    """
    gen = ScriptGenerator(reference_project, generator=_fake_prompt_authoring_generator(BAD_PROMPT_AUTHORING_UNIT_TEXT))
    with pytest.raises(DraftViolation):
        await gen.generate(episode=1)

    path = _prompt_authoring_quarantine(reference_project)
    envelope = _json.loads(path.read_text(encoding="utf-8"))
    envelope["content"]["units"][0].pop("text")
    path.write_text(_json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(DraftViolation, match="schema_invalid"):
        await ScriptGenerator(reference_project).promote_reference_prompt_authoring_draft(episode=1)

    assert path.exists()
    assert not _script_path(reference_project).exists()
    refreshed = _json.loads(path.read_text(encoding="utf-8"))
    assert [v["code"] for v in refreshed["violations"]] == ["schema_invalid"]


@pytest.mark.asyncio
async def test_prompt_authoring_duration_off_tier_after_merge_quarantines(wide_tier_reference_project: Path):
    """合并之后才判出的档位越界同样落待修复草稿——这份展开已经付过费了。

    prompt_authoring 可以给 unit 增删 `@` 引用，生效档位随之换一套：script_plan 那个 2 秒的无引用 unit 在展开时
    加进了引用，档位就从 1–16 秒收窄到 3–16 秒。参考图约束只做收窄，故「展开后才越界」只可能
    发生在增加引用的方向上。这一判在 `_add_metadata` 里、在保结构 diff 之后，不接住的话产物
    只存在于内存里，错误却让调用方重新生成。
    """
    project = wide_tier_reference_project
    _write_script_plan(
        project,
        _json.dumps({"units": [{"unit_id": "E1U01", "text": "他推门", "duration_seconds": 2}]}, ensure_ascii=False),
    )
    with_reference_text = "镜头1：中景，平视。@[主角] 推开门，侧身跨过门槛。"
    gen = ScriptGenerator(
        project,
        generator=_fake_prompt_authoring_generator(with_reference_text),
        config_resolver=_stub_resolver({}),
    )

    with pytest.raises(DraftViolation) as excinfo:
        await gen.generate(episode=1)

    assert "生效档位" in str(excinfo.value)
    assert not _script_path(project).exists()
    envelope = _json.loads(_prompt_authoring_quarantine(project).read_text(encoding="utf-8"))
    assert [v["code"] for v in envelope["violations"]] == ["duration_off_tier"]
    # 草稿装的仍是 Agent 要改的那一层正文，去掉 `@` 引用即可重新晋升
    assert envelope["content"]["units"][0]["text"] == with_reference_text


@pytest.mark.asyncio
async def test_promote_prompt_authoring_draft_revalidates_edited_script_plan(reference_project: Path):
    """晋升前按产出路径同一份预判重判 script_plan 现值：草稿在场期间 Web 端改坏 script_plan 不能借晋升落盘。

    编辑器对人写正文只出 warning，改出未登记的 @[名称] 能存下去；而保结构 diff 只比对 prompt_authoring
    正文与 script_plan 的镜头/台词结构，不复判 script_plan 自身的正文合法性。
    """
    gen = ScriptGenerator(reference_project, generator=_fake_prompt_authoring_generator(BAD_PROMPT_AUTHORING_UNIT_TEXT))
    with pytest.raises(DraftViolation):
        await gen.generate(episode=1)

    path = _prompt_authoring_quarantine(reference_project)
    envelope = _json.loads(path.read_text(encoding="utf-8"))
    envelope["content"]["units"][0]["text"] = PROMPT_AUTHORING_UNIT_TEXT
    path.write_text(_json.dumps(envelope, ensure_ascii=False), encoding="utf-8")

    script_plan = reference_project / "drafts" / "episode_1" / "script_plan_reference_units.json"
    script_plan_data = _json.loads(script_plan.read_text(encoding="utf-8"))
    script_plan_data["units"][0]["text"] = "@[路人甲] 推开 @[酒馆] 的门"
    script_plan.write_text(_json.dumps(script_plan_data, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(DraftViolation, match="script_plan"):
        await ScriptGenerator(reference_project).promote_reference_prompt_authoring_draft(episode=1)

    assert path.exists()
    assert not _script_path(reference_project).exists()


@pytest.mark.asyncio
async def test_promote_prompt_authoring_draft_without_draft(reference_project: Path):
    with pytest.raises(FileNotFoundError, match="没有可晋升的 prompt_authoring 待修复草稿"):
        await ScriptGenerator(reference_project).promote_reference_prompt_authoring_draft(episode=1)


@pytest.mark.asyncio
async def test_prompt_authoring_refuses_to_run_while_script_plan_quarantined(reference_project: Path):
    """script_plan 草稿还在场时不跑 prompt_authoring：正式 script_plan 仍是上一版，拿它生成等于静默换回旧内容。"""
    write_quarantine(
        reference_project,
        1,
        QUARANTINE_KIND_SCRIPT_PLAN,
        content={"units": []},
        violations=[DraftViolation("坏", code="empty_text", label="unit E1U01")],
    )
    gen = ScriptGenerator(reference_project, generator=_fake_prompt_authoring_generator(PROMPT_AUTHORING_UNIT_TEXT))
    with pytest.raises(ValueError, match="有待修复草稿"):
        await gen.generate(episode=1)
