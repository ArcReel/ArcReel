"""Tests for reference_video_duration_resolution."""

from __future__ import annotations

from functools import partial

import pytest

from server.services.reference_video_tasks import (
    FALLBACK_UNIT_DURATION,
    ProjectDurationContext,
    default_unit_duration,
    effective_reference_durations,
)


def test_effective_reference_durations_applies_reference_constraint_only_when_images_sent():
    """参考图约束只在确实带图时施加：backend 同样只在 reference_images 非空时施加它。"""
    narrow = partial(effective_reference_durations, "gemini-aistudio", "veo-3.1-generate-preview", [4, 6, 8], "720p")
    # Veo 3.1 全局支持 [4, 6, 8]，带参考图时只接受 8 秒
    assert narrow(with_reference_images=True) == [8]
    # 无图单元（通用路径允许空 references、ad 缺图退化为纯文本）：720p 纯文本路径仍是全集
    assert narrow(with_reference_images=False) == [4, 6, 8]
    # 未登记型号（中转站 / 自定义供应商包装）无声明可依：退回原全集，不比收窄前更严
    assert effective_reference_durations(
        "gemini-aistudio", "veo-3.1-via-relay", [4, 6, 8], "720p", with_reference_images=True
    ) == [4, 6, 8]


async def test_project_video_resolution_falls_back_like_executor(monkeypatch: pytest.MonkeyPatch):
    """未显式配置分辨率时预检取 provider fallback，与执行层的 resolution_or_fallback 同源。

    停在 None 会漏掉「按 fallback 分辨率才生效」的档位约束：Veo 未配分辨率时执行层按 1080p
    下发、只接受 8 秒，预检却按全集判 6 秒为档位成员而不弹确认——成片比剧本长且没问过用户。
    """
    from server.services import reference_video_tasks as rvt

    class _FakeResolver:
        def __init__(self, *_a, **_kw):
            pass

        async def resolve_resolution(self, *_a, **_kw):
            return None

    monkeypatch.setattr(rvt, "ConfigResolver", _FakeResolver)
    assert await rvt._project_video_resolution({}, "gemini-aistudio", "veo-3.1-generate-preview") == "1080p"


async def test_resolve_project_duration_context_resolves_caps_and_resolution_once(monkeypatch: pytest.MonkeyPatch):
    """项目能力与分辨率各只解析一次：批量预检把这次结果复用给每个 unit。"""
    from server.services import reference_video_tasks as rvt

    caps_calls = 0
    resolution_calls = 0

    async def fake_caps(_project, *, degraded_to, capability=None, episode=None):
        nonlocal caps_calls
        caps_calls += 1
        return {"provider_id": "gemini-aistudio", "model": "veo-3.1-generate-preview", "supported_durations": [4, 6, 8]}

    async def fake_resolution(_project, _provider_id, _model_id):
        nonlocal resolution_calls
        resolution_calls += 1
        return "720p"

    monkeypatch.setattr(rvt, "project_video_caps", fake_caps)
    monkeypatch.setattr(rvt, "_project_video_resolution", fake_resolution)

    ctx = await rvt.resolve_project_duration_context({})

    assert caps_calls == 1
    assert resolution_calls == 1
    assert ctx == ProjectDurationContext(
        supported_durations=(4, 6, 8),
        resolution="720p",
        provider_id="gemini-aistudio",
        model_name="veo-3.1-generate-preview",
    )


async def test_resolve_project_duration_context_skips_resolution_when_no_durations(monkeypatch: pytest.MonkeyPatch):
    """档位不可解析时分辨率也不解析——空档位下分辨率约束无意义，省一趟 IO。"""
    from server.services import reference_video_tasks as rvt

    resolution_calls = 0

    async def fake_caps(_project, *, degraded_to, capability=None, episode=None):
        return {}

    async def fake_resolution(*_a, **_kw):
        nonlocal resolution_calls
        resolution_calls += 1
        return "720p"

    monkeypatch.setattr(rvt, "project_video_caps", fake_caps)
    monkeypatch.setattr(rvt, "_project_video_resolution", fake_resolution)

    ctx = await rvt.resolve_project_duration_context({})

    assert resolution_calls == 0
    assert ctx.supported_durations == ()
    assert ctx.resolution is None


def test_default_unit_duration_narrows_by_references():
    """新建 unit 若已带 references，默认时长要按参考图约束收窄——否则会给出一个立刻被
    请求投影打回、要求用户确认的默认值——两处判据须保持一致。"""
    ctx = ProjectDurationContext(
        supported_durations=(4, 6, 8),
        resolution="720p",
        provider_id="gemini-aistudio",
        model_name="veo-3.1-generate-preview",
    )
    project = {}
    # 不带参考图：720p 纯文本路径仍是全集，首档 4 秒。
    assert default_unit_duration(ctx, project, with_references=False) == 4
    # 带参考图：Veo 3.1 720p 带图仅接受 8 秒，默认值须落在这个收窄后的集合内。
    assert default_unit_duration(ctx, project, with_references=True) == 8


def test_default_unit_duration_falls_back_when_tiers_unavailable():
    """档位解析失败（supported_durations 为空）时直接退到兜底值——档位缺位下无从校验
    偏好是否可申请，不采信项目偏好。"""
    ctx = ProjectDurationContext(
        supported_durations=(),
        resolution=None,
        provider_id="gemini-aistudio",
        model_name=None,
    )
    assert default_unit_duration(ctx, {"default_duration": 120}, with_references=False) == FALLBACK_UNIT_DURATION
    assert default_unit_duration(ctx, {"default_duration": 12}, with_references=False) == FALLBACK_UNIT_DURATION


def test_default_unit_duration_takes_min_of_unordered_custom_tiers():
    """自定义供应商声明的档位可能不按升序排列（如 [8, 4]）：取最小值而非第一项，
    否则默认值会比前端下拉展示的首选项（升序排序后的最短档位）更贵。"""
    ctx = ProjectDurationContext(
        supported_durations=(8, 4),
        resolution=None,
        provider_id="gemini-aistudio",
        model_name="veo-3.1-via-relay",  # 未登记型号：不施加约束，原样传递声明顺序
    )
    assert default_unit_duration(ctx, {}, with_references=False) == 4
