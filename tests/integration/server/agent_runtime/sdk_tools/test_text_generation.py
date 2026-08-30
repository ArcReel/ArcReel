"""Tests for text_generation."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from lib import script_review
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.agent_runtime.sdk_tools.text_generation import (
    generate_episode_script_tool,
    generate_script_plan_tool,
    get_video_capabilities_tool,
)
from server.media_tools.context import ToolContext
from server.text_generation import TextGenerationRequest, _parse_normalized_content
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import (
    call,
    fake_caps_resolver,
    use_fake_caps,
)

# i2v 桶不可解析：不带图档位随之回退按 r2v 桶求值（``reference_unit_duration_tiers``）。
_NO_I2V = {"i2v": ValueError("i2v bucket unresolvable in this test")}

# ---------------------------------------------------------------------------
# text_generation
# ---------------------------------------------------------------------------


async def test_get_video_capabilities_happy(fake_ctx: ToolContext) -> None:
    use_fake_caps(fake_ctx, provider_id="fake", supported_durations=[4, 6, 8])
    tool_obj = get_video_capabilities_tool(fake_ctx)
    assert tool_obj.name == "get_video_capabilities"
    assert isinstance(tool_obj.input_schema, dict)
    assert "project" not in tool_obj.input_schema["properties"]
    out = await call(tool_obj, {})
    assert out.get("is_error") is not True
    assert json.loads(out["content"][0]["text"])["video_capabilities"]["provider_id"] == "fake"


async def test_get_video_capabilities_resolves_by_project(fake_ctx: ToolContext) -> None:
    """能力按项目生成模式解析：工具不收集号，多余的集号入参被忽略、不改变解析口径。"""
    resolver = use_fake_caps(fake_ctx, provider_id="fake", supported_durations=[4, 6, 8])
    tool_obj = get_video_capabilities_tool(fake_ctx)
    assert (await call(tool_obj, {})).get("is_error") is not True
    assert (await call(tool_obj, {"episode": 3})).get("is_error") is not True
    assert resolver.capability_calls == [None, None]


async def test_get_video_capabilities_annotates_reference_unit_tiers(fake_ctx: ToolContext) -> None:
    """参考路径项目另返回两套逐 unit 生效档位，供手工改 script_plan 时与生成侧对同一份数字。"""
    fake_ctx.pm.project_payload["model_settings"] = {"gemini-aistudio/veo-3.1-generate-preview": {"resolution": "720p"}}
    use_fake_caps(
        fake_ctx,
        provider_id="gemini-aistudio",
        model="veo-3.1-generate-preview",
        supported_durations=[4, 6, 8],
        generation_mode="reference_video",
        capability_errors=_NO_I2V,
    )
    out = await call(get_video_capabilities_tool(fake_ctx), {})
    assert out.get("is_error") is not True, out
    payload = json.loads(out["content"][0]["text"])["video_capabilities"]
    assert payload["reference_unit_durations"] == {"with_references": [8], "without_references": [4, 6, 8]}
    # 全集原样保留：它是型号声明，不是生效档位
    assert payload["supported_durations"] == [4, 6, 8]


@pytest.mark.parametrize(
    ("generation_mode", "content_mode"),
    [("storyboard", "drama"), ("reference_video", "ad")],
)
async def test_get_video_capabilities_skips_tiers_off_episode_reference_path(
    fake_ctx: ToolContext, generation_mode: str, content_mode: str
) -> None:
    """非剧集参考路径不补该字段：其它路径没有逐 unit 引用状态，ad 分镜时长也不受档位枚举管辖。"""
    use_fake_caps(
        fake_ctx,
        provider_id="gemini-aistudio",
        model="veo-3.1-generate-preview",
        supported_durations=[4, 6, 8],
        generation_mode=generation_mode,
        content_mode=content_mode,
    )
    out = await call(get_video_capabilities_tool(fake_ctx), {})
    payload = json.loads(out["content"][0]["text"])["video_capabilities"]
    assert "reference_unit_durations" not in payload


async def test_get_video_capabilities_shares_rest_resolution_entry(fake_ctx: ToolContext) -> None:
    """Agent 工具把闭包项目交给 ``ConfigResolver.video_capabilities_for_project``。

    解析器不按项目名回到全局项目目录，非默认 projects_root 的会话也读取闭包里的项目。
    """
    resolver = use_fake_caps(fake_ctx, provider_id="kling", model="kling-v3-omni", supported_durations=[5])
    out = await call(get_video_capabilities_tool(fake_ctx), {})
    assert out.get("is_error") is not True, out
    assert json.loads(out["content"][0]["text"])["video_capabilities"]["model"] == "kling-v3-omni"
    assert resolver.project_payloads == [fake_ctx.pm.project_payload]


async def test_get_video_capabilities_error(fake_ctx: ToolContext) -> None:
    use_fake_caps(fake_ctx, error=FileNotFoundError("missing project.json"))
    tool_obj = get_video_capabilities_tool(fake_ctx)
    out = await call(tool_obj, {})
    assert out.get("is_error") is True


@pytest.mark.parametrize("content_mode", ["ad", "unsupported"])
async def test_generate_script_plan_rejects_inapplicable_content_modes(
    fake_ctx: ToolContext, content_mode: str
) -> None:
    fake_ctx.pm.project_payload["content_mode"] = content_mode
    resolver = use_fake_caps(fake_ctx)
    caller_thread = threading.get_ident()

    out = await call(generate_script_plan_tool(fake_ctx), {"episode": 1, "dry_run": True})

    assert out.get("is_error") is True
    assert json.loads(out["content"][0]["text"])["problem"]["code"] == "generation_refused"
    assert resolver.capability_calls == []
    assert fake_ctx.pm.readonly_load_threads
    assert all(thread != caller_thread for thread in fake_ctx.pm.readonly_load_threads)


@pytest.mark.parametrize("factory", [generate_episode_script_tool, generate_script_plan_tool])
def test_generation_tools_require_positive_episode(fake_ctx: ToolContext, factory) -> None:
    assert factory(fake_ctx).input_schema["properties"]["episode"]["minimum"] == 1


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "1"])
def test_text_generation_request_rejects_non_positive_or_non_integer_episode(bad: Any) -> None:
    with pytest.raises(ValueError, match="positive integer"):
        TextGenerationRequest(episode=bad)


async def test_generate_episode_script_dry_run(fake_ctx: ToolContext, monkeypatch) -> None:
    from server import text_generation as mod

    project_path = fake_ctx.project_path
    drafts = project_path / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "script_plan_segments.json").write_text("script_plan content", encoding="utf-8")
    (project_path / "project.json").write_text(json.dumps({"content_mode": "narration"}), encoding="utf-8")

    class _FakeGenerator:
        def __init__(self, _path, **_kwargs):
            pass

        async def build_prompt(self, _episode, *, instructions=None, **_kwargs):
            return "fake prompt"

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)
    tool_obj = generate_episode_script_tool(fake_ctx)
    out = await call(tool_obj, {"episode": 1, "dry_run": True})
    assert out.get("is_error") is not True
    assert "fake prompt" in out["content"][0]["text"]


async def test_generate_episode_script_missing_script_plan(fake_ctx: ToolContext) -> None:
    tool_obj = generate_episode_script_tool(fake_ctx)
    out = await call(tool_obj, {"episode": 99})
    assert out.get("is_error") is True


async def test_generate_episode_script_writes_to_default_project_scripts(fake_ctx: ToolContext, monkeypatch) -> None:
    """output 参数已下线；写出路径必须由 ScriptGenerator 内部决定，handler 不应让 Agent 控制。"""
    from server import text_generation as mod

    project_path = fake_ctx.project_path
    drafts = project_path / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    script_plan = drafts / "script_plan_segments.json"
    script_plan.write_text("script_plan", encoding="utf-8")
    # script_plan→prompt_authoring 内容确认：须先确认才放行生成，否则 handler 早返阻塞而非调 ScriptGenerator。
    # 把已存确认指纹对齐当前 script_plan 内容指纹，模拟「用户已在 Web 确认」。
    fingerprint = script_review.content_fingerprint(script_plan)
    (project_path / "project.json").write_text(
        json.dumps(
            {
                "content_mode": "narration",
                "episodes": [{"episode": 1, "script_plan_review": {"fingerprint": fingerprint, "confirmed_at": "t"}}],
            }
        ),
        encoding="utf-8",
    )

    captured: dict[str, dict[str, Any]] = {"calls": {}}

    class _FakeGenerator:
        @classmethod
        async def create(cls, _path, **_kwargs):
            return cls()

        async def generate(self, **kwargs) -> Path:
            captured["calls"] = kwargs
            return project_path / "scripts" / "episode_1.json"

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)
    tool_obj = generate_episode_script_tool(fake_ctx)

    out = await call(tool_obj, {"episode": 1})
    assert out.get("is_error") is not True
    # handler 不再传 output_path —— ScriptGenerator 自己决定写到哪里
    assert "output_path" not in captured["calls"]


async def test_generate_episode_script_ad_skips_script_plan(fake_ctx: ToolContext, monkeypatch) -> None:
    """ad 一键生成不依赖 script_plan 中间文件：缺 drafts/ 也不报 script_plan 错误。"""
    from server import text_generation as mod

    project_path = fake_ctx.project_path
    (project_path / "project.json").write_text(
        json.dumps({"content_mode": "ad", "target_duration": 30}), encoding="utf-8"
    )

    class _FakeGenerator:
        @classmethod
        async def create(cls, _path, **_kwargs):
            return cls()

        async def generate(self, **_kwargs) -> Path:
            return project_path / "scripts" / "episode_1.json"

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)
    tool_obj = generate_episode_script_tool(fake_ctx)
    out = await call(tool_obj, {"episode": 1})
    assert out.get("is_error") is not True


async def test_generate_episode_script_scope_reaches_the_generator(fake_ctx: ToolContext, monkeypatch) -> None:
    """scope / entry_ids 两个工具参数原样落到 ScriptGenerator.generate，回执列出被重写的条目。"""
    from server import text_generation as mod

    project_path = fake_ctx.project_path
    (project_path / "project.json").write_text(
        json.dumps({"content_mode": "ad", "target_duration": 30}), encoding="utf-8"
    )
    captured: dict[str, Any] = {}

    class _FakeGenerator:
        @classmethod
        async def create(cls, _path, **_kwargs):
            return cls()

        async def generate(self, **kwargs) -> Path:
            captured.update(kwargs)
            kwargs["rewritten_entry_ids"].append("E1S02")
            return project_path / "scripts" / "episode_1.json"

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)
    tool_obj = generate_episode_script_tool(fake_ctx)

    out = await call(tool_obj, {"episode": 1, "entry_ids": ["E1S02"]})
    assert out.get("is_error") is not True
    assert captured["scope"] == ("E1S02",)
    assert "E1S02" in out["content"][0]["text"]

    await call(tool_obj, {"episode": 1, "scope": "all"})
    assert captured["scope"] == "all"


async def test_generate_episode_script_unknown_entry_id_is_refused_not_internal(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """点名了脚本规划里没有的条目：报「拒绝生成」，不冒成 internal_error 引导 Agent 原样重试。"""
    from lib.script_plan_entries import ScriptPlanEntryError
    from server import text_generation as mod

    project_path = fake_ctx.project_path
    (project_path / "project.json").write_text(
        json.dumps({"content_mode": "ad", "target_duration": 30}), encoding="utf-8"
    )

    class _FakeGenerator:
        @classmethod
        async def create(cls, _path, **_kwargs):
            return cls()

        async def generate(self, **_kwargs) -> Path:
            raise ScriptPlanEntryError("scope 指定的条目 id 不在当前脚本规划内: ['E9U99']")

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)
    out = await call(generate_episode_script_tool(fake_ctx), {"episode": 1, "entry_ids": ["E9U99"]})

    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "重写范围无效" in text
    assert "generate_episode_script 失败" not in text


async def test_generate_episode_script_rejects_entry_ids_with_scope_all(fake_ctx: ToolContext) -> None:
    """两种范围同时给出即请求自相矛盾，在入口拒绝而不是任选其一。"""
    tool_obj = generate_episode_script_tool(fake_ctx)
    out = await call(tool_obj, {"episode": 1, "scope": "all", "entry_ids": ["E1S01"]})
    assert out.get("is_error") is True


async def test_generate_episode_script_scope_does_not_bypass_the_review_gate(fake_ctx: ToolContext) -> None:
    """内容确认未通过时，增量与整集两条范围一律被阻塞。"""
    project_path = fake_ctx.project_path
    drafts = project_path / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "script_plan_segments.json").write_text("script_plan", encoding="utf-8")
    (project_path / "project.json").write_text(json.dumps({"content_mode": "narration"}), encoding="utf-8")
    tool_obj = generate_episode_script_tool(fake_ctx)

    for args in ({"episode": 1}, {"episode": 1, "scope": "all"}, {"episode": 1, "entry_ids": ["E1S01"]}):
        out = await call(tool_obj, args)
        assert out.get("is_error") is True, args
        assert "内容确认" in out["content"][0]["text"]


def test_parse_normalized_content_uses_dynamic_duration_schema() -> None:
    """_parse_normalized_content 复用按 supported_durations 构造的动态 schema：合法 duration 经模型
    校验并补全默认字段；超出枚举的 duration 触发 fail-loud（抛 ValueError），而非被静态模型(ge=1,le=60)
    静默放行、也不降级保留未校验内容写盘。"""
    from lib.script_models import build_drama_normalized_script_model

    model = build_drama_normalized_script_model([4, 6, 8])
    base_scene = {
        "scene_id": "E1S01",
        "duration_seconds": 8,
        "characters_in_scene": ["林清"],
        "scene_description": "林清立于窗前。",
    }

    valid = _parse_normalized_content(json.dumps({"title": "t", "scenes": [base_scene]}), model)
    # 合法 duration → 模型校验通过，补全 DramaSceneContent 默认字段（source_text 默认空串）
    assert valid["scenes"][0]["duration_seconds"] == 8
    assert valid["scenes"][0]["source_text"] == ""

    bad = {**base_scene, "duration_seconds": 5}  # 5 不在 supported_durations
    # 超出枚举 → 动态 schema 校验失败 → fail-loud 抛 ValueError，不把未校验内容当成正式 script_plan 落盘
    with pytest.raises(ValueError, match="script_plan 规范化内容结构校验失败"):
        _parse_normalized_content(json.dumps({"title": "t", "scenes": [bad]}), model)


async def test_fetch_caps_with_fallback_uses_write_layer_default() -> None:
    """resolver 失败时软回退须与自定义供应商写入层的保守默认（duration_presets.DEFAULT_FALLBACK）
    同一真相源——独立维护第二套回退集会让 LLM 拿到供应商未必支持的时长。"""
    from lib.custom_provider.duration_presets import DEFAULT_FALLBACK
    from server import text_generation as mod

    resolver = fake_caps_resolver(error=ValueError("no provider configured"))
    default, durations = await mod._fetch_caps_with_fallback({}, 1, config_resolver=resolver)
    assert default is None
    assert durations == DEFAULT_FALLBACK


async def test_fetch_caps_with_fallback_drops_out_of_range_default() -> None:
    """收窄后落在集合外的已保存 default_duration 归 None（回到 auto 档），不拖垮整个工具。

    ``build_normalize_prompt`` 对非成员 default 是 fail-loud 的：用户在 720p 下存过 4 秒、
    改到 1080p 后 Veo 收窄为 [8]，不归 None 会让 normalize_drama_script 直接抛 ValueError。
    """
    from server import text_generation as mod

    veo = {"provider_id": "gemini-aistudio", "model": "veo-3.1-generate-preview"}
    project_1080p = {"model_settings": {"gemini-aistudio/veo-3.1-generate-preview": {"resolution": "1080p"}}}

    narrowed = fake_caps_resolver(supported_durations=[4, 6, 8], default_duration=4, **veo)
    default, durations = await mod._fetch_caps_with_fallback(project_1080p, 1, config_resolver=narrowed)
    assert default is None
    assert durations == [8]

    in_range = fake_caps_resolver(supported_durations=[4, 6, 8], default_duration=8, **veo)
    default, durations = await mod._fetch_caps_with_fallback({}, 1, config_resolver=in_range)
    assert default == 8
    assert durations == [4, 6, 8]


async def test_fetch_video_caps_narrows_durations_by_constraints() -> None:
    """交给 LLM 的时长集合已按项目分辨率经联动约束收窄。

    Veo 项目保存 1080p 时只接受 8 秒；不收窄的话 drama / narration 拆分会产出 4/6 秒镜头，
    视频入队时才被 backend 拒。
    """
    from server.media_tools import context as ctx_mod

    resolver = fake_caps_resolver(
        provider_id="gemini-aistudio",
        model="veo-3.1-generate-preview",
        supported_durations=[4, 6, 8],
        default_duration=4,
    )

    project_1080p = {"model_settings": {"gemini-aistudio/veo-3.1-generate-preview": {"resolution": "1080p"}}}
    default, durations = await ctx_mod.fetch_video_caps(project_1080p, config_resolver=resolver)
    assert durations == [8]
    # default_duration 原样返回（用户配置值），成员性由调用方按各自口径判定
    assert default == 4

    # 未配置分辨率：普通路径省略 resolution 参数，供应商按自己的默认档位（Veo 720p）接受 4/6/8，
    # 故不施加分辨率约束——按 provider 兜底档位收窄会凭空把剧本节奏锁死 8 秒。
    _default, durations = await ctx_mod.fetch_video_caps({}, config_resolver=resolver)
    assert durations == [4, 6, 8]

    # 项目显式选了无声明的分辨率时不收窄。
    project = {"model_settings": {"gemini-aistudio/veo-3.1-generate-preview": {"resolution": "720p"}}}
    _default, durations = await ctx_mod.fetch_video_caps(project, config_resolver=resolver)
    assert durations == [4, 6, 8]

    # 参考图路径：即便分辨率无声明也收窄
    _default, durations = await ctx_mod.fetch_video_caps(
        project, generation_mode="reference_video", config_resolver=resolver
    )
    assert durations == [8]


async def test_normalize_drama_script_dry_run(fake_ctx: ToolContext) -> None:
    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter1.txt").write_text("从前有座山", encoding="utf-8")

    use_fake_caps(fake_ctx)
    tool_obj = generate_script_plan_tool(fake_ctx)
    out = await call(tool_obj, {"episode": 1, "dry_run": True})
    assert out.get("is_error") is not True
    assert "DRY RUN" in out["content"][0]["text"]


async def test_normalize_drama_script_projects_durable_inputs_once(fake_ctx: ToolContext, monkeypatch) -> None:
    from lib import artifact_provenance

    source_dir = fake_ctx.project_path / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "episode_1.txt").write_text("从前有座山", encoding="utf-8")
    calls = 0
    original = artifact_provenance.project_script_plan_prompt_inputs

    def counted_projection(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(artifact_provenance, "project_script_plan_prompt_inputs", counted_projection)
    use_fake_caps(fake_ctx)

    result = await call(generate_script_plan_tool(fake_ctx), {"episode": 1, "dry_run": True})

    assert result.get("is_error") is not True, result
    assert calls == 1


async def test_normalize_drama_script_wires_target_language(fake_ctx: ToolContext) -> None:
    """normalize 把项目 source_language 透传为 build_normalize_prompt 的 target_language——
    非中文项目的 script_plan 输出语言据此切换，而非恒退默认中文。"""

    # 工具经 ctx.pm.load_project 取项目；source_language 是输出语言的唯一真相源
    fake_ctx.pm.project_payload["source_language"] = "English"
    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter1.txt").write_text("once upon a time", encoding="utf-8")

    use_fake_caps(fake_ctx)
    tool_obj = generate_script_plan_tool(fake_ctx)
    out = await call(tool_obj, {"episode": 1, "dry_run": True})
    assert out.get("is_error") is not True
    assert "English" in out["content"][0]["text"]


async def test_normalize_drama_script_rejects_empty_scenes(fake_ctx: ToolContext, monkeypatch) -> None:
    """normalize 产出空 scenes → 工具报错，不把空 script_plan 当成功产物写盘（与 _load_drama_script_plan_content 同口径）。"""
    from server import text_generation as mod

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter1.txt").write_text("从前有座山", encoding="utf-8")

    class _EmptyGenerator:
        async def generate(self, _request, project_name=None):
            class _R:
                text = json.dumps({"title": "第一集", "scenes": []}, ensure_ascii=False)

            return _R()

    async def fake_create(task_type, project_name=None):
        return _EmptyGenerator()

    use_fake_caps(fake_ctx)
    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)

    tool_obj = generate_script_plan_tool(fake_ctx)
    out = await call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    # 空 scenes 不写盘，避免生成阶段才必然失败
    assert not (project_path / "drafts" / "episode_1" / "script_plan_normalized_script.json").exists()


async def test_normalize_drama_script_injects_episode_into_prompt(fake_ctx: ToolContext) -> None:
    """工具必须把 episode 注入 build_normalize_prompt，避免 LLM 写错 E\\d+ 前缀。"""

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter2.txt").write_text("第二集开场", encoding="utf-8")

    use_fake_caps(fake_ctx)
    tool_obj = generate_script_plan_tool(fake_ctx)
    out = await call(tool_obj, {"episode": 2, "dry_run": True, "source": "source/chapter2.txt"})
    assert out.get("is_error") is not True, out
    prompt_text = out["content"][0]["text"]
    assert "E2S01" in prompt_text
    assert "第 2 集" in prompt_text or "E2S{两位序号}" in prompt_text
    assert "E1S01" not in prompt_text


async def test_normalize_drama_script_injects_episode_outline(fake_ctx: ToolContext) -> None:
    """分集大纲（故事节点 / 钩子）随 script_plan 注入 normalize prompt（见 ADR 0041）。"""

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter1.txt").write_text("从前有座山", encoding="utf-8")
    fake_ctx.pm.project_payload["episodes"] = [
        {
            "episode": 1,
            "title": "初入江湖",
            "hook": "少年坠崖生死未卜",
            "outline": {"story_beats": ["少年下山"], "next_episode_teaser": None},
        }
    ]

    use_fake_caps(fake_ctx)
    tool_obj = generate_script_plan_tool(fake_ctx)
    out = await call(tool_obj, {"episode": 1, "dry_run": True})
    assert out.get("is_error") is not True, out
    prompt_text = out["content"][0]["text"]
    assert "少年下山" in prompt_text
    assert "少年坠崖生死未卜" in prompt_text


async def test_normalize_drama_script_passes_project_name_to_backend(fake_ctx: ToolContext, monkeypatch) -> None:
    """工具必须把 ctx.project_name 传给 TextGenerator.create/generate，
    否则项目级文本档位覆盖被跳过，且 usage tracking 会丢 project_name。"""
    from server import text_generation as mod

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter1.txt").write_text("从前有座山", encoding="utf-8")

    captured: dict[str, Any] = {}

    class _FakeGenerator:
        async def generate(self, _request, project_name=None):
            captured["generate_project_name"] = project_name

            class _R:
                # script_plan 产出结构化 JSON（DramaNormalizedScript），非 markdown 表
                text = json.dumps(
                    {
                        "title": "第一集",
                        "scenes": [
                            {
                                "scene_id": "E1S01",
                                "duration_seconds": 4,
                                "segment_break": False,
                                "characters_in_scene": [],
                                "scenes": [],
                                "props": [],
                                "scene_description": "山中清晨",
                                "utterances": [],
                                "source_text": "从前有座山",
                            }
                        ],
                    },
                    ensure_ascii=False,
                )

            return _R()

    async def fake_create(task_type, project_name=None):
        captured["task_type"] = task_type
        captured["create_project_name"] = project_name
        return _FakeGenerator()

    use_fake_caps(fake_ctx)
    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)

    tool_obj = generate_script_plan_tool(fake_ctx)
    out = await call(tool_obj, {"episode": 1})

    assert out.get("is_error") is not True, out
    assert captured["task_type"] is mod.TextTaskType.SCRIPT
    assert captured["create_project_name"] == "demo", (
        f"normalize_drama_script 必须向 TextGenerator.create 传入 project_name，"
        f"实际传入: {captured.get('create_project_name')!r}"
    )
    assert captured["generate_project_name"] == "demo", (
        f"normalize_drama_script 必须向 TextGenerator.generate 传入 project_name，"
        f"实际传入: {captured.get('generate_project_name')!r}"
    )


async def test_normalize_drama_script_registers_the_frozen_explicit_source_basis(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    from lib.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
    from lib.artifact_provenance import build_script_plan_basis
    from server import text_generation as mod

    project = {
        **fake_ctx.pm.project_payload,
        "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "content_mode": "drama",
        "generation_mode": "storyboard",
        "source_kind": "novel",
        "source_language": "中文",
    }
    fake_ctx.pm.project_payload = project
    project_file = fake_ctx.project_path / "project.json"
    project_file.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    source_path = fake_ctx.project_path / "source" / "selected.txt"
    source_path.parent.mkdir(parents=True)
    frozen_source = "被显式选中的生成原文"
    source_path.write_text(frozen_source, encoding="utf-8")
    expected = build_script_plan_basis(frozen_source, episode=1, project=project)

    class _Generator:
        async def generate(self, _request, project_name=None):
            source_path.write_text("等待供应商期间改过的原文", encoding="utf-8")
            latest = {**project, "source_language": "English"}
            fake_ctx.pm.project_payload = latest
            project_file.write_text(json.dumps(latest, ensure_ascii=False), encoding="utf-8")
            return type(
                "_Result",
                (),
                {
                    "text": json.dumps(
                        {
                            "title": "第一集",
                            "scenes": [
                                {
                                    "scene_id": "E1S01",
                                    "duration_seconds": 4,
                                    "segment_break": False,
                                    "characters_in_scene": [],
                                    "scenes": [],
                                    "props": [],
                                    "scene_description": "山中清晨",
                                    "utterances": [],
                                    "source_text": frozen_source,
                                }
                            ],
                        },
                        ensure_ascii=False,
                    )
                },
            )()

    async def fake_create(_task_type, project_name=None):
        return _Generator()

    use_fake_caps(fake_ctx)
    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)

    result = await call(
        generate_script_plan_tool(fake_ctx),
        {"episode": 1, "source": "source/selected.txt"},
    )

    assert result.get("is_error") is not True, result
    entry = ProjectArtifactManifestAdapter(fake_ctx.project_path).get_entry(ArtifactKey.episode_script_plan(1))
    assert entry is not None
    assert entry.basis_digest == expected.digest


async def test_normalize_drama_script_preserves_legacy_request_basis_when_manifest_activates(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    from lib.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
    from lib.artifact_provenance import build_script_plan_basis
    from server import text_generation as mod

    project = {
        **fake_ctx.pm.project_payload,
        "schema_version": 7,
        "title": "项目",
        "content_mode": "drama",
        "generation_mode": "storyboard",
        "source_kind": "novel",
        "source_language": "中文",
        "overview": {},
        "episodes": [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
    }
    fake_ctx.pm.project_payload = project
    project_file = fake_ctx.project_path / "project.json"
    project_file.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    source_dir = fake_ctx.project_path / "source"
    source_dir.mkdir(parents=True)
    selected_source = source_dir / "selected.txt"
    selected_source.write_text("实际发送给供应商的原文", encoding="utf-8")
    (source_dir / "episode_1.txt").write_text("激活器可重建的另一份原文", encoding="utf-8")
    expected = build_script_plan_basis("实际发送给供应商的原文", episode=1, project=project)

    class _Generator:
        async def generate(self, _request, project_name=None):
            activated = {**project, "schema_version": 8}
            fake_ctx.pm.project_payload = activated
            project_file.write_text(json.dumps(activated, ensure_ascii=False), encoding="utf-8")
            return type(
                "_Result",
                (),
                {
                    "text": json.dumps(
                        {
                            "title": "第一集",
                            "scenes": [
                                {
                                    "scene_id": "E1S01",
                                    "duration_seconds": 4,
                                    "segment_break": False,
                                    "characters_in_scene": [],
                                    "scenes": [],
                                    "props": [],
                                    "scene_description": "山中清晨",
                                    "utterances": [],
                                    "source_text": "实际发送给供应商的原文",
                                }
                            ],
                        },
                        ensure_ascii=False,
                    )
                },
            )()

    async def fake_create(_task_type, project_name=None):
        return _Generator()

    use_fake_caps(fake_ctx)
    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)

    result = await call(
        generate_script_plan_tool(fake_ctx),
        {"episode": 1, "source": "source/selected.txt"},
    )

    assert result.get("is_error") is not True, result
    entry = ProjectArtifactManifestAdapter(fake_ctx.project_path).get_entry(ArtifactKey.episode_script_plan(1))
    assert entry is not None
    assert entry.basis_digest == expected.digest


async def test_normalize_drama_script_marks_mixed_machine_candidate_before_review(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    from server import text_generation as mod

    project_path = fake_ctx.project_path
    source_dir = project_path / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "chapter1.txt").write_text("从前有座山", encoding="utf-8")

    class _FakeGenerator:
        async def generate(self, _request, project_name=None):
            class _Result:
                text = json.dumps(
                    {
                        "title": "第一集",
                        "scenes": [
                            {
                                "scene_id": "E1S01",
                                "duration_seconds": 4,
                                "segment_break": False,
                                "characters_in_scene": ["阿离"],
                                "scenes": [],
                                "props": [],
                                "scene_description": "阿离站在山门前。",
                                "utterances": [
                                    {"kind": "dialogue", "speaker": "阿离", "text": "我回来了。"},
                                    {"kind": "voiceover", "speaker": None, "text": "三年后。"},
                                ],
                                "source_text": "三年后，阿离回到山门。",
                            }
                        ],
                    },
                    ensure_ascii=False,
                )

            return _Result()

    async def fake_create(_task_type, project_name=None):
        return _FakeGenerator()

    use_fake_caps(fake_ctx)
    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)

    result = await call(generate_script_plan_tool(fake_ctx), {"episode": 1})

    assert result.get("is_error") is not True, result
    saved = json.loads(
        (project_path / "drafts" / "episode_1" / "script_plan_normalized_script.json").read_text(encoding="utf-8")
    )
    assert saved["scenes"][0]["needs_replan"] is True
    assert [utterance["text"] for utterance in saved["scenes"][0]["utterances"]] == ["我回来了。", "三年后。"]


async def test_normalize_drama_script_no_source(fake_ctx: ToolContext) -> None:
    tool_obj = generate_script_plan_tool(fake_ctx)
    out = await call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True


async def test_normalize_drama_script_injects_instructions(fake_ctx: ToolContext) -> None:
    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter1.txt").write_text("从前有座山", encoding="utf-8")

    use_fake_caps(fake_ctx)
    tool_obj = generate_script_plan_tool(fake_ctx)
    out = await call(tool_obj, {"episode": 1, "dry_run": True, "instructions": "打斗场面多拆几个短镜头"})
    assert out.get("is_error") is not True, out
    prompt_text = out["content"][0]["text"]
    assert "# 用户意见" in prompt_text
    assert "打斗场面多拆几个短镜头" in prompt_text


async def test_generate_episode_script_forwards_instructions(fake_ctx: ToolContext, monkeypatch) -> None:
    """handler 把 instructions 原样转交 ScriptGenerator（dry_run 与生成路径同口径）。"""
    from server import text_generation as mod

    project_path = fake_ctx.project_path
    drafts = project_path / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    script_plan = drafts / "script_plan_segments.json"
    script_plan.write_text("script_plan", encoding="utf-8")
    fingerprint = script_review.content_fingerprint(script_plan)
    (project_path / "project.json").write_text(
        json.dumps(
            {
                "content_mode": "narration",
                "episodes": [{"episode": 1, "script_plan_review": {"fingerprint": fingerprint, "confirmed_at": "t"}}],
            }
        ),
        encoding="utf-8",
    )

    captured: dict[str, Any] = {}

    class _FakeGenerator:
        def __init__(self, _path, **_kwargs):
            pass

        @classmethod
        async def create(cls, _path, **_kwargs):
            return cls(_path)

        async def build_prompt(self, _episode, *, instructions=None, **_kwargs):
            captured["build_prompt"] = instructions
            return "fake prompt"

        async def generate(self, *, episode, instructions=None, **_kwargs):
            captured["generate"] = instructions
            return project_path / "scripts" / "episode_1.json"

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)
    tool_obj = generate_episode_script_tool(fake_ctx)

    out = await call(tool_obj, {"episode": 1, "dry_run": True, "instructions": "偏好特写镜头"})
    assert out.get("is_error") is not True, out
    assert captured["build_prompt"] == "偏好特写镜头"

    out = await call(tool_obj, {"episode": 1, "instructions": "偏好特写镜头"})
    assert out.get("is_error") is not True, out
    assert captured["generate"] == "偏好特写镜头"


async def test_generate_episode_script_reference_legacy_md_hints_resplit(fake_ctx: ToolContext) -> None:
    """reference_video 集仅存旧 .md 拆分表时，generate_episode_script 给出重跑拆分提示。"""
    project_path = fake_ctx.project_path
    (project_path / "project.json").write_text(
        json.dumps(
            {
                "content_mode": "narration",
                "generation_mode": "reference_video",
                "episodes": [{"episode": 1, "generation_mode": "reference_video"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    drafts = project_path / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "script_plan_reference_units.md").write_text("| E1U1 |", encoding="utf-8")

    tool_obj = generate_episode_script_tool(fake_ctx)
    out = await call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "调用 generate_script_plan" in text
    assert "script_plan_reference_units.json" in text
