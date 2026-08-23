"""Tests for text_generation (split from test_sdk_tools.py)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lib import script_review
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.text_generation import (
    _parse_normalized_content,
    generate_episode_script_tool,
    get_video_capabilities_tool,
    normalize_drama_script_tool,
    split_narration_segments_tool,
    split_reference_video_units_tool,
)
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import (
    _call,
)

pytestmark = pytest.mark.usefixtures("_stub_audio_switch_guard", "_stub_reference_request_projection")

# ---------------------------------------------------------------------------
# text_generation
# ---------------------------------------------------------------------------


async def test_get_video_capabilities_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def fake_resolve(_project, _episode=None):
        return {"provider_id": "fake", "supported_durations": [4, 6, 8]}

    monkeypatch.setattr(mod, "_resolve_video_capabilities", fake_resolve)
    tool_obj = get_video_capabilities_tool(fake_ctx)
    out = await _call(tool_obj, {})
    assert out.get("is_error") is not True
    assert json.loads(out["content"][0]["text"])["provider_id"] == "fake"


async def test_get_video_capabilities_resolves_by_project(fake_ctx: ToolContext, monkeypatch) -> None:
    """能力按项目生成模式解析：工具不收集号，多余的集号入参被忽略、不改变解析口径。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    seen: list[str] = []

    async def fake_resolve(project_name):
        seen.append(project_name)
        return {"provider_id": "fake", "supported_durations": [4, 6, 8]}

    monkeypatch.setattr(mod, "_resolve_video_capabilities", fake_resolve)
    tool_obj = get_video_capabilities_tool(fake_ctx)
    assert (await _call(tool_obj, {})).get("is_error") is not True
    assert (await _call(tool_obj, {"episode": 3})).get("is_error") is not True
    assert seen == [fake_ctx.project_name, fake_ctx.project_name]


async def test_get_video_capabilities_annotates_reference_unit_tiers(fake_ctx: ToolContext, monkeypatch) -> None:
    """参考路径项目另返回两套逐 unit 生效档位，供手工改 step1 时与生成侧对同一份数字。"""
    from server.agent_runtime.sdk_tools import _context
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def fake_resolve(_project):
        return {
            "provider_id": "gemini-aistudio",
            "model": "veo-3.1-generate-preview",
            "supported_durations": [4, 6, 8],
            "generation_mode": "reference_video",
        }

    fake_ctx.pm.project_payload["model_settings"] = {  # type: ignore[attr-defined]
        "gemini-aistudio/veo-3.1-generate-preview": {"resolution": "720p"}
    }
    monkeypatch.setattr(mod, "_resolve_video_capabilities", fake_resolve)

    async def _no_i2v(_project, *, capability=None):
        raise ValueError("i2v bucket unresolvable in this test")

    monkeypatch.setattr(_context, "resolve_video_caps", _no_i2v)
    out = await _call(get_video_capabilities_tool(fake_ctx), {})
    assert out.get("is_error") is not True, out
    payload = json.loads(out["content"][0]["text"])
    assert payload["reference_unit_durations"] == {"with_references": [8], "without_references": [4, 6, 8]}
    # 全集原样保留：它是型号声明，不是生效档位
    assert payload["supported_durations"] == [4, 6, 8]


