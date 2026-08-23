"""Tests for split_reference_video_units (split from test_sdk_tools.py)."""

from __future__ import annotations

import json
from typing import Any

from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.text_generation import (
    split_reference_video_units_tool,
)
from tests.fakes import fake_reference_caps_fetcher
from tests.integration.server.agent_runtime.sdk_tools.conftest import (
    _RV_NOVEL,
    _call,
    _derived_reference_names,
    _read_rv_quarantine,
    _run_rv_split,
    _rv_generator_returning,
    _rv_source,
    _rv_step1_path,
    _rv_unit,
)

# ---------------------------------------------------------------------------
# split_reference_video_units
# ---------------------------------------------------------------------------


async def test_fetch_reference_caps_with_fallback_returns_declared_slots(monkeypatch) -> None:
    """unit 时长就是发给供应商的那个值，档位原样取自模型声明（不与任何静态区间求交）。"""
    from server.agent_runtime.sdk_tools import _context
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def _fake_caps(_project, _episode=None):
        return {"supported_durations": [1, 8, 16, 18], "max_duration": 18, "default_duration": 16}

    monkeypatch.setattr(mod, "resolve_video_caps", _fake_caps)

    async def _no_i2v(_project, *, capability=None):
        raise ValueError("i2v bucket unresolvable in this test")

    monkeypatch.setattr(_context, "resolve_video_caps", _no_i2v)

    caps = await mod._fetch_reference_caps_with_fallback({}, 1)

    assert caps.durations == [1, 8, 16, 18]
    assert caps.reference_durations == [1, 8, 16, 18]
    assert caps.text_durations == [1, 8, 16, 18]
    assert caps.max_duration == 18
    assert caps.default_duration == 16  # 是档位成员，照常采信
    assert caps.max_refs is None


async def test_fetch_reference_caps_with_fallback_narrows_unit_duration_cap(monkeypatch) -> None:
    """档位随联动约束收窄：海螺在 1080p 下只接受 6 秒，全集是 [6, 10]。

    不收窄的话 step1 会按 10 秒拆出 unit，step2 的枚举 schema 再把它判非法。
    """
    from server.agent_runtime.sdk_tools import _context
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def _fake_caps(_project, _episode=None):
        return {
            "provider_id": "minimax",
            "model": "MiniMax-Hailuo-2.3",
            "supported_durations": [6, 10],
            "max_duration": 10,
            "default_duration": None,
        }

    monkeypatch.setattr(mod, "resolve_video_caps", _fake_caps)

    async def _no_i2v(_project, *, capability=None):
        raise ValueError("i2v bucket unresolvable in this test")

    monkeypatch.setattr(_context, "resolve_video_caps", _no_i2v)

    project = {"model_settings": {"minimax/MiniMax-Hailuo-2.3": {"resolution": "1080p"}}}
    caps = await mod._fetch_reference_caps_with_fallback(project, 1)
    assert caps.durations == [6]
    assert caps.max_duration == 6


async def test_fetch_reference_caps_with_fallback_narrows_slots_by_resolution(monkeypatch) -> None:
    """分辨率联动约束同样收窄 unit 档位：Veo 1080p 下只接受 8 秒。"""
    from server.agent_runtime.sdk_tools import _context
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def _fake_caps(_project, _episode=None):
        return {
            "provider_id": "gemini-aistudio",
            "model": "veo-3.1-generate-preview",
            "supported_durations": [4, 6, 8],
            "max_duration": 8,
            "default_duration": None,
        }

    monkeypatch.setattr(mod, "resolve_video_caps", _fake_caps)

    async def _no_i2v(_project, *, capability=None):
        raise ValueError("i2v bucket unresolvable in this test")

    monkeypatch.setattr(_context, "resolve_video_caps", _no_i2v)

    project = {"model_settings": {"gemini-aistudio/veo-3.1-generate-preview": {"resolution": "1080p"}}}
    caps = await mod._fetch_reference_caps_with_fallback(project, 1)
    assert caps.durations == [8]
    assert caps.max_duration == 8


