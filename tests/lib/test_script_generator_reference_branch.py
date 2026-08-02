"""ScriptGenerator reference_video 分支测试。"""

import json as _json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from lib import script_review
from lib.reference_video.draft_validation import DraftViolation
from lib.script_generator import ScriptGenerator

STEP1_UNITS_JSON = _json.dumps(
    {
        "units": [
            {
                "unit_id": "E1U01",
                "shots": [{"text": "@[主角] 推开 @[酒馆] 的门"}],
                "duration_seconds": 4,
                "references": [
                    {"type": "character", "name": "主角"},
                    {"type": "scene", "name": "酒馆"},
                ],
            }
        ]
    },
    ensure_ascii=False,
)


def _step2_response(*texts: str, title: str = "t") -> str:
    """step2 的 LLM 产出：扁平 ``{title, units: [{text}]}``——unit_id / 时长 / references 不进输出。"""
    return _json.dumps({"title": title, "units": [{"text": t} for t in texts]}, ensure_ascii=False)


def _fake_step2_generator(*texts: str) -> MagicMock:
    generator = MagicMock()
    generator.model = "mock"
    generator.generate = AsyncMock(return_value=MagicMock(text=_step2_response(*texts)))
    return generator


#: 与 ``STEP1_UNITS_JSON`` 单 unit 对应的合法视觉展开：镜头数不变、无台词行可改。
STEP2_UNIT_TEXT = "镜头1：中景，平视。@[主角] 推开 @[酒馆] 的门，侧身跨过门槛。"


@pytest.fixture
def reference_project(tmp_path: Path) -> Path:
    """造一个 reference_video 模式的最小项目。"""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "project.json").write_text(
        """{
          "title": "t",
          "content_mode": "narration",
          "generation_mode": "reference_video",
          "_supported_durations": [4, 8],
          "overview": {"synopsis": "s", "genre": "g", "theme": "th", "world_setting": "w"},
          "style": "国漫",
          "style_description": "水墨",
          "characters": {"主角": {"description": "d"}},
          "scenes": {"酒馆": {"description": "d"}},
          "props": {},
          "episodes": [{"episode": 1, "title": "t1", "generation_mode": "reference_video"}]
        }""",
        encoding="utf-8",
    )
    drafts = project_dir / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "step1_reference_units.json").write_text(STEP1_UNITS_JSON, encoding="utf-8")
    return project_dir


@pytest.mark.asyncio
@pytest.mark.integration
async def test_script_generator_build_prompt_selects_reference_branch(reference_project: Path):
    """当 generation_mode == reference_video 时，build_prompt 必须走 reference 分支。"""
    gen = ScriptGenerator(reference_project)
    prompt = await gen.build_prompt(episode=1)
    # reference 分支特征标签
    assert "视觉展开" in prompt
    assert "<step1_units>" in prompt
    assert "@[名称]" in prompt
    # 不应出现 narration / drama 特征
    assert "characters_in_segment" not in prompt


@pytest.mark.asyncio
@pytest.mark.integration
async def test_script_generator_reads_step1_reference_units(reference_project: Path):
    gen = ScriptGenerator(reference_project)
    prompt = await gen.build_prompt(episode=1)
    # step1 正文须经机械渲染进入 prompt（带 镜头N： header 的书写层文本 + unit 时长）
    assert "镜头1：@[主角] 推开 @[酒馆] 的门" in prompt
    assert "（时长 4s）" in prompt
    # unit_id 由序号机械派生，不下发给 step2
    assert "E1U01" not in prompt


@pytest.mark.asyncio
@pytest.mark.integration
async def test_script_generator_uses_reference_schema_on_generate(reference_project: Path):
    """step2 用扁平 schema 出正文，落盘结构由 step1 + 正文机械合成。"""
    from lib.script_models import ReferenceStep2FlatScript

    fake_generator = _fake_step2_generator(STEP2_UNIT_TEXT)

    gen = ScriptGenerator(reference_project, generator=fake_generator)

    out = await gen.generate(episode=1)
    assert out.exists()
    import json as _j

    data = _j.loads(out.read_text(encoding="utf-8"))
    # 参考视频集 content_mode 继承项目级 narration/drama；生成模式由独立的
    # generation_mode 字段表达。
    assert data["content_mode"] == "narration"
    assert data["generation_mode"] == "reference_video"
    assert len(data["video_units"]) == 1
    unit = data["video_units"][0]
    # unit_id / 时长沿用 step1；shots / references 由正文机械派生
    assert unit["unit_id"] == "E1U01"
    assert unit["duration_seconds"] == 4
    assert unit["shots"][0]["text"].startswith("中景，平视。")
    assert unit["references"] == [
        {"type": "character", "name": "主角"},
        {"type": "scene", "name": "酒馆"},
    ]

    # step2 的 response_schema 是扁平形状，且不含 duration_seconds——时长没让 LLM 写
    schema = fake_generator.generate.await_args.args[0].response_schema
    assert schema is ReferenceStep2FlatScript
    assert "duration_seconds" not in _json.dumps(schema.model_json_schema())