@pytest.mark.parametrize(
    ("generation_mode", "content_mode"),
    [("storyboard", "drama"), ("reference_video", "ad")],
)
async def test_get_video_capabilities_skips_tiers_off_episode_reference_path(
    fake_ctx: ToolContext, monkeypatch, generation_mode: str, content_mode: str
) -> None:
    """非剧集参考路径不补该字段：其它路径没有逐 unit 引用状态，ad 分镜时长也不受档位枚举管辖。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def fake_resolve(_project):
        return {
            "provider_id": "gemini-aistudio",
            "model": "veo-3.1-generate-preview",
            "supported_durations": [4, 6, 8],
            "generation_mode": generation_mode,
            "content_mode": content_mode,
        }

    monkeypatch.setattr(mod, "_resolve_video_capabilities", fake_resolve)
    out = await _call(get_video_capabilities_tool(fake_ctx), {})
    assert "reference_unit_durations" not in json.loads(out["content"][0]["text"])


async def test_get_video_capabilities_shares_rest_resolution_entry(fake_ctx: ToolContext, monkeypatch) -> None:
    """Agent 工具与 REST 能力查询走同一个解析入口 ``ConfigResolver.video_capabilities``。

    两侧各自解析会让 Agent 写剧本时看到的时长 / 参考图上限与界面显示的不是同一个模型。
    """
    from lib.config.resolver import ConfigResolver

    seen: list[str] = []

    async def fake_video_capabilities(_self, project_name=None, episode=None):
        seen.append(project_name)
        return {"provider_id": "kling", "model": "kling-v3-omni", "supported_durations": [5]}

    monkeypatch.setattr(ConfigResolver, "video_capabilities", fake_video_capabilities)
    out = await _call(get_video_capabilities_tool(fake_ctx), {})
    assert out.get("is_error") is not True, out
    assert json.loads(out["content"][0]["text"])["model"] == "kling-v3-omni"
    assert seen == [fake_ctx.project_name]


async def test_get_video_capabilities_error(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def fake_resolve(_project, _episode=None):
        raise FileNotFoundError("missing project.json")

    monkeypatch.setattr(mod, "_resolve_video_capabilities", fake_resolve)
    tool_obj = get_video_capabilities_tool(fake_ctx)
    out = await _call(tool_obj, {})
    assert out.get("is_error") is True


async def test_generate_episode_script_dry_run(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    drafts = project_path / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    (drafts / "step1_segments.json").write_text("step1 content", encoding="utf-8")
    (project_path / "project.json").write_text(json.dumps({"content_mode": "narration"}), encoding="utf-8")

    class _FakeGenerator:
        def __init__(self, _path):
            pass

        async def build_prompt(self, _episode, *, instructions=None):
            return "fake prompt"

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)
    tool_obj = generate_episode_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1, "dry_run": True})
    assert out.get("is_error") is not True
    assert "fake prompt" in out["content"][0]["text"]


async def test_generate_episode_script_missing_step1(fake_ctx: ToolContext) -> None:
    tool_obj = generate_episode_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 99})
    assert out.get("is_error") is True


async def test_generate_episode_script_writes_to_default_project_scripts(fake_ctx: ToolContext, monkeypatch) -> None:
    """output 参数已下线；写出路径必须由 ScriptGenerator 内部决定，handler 不应让 Agent 控制。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    drafts = project_path / "drafts" / "episode_1"
    drafts.mkdir(parents=True)
    step1 = drafts / "step1_segments.json"
    step1.write_text("step1", encoding="utf-8")
    # step1→step2 内容确认：须先确认才放行生成，否则 handler 早返阻塞而非调 ScriptGenerator。
    # 把已存确认指纹对齐当前 step1 内容指纹，模拟「用户已在 Web 确认」。
    fingerprint = script_review.content_fingerprint(step1)
    (project_path / "project.json").write_text(
        json.dumps(
            {
                "content_mode": "narration",
                "episodes": [{"episode": 1, "step1_review": {"fingerprint": fingerprint, "confirmed_at": "t"}}],
            }
        ),
        encoding="utf-8",
    )

    captured: dict[str, dict[str, Any]] = {"calls": {}}

    class _FakeGenerator:
        @classmethod
        async def create(cls, _path):
            return cls()

        async def generate(self, **kwargs) -> Path:
            captured["calls"] = kwargs
            return project_path / "scripts" / "episode_1.json"

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)
    tool_obj = generate_episode_script_tool(fake_ctx)

    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is not True
    # handler 不再传 output_path —— ScriptGenerator 自己决定写到哪里
    assert "output_path" not in captured["calls"]