async def test_reference_unit_duration_tiers_does_not_assume_containment(monkeypatch) -> None:
    """两套档位之间无包含关系可假定：两条约束自相矛盾时带图那套反而更宽。

    ``constrain_durations`` 在交集为空时回退到未收窄候选，故型号同时声明「带图仅 8s」与
    「1080p 仅 6s」时，带图集回退成全集、不带图集收成 [6]。调用方须显式取并集当枚举。
    i2v 桶解析按不可解析处理——退回两桶同模型口径，联动矛盾在单模型内就能成立。
    """
    from lib.config import resolver as resolver_mod
    from lib.config.registry import ModelInfo
    from server.agent_runtime.sdk_tools import _context

    contradictory = ModelInfo(
        display_name="contradictory",
        media_type="video",
        capabilities=[],
        supported_durations=[4, 6, 8],
        duration_resolution_constraints={"1080p": [6]},
        reference_image_durations=[8],
    )
    monkeypatch.setattr(resolver_mod, "model_info_for", lambda *_args: contradictory)

    async def _no_i2v(_project, *, capability=None):
        raise ValueError("i2v bucket unresolvable")

    monkeypatch.setattr(_context, "resolve_video_caps", _no_i2v)

    project = {"model_settings": {"p/m": {"resolution": "1080p"}}}
    with_refs, without_refs = await _context.reference_unit_duration_tiers(
        project, {"provider_id": "p", "model": "m"}, [4, 6, 8]
    )

    assert with_refs == [4, 6, 8]
    assert without_refs == [6]
    assert not set(with_refs) <= set(without_refs)


async def test_reference_unit_duration_tiers_without_refs_follow_i2v_bucket(monkeypatch) -> None:
    """不带图档位按 i2v 桶模型求值：无引用 unit 执行期降级到 i2v 桶执行，创作侧放行的秒数
    须与该桶模型的声明一致，否则会放行 r2v 独有档位、漏掉 i2v 独有档位。"""
    from server.agent_runtime.sdk_tools import _context

    async def _i2v_caps(_project, *, capability=None):
        assert capability == "i2v"
        return {"provider_id": "ark", "model": "doubao-seedance-1-5-pro-251215", "supported_durations": [5, 10]}

    monkeypatch.setattr(_context, "resolve_video_caps", _i2v_caps)

    with_refs, without_refs = await _context.reference_unit_duration_tiers(
        {}, {"provider_id": "minimax", "model": "S2V-01"}, [6, 10]
    )

    assert with_refs == [6, 10]
    assert without_refs == [5, 10]


async def test_fetch_reference_caps_with_fallback_splits_tiers_by_reference_state(monkeypatch) -> None:
    """「参考图↔时长」约束逐 unit 生效：Veo 720p 下带引用只剩 8 秒，无引用仍有 4/6/8。

    枚举与 prompt 候选取并集——一律按带图收窄会把无引用 unit 本可申请的短档也收掉。
    """
    from server.agent_runtime.sdk_tools import _context
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def _fake_caps(_project, _episode=None):
        return {
            "provider_id": "gemini-aistudio",
            "model": "veo-3.1-generate-preview",
            "supported_durations": [4, 6, 8],
            "max_duration": 8,
            "default_duration": None,
        }

    monkeypatch.setattr(mod, "resolve_video_caps", _fake_caps)

    async def _no_i2v(_project, *, capability=None):
        raise ValueError("i2v bucket unresolvable in this test")

    monkeypatch.setattr(_context, "resolve_video_caps", _no_i2v)

    project = {"model_settings": {"gemini-aistudio/veo-3.1-generate-preview": {"resolution": "720p"}}}
    caps = await mod._fetch_reference_caps_with_fallback(project, 1)
    assert caps.reference_durations == [8]
    assert caps.text_durations == [4, 6, 8]
    assert caps.durations == [4, 6, 8]
    assert caps.max_duration == 8
    assert caps.tiers_for(has_references=True) == [8]
    assert caps.tiers_for(has_references=False) == [4, 6, 8]