@pytest.mark.asyncio
@pytest.mark.integration
async def test_script_generator_overrides_llm_duration_with_step1_confirmed_value(reference_project: Path):
    """unit 时长的单一真相是 step1 审阅确认的值：step2 根本不产出该字段，落盘值机械取自
    step1（时长即计费，不给 LLM 留任何改写入口）。
    """
    gen = ScriptGenerator(reference_project, generator=_fake_step2_generator(STEP2_UNIT_TEXT))
    out = await gen.generate(episode=1)

    data = _json.loads(out.read_text(encoding="utf-8"))
    assert data["video_units"][0]["duration_seconds"] == 4  # step1 确认值
    assert data["duration_seconds"] == 4


@pytest.mark.asyncio
@pytest.mark.integration
async def test_script_generator_rejects_confirmed_duration_outside_effective_tiers(reference_project: Path):
    """step1 校验用未收窄的 raw 档位（此处 [4,8]），但本集实际按参考图收窄到 [8]：确认时
    合法的 4 秒不再是收窄后的合法值。这种情况下不能静默取档改写落盘——用户审阅通过的
    时长/费用会被换成一个从未过目的值，须 fail-loud 要求重新审阅确认。

    拦截须发生在 TextBackend 调用之前：带引用与不带引用两种生效档位都不接受该确认时长时，
    本次生成必然失败：放到输出解析阶段才拦，用户已经为它付了费。
    """
    fake_generator = MagicMock()
    fake_generator.model = "mock"
    fake_generator.generate = AsyncMock(
        return_value=MagicMock(
            text=(
                '{"episode":1,"title":"t",'
                '"summary":"s","novel":{"title":"t","chapter":"1"},'
                '"video_units":[{"unit_id":"E1U01",'
                '"shots":[{"text":"@主角 推门"}],'
                '"references":[{"type":"character","name":"主角"}],'
                '"duration_seconds":8,"transition_to_next":"cut"}]}'
            )
        )
    )

    gen = ScriptGenerator(reference_project, generator=fake_generator)
    with (
        # 显式固定 caps，不依赖真实 DB 解析结果——同 test_script_generator_takes_duration_
        # tier_from_final_output_references_not_step1 的理由。
        patch.object(ScriptGenerator, "_fetch_video_capabilities", AsyncMock(return_value={})),
        patch.object(ScriptGenerator, "_resolve_supported_durations", return_value=[8]),
    ):
        with pytest.raises(ValueError, match="不在当前生效档位"):
            await gen.generate(episode=1)
    fake_generator.generate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_script_generator_narrows_duration_tiers_per_unit_not_episode_wide(reference_project: Path):
    """同集内一个 unit 带参考图（收窄到 [8]）、另一个不带（仍是 [4,8]）：后者本已合法的
    确认值 4 秒不应因前者的收窄被连带改成 8——取档须按每个 unit 自己的参考图状态重算
    生效档位，不套用 episode 级 any(...) 收窄出的粗粒度集合。
    """
    drafts = reference_project / "drafts" / "episode_1"
    (drafts / "step1_reference_units.json").write_text(
        _json.dumps(
            {
                "units": [
                    {
                        "unit_id": "E1U01",
                        "shots": [{"text": "@[主角] 推门"}],
                        "duration_seconds": 8,
                        "references": [{"type": "character", "name": "主角"}],
                    },
                    {
                        "unit_id": "E1U02",
                        "shots": [{"text": "空镜"}],
                        "duration_seconds": 4,
                        "references": [],
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    fake_generator = _fake_step2_generator("镜头1：中景。@[主角] 推门", "镜头1：空镜，风吹过门廊")

    def _fake_supported_durations(self, caps=None, *, gen_mode, uses_reference_images=None):
        return [8] if uses_reference_images else [4, 8]

    gen = ScriptGenerator(reference_project, generator=fake_generator)
    with patch.object(ScriptGenerator, "_resolve_supported_durations", _fake_supported_durations):
        out = await gen.generate(episode=1)

    data = _json.loads(out.read_text(encoding="utf-8"))
    units_by_id = {u["unit_id"]: u for u in data["video_units"]}
    assert units_by_id["E1U01"]["duration_seconds"] == 8
    assert units_by_id["E1U02"]["duration_seconds"] == 4  # 未被另一个带图 unit 的收窄连带改动


@pytest.mark.asyncio
@pytest.mark.integration
async def test_script_generator_takes_duration_tier_from_final_output_references_not_step1(
    reference_project: Path,
):
    """step1 拆分时某 unit 带引用（按带图档位确认，仅 8 秒合法），但 step2 输出给这个 unit
    去掉了引用（回落到纯文本档位 [4, 8]，4 秒合法）：取档须按最终落地的 references 状态
    重算，不能沿用 step1 的旧状态——按 step1 状态取档会把本已合法的确认值误判为不合法。
    """
    drafts = reference_project / "drafts" / "episode_1"
    (drafts / "step1_reference_units.json").write_text(
        _json.dumps(
            {
                "units": [
                    {
                        "unit_id": "E1U01",
                        "shots": [{"text": "@[主角] 推门"}],
                        "duration_seconds": 4,
                        "references": [{"type": "character", "name": "主角"}],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    fake_generator = _fake_step2_generator("镜头1：空镜，门廊在风里轻响")

    def _fake_supported_durations(self, caps=None, *, gen_mode, uses_reference_images=None):
        return [8] if uses_reference_images else [4, 8]

    gen = ScriptGenerator(reference_project, generator=fake_generator)
    with (
        # 显式固定 caps，不依赖真实 DB 解析结果——_fetch_video_capabilities 解析失败时按
        # 文档返回 None，环境不同（如缺 DB 迁移的测试容器）会让本测试的前提静默漂移。
        patch.object(ScriptGenerator, "_fetch_video_capabilities", AsyncMock(return_value={})),
        patch.object(ScriptGenerator, "_resolve_supported_durations", _fake_supported_durations),
    ):
        out = await gen.generate(episode=1)

    data = _json.loads(out.read_text(encoding="utf-8"))
    unit = data["video_units"][0]
    assert unit["references"] == []
    assert unit["duration_seconds"] == 4  # 按最终 references（无图）取档合法，不因 step1 的带图状态被误判


@pytest.mark.asyncio
@pytest.mark.integration
async def test_script_generator_reclamps_duration_even_when_caps_unavailable(reference_project: Path):
    """caps 解析失败（``_fetch_video_capabilities`` 按其文档在这种情况下返回 None）不代表
    取不到任何档位——``_resolve_supported_durations`` 自带 caps → project.json → registry
    三级回退，project.json 的 ``_supported_durations`` 仍能兜底。回填逻辑须无条件取档，
    不能因为 caps 是 None 就保留一个未经取档的值。

    与其它同类测试一样另 mock ``_resolve_supported_durations`` 本身（而非验证真实回退链
    ——那是 config resolver 层的测试范畴）：本 fixture 未注册模型，caps=None 时的回退结果
    与 caps 非 None 时一样都是 project.json 全量声明的 [4, 8]，raw 值只要合法就不会触发
    任何取档，测不出「取档有没有被跳过」这个真正要验证的行为；只有固定取档结果本身，
    才能构造出「已确认值不在生效档位内」的场景来证明重取档确实执行了——如今这种不合法
    直接 fail-loud，执行了就必抛错，不执行就会静默用未取档的 4 落盘成功。
    """
    fake_generator = MagicMock()
    fake_generator.model = "mock"
    fake_generator.generate = AsyncMock(
        return_value=MagicMock(
            text=(
                '{"episode":1,"title":"t",'
                '"summary":"s","novel":{"title":"t","chapter":"1"},'
                '"video_units":[{"unit_id":"E1U01",'
                '"shots":[{"text":"@主角 推门"}],'
                '"references":[{"type":"character","name":"主角"}],'
                '"duration_seconds":8,"transition_to_next":"cut"}]}'
            )
        )
    )

    gen = ScriptGenerator(reference_project, generator=fake_generator)
    with (
        patch.object(ScriptGenerator, "_fetch_video_capabilities", AsyncMock(return_value=None)),
        patch.object(ScriptGenerator, "_resolve_supported_durations", return_value=[8]),
    ):
        with pytest.raises(ValueError, match="不在当前生效档位"):
            await gen.generate(episode=1)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_script_generator_rejects_step2_unit_count_change(reference_project: Path):
    """step2 合并 / 拆分 / 增删 unit：unit 数是 step1 已确认的内容契约，改动即响亮失败。"""
    gen = ScriptGenerator(reference_project, generator=_fake_step2_generator(STEP2_UNIT_TEXT, "镜头1：多出来的一段"))
    with pytest.raises(ValueError, match="unit 数"):
        await gen.generate(episode=1)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_script_generator_rejects_step2_dialogue_rewrite(reference_project: Path):
    """台词规范行逐字不变：step2 改词即失败，不静默接受被改成「好配画面」的台词。"""
    drafts = reference_project / "drafts" / "episode_1"
    (drafts / "step1_reference_units.json").write_text(
        _json.dumps(
            {
                "units": [
                    {
                        "unit_id": "E1U01",
                        "shots": [{"text": "@[主角] 推门\n@[主角]：{我来了。}"}],
                        "duration_seconds": 4,
                        "references": [{"type": "character", "name": "主角"}],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    gen = ScriptGenerator(
        reference_project,
        generator=_fake_step2_generator("镜头1：中景。@[主角] 推门跨入\n@[主角]：{我到了。}"),
    )
    with pytest.raises(ValueError, match="台词"):
        await gen.generate(episode=1)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_script_generator_accepts_step2_expansion_keeping_dialogue(reference_project: Path):
    """描述行自由展开、台词行逐字保留 → 放行，并把台词说话人排除在参考图之外。"""
    drafts = reference_project / "drafts" / "episode_1"
    (drafts / "step1_reference_units.json").write_text(
        _json.dumps(
            {
                "units": [
                    {
                        "unit_id": "E1U01",
                        "shots": [{"text": "@[主角] 推门\n@[主角]：{我来了。}"}],
                        "duration_seconds": 4,
                        "references": [{"type": "character", "name": "主角"}],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    gen = ScriptGenerator(
        reference_project,
        generator=_fake_step2_generator("镜头1：中景，平视。@[主角] 推开 @[酒馆] 的门，跨过门槛\n@[主角]：{我来了。}"),
    )
    out = await gen.generate(episode=1)
    unit = _json.loads(out.read_text(encoding="utf-8"))["video_units"][0]
    assert unit["references"] == [
        {"type": "character", "name": "主角"},
        {"type": "scene", "name": "酒馆"},
    ]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_script_generator_rejects_step2_unregistered_mention(reference_project: Path):
    """step2 新增的 mention 同样过登记校验：未登记资产名不得混进正文。"""
    gen = ScriptGenerator(reference_project, generator=_fake_step2_generator("镜头1：@[主角] 与 @[路人乙] 对视"))
    with pytest.raises(ValueError, match="未登记"):
        await gen.generate(episode=1)


@pytest.mark.asyncio
async def test_script_generator_reference_branch_inherits_drama_content_mode(tmp_path: Path):
    """drama 项目下生成的参考视频集 content_mode 必须为 drama。

    Pydantic 的 ReferenceVideoScript.content_mode 默认 "narration"，model_dump 会
    把该默认值写入 dict；_add_metadata 必须显式覆盖而非 setdefault，否则 drama 项目
    的参考视频集会被错误标记成 narration。
    """
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / "project.json").write_text(
        """{
          "title": "t",
          "content_mode": "drama",
          "generation_mode": "reference_video",
          "_supported_durations": [4, 8],
          "overview": {"synopsis": "s", "genre": "g", "theme": "th", "world_setting": "w"},
          "style": "国漫", "style_description": "水墨",
          "characters": {"主角": {"description": "d"}},
          "scenes": {"酒馆": {"description": "d"}}, "props": {},
          "episodes": [{"episode": 1, "title": "t1", "generation_mode": "reference_video"}]
        }""",
        encoding="utf-8",
    )
    drafts = project_dir / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "step1_reference_units.json").write_text(STEP1_UNITS_JSON, encoding="utf-8")

    gen = ScriptGenerator(project_dir, generator=_fake_step2_generator("镜头1：中景。@[主角] 推门"))
    out = await gen.generate(episode=1)

    import json as _j

    data = _j.loads(out.read_text(encoding="utf-8"))
    assert data["content_mode"] == "drama"
    assert data["generation_mode"] == "reference_video"


@pytest.mark.parametrize(
    "caps, expected",
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
    "video_backend, expected",
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
    """project.json 缺 video_backend 且无 _supported_durations 且 caps 不可解析时，build_prompt 应抛 ValueError。

    设计意图：supported_durations 是单一真相源，必须由 caps（DB 全局默认）或 project.json 显式声明提供；
    都拿不到才 fail loud，避免向 LLM 注入兜底 [4, 8] 误导生成。
    用 mock 把 _fetch_video_capabilities 强制返 None，模拟无任何 model 配置的环境。
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
    (drafts / "step1_reference_units.json").write_text(STEP1_UNITS_JSON, encoding="utf-8")

    gen = ScriptGenerator(project_dir)
    with patch(
        "lib.script_generator.ScriptGenerator._fetch_video_capabilities",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(ValueError, match="supported_durations"):
            await gen.build_prompt(episode=1)


@pytest.mark.asyncio
async def test_fetch_video_capabilities_swallows_db_errors(reference_project: Path):
    """CI 回归：裸测试容器缺 migration 时 ConfigResolver 会抛 OperationalError；
    _fetch_video_capabilities 必须 fallback 返 None，不让 generate() 崩溃。
    """
    gen = ScriptGenerator(reference_project)
    with patch(
        "lib.script_generator.ConfigResolver.video_capabilities_for_project",
        new=AsyncMock(side_effect=OperationalError("SELECT ...", {}, Exception("no such table: system_setting"))),
    ):
        caps = await gen._fetch_video_capabilities()
    assert caps is None


@pytest.mark.asyncio
async def test_effective_generation_mode_honors_episode_override(tmp_path: Path):
    """当 project=storyboard 但 episode=reference_video 时，build_prompt 必须走 reference 分支。

    解析规则：``effective_mode(project, episode) = episode.generation_mode or
    project.generation_mode or "storyboard"``。
    """
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    import json as _j

    (project_dir / "project.json").write_text(
        _j.dumps(
            {
                "title": "t",
                "content_mode": "narration",
                "generation_mode": "storyboard",  # 项目级是 storyboard
                "_supported_durations": [4, 8],
                "overview": {"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
                "style": "s",
                "style_description": "d",
                "characters": {"A": {"description": "d"}},
                "scenes": {},
                "props": {},
                "episodes": [
                    {"episode": 1, "generation_mode": "reference_video"},  # 集级覆盖为 reference
                ],
            }
        ),
        encoding="utf-8",
    )
    drafts = project_dir / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "step1_reference_units.json").write_text(STEP1_UNITS_JSON, encoding="utf-8")

    gen = ScriptGenerator(project_dir)
    prompt = await gen.build_prompt(episode=1)
    # 走 reference 分支：step2 视觉展开模板
    assert "视觉展开" in prompt
    assert "<step1_units>" in prompt
    assert "@[名称]" in prompt


@pytest.mark.asyncio
@pytest.mark.integration
async def test_script_generator_reads_legacy_step1_draft_without_source_text(reference_project: Path):
    """存量 step1 草稿（无 source_text，per-shot 时长已由迁移收编）仍能被新校验器读取并跑完 step2。

    ``source_text`` 是本轮新增的原文锚：拆分工具产出时校验后落盘，但存量草稿产于其前。
    默认空串使读取照常通过，不要求用户重跑拆分。
    """
    saved = _json.loads((reference_project / "drafts" / "episode_1" / "step1_reference_units.json").read_text("utf-8"))
    assert "source_text" not in saved["units"][0]

    gen = ScriptGenerator(reference_project, generator=_fake_step2_generator(STEP2_UNIT_TEXT))
    out = await gen.generate(episode=1)
    assert _json.loads(out.read_text(encoding="utf-8"))["video_units"][0]["unit_id"] == "E1U01"


@pytest.mark.asyncio
async def test_reference_step1_legacy_md_prompts_resplit(reference_project: Path):
    """仅存在结构化前的旧 .md 拆分表时，给出明确的「重跑拆分」提示而非笼统缺文件错误。"""
    drafts = reference_project / "drafts" / "episode_1"
    (drafts / "step1_reference_units.json").unlink()
    (drafts / "step1_reference_units.md").write_text("| E1U1 | Shot1(4s) |", encoding="utf-8")

    gen = ScriptGenerator(reference_project)
    with pytest.raises(FileNotFoundError, match="split-reference-video-units"):
        await gen.build_prompt(episode=1)


@pytest.mark.asyncio
async def test_reference_step1_missing_raises(reference_project: Path):
    drafts = reference_project / "drafts" / "episode_1"
    (drafts / "step1_reference_units.json").unlink()

    gen = ScriptGenerator(reference_project)
    with pytest.raises(FileNotFoundError, match="video_unit 拆分"):
        await gen.build_prompt(episode=1)


@pytest.mark.asyncio
async def test_reference_step1_rejects_out_of_enum_duration(reference_project: Path):
    """读取侧复验 unit 时长 ∈ supported_durations，防手工编辑漂移出非法时长。"""
    drafts = reference_project / "drafts" / "episode_1"
    (drafts / "step1_reference_units.json").write_text(
        _json.dumps({"units": [{"unit_id": "E1U01", "shots": [{"text": "@[主角] 转身"}], "duration_seconds": 5}]}),
        encoding="utf-8",
    )

    gen = ScriptGenerator(reference_project)
    # 固定能力来源为 project.json 的 _supported_durations=[4,8]，隔离 DB 全局默认干扰
    with patch(
        "lib.script_generator.ScriptGenerator._fetch_video_capabilities",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(ValueError, match="时长非法"):
            await gen.build_prompt(episode=1)


@pytest.mark.asyncio
async def test_reference_step1_rejects_duplicate_unit_ids(reference_project: Path):
    drafts = reference_project / "drafts" / "episode_1"
    unit = {"unit_id": "E1U01", "shots": [{"text": "@[主角] 转身"}], "duration_seconds": 4}
    (drafts / "step1_reference_units.json").write_text(_json.dumps({"units": [unit, dict(unit)]}), encoding="utf-8")

    gen = ScriptGenerator(reference_project)
    with pytest.raises(ValueError, match="unit_id 重复"):
        await gen.build_prompt(episode=1)


@pytest.mark.integration
def test_reference_step1_migration_carries_confirmation_forward(reference_project: Path):
    """迁移回写让 step1 内容指纹漂移；若该集已确认（指纹恰是迁移前内容），须把确认指纹
    平移到迁移后的值，否则仅 build_prompt/dry-run 预览一次就会把已确认分集退回待审。
    """
    drafts = reference_project / "drafts" / "episode_1"
    legacy = {"units": [{"unit_id": "E1U01", "shots": [{"duration": 4, "text": "@[主角] 转身"}]}]}
    step1_path = drafts / "step1_reference_units.json"
    step1_path.write_text(_json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    before = script_review.content_fingerprint(step1_path)

    project_path = reference_project / "project.json"
    project = _json.loads(project_path.read_text(encoding="utf-8"))
    project["episodes"][0]["step1_review"] = {"fingerprint": before, "confirmed_at": "2026-01-01T00:00:00Z"}
    project_path.write_text(_json.dumps(project, ensure_ascii=False), encoding="utf-8")

    gen = ScriptGenerator(reference_project)
    gen._load_reference_step1(episode=1, supported_durations=[4, 8])

    after_project = _json.loads(project_path.read_text(encoding="utf-8"))
    review = after_project["episodes"][0]["step1_review"]
    after = script_review.content_fingerprint(step1_path)
    assert review["fingerprint"] == after
    assert review["fingerprint"] != before


@pytest.mark.integration
def test_reference_step1_migration_carries_confirmation_confirmed_after_construction(reference_project: Path):
    """确认发生在 ScriptGenerator 构造之后（如 generate() 内 await _fetch_video_capabilities()
    期间用户经 ScriptReviewService.confirm() 并发确认）：self.project_json 是构造时的旧快照，
    看不到这次确认，但迁移写回仍须正确搬移它——不能用这份旧快照做前置短路。
    """
    drafts = reference_project / "drafts" / "episode_1"
    legacy = {"units": [{"unit_id": "E1U01", "shots": [{"duration": 4, "text": "@[主角] 转身"}]}]}
    step1_path = drafts / "step1_reference_units.json"
    step1_path.write_text(_json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    before = script_review.content_fingerprint(step1_path)

    # 构造时 project.json 尚无确认记录。
    gen = ScriptGenerator(reference_project)

    # 构造之后才发生确认（模拟并发的 ScriptReviewService.confirm()），self.project_json 不刷新。
    project_path = reference_project / "project.json"
    project = _json.loads(project_path.read_text(encoding="utf-8"))
    project["episodes"][0]["step1_review"] = {"fingerprint": before, "confirmed_at": "2026-01-01T00:00:00Z"}
    project_path.write_text(_json.dumps(project, ensure_ascii=False), encoding="utf-8")
    assert "step1_review" not in gen.project_json["episodes"][0]  # 构造时的快照确实还没有它

    gen._load_reference_step1(episode=1, supported_durations=[4, 8])

    after_project = _json.loads(project_path.read_text(encoding="utf-8"))
    review = after_project["episodes"][0]["step1_review"]
    after = script_review.content_fingerprint(step1_path)
    assert review["fingerprint"] == after
    assert review["confirmed_at"] == "2026-01-01T00:00:00Z"
    assert review["confirmed_at"] == "2026-01-01T00:00:00Z"


@pytest.mark.integration
def test_reference_step1_migration_does_not_carry_confirmation_when_duration_is_clamped(reference_project: Path):
    """迁移带 warnings（求和时长不在模型档位内，被取档改写）不是纯格式收编：已确认分集
    须退回待审，不能平移确认——取档后的秒数不是用户确认时看到的值。

    退回待审的同时本次调用也须中止：审阅 gate 判的是迁移前状态、已按「已确认」放行，
    改写发生在放行之后，继续下去就会按用户从未过目的秒数走完付费的 step2。
    """
    drafts = reference_project / "drafts" / "episode_1"
    legacy = {"units": [{"unit_id": "E1U01", "shots": [{"duration": 4, "text": "@[主角] 转身"}]}]}
    step1_path = drafts / "step1_reference_units.json"
    step1_path.write_text(_json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    before = script_review.content_fingerprint(step1_path)

    project_path = reference_project / "project.json"
    project = _json.loads(project_path.read_text(encoding="utf-8"))
    project["episodes"][0]["step1_review"] = {"fingerprint": before, "confirmed_at": "2026-01-01T00:00:00Z"}
    project_path.write_text(_json.dumps(project, ensure_ascii=False), encoding="utf-8")

    gen = ScriptGenerator(reference_project)
    # 求和 4s 不是模型档位成员，取档改写为 8s——这一步产生 warning。
    with pytest.raises(ValueError, match="尚未经审阅确认"):
        gen._load_reference_step1(episode=1, supported_durations=[8])

    # 迁移本身已幂等落盘（中止的是本次生成，不是迁移）。
    assert _json.loads(step1_path.read_text(encoding="utf-8"))["units"][0]["duration_seconds"] == 8

    after_project = _json.loads(project_path.read_text(encoding="utf-8"))
    review = after_project["episodes"][0]["step1_review"]
    assert review["fingerprint"] == before  # 未被平移，仍是迁移前的旧指纹——照常判定为待审


@pytest.mark.integration
async def test_step1_text_violation_is_caught_before_the_paid_step2_call(reference_project: Path):
    """step1 正文的语法违约在调用文本模型之前就被拦下，且错误指名 step1。

    编辑器侧保存只做结构校验（人写的文本有作者意图要保护，语法问题仅出 warning），手工编辑
    过的 step1 因而可能带着未登记的 `@[名称]` 进到生成。step2 会逐字保留这段正文，违约必然
    原样复现——不在调用前判，就要付完 step2 的钱才失败，且错误指向 step2「改坏了」。
    """
    drafts = reference_project / "drafts" / "episode_1"
    (drafts / "step1_reference_units.json").write_text(
        _json.dumps(
            {"units": [{"unit_id": "E1U01", "duration_seconds": 4, "shots": [{"text": "@[查无此人} 推门"}]}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    fake_generator = _fake_step2_generator(STEP2_UNIT_TEXT)

    gen = ScriptGenerator(reference_project, generator=fake_generator)

    with pytest.raises(DraftViolation, match="来自 step1"):
        await gen.generate(episode=1)
    fake_generator.generate.assert_not_awaited()