async def test_generate_episode_script_ad_skips_step1(fake_ctx: ToolContext, monkeypatch) -> None:
    """ad 一键生成不依赖 step1 中间文件：缺 drafts/ 也不报 step1 错误。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    (project_path / "project.json").write_text(
        json.dumps({"content_mode": "ad", "target_duration": 30}), encoding="utf-8"
    )

    class _FakeGenerator:
        @classmethod
        async def create(cls, _path):
            return cls()

        async def generate(self, **_kwargs) -> Path:
            return project_path / "scripts" / "episode_1.json"

    monkeypatch.setattr(mod, "ScriptGenerator", _FakeGenerator)
    tool_obj = generate_episode_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is not True


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
    # 超出枚举 → 动态 schema 校验失败 → fail-loud 抛 ValueError，不把未校验内容当成正式 step1 落盘
    with pytest.raises(ValueError, match="step1 规范化内容结构校验失败"):
        _parse_normalized_content(json.dumps({"title": "t", "scenes": [bad]}), model)


async def test_fetch_caps_with_fallback_uses_write_layer_default(monkeypatch) -> None:
    """resolver 失败时软回退须与自定义供应商写入层的保守默认（duration_presets.DEFAULT_FALLBACK）
    同一真相源——独立维护第二套回退集会让 LLM 拿到供应商未必支持的时长。"""
    from lib.custom_provider.duration_presets import DEFAULT_FALLBACK
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def raising_caps(_p, *, episode=None, generation_mode=None):
        raise ValueError("no provider configured")

    monkeypatch.setattr(mod, "fetch_video_caps", raising_caps)
    default, durations = await mod._fetch_caps_with_fallback({}, 1)
    assert default is None
    assert durations == DEFAULT_FALLBACK


async def test_fetch_caps_with_fallback_drops_out_of_range_default(monkeypatch) -> None:
    """收窄后落在集合外的已保存 default_duration 归 None（回到 auto 档），不拖垮整个工具。

    ``build_normalize_prompt`` 对非成员 default 是 fail-loud 的：用户在 720p 下存过 4 秒、
    改到 1080p 后 Veo 收窄为 [8]，不归 None 会让 normalize_drama_script 直接抛 ValueError。
    """
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def _narrowed_caps(_p, *, episode=None, generation_mode=None):
        return 4, [8]

    monkeypatch.setattr(mod, "fetch_video_caps", _narrowed_caps)
    default, durations = await mod._fetch_caps_with_fallback({}, 1)
    assert default is None
    assert durations == [8]

    async def _in_range_caps(_p, *, episode=None, generation_mode=None):
        return 8, [4, 6, 8]

    monkeypatch.setattr(mod, "fetch_video_caps", _in_range_caps)
    default, durations = await mod._fetch_caps_with_fallback({}, 1)
    assert default == 8
    assert durations == [4, 6, 8]


async def test_fetch_video_caps_narrows_durations_by_constraints(monkeypatch) -> None:
    """交给 LLM 的时长集合已按项目分辨率经联动约束收窄。

    Veo 项目保存 1080p 时只接受 8 秒；不收窄的话 drama / narration 拆分会产出 4/6 秒镜头，
    视频入队时才被 backend 拒。
    """
    from server.agent_runtime.sdk_tools import _context as ctx_mod

    async def _fake_caps(_project, _episode=None):
        return {
            "provider_id": "gemini-aistudio",
            "model": "veo-3.1-generate-preview",
            "supported_durations": [4, 6, 8],
            "default_duration": 4,
        }

    monkeypatch.setattr(ctx_mod, "resolve_video_caps", _fake_caps)

    project_1080p = {"model_settings": {"gemini-aistudio/veo-3.1-generate-preview": {"resolution": "1080p"}}}
    default, durations = await ctx_mod.fetch_video_caps(project_1080p)
    assert durations == [8]
    # default_duration 原样返回（用户配置值），成员性由调用方按各自口径判定
    assert default == 4

    # 未配置分辨率：普通路径省略 resolution 参数，供应商按自己的默认档位（Veo 720p）接受 4/6/8，
    # 故不施加分辨率约束——按 provider 兜底档位收窄会凭空把剧本节奏锁死 8 秒。
    _default, durations = await ctx_mod.fetch_video_caps({})
    assert durations == [4, 6, 8]

    # 项目显式选了无声明的分辨率时不收窄。
    project = {"model_settings": {"gemini-aistudio/veo-3.1-generate-preview": {"resolution": "720p"}}}
    _default, durations = await ctx_mod.fetch_video_caps(project)
    assert durations == [4, 6, 8]

    # 参考图路径：即便分辨率无声明也收窄
    _default, durations = await ctx_mod.fetch_video_caps(project, generation_mode="reference_video")
    assert durations == [8]


async def test_normalize_drama_script_dry_run(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter1.txt").write_text("从前有座山", encoding="utf-8")

    async def fake_caps(_p, _episode=None):
        return 4, [4, 6, 8]

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    tool_obj = normalize_drama_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1, "dry_run": True})
    assert out.get("is_error") is not True
    assert "DRY RUN" in out["content"][0]["text"]


@pytest.mark.parametrize(
    ("tool_factory", "content_mode", "generation_mode"),
    [
        (normalize_drama_script_tool, "narration", "storyboard"),
        (split_narration_segments_tool, "drama", "storyboard"),
        (split_reference_video_units_tool, "drama", "storyboard"),
    ],
    ids=("normalize", "narration-split", "reference-split"),
)
async def test_step1_tools_reject_incompatible_project_axes_before_capability_lookup(
    fake_ctx: ToolContext,
    monkeypatch,
    tool_factory,
    content_mode: str,
    generation_mode: str,
) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    fake_ctx.pm.project_payload.update(  # type: ignore[attr-defined]
        content_mode=content_mode,
        generation_mode=generation_mode,
    )
    source_dir = fake_ctx.project_path / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "episode_1.txt").write_text("从前有座山", encoding="utf-8")

    async def unexpected_caps(*_args, **_kwargs):
        pytest.fail("incompatible step1 tool must reject before capability lookup")

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", unexpected_caps)
    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", unexpected_caps)

    result = await _call(tool_factory(fake_ctx), {"episode": 1, "dry_run": True})

    assert result.get("is_error") is True
    message = result["content"][0]["text"]
    assert content_mode in message
    assert generation_mode in message


async def test_normalize_drama_script_projects_durable_inputs_once(fake_ctx: ToolContext, monkeypatch) -> None:
    from lib import artifact_provenance
    from server.agent_runtime.sdk_tools import text_generation as mod

    source_dir = fake_ctx.project_path / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "episode_1.txt").write_text("从前有座山", encoding="utf-8")
    calls = 0
    original = artifact_provenance.project_step1_prompt_inputs

    def counted_projection(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    async def fake_caps(_project, _episode=None):
        return 4, [4, 6, 8]

    monkeypatch.setattr(artifact_provenance, "project_step1_prompt_inputs", counted_projection)
    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)

    result = await _call(normalize_drama_script_tool(fake_ctx), {"episode": 1, "dry_run": True})

    assert result.get("is_error") is not True, result
    assert calls == 1


async def test_normalize_drama_script_wires_target_language(fake_ctx: ToolContext, monkeypatch) -> None:
    """normalize 把项目 source_language 透传为 build_normalize_prompt 的 target_language——
    非中文项目的 step1 输出语言据此切换，而非恒退默认中文。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    # 工具经 ctx.pm.load_project 取项目；source_language 是输出语言的唯一真相源
    fake_ctx.pm.project_payload["source_language"] = "English"  # type: ignore[attr-defined]
    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter1.txt").write_text("once upon a time", encoding="utf-8")

    async def fake_caps(_p, _episode=None):
        return 4, [4, 6, 8]

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    tool_obj = normalize_drama_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1, "dry_run": True})
    assert out.get("is_error") is not True
    assert "English" in out["content"][0]["text"]