async def test_fetch_reference_caps_with_fallback_uses_write_layer_default(monkeypatch) -> None:
    """rv 路径的软回退与 _fetch_caps_with_fallback 同口径，取 duration_presets.DEFAULT_FALLBACK。"""
    from lib.custom_provider.duration_presets import DEFAULT_FALLBACK
    from server.agent_runtime.sdk_tools import _context
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def _raising_caps(_project, _episode=None):
        raise ValueError("no provider configured")

    monkeypatch.setattr(mod, "resolve_video_caps", _raising_caps)

    async def _no_i2v(_project, *, capability=None):
        raise ValueError("i2v bucket unresolvable in this test")

    monkeypatch.setattr(_context, "resolve_video_caps", _no_i2v)
    caps = await mod._fetch_reference_caps_with_fallback({}, 1)
    assert caps.default_duration is None
    assert caps.durations == DEFAULT_FALLBACK
    assert caps.max_duration == max(DEFAULT_FALLBACK)
    assert caps.max_refs is None


async def test_fetch_reference_caps_with_fallback_preserves_silent_intent_on_failure(monkeypatch) -> None:
    """能力查询失败时，`raw["requested_generate_audio"]` 仍随项目覆盖走，不回退成 True。

    它不依赖能力接口独立解析（同 generation_context.py），否则声音提示层会漏发
    WARN_SILENT_EPISODE，误导用户以为本集仍会尝试组装参考音频。独立解析本身照原样
    mock 掉（不经 async_session_factory 打真实 DB）：这条测不验证 DB 读取，只验证
    能力查询失败下 caps 字典的组装口径，打真 DB 只会让结果依赖本机是否已初始化好应用库。
    """
    from lib.config.resolver import ConfigResolver
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def _raising_caps(_project, _episode=None):
        raise ValueError("no provider configured")

    async def _fake_project_audio(self, project):
        return bool(project.get("video_generate_audio", True))

    monkeypatch.setattr(mod, "resolve_video_caps", _raising_caps)
    monkeypatch.setattr(ConfigResolver, "video_generate_audio_for_project", _fake_project_audio)
    caps = await mod._fetch_reference_caps_with_fallback({"video_generate_audio": False}, 1)
    assert caps.voice.requested_generate_audio is False


async def test_fetch_reference_caps_with_fallback_degrades_silent_on_double_failure(monkeypatch) -> None:
    """独立解析也失败（双重故障）时收紧到 False，不得落回 True。

    与其余能力字段「不明时不额外收紧」相反：这里不明时假定无声，代价只是少发一条声音
    提示；假定有声则会让 `derive_voice_bindings` 在派生阶段继续算参考音频，误导排查方向。
    """
    from lib.config.resolver import ConfigResolver
    from server.agent_runtime.sdk_tools import text_generation as mod

    async def _raising_caps(_project, _episode=None):
        raise ValueError("no provider configured")

    async def _raising_project_audio(self, _project):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(mod, "resolve_video_caps", _raising_caps)
    monkeypatch.setattr(ConfigResolver, "video_generate_audio_for_project", _raising_project_audio)
    caps = await mod._fetch_reference_caps_with_fallback({"video_generate_audio": False}, 1)
    assert caps.voice.requested_generate_audio is False


async def test_split_reference_video_units_dry_run(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", fake_reference_caps_fetcher())

    tool_obj = split_reference_video_units_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1, "dry_run": True})
    assert out.get("is_error") is not True, out
    prompt_text = out["content"][0]["text"]
    assert "DRY RUN" in prompt_text
    # 集号、资产候选与能力约束进 prompt；引用语法规范随之注入
    assert "第 1 集" in prompt_text
    assert "张三" in prompt_text
    assert "12 秒" in prompt_text
    assert "分段前缀" in prompt_text


