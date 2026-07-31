from __future__ import annotations

import json
import tempfile
from contextlib import asynccontextmanager, contextmanager
from functools import partial
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.reference_video.errors import MissingReferenceError
from server.services.reference_video_tasks import (
    ProjectDurationContext,
    _apply_provider_constraints,
    _render_unit_prompt,
    _resolve_unit_references,
    effective_reference_durations,
    precheck_unit,
)


def _load_project_and_unit(proj_dir: Path, unit_id: str) -> tuple[dict, dict]:
    project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    script = json.loads((proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
    unit = next(u for u in script["video_units"] if u["unit_id"] == unit_id)
    return project, unit


def _write_project(tmp_path: Path) -> Path:
    project = {
        "title": "T",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "style": "s",
        "characters": {"张三": {"description": "x", "character_sheet": "characters/张三.png"}},
        "scenes": {"酒馆": {"description": "x", "scene_sheet": "scenes/酒馆.png"}},
        "props": {},
        "episodes": [{"episode": 1, "title": "E1", "script_file": "scripts/episode_1.json"}],
    }
    script = {
        "episode": 1,
        "title": "E1",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "summary": "x",
        "novel": {"title": "t", "chapter": "c"},
        "duration_seconds": 8,
        "video_units": [
            {
                "unit_id": "E1U1",
                "shots": [{"duration": 3, "text": "Shot 1 (3s): @张三 推门"}],
                "references": [
                    {"type": "character", "name": "张三"},
                    {"type": "scene", "name": "酒馆"},
                ],
                "duration_seconds": 3,
                "duration_override": False,
                "transition_to_next": "cut",
                "note": None,
                "generated_assets": {
                    "storyboard_image": None,
                    "storyboard_last_image": None,
                    "grid_id": None,
                    "grid_cell_index": None,
                    "video_clip": None,
                    "video_uri": None,
                    "status": "pending",
                },
            },
        ],
    }
    proj_dir = tmp_path / "demo"
    proj_dir.mkdir()
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    (proj_dir / "scripts").mkdir()
    (proj_dir / "scripts" / "episode_1.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    (proj_dir / "characters").mkdir()
    _TINY_PNG = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x04\x00\x00\x00\x04"
        b"\x08\x02\x00\x00\x00&\x93\t)\x00\x00\x00\x13IDATx\x9cc<\x91b\xc4\x00"
        b"\x03Lp\x16^\x0e\x00E\xf6\x01f\xac\xf5\x15\xfa\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    (proj_dir / "characters" / "张三.png").write_bytes(_TINY_PNG)
    (proj_dir / "scenes").mkdir()
    (proj_dir / "scenes" / "酒馆.png").write_bytes(_TINY_PNG)
    return proj_dir


def _wire_context(
    monkeypatch: pytest.MonkeyPatch,
    rvt,
    fake_generator,
    *,
    backend_name: str,
    backend_model: str,
    registry_provider_id: str | None = None,
    resolution_or_fallback: str = "1080p",
    resolution: str | None = None,
    max_refs: int | None = None,
    max_duration: int | None = None,
    supported_durations: tuple[int, ...] = (),
) -> None:
    """把 fake generator + video lane 值包成 GenerationContext，替换 resolve_generation_context 单点。

    executor 迁移后不再触碰 MediaGenerator 私有属性、不再手工重建 provider 身份——所有
    provider/backend 身份、能力上限、resolution 均由 GenerationContext 的 video lane 提供。
    能力上限与 resolution 的解析逻辑本身在 tests/server/test_generation_context.py 覆盖，此处
    只需喂入 lane 值验证 executor 的下游 clamp / 守卫 / 透传行为。

    ``registry_provider_id`` 缺省与 ``backend_name`` 相同（多数供应商如此）；族别名供应商
    （如 ark-agent-plan 族复用 Ark backend）两者不同，需显式区分以覆盖 registry 查表路径。
    """
    from lib.config.resolver import ProviderModel
    from server.services.generation_context import GenerationContext, VideoLaneResult

    lane = VideoLaneResult(
        provider_model=ProviderModel(provider_id=registry_provider_id or backend_name, model_id=backend_model),
        backend_name=backend_name,
        backend_model=backend_model,
        resolution=resolution,
        resolution_or_fallback=resolution_or_fallback,
        supported_durations=supported_durations,
        max_duration=max_duration,
        max_reference_images=max_refs,
    )
    ctx = GenerationContext(generator=fake_generator, video_lane=lane)

    async def _fake_resolve(*_args, **_kwargs):
        return ctx

    monkeypatch.setattr(rvt, "resolve_generation_context", _fake_resolve)


def _wire_locked_script(fake_pm: MagicMock) -> None:
    """让 fake_pm.locked_script 产出磁盘上的真实剧本 dict。

    finalize 写回 unit 资产时会在剧本中查找 unit 并在缺失时抛 KeyError，
    裸 MagicMock 的 script.get("video_units") 不是 list 会直接炸。
    """
    proj_dir = fake_pm.get_project_path.return_value

    @contextmanager
    def _locked(_name, script_file, *, validate=True):
        yield json.loads((proj_dir / script_file).read_text(encoding="utf-8"))

    fake_pm.locked_script.side_effect = _locked


def test_resolve_unit_references_maps_sheets(tmp_path: Path):
    proj_dir = _write_project(tmp_path)
    project, unit = _load_project_and_unit(proj_dir, "E1U1")
    resolved = _resolve_unit_references(project, proj_dir, unit["references"])
    assert [p.name for p in resolved] == ["张三.png", "酒馆.png"]


def test_resolve_unit_references_missing_sheet_raises(tmp_path: Path):
    proj_dir = _write_project(tmp_path)
    project, unit = _load_project_and_unit(proj_dir, "E1U1")
    # 删掉 character sheet，模拟未生成的情况
    (proj_dir / "characters" / "张三.png").unlink()
    with pytest.raises(MissingReferenceError) as excinfo:
        _resolve_unit_references(project, proj_dir, unit["references"])
    assert ("character", "张三") in excinfo.value.missing


def test_resolve_unit_references_unknown_name_raises(tmp_path: Path):
    proj_dir = _write_project(tmp_path)
    project, _ = _load_project_and_unit(proj_dir, "E1U1")
    bad_refs = [{"type": "prop", "name": "不存在的道具"}]
    with pytest.raises(MissingReferenceError) as excinfo:
        _resolve_unit_references(project, proj_dir, bad_refs)
    assert ("prop", "不存在的道具") in excinfo.value.missing


def test_render_unit_prompt_rejects_empty_shots():
    """执行层保留一道防御性空检查：提示词源是可变 script、执行期重读，结构校验上移到
    入队守卫点后仍需挡住「入队后被改空 / 在途遗留任务」漏过的空提示词，避免尾词追加后
    被当成有效 prompt 提交给付费 backend。"""
    unit = {
        "shots": [
            {"duration": 3, "text": ""},
            {"duration": 2, "text": "   "},
        ],
        "references": [{"type": "character", "name": "张三"}],
    }
    with pytest.raises(ValueError, match="empty"):
        _render_unit_prompt(unit)


def test_render_unit_prompt_replaces_mentions_in_order():
    unit = {
        "shots": [
            {"duration": 3, "text": "Shot 1 (3s): @张三 推门"},
            {"duration": 5, "text": "Shot 2 (5s): 对面的 @张三 抬眼，背景是 @酒馆"},
        ],
        "references": [
            {"type": "character", "name": "张三"},
            {"type": "scene", "name": "酒馆"},
        ],
    }
    rendered = _render_unit_prompt(unit)
    assert "[图1]" in rendered
    assert "[图2]" in rendered
    assert "@张三" not in rendered
    # Shot header 保留
    assert "Shot 1 (3s):" in rendered
    assert "Shot 2 (5s):" in rendered


@pytest.mark.unit
def test_apply_provider_constraints_over_largest_slot_requests_largest_and_clamps_refs():
    # caps 由调用方从 GenerationContext 的 video lane 取得；
    # 这里直接提供 model 级档位集模拟已 resolve 的结果。
    refs = [Path(f"/tmp/ref{i}.png") for i in range(5)]
    new_refs, new_duration, warnings = _apply_provider_constraints(
        provider="gemini",
        model="veo-3.1-generate-preview",
        max_refs=3,
        supported_durations=[4, 6, 8],
        references=refs,
        duration_seconds=12,
    )
    assert len(new_refs) == 3
    assert new_duration == 8
    assert any("ref_duration_exceeded" in w["key"] for w in warnings)
    assert any("ref_too_many_images" in w["key"] for w in warnings)


@pytest.mark.unit
def test_apply_provider_constraints_between_slots_rounds_up():
    """区间内的非成员总时长按容量语义向上取档，不再抛 VideoCapabilityError。"""
    refs = [Path("/tmp/ref0.png")]
    _, new_duration, warnings = _apply_provider_constraints(
        provider="gemini",
        model="veo-3.1-generate-preview",
        max_refs=3,
        supported_durations=[4, 8, 12],
        references=refs,
        duration_seconds=5,
    )
    assert new_duration == 8
    assert [w["key"] for w in warnings] == ["ref_duration_rounded_up"]


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
async def test_resolve_project_duration_context_resolves_caps_and_resolution_once(monkeypatch: pytest.MonkeyPatch):
    """项目能力与分辨率各只解析一次：批量预检把这次结果复用给每个 unit（见 precheck_unit）。"""
    from server.services import reference_video_tasks as rvt

    caps_calls = 0
    resolution_calls = 0

    async def fake_caps(_project, *, degraded_to):
        nonlocal caps_calls
        caps_calls += 1
        return {"provider_id": "gemini-aistudio", "model": "veo-3.1-generate-preview", "supported_durations": [4, 6, 8]}

    async def fake_resolution(_project, _provider_id, _model_id):
        nonlocal resolution_calls
        resolution_calls += 1
        return "720p"

    monkeypatch.setattr(rvt, "_project_video_caps", fake_caps)
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


@pytest.mark.unit
async def test_resolve_project_duration_context_skips_resolution_when_no_durations(monkeypatch: pytest.MonkeyPatch):
    """档位不可解析时分辨率也不解析——空档位下分辨率约束无意义，省一趟 IO。"""
    from server.services import reference_video_tasks as rvt

    resolution_calls = 0

    async def fake_caps(_project, *, degraded_to):
        return {}

    async def fake_resolution(*_a, **_kw):
        nonlocal resolution_calls
        resolution_calls += 1
        return "720p"

    monkeypatch.setattr(rvt, "_project_video_caps", fake_caps)
    monkeypatch.setattr(rvt, "_project_video_resolution", fake_resolution)

    ctx = await rvt.resolve_project_duration_context({})

    assert resolution_calls == 0
    assert ctx.supported_durations == ()
    assert ctx.resolution is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("total_seconds", "with_references", "expected_seconds", "expected_adjustment"),
    [
        (8, True, 8, "exact"),
        (5, True, 8, "up"),
        (20, True, 8, "down"),
        (5, False, 6, "up"),
    ],
)
def test_precheck_unit_is_pure_and_matches_slot_semantics(
    total_seconds, with_references, expected_seconds, expected_adjustment
):
    """precheck_unit 不触 DB，按 ctx 里已解析好的档位/分辨率为单个 unit 取档；带图/不带图
    条件档位求交（Veo 3.1 720p 带图仅 8 秒、不带图仍是全集）与容量语义（exact/up/down）
    行为与重构前一致。"""
    ctx = ProjectDurationContext(
        supported_durations=(4, 6, 8),
        resolution="720p",
        provider_id="gemini-aistudio",
        model_name="veo-3.1-generate-preview",
    )
    unit = {
        "duration_seconds": total_seconds,
        "references": [{"type": "character", "name": "张三"}] if with_references else [],
    }
    slot = precheck_unit(ctx, unit, None)
    assert slot.seconds == expected_seconds
    assert slot.adjustment == expected_adjustment


@pytest.mark.unit
def test_precheck_unit_unconstrained_when_context_has_no_durations():
    """能力不可解析（ctx.supported_durations 为空）时原样透传，沿用现状放行不弹确认。"""
    ctx = ProjectDurationContext(supported_durations=(), resolution=None, provider_id="", model_name=None)
    unit = {"duration_seconds": 7, "references": []}
    slot = precheck_unit(ctx, unit, None)
    assert slot.seconds == 7
    assert slot.adjustment == "unconstrained"
    assert slot.needs_confirmation is False


@pytest.mark.unit
def test_apply_provider_constraints_narrows_by_call_conditions():
    """执行层取档前按本次调用条件收窄：带图 5 秒取 8（而非执行期必被拒的 6），无图仍取 6。"""
    ref = Path(tempfile.gettempdir()) / "ref0.png"
    _, with_images, _ = _apply_provider_constraints(
        provider="gemini",
        model="veo-3.1-generate-preview",
        max_refs=3,
        supported_durations=[4, 6, 8],
        references=[ref],
        duration_seconds=5,
        registry_provider_id="gemini-aistudio",
        resolution="720p",
    )
    assert with_images == 8
    _, without_images, _ = _apply_provider_constraints(
        provider="gemini",
        model="veo-3.1-generate-preview",
        max_refs=3,
        supported_durations=[4, 6, 8],
        references=[],
        duration_seconds=5,
        registry_provider_id="gemini-aistudio",
        resolution="720p",
    )
    assert without_images == 6


def test_apply_provider_constraints_sora_single_ref():
    refs = [Path(f"/tmp/ref{i}.png") for i in range(3)]
    new_refs, _, warnings = _apply_provider_constraints(
        provider="openai",
        model="sora-2",
        max_refs=1,
        supported_durations=[4, 8, 12],
        references=refs,
        duration_seconds=8,
    )
    assert len(new_refs) == 1
    assert any("ref_sora_single_ref" in w["key"] for w in warnings)


def test_apply_provider_constraints_ark_keeps_nine():
    refs = [Path(f"/tmp/ref{i}.png") for i in range(9)]
    new_refs, new_duration, warnings = _apply_provider_constraints(
        provider="ark",
        model="doubao-seedance-2-0-260128",
        max_refs=9,
        supported_durations=list(range(1, 16)),
        references=refs,
        duration_seconds=12,
    )
    assert len(new_refs) == 9
    assert new_duration == 12
    assert warnings == []


def test_apply_provider_constraints_none_caps_skip_clamp():
    """当 ConfigResolver 解析失败（例如无 DB 的 CI 环境），调用方传 None / 空档位集 →
    不裁剪任何维度、时长原样透传，把决策推到 backend 自己去报错。"""
    refs = [Path(f"/tmp/ref{i}.png") for i in range(5)]
    new_refs, new_duration, warnings = _apply_provider_constraints(
        provider="grok",
        model="grok-imagine-video",
        max_refs=None,
        supported_durations=[],
        references=refs,
        duration_seconds=30,
    )
    assert new_refs == refs
    assert new_duration == 30
    assert warnings == []


def test_apply_provider_constraints_custom_provider_model_granular():
    """Custom provider 场景：档位集由自定义 model.supported_durations 决定，
    无需 PROVIDER_MAX_DURATION 常量查表。传入 duration=18 超过最大档位 → 按 10 申请。"""
    refs = [Path(f"/tmp/ref{i}.png") for i in range(2)]
    new_refs, new_duration, warnings = _apply_provider_constraints(
        provider="custom-openai",
        model="my-custom-video",
        max_refs=9,
        supported_durations=[4, 8, 10],
        references=refs,
        duration_seconds=18,
    )
    assert new_refs == refs
    assert new_duration == 10
    assert any(w["key"] == "ref_duration_exceeded" for w in warnings)
    assert not any(w["key"] == "ref_too_many_images" for w in warnings)


@pytest.mark.asyncio
async def test_execute_reference_video_task_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    proj_dir = _write_project(tmp_path)

    # Patch project_manager helpers
    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir

    def fake_load_script(_project_name, _filename):
        return json.loads((proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))

    fake_pm.load_script.side_effect = fake_load_script
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    # Mock generator.generate_video_async: 创建伪视频文件
    async def _fake_generate_video_async(**kwargs):
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00\x00 ftypmp42")
        # (output_path, version, video_ref, video_uri)
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    _wire_context(monkeypatch, rvt, fake_generator, backend_name="ark", backend_model="doubao-seedance-2-0-260128")

    # Patch thumbnail extractor → success
    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    result = await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )
    assert result["resource_type"] == "reference_videos"
    assert result["resource_id"] == "E1U1"
    assert result["file_path"].endswith("E1U1.mp4")


@pytest.mark.asyncio
async def test_execute_reference_video_task_clears_stale_video_uri_and_thumbnail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """重跑时新结果不含 video_uri 且缩略图提取失败 → 旧 video_uri / video_thumbnail 必须被清空，
    不能保留指向过期 URI / 已删除文件的旧值。"""
    proj_dir = _write_project(tmp_path)

    # 预置上一次成功生成留下的旧产物
    script_path = proj_dir / "scripts" / "episode_1.json"
    script_data = json.loads(script_path.read_text(encoding="utf-8"))
    ga = script_data["video_units"][0]["generated_assets"]
    ga["video_uri"] = "https://old/expired.mp4"
    ga["video_thumbnail"] = "reference_videos/thumbnails/E1U1.jpg"
    script_path.write_text(json.dumps(script_data, ensure_ascii=False), encoding="utf-8")

    from server.services import reference_video_tasks as rvt

    # locked_script 用真实 contextmanager 回写到 live_script，供断言读取
    live_script = json.loads(script_path.read_text(encoding="utf-8"))

    @contextmanager
    def _locked_script(_name, _file, *, validate=True):
        yield live_script

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(script_path.read_text(encoding="utf-8"))
    fake_pm.locked_script.side_effect = _locked_script
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    # 新后端不返回 video_uri（第 4 个元素为 None）
    async def _fake_generate_video_async(**kwargs):
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00\x00 ftypmp42")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    _wire_context(monkeypatch, rvt, fake_generator, backend_name="ark", backend_model="doubao-seedance-2-0-260128")

    # 缩略图提取失败 → thumb_rel=None
    async def _fake_extract(*_a, **_k):
        return False

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    ga_after = live_script["video_units"][0]["generated_assets"]
    assert "video_uri" not in ga_after
    assert "video_thumbnail" not in ga_after
    # 正常产物仍正确写入
    assert ga_after["video_clip"] == "reference_videos/E1U1.mp4"
    assert ga_after["status"] == "completed"


@pytest.mark.asyncio
async def test_execute_reference_video_task_grok_uses_provider_default_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Regression: Grok 视频生成必须用 720p（xai_sdk 的 VideoResolutionMap 只接受 480p/720p；
    参考视频 executor 若回退到 MediaGenerator 默认 1080p，会在 SDK 抛 `Invalid video resolution 1080p`）。
    executor 必须把 video lane 的 `resolution_or_fallback` 原样传给 generate_video_async——
    档位的解析/兜底逻辑（provider fallback、model_settings 优先级）在
    tests/server/test_generation_context.py 覆盖。
    """
    proj_dir = _write_project(tmp_path)

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00\x00 ftypmp42")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-21T22:00:00"}]}
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="grok",
        backend_model="grok-imagine-video",
        resolution_or_fallback="720p",
    )

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    assert captured.get("resolution") == "720p", (
        f"Grok executor 必须显式传 720p，否则 MediaGenerator 默认 1080p 会被 xai_sdk 拒绝。"
        f"实际收到: {captured.get('resolution')!r}"
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_execute_reference_video_task_narrows_durations_by_registry_provider_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """条件档位收窄按规范 registry provider_id 查表，不按 backend 报告的族名。

    族别名供应商（如 ark-agent-plan 族复用 Ark backend）的 backend_name 不是 registry key：
    拿它查 ModelInfo 会静默落空，收窄整个失效——3 秒剧本会取到 4 秒，而 Veo 3.1 带参考图
    只接受 8 秒，执行期必然被 backend 拒绝。
    """
    proj_dir = _write_project(tmp_path)

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00\x00 ftypmp42")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-21T22:00:00"}]}
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="ark-agent-plan",
        registry_provider_id="gemini-aistudio",
        backend_model="veo-3.1-generate-preview",
        resolution_or_fallback="720p",
        supported_durations=(4, 6, 8),
    )

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    # 3 秒剧本 + 带参考图：按 registry 声明收窄到 [8]。落空则取全集首个能装下的 4 秒。
    assert captured.get("duration_seconds") == 8


@pytest.mark.asyncio
async def test_execute_reference_video_task_missing_reference_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    proj_dir = _write_project(tmp_path)
    (proj_dir / "characters" / "张三.png").unlink()

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    with pytest.raises(MissingReferenceError):
        await rvt.execute_reference_video_task(
            "demo",
            "E1U1",
            {"script_file": "scripts/episode_1.json"},
            user_id="u1",
        )


@pytest.mark.asyncio
async def test_execute_reference_video_task_uses_real_media_generator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """回归守门：executor 必须走真实 MediaGenerator._get_output_path。

    只 mock 最外层的 VideoBackend.generate — 若未来哪次又漏注册新 resource_type
    到 lib.resource_paths，这条测试会立刻爆 ValueError。
    """
    from lib.media_generator import MediaGenerator
    from lib.version_manager import VersionManager
    from lib.video_backends.base import VideoCapabilities, VideoGenerationResult
    from server.services import reference_video_tasks as rvt

    proj_dir = _write_project(tmp_path)

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    # 只 mock 最外层：VideoBackend（唯一的真外部依赖）+ Ledger/ConfigResolver
    # （这俩摸 DB，测试无 DB）。VersionManager 用真实实现 —— 这样 VersionManager
    # 自己的白名单（RESOURCE_TYPES / EXTENSIONS）也被这条路径守住，
    # 任何一处三张注册表漏登记都会在此爆 ValueError。
    captured_requests: list = []

    class _FakeVideoBackend:
        name = "ark"
        model = "doubao-seedance-2-0-260128"
        capabilities: set = set()

        @property
        def video_capabilities(self):
            return VideoCapabilities(max_reference_images=9)

        async def generate(self, request):
            captured_requests.append(request)
            request.output_path.parent.mkdir(parents=True, exist_ok=True)
            request.output_path.write_bytes(b"\x00\x00\x00 ftypmp42")
            return VideoGenerationResult(
                video_path=request.output_path,
                provider=self.name,
                model=self.model,
                duration_seconds=request.duration_seconds,
                video_uri="uri-x",
                usage_tokens=0,
                generate_audio=False,
            )

    class _FakeLedger:
        @asynccontextmanager
        async def record(self, **_kwargs):
            class _Call:
                call_id = 1

                def success(self, _result):
                    pass

            yield _Call()

    class _FakeConfigResolver:
        async def video_generate_audio(self, _project_name=None):
            return False

        async def reference_payload_limits(self, _provider_id=None):
            from lib.config.service import (
                _DEFAULT_REFERENCE_SINGLE_MAX_BYTES,
                _DEFAULT_REFERENCE_TOTAL_MAX_BYTES,
            )

            return _DEFAULT_REFERENCE_TOTAL_MAX_BYTES, _DEFAULT_REFERENCE_SINGLE_MAX_BYTES

    # object.__new__ 绕过 MediaGenerator.__init__（避开 __init__ 里的 Ledger 对 DB 的初始化）
    real_gen = object.__new__(MediaGenerator)
    real_gen.project_path = proj_dir
    real_gen.project_name = "demo"
    real_gen._rate_limiter = None
    real_gen._image_backend = None
    real_gen._video_backend = _FakeVideoBackend()
    real_gen._user_id = "u1"
    real_gen._config = _FakeConfigResolver()
    real_gen._image_provider_id = None
    real_gen._video_provider_id = None
    real_gen.versions = VersionManager(proj_dir)
    real_gen.ledger = _FakeLedger()

    _wire_context(monkeypatch, rvt, real_gen, backend_name="ark", backend_model="doubao-seedance-2-0-260128")

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    result = await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    # Backend 被真实调用一次，且 output_path 走 resource_relative_path("reference_videos", ...) 模板
    assert len(captured_requests) == 1
    req = captured_requests[0]
    assert req.output_path == (proj_dir / "reference_videos" / "E1U1.mp4")
    # 真实文件落盘
    assert (proj_dir / "reference_videos" / "E1U1.mp4").exists()
    assert result["file_path"] == "reference_videos/E1U1.mp4"
    assert result["video_uri"] == "uri-x"
    # 真实 VersionManager 闭环：版本文件落入 versions/reference_videos/
    version_dir = proj_dir / "versions" / "reference_videos"
    assert version_dir.exists()
    assert any(p.suffix == ".mp4" for p in version_dir.iterdir())


@pytest.mark.asyncio
async def test_execute_reference_video_task_passes_source_refs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """R2V 退场回归：executor 把**源 sheet 路径**直接交给 generate_video_async（单次调用），
    压缩下沉咽喉层——不再预压缩到临时文件、不再有 R2V 层的二次压缩重试。
    """
    proj_dir = _write_project(tmp_path)

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    captured: dict = {}
    call_count = {"n": 0}

    async def _fake_generate_video_async(**kwargs):
        call_count["n"] += 1
        captured["reference_images"] = kwargs.get("reference_images")
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    _wire_context(monkeypatch, rvt, fake_generator, backend_name="grok", backend_model="grok-imagine-video")

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    result = await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )
    # 单次调用：R2V 层不再做二次压缩重试
    assert call_count["n"] == 1
    assert result["resource_id"] == "E1U1"
    # 传给咽喉层的恰是源 sheet 路径（项目目录内真实文件），而非临时压缩副本——
    # 压缩已下沉到 MediaGenerator 咽喉层
    refs = [Path(p).resolve() for p in captured["reference_images"]]
    assert refs == [
        (proj_dir / "characters" / "张三.png").resolve(),
        (proj_dir / "scenes" / "酒馆.png").resolve(),
    ]


@pytest.mark.asyncio
async def test_execute_reference_video_task_clamps_via_lane_caps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """回归守门：executor 的 duration/refs clamp 必须走 video lane 的 model 粒度 caps，
    不再走老的 PROVIDER_MAX_DURATION provider 级常量。

    lane 喂入自定义 caps (max_duration=6, max_reference_images=1)，传入
    duration_seconds=15 / 2 张 refs，期望 generate_video_async 实际收到
    duration=6 且 reference_images 只有 1 张。
    """
    proj_dir = _write_project(tmp_path)

    # 改造 unit 让它有 2 张 refs + 15s duration，便于验证 clamp
    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["duration_seconds"] = 15
    # characters 已有 张三 sheet；scenes 已有 酒馆 sheet —— refs 已是 2 张
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    # lane 喂入假 caps —— 模拟 "supported_durations=[2,4,6]", max_reference_images=1 的 custom model
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="custom-openai",
        backend_model="my-custom-video",
        max_refs=1,
        max_duration=6,
        supported_durations=(2, 4, 6),
    )

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    assert captured["duration_seconds"] == 6
    assert len(captured["reference_images"]) == 1


@pytest.mark.asyncio
async def test_execute_reference_video_task_prompt_matches_clipped_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """回归守门：prompt 里的 [图N] 索引必须与 backend 收到的 reference_images 对齐。

    原实现用整条 `unit.references` 渲染 prompt，裁剪后 [图N] 会越界（例如 5 张裁到 1 张，
    prompt 里仍出现 [图5]）。修复后应当按 `constrained_refs` 长度重新 slice references。
    """
    proj_dir = _write_project(tmp_path)

    # 新增一个道具 sheet，让 unit 拥有 3 张 refs（1 character + 1 scene + 1 prop）。
    (proj_dir / "props").mkdir()
    (proj_dir / "props" / "瓶子.png").write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x04\x00\x00\x00\x04"
        b"\x08\x02\x00\x00\x00&\x93\t)\x00\x00\x00\x13IDATx\x9cc<\x91b\xc4\x00"
        b"\x03Lp\x16^\x0e\x00E\xf6\x01f\xac\xf5\x15\xfa\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    project_path = proj_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["props"] = {"瓶子": {"description": "x", "prop_sheet": "props/瓶子.png"}}
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    # 时长取 sora supported_durations 成员（4），避免触发执行层 duration 能力守卫；本测试聚焦 refs 裁剪。
    script["video_units"][0]["shots"] = [{"duration": 4, "text": "Shot 1 (4s): @张三 在 @酒馆 拿起 @瓶子"}]
    script["video_units"][0]["duration_seconds"] = 4
    script["video_units"][0]["references"] = [
        {"type": "character", "name": "张三"},
        {"type": "scene", "name": "酒馆"},
        {"type": "prop", "name": "瓶子"},
    ]
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads(project_path.read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    # Sora 上限 1 张（provider_id=openai, model=sora-2）
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="openai",
        backend_model="sora-2",
        max_refs=1,
        max_duration=12,
        supported_durations=(4, 8, 12),
    )

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    # 3 张裁到 1 张，prompt 里只能出现 [图1]，不能出现 [图2]/[图3]
    assert len(captured["reference_images"]) == 1
    prompt = captured["prompt"]
    assert "[图1]" in prompt
    assert "[图2]" not in prompt
    assert "[图3]" not in prompt
    # 被裁掉的 @酒馆 / @瓶子 按 render_prompt_for_backend 的 "未注册保留原样" fallback 保留
    assert "@酒馆" in prompt or "@瓶子" in prompt


@pytest.mark.integration
@pytest.mark.asyncio
async def test_execute_reference_video_task_rounds_up_non_member_duration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """执行层取档：unit 总时长落在区间内但不是档位成员时，按能装下它的最小档位申请生成，
    不再抛 VideoCapabilityError；成片不裁剪，取档结果记入任务 warning。
    """
    proj_dir = _write_project(tmp_path)
    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    # 5 不是 [4,8,12] 成员 → 按 8 秒申请
    script["video_units"][0]["shots"] = [{"duration": 5, "text": "Shot 1 (5s): @张三 推门"}]
    script["video_units"][0]["duration_seconds"] = 5
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    captured: dict = {}

    async def _fake_generate_video_async(**kwargs):
        captured.update(kwargs)
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="openai",
        backend_model="sora-2",
        max_refs=9,
        max_duration=12,
        supported_durations=(4, 8, 12),
    )

    result = await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )
    assert captured["duration_seconds"] == 8
    warnings = result["warnings"]
    assert [w["key"] for w in warnings] == ["ref_duration_rounded_up"]
    assert warnings[0]["params"] == {"total": 5, "duration": 8, "model": "sora-2"}


async def test_execute_reference_video_task_persists_effective_duration_when_rounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """取档偏移剧本编排（adjustment != exact）时，effective_duration 写回 task payload，
    供 resume 路径（``server.services.resume_executor``）读到与本次实际申请一致的秒数。
    """
    proj_dir = _write_project(tmp_path)
    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["shots"] = [{"duration": 5, "text": "Shot 1 (5s): @张三 推门"}]
    script["video_units"][0]["duration_seconds"] = 5
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    async def _fake_generate_video_async(**kwargs):
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="openai",
        backend_model="sora-2",
        max_refs=9,
        max_duration=12,
        supported_durations=(4, 8, 12),
    )

    fake_queue = MagicMock()
    fake_queue.persist_effective_duration = AsyncMock()
    monkeypatch.setattr(rvt, "get_generation_queue", lambda: fake_queue)

    await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
        task_id="task-1",
    )

    fake_queue.persist_effective_duration.assert_awaited_once_with("task-1", 8)


async def test_execute_reference_video_task_persists_duration_when_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """未偏移（adjustment == exact）时同样要写回：入队 payload 从不携带 duration_seconds，
    不写回会让 resume 回退到 project.default_duration 而非该 unit 自己的时长。"""
    proj_dir = _write_project(tmp_path)

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    async def _fake_generate_video_async(**kwargs):
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="openai",
        backend_model="sora-2",
        max_refs=9,
        max_duration=12,
        supported_durations=(3, 8, 12),
    )

    fake_queue = MagicMock()
    fake_queue.persist_effective_duration = AsyncMock()
    monkeypatch.setattr(rvt, "get_generation_queue", lambda: fake_queue)

    await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
        task_id="task-1",
    )

    fake_queue.persist_effective_duration.assert_awaited_once_with("task-1", 3)


def test_apply_unit_video_assets_distinguishes_failures():
    """结构损坏与 unit 不存在抛不同异常：还原侧据此区分「脏脚本告警」与「正常跳过」。

    结构损坏的两类异常会经 upload_unit_video 路由回传终端用户，故须带具体 i18n key
    （默认兜底 key 会让 en/vi 用户只看到无信息的通用句）。
    """
    from lib.script_editor import ScriptEditError
    from server.services.reference_video_tasks import apply_unit_video_assets

    with pytest.raises(ScriptEditError) as unit_lists_broken:
        apply_unit_video_assets({"video_units": "broken"}, "E1U1", video_uri=None, thumb_rel=None)
    assert unit_lists_broken.value.key == "script_edit_unit_lists_invalid"
    with pytest.raises(ScriptEditError) as unit_lists_missing:
        apply_unit_video_assets({}, "E1U1", video_uri=None, thumb_rel=None)
    assert unit_lists_missing.value.key == "script_edit_unit_lists_invalid"
    with pytest.raises(ScriptEditError) as assets_broken:
        apply_unit_video_assets(
            {"video_units": [{"unit_id": "E1U1", "generated_assets": "broken"}]},
            "E1U1",
            video_uri=None,
            thumb_rel=None,
        )
    assert assets_broken.value.key == "script_edit_generated_assets_invalid"
    with pytest.raises(KeyError):
        apply_unit_video_assets({"video_units": []}, "E1U1", video_uri=None, thumb_rel=None)

    script = {"video_units": [{"unit_id": "E1U1", "generated_assets": {"video_uri": "https://old"}}]}
    apply_unit_video_assets(script, "E1U1", video_uri=None, thumb_rel="reference_videos/thumbnails/E1U1.jpg")
    ga = script["video_units"][0]["generated_assets"]
    assert ga["video_clip"] == "reference_videos/E1U1.mp4"
    assert "video_uri" not in ga
    assert ga["video_thumbnail"] == "reference_videos/thumbnails/E1U1.jpg"
    assert ga["status"] == "completed"


def test_apply_unit_video_assets_stamps_video_generated_at():
    """每次写回 video_clip 都机械戳 video_generated_at（#1492 存量过渡横幅计数依据）。"""
    from server.services.reference_video_tasks import apply_unit_video_assets

    script = {"video_units": [{"unit_id": "E1U1", "generated_assets": {}}]}
    apply_unit_video_assets(script, "E1U1", video_uri=None, thumb_rel=None)
    first_stamp = script["video_units"][0]["generated_assets"]["video_generated_at"]
    assert isinstance(first_stamp, str) and first_stamp

    # 重新生成（第二次写回）必须刷新时间戳，不能沿用旧值
    apply_unit_video_assets(script, "E1U1", video_uri=None, thumb_rel=None)
    second_stamp = script["video_units"][0]["generated_assets"]["video_generated_at"]
    assert isinstance(second_stamp, str) and second_stamp


def test_apply_unit_video_assets_honors_explicit_generated_at():
    """版本还原传入被还原版本的原始入库时间，不把旧内容洗成「刚生成」。"""
    from server.services.reference_video_tasks import apply_unit_video_assets

    script = {"video_units": [{"unit_id": "E1U1", "generated_assets": {}}]}
    apply_unit_video_assets(script, "E1U1", video_uri=None, thumb_rel=None)

    restored_at = "2020-01-01T00:00:00+00:00"
    apply_unit_video_assets(script, "E1U1", video_uri=None, thumb_rel=None, generated_at=restored_at)
    assert script["video_units"][0]["generated_assets"]["video_generated_at"] == restored_at