async def test_normalize_drama_script_rejects_empty_scenes(fake_ctx: ToolContext, monkeypatch) -> None:
    """normalize 产出空 scenes → 工具报错，不把空 step1 当成功产物写盘（与 _load_drama_step1_content 同口径）。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter1.txt").write_text("从前有座山", encoding="utf-8")

    async def fake_caps(_p, _episode=None):
        return 4, [4, 6, 8]

    class _EmptyGenerator:
        async def generate(self, _request, project_name=None):
            class _R:
                text = json.dumps({"title": "第一集", "scenes": []}, ensure_ascii=False)

            return _R()

    async def fake_create(task_type, project_name=None):
        return _EmptyGenerator()

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)

    tool_obj = normalize_drama_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
    # 空 scenes 不写盘，避免生成阶段才必然失败
    assert not (project_path / "drafts" / "episode_1" / "step1_normalized_script.json").exists()


async def test_normalize_drama_script_injects_episode_into_prompt(fake_ctx: ToolContext, monkeypatch) -> None:
    """工具必须把 episode 注入 build_normalize_prompt，避免 LLM 写错 E\\d+ 前缀。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter2.txt").write_text("第二集开场", encoding="utf-8")

    async def fake_caps(_p, _episode=None):
        return 4, [4, 6, 8]

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    tool_obj = normalize_drama_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 2, "dry_run": True, "source": "source/chapter2.txt"})
    assert out.get("is_error") is not True, out
    prompt_text = out["content"][0]["text"]
    assert "E2S01" in prompt_text
    assert "第 2 集" in prompt_text or "E2S{两位序号}" in prompt_text
    assert "E1S01" not in prompt_text