async def test_split_reference_video_units_happy_derives_structure(fake_ctx: ToolContext, monkeypatch) -> None:
    """happy path：LLM 只写扁平正文，正文逐字落盘，只有 unit_id 由工具机械派生。"""
    from server.agent_runtime.sdk_tools import text_generation as mod

    _rv_source(fake_ctx)
    captured: dict[str, Any] = {}
    text = "@[张三] 走向 @[村口]\n@[张三] 停下脚步"
    units = [_rv_unit(text)]
    monkeypatch.setattr(mod, "_fetch_reference_caps_with_fallback", fake_reference_caps_fetcher())
    monkeypatch.setattr(mod.TextGenerator, "create", _rv_generator_returning(units, captured))

    out = await _call(split_reference_video_units_tool(fake_ctx), {"episode": 1})
    assert out.get("is_error") is not True, out

    saved = json.loads(_rv_step1_path(fake_ctx).read_text(encoding="utf-8"))
    unit = saved["units"][0]
    assert unit["unit_id"] == "E1U01"
    assert unit["text"] == text
    # 参考图不落盘：读侧一律从正文的 @[名称] 派生
    assert "references" not in unit
    assert _derived_reference_names(fake_ctx, unit["text"]) == ["张三", "村口"]
    assert unit["source_text"] == _RV_NOVEL
    assert captured["task_type"] is mod.TextTaskType.SCRIPT
    assert captured["create_project_name"] == "demo"
    assert captured["generate_project_name"] == "demo"


async def test_split_reference_video_units_numbers_unit_ids_by_order(fake_ctx: ToolContext, monkeypatch) -> None:
    """unit_id 按数组序号机械编号：LLM 不写 id，也就不存在重复 / 错集号可写。"""
    _rv_source(fake_ctx)
    units = [_rv_unit("@[张三] 起身"), _rv_unit("@[张三] 出门")]
    out = await _run_rv_split(fake_ctx, monkeypatch, units)
    assert out.get("is_error") is not True, out
    saved = json.loads(_rv_step1_path(fake_ctx).read_text(encoding="utf-8"))
    assert [u["unit_id"] for u in saved["units"]] == ["E1U01", "E1U02"]


async def test_split_reference_video_units_derives_dialogue_without_reference_image(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """台词记号的说话人位不进参考图（画外说话的角色附参考图会诱导入画）。"""
    _rv_source(fake_ctx)
    units = [_rv_unit("门开了\n@[张三]：{我来了。}")]
    out = await _run_rv_split(fake_ctx, monkeypatch, units)
    assert out.get("is_error") is not True, out
    saved = json.loads(_rv_step1_path(fake_ctx).read_text(encoding="utf-8"))
    assert _derived_reference_names(fake_ctx, saved["units"][0]["text"]) == []


async def test_split_reference_video_units_rejects_unregistered_asset(fake_ctx: ToolContext, monkeypatch) -> None:
    """正文引用未登记资产名 → fail-loud，不写盘（资产名引用完整性）。"""
    _rv_source(fake_ctx)
    out = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[不存在的人] 出场")])
    assert out.get("is_error") is True
    assert "未登记" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


async def test_split_reference_video_units_rejects_unregistered_speaker(fake_ctx: ToolContext, monkeypatch) -> None:
    """说话人位未登记同样阻断：说话人决定该句台词绑哪段参考音频。"""
    _rv_source(fake_ctx)
    out = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("门开了\n@[无名氏]：{我来了。}")])
    assert out.get("is_error") is True
    assert "说话人未登记" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


async def test_split_reference_video_units_rejects_over_max_refs(fake_ctx: ToolContext, monkeypatch) -> None:
    _rv_source(fake_ctx)
    out = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[张三] 与 @[李四] 在 @[村口]")], max_refs=2)
    assert out.get("is_error") is True
    assert "参考图数" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


async def test_split_reference_video_units_rejects_duration_off_reference_tier(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """带 `@` 引用的 unit 取了只有无引用 unit 才合法的时长 → 判违约、不写正式文件。

    枚举卡的是两套档位的并集，这类越界过得了 schema；不在此拦，执行期才会申请不到。
    """
    _rv_source(fake_ctx)
    out = await _run_rv_split(
        fake_ctx,
        monkeypatch,
        [_rv_unit("@[张三] 起身", duration=4)],
        reference_durations=(8,),
    )
    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "生效档位" in text and "[8]" in text
    # 与其余违约类同口径落草稿：档位越界同样是 Agent 改一改草稿就能修好的内容违约
    assert not _rv_step1_path(fake_ctx).exists()
    assert [v["code"] for v in _read_rv_quarantine(fake_ctx)["violations"]] == ["duration_off_tier"]


async def test_split_reference_video_units_accepts_wide_tier_without_references(
    fake_ctx: ToolContext, monkeypatch
) -> None:
    """无 `@` 引用的 unit 不受「参考图↔时长」约束，仍可取更短的档位。"""
    _rv_source(fake_ctx)
    out = await _run_rv_split(
        fake_ctx,
        monkeypatch,
        [_rv_unit("门被风吹开", duration=4)],
        reference_durations=(8,),
    )
    assert out.get("is_error") is not True, out
    saved = json.loads(_rv_step1_path(fake_ctx).read_text(encoding="utf-8"))
    assert saved["units"][0]["duration_seconds"] == 4
    assert _derived_reference_names(fake_ctx, saved["units"][0]["text"]) == []


async def test_split_reference_video_units_rejects_out_of_enum_duration(fake_ctx: ToolContext, monkeypatch) -> None:
    """本地校验复用动态 schema：超出 supported_durations 的 unit 时长被拦截，不落盘。"""
    _rv_source(fake_ctx)
    out = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[张三] 起身", duration=5)])
    assert out.get("is_error") is True
    assert "step1 拆分内容结构校验失败" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


async def test_split_reference_video_units_rejects_empty_units(fake_ctx: ToolContext, monkeypatch) -> None:
    _rv_source(fake_ctx)
    out = await _run_rv_split(fake_ctx, monkeypatch, [])
    assert out.get("is_error") is True
    assert not _rv_step1_path(fake_ctx).exists()


async def test_split_reference_video_units_rejects_non_verbatim_source_text(fake_ctx: ToolContext, monkeypatch) -> None:
    """source_text 非源文逐字子串 → 响亮失败（模型转述 / 杜撰原文）。"""
    _rv_source(fake_ctx)
    units = [_rv_unit("@[张三] 起身", source_text="张三在城里等人")]
    out = await _run_rv_split(fake_ctx, monkeypatch, units)
    assert out.get("is_error") is True
    assert "不是小说原文的逐字片段" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


async def test_split_reference_video_units_accepts_source_text_substring(fake_ctx: ToolContext, monkeypatch) -> None:
    """锚只需是源文子串：unit 是画面单元，不必覆盖整段原文。"""
    _rv_source(fake_ctx)
    units = [_rv_unit("@[张三] 起身", source_text="张三在村口")]
    out = await _run_rv_split(fake_ctx, monkeypatch, units)
    assert out.get("is_error") is not True, out


async def test_split_reference_video_units_rejects_dialogue_overload(fake_ctx: ToolContext, monkeypatch) -> None:
    """台词量按语速估算超过 unit 时长（宽容系数外）→ 阻断。"""
    _rv_source(fake_ctx)
    long_line = "这是一段非常长的台词" * 6  # 60 字，zh 语速 5 字/秒 → 约 12 秒
    units = [_rv_unit(f"@[张三] 起身\n@[张三]：{{{long_line}}}", duration=4)]
    out = await _run_rv_split(fake_ctx, monkeypatch, units)
    assert out.get("is_error") is True
    assert "超过该 unit" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


async def test_split_reference_video_units_rejects_braces_in_description(fake_ctx: ToolContext, monkeypatch) -> None:
    """画面描述误用花括号保留语法 → 阻断（没被识别成发声记号的花括号须响亮失败）。"""
    _rv_source(fake_ctx)
    out = await _run_rv_split(fake_ctx, monkeypatch, [_rv_unit("@[张三] 推门，音量 {}，转身离开")])
    assert out.get("is_error") is True
    assert "花括号" in out["content"][0]["text"]
    assert not _rv_step1_path(fake_ctx).exists()


async def test_split_reference_video_units_no_source(fake_ctx: ToolContext) -> None:
    tool_obj = split_reference_video_units_tool(fake_ctx)
    out = await _call(tool_obj, {"episode": 1})
    assert out.get("is_error") is True