async def test_normalize_drama_script_injects_episode_outline(fake_ctx: ToolContext, monkeypatch) -> None:
    """内容抽取前移后，分集大纲（故事节点 / 钩子）随 step1 注入 normalize prompt（见 ADR 0041）。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter1.txt").write_text("从前有座山", encoding="utf-8")
    fake_ctx.pm.project_payload["episodes"] = [  # type: ignore[attr-defined]
        {
            "episode": 1,
            "title": "初入江湖",
            "hook": "少年坠崖生死未卜",
            "outline": {"story_beats": ["少年下山"], "next_episode_teaser": None},
        }
    ]

    async def fake_caps(_p, _episode=None):
        return 4, [4, 6, 8]

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    tool_obj = normalize_drama_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1, "dry_run": True})
    assert out.get("is_error") is not True, out
    prompt_text = out["content"][0]["text"]
    assert "少年下山" in prompt_text
    assert "少年坠崖生死未卜" in prompt_text


async def test_normalize_drama_script_passes_project_name_to_backend(fake_ctx: ToolContext, monkeypatch) -> None:
    """工具必须把 ctx.project_name 传给 TextGenerator.create/generate，
    否则项目级文本档位覆盖被跳过，且 usage tracking 会丢 project_name。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    src = project_path / "source"
    src.mkdir(parents=True)
    (src / "chapter1.txt").write_text("从前有座山", encoding="utf-8")

    async def fake_caps(_p, _episode=None):
        return 4, [4, 6, 8]

    captured: dict[str, Any] = {}

    class _FakeGenerator:
        async def generate(self, _request, project_name=None):
            captured["generate_project_name"] = project_name

            class _R:
                # step1 现在产出结构化 JSON（DramaNormalizedScript），非 markdown 表
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

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)

    tool_obj = normalize_drama_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})

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
    from lib.artifact_provenance import build_step1_basis
    from server.agent_runtime.sdk_tools import text_generation as mod

    project = {
        **fake_ctx.pm.project_payload,  # type: ignore[attr-defined]
        "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
        "content_mode": "drama",
        "generation_mode": "storyboard",
        "source_kind": "novel",
        "source_language": "中文",
    }
    fake_ctx.pm.project_payload = project  # type: ignore[attr-defined]
    project_file = fake_ctx.project_path / "project.json"
    project_file.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    source_path = fake_ctx.project_path / "source" / "selected.txt"
    source_path.parent.mkdir(parents=True)
    frozen_source = "被显式选中的生成原文"
    source_path.write_text(frozen_source, encoding="utf-8")
    expected = build_step1_basis(frozen_source, episode=1, project=project)

    async def fake_caps(_project, _episode=None):
        return 4, [4, 6, 8]

    class _Generator:
        async def generate(self, _request, project_name=None):
            source_path.write_text("等待供应商期间改过的原文", encoding="utf-8")
            latest = {**project, "source_language": "English"}
            fake_ctx.pm.project_payload = latest  # type: ignore[attr-defined]
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

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)

    result = await _call(
        normalize_drama_script_tool(fake_ctx),
        {"episode": 1, "source": "source/selected.txt"},
    )

    assert result.get("is_error") is not True, result
    entry = ProjectArtifactManifestAdapter(fake_ctx.project_path).get_entry(ArtifactKey.episode_step1(1))
    assert entry is not None
    assert entry.basis_digest == expected.digest


async def test_normalize_drama_script_preserves_legacy_request_basis_when_manifest_activates(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    from lib.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
    from lib.artifact_provenance import build_step1_basis
    from server.agent_runtime.sdk_tools import text_generation as mod

    project = {
        **fake_ctx.pm.project_payload,  # type: ignore[attr-defined]
        "schema_version": 7,
        "title": "项目",
        "content_mode": "drama",
        "generation_mode": "storyboard",
        "source_kind": "novel",
        "source_language": "中文",
        "overview": {},
        "episodes": [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}],
    }
    fake_ctx.pm.project_payload = project  # type: ignore[attr-defined]
    project_file = fake_ctx.project_path / "project.json"
    project_file.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    source_dir = fake_ctx.project_path / "source"
    source_dir.mkdir(parents=True)
    selected_source = source_dir / "selected.txt"
    selected_source.write_text("实际发送给供应商的原文", encoding="utf-8")
    (source_dir / "episode_1.txt").write_text("激活器可重建的另一份原文", encoding="utf-8")
    expected = build_step1_basis("实际发送给供应商的原文", episode=1, project=project)

    async def fake_caps(_project, _episode=None):
        return 4, [4, 6, 8]

    class _Generator:
        async def generate(self, _request, project_name=None):
            activated = {**project, "schema_version": 8}
            fake_ctx.pm.project_payload = activated  # type: ignore[attr-defined]
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

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)

    result = await _call(
        normalize_drama_script_tool(fake_ctx),
        {"episode": 1, "source": "source/selected.txt"},
    )

    assert result.get("is_error") is not True, result
    entry = ProjectArtifactManifestAdapter(fake_ctx.project_path).get_entry(ArtifactKey.episode_step1(1))
    assert entry is not None
    assert entry.basis_digest == expected.digest


async def test_normalize_drama_script_marks_mixed_machine_candidate_before_review(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    project_path = fake_ctx.project_path
    source_dir = project_path / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "chapter1.txt").write_text("从前有座山", encoding="utf-8")

    async def fake_caps(_project, _episode=None):
        return 4, [4, 6, 8]

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

    monkeypatch.setattr(mod, "_fetch_caps_with_fallback", fake_caps)
    monkeypatch.setattr(mod.TextGenerator, "create", fake_create)

    result = await _call(normalize_drama_script_tool(fake_ctx), {"episode": 1})

    assert result.get("is_error") is not True, result
    saved = json.loads(
        (project_path / "drafts" / "episode_1" / "step1_normalized_script.json").read_text(encoding="utf-8")
    )
    assert saved["scenes"][0]["needs_replan"] is True
    assert [utterance["text"] for utterance in saved["scenes"][0]["utterances"]] == ["我回来了。", "三年后。"]


async def test_normalize_drama_script_no_source(fake_ctx: ToolContext) -> None:
    tool_obj = normalize_drama_script_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True


# ---------------------------------------------------------------------------
# _build_prompt：Style 去重 + 「画风：」前缀清理
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_structured_no_duplicate_style(self) -> None:
        from server.agent_runtime.sdk_tools.enqueue_storyboards import _build_prompt

        segment = {
            "segment_id": "E1S01",
            "image_prompt": {
                "scene": "村口黄昏",
                "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
            },
        }
        out = _build_prompt(segment, "画风：真人电视剧风格", "Soft light", "segment_id")

        # Style 只出现一次（YAML 内），不再有前缀 "Style: ..." 行重复注入
        assert out.count("Style:") == 1
        # 「画风：」前缀被清理，不会渲染成 "Style: 画风：..."
        assert "画风：" not in out
        assert "Style: 真人电视剧风格" in out
        # style_description 仍以 Visual style 前缀注入
        assert out.startswith("Visual style: Soft light")

    def test_unstructured_keeps_style_prefix_normalized(self) -> None:
        from server.agent_runtime.sdk_tools.enqueue_storyboards import _build_prompt

        segment = {"segment_id": "E1S02", "image_prompt": "村口黄昏的长镜头"}
        out = _build_prompt(segment, "画风：真人电视剧风格", "", "segment_id")

        # 非结构化纯字符串 prompt 不含 Style，前缀补上且去掉「画风：」
        assert out.count("Style:") == 1
        assert "画风：" not in out
        assert out.startswith("Style: 真人电视剧风格")
        assert "\n\n村口黄昏的长镜头\n\n" in out
        assert out.endswith("画面避免：水印、多余文字、Logo。")
