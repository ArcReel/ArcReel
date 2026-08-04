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
    FALLBACK_UNIT_DURATION,
    ProjectDurationContext,
    _apply_provider_constraints,
    _render_unit_prompt,
    _resolve_ad_unit_reference_entries,
    _resolve_unit_references,
    default_unit_duration,
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
                "shots": [{"text": "@张三 推门"}],
                "references": [
                    {"type": "character", "name": "张三"},
                    {"type": "scene", "name": "酒馆"},
                ],
                "duration_seconds": 3,
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
    voice_consistency: str = "soft",
    max_reference_audio_count: int = 0,
    reference_audio_per_image: bool = False,
    requested_generate_audio: bool = True,
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
        voice_consistency=voice_consistency,  # type: ignore[arg-type]
        max_reference_audio_count=max_reference_audio_count,
        reference_audio_per_image=reference_audio_per_image,
        requested_generate_audio=requested_generate_audio,
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


@pytest.mark.unit
def test_resolve_unit_references_maps_sheets(tmp_path: Path):
    proj_dir = _write_project(tmp_path)
    project, unit = _load_project_and_unit(proj_dir, "E1U1")
    resolved = _resolve_unit_references(project, proj_dir, unit["references"])
    assert [p.name for p in resolved] == ["张三.png", "酒馆.png"]


@pytest.mark.unit
def test_resolve_unit_references_missing_sheet_raises(tmp_path: Path):
    proj_dir = _write_project(tmp_path)
    project, unit = _load_project_and_unit(proj_dir, "E1U1")
    # 删掉 character sheet，模拟未生成的情况
    (proj_dir / "characters" / "张三.png").unlink()
    with pytest.raises(MissingReferenceError) as excinfo:
        _resolve_unit_references(project, proj_dir, unit["references"])
    assert ("character", "张三") in excinfo.value.missing


@pytest.mark.unit
def test_resolve_unit_references_unknown_name_raises(tmp_path: Path):
    proj_dir = _write_project(tmp_path)
    project, _ = _load_project_and_unit(proj_dir, "E1U1")
    bad_refs = [{"type": "prop", "name": "不存在的道具"}]
    with pytest.raises(MissingReferenceError) as excinfo:
        _resolve_unit_references(project, proj_dir, bad_refs)
    assert ("prop", "不存在的道具") in excinfo.value.missing


@pytest.mark.integration
def test_resolve_unit_references_resolves_nfd_registered_name_by_nfc_reference(tmp_path: Path):
    """资产以 NFD key 登记、unit.references 存的是解析器归一后的 NFC name：解析必须仍能命中，
    否则 draft 校验放行（判「已登记」）而执行层查不到，会晚至生成时才响 MissingReferenceError。"""
    import unicodedata

    name_nfc = unicodedata.normalize("NFC", "Hiếu")
    name_nfd = unicodedata.normalize("NFD", "Hiếu")
    assert name_nfc != name_nfd

    proj_dir = _write_project(tmp_path)
    project, _ = _load_project_and_unit(proj_dir, "E1U1")
    project["characters"][name_nfd] = {"description": "x", "character_sheet": "characters/hieu.png"}
    (proj_dir / "characters" / "hieu.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    refs = [{"type": "character", "name": name_nfc}]
    resolved = _resolve_unit_references(project, proj_dir, refs)
    assert [p.name for p in resolved] == ["hieu.png"]


@pytest.mark.integration
def test_resolve_unit_references_dedupes_nfc_nfd_pair(tmp_path: Path):
    """unit.references 携带同一角色的 NFC/NFD 两条记录（PATCH 不做数组内去重）：解析须按
    类型+归一名去重为一条，否则 _apply_provider_constraints 把同一张图计入两个参考名额，
    挤掉后面一条真正不同的参考、且给 provider 发送重复图片。"""
    import unicodedata

    name_nfc = unicodedata.normalize("NFC", "Hiếu")
    name_nfd = unicodedata.normalize("NFD", "Hiếu")
    assert name_nfc != name_nfd

    proj_dir = _write_project(tmp_path)
    project, _ = _load_project_and_unit(proj_dir, "E1U1")
    project["characters"][name_nfc] = {"description": "x", "character_sheet": "characters/hieu.png"}
    (proj_dir / "characters" / "hieu.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    refs = [{"type": "character", "name": name_nfc}, {"type": "character", "name": name_nfd}]
    resolved = _resolve_unit_references(project, proj_dir, refs)
    assert [p.name for p in resolved] == ["hieu.png"]


@pytest.mark.integration
def test_resolve_ad_unit_reference_entries_dedupes_nfc_nfd_pair(tmp_path: Path):
    """同一 ad unit 内两个镜头以不同编码形式引用同一角色：归一化查找会把两条都解析到同一张
    图，_resolve_ad_unit_reference_entries 须按归一形式去重为一条，否则同一张参考图占两个
    槽位。"""
    import unicodedata

    proj_dir = _write_project(tmp_path)
    project, _ = _load_project_and_unit(proj_dir, "E1U1")
    name_nfc = unicodedata.normalize("NFC", "Hiếu")
    name_nfd = unicodedata.normalize("NFD", "Hiếu")
    assert name_nfc != name_nfd
    project["characters"][name_nfd] = {"description": "x", "character_sheet": "characters/hieu.png"}
    (proj_dir / "characters" / "hieu.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    refs = [
        {"type": "character", "name": name_nfc},
        {"type": "character", "name": name_nfd},
    ]
    entries, warnings = _resolve_ad_unit_reference_entries(project, proj_dir, refs)
    assert [e["image"].name for e in entries] == ["hieu.png"]
    assert warnings == []


@pytest.mark.integration
def test_resolve_ad_unit_reference_entries_nfd_reference_hits_nfc_registered_asset(tmp_path: Path):
    """镜头以 NFD 形式引用、资产表按 NFC 登记：解析须命中该资产的 sheet 而非软跳过，且条目的
    name/label 写归一形式——下游按名字判等（第一段主体绑定、参考音频图号对齐）才落在同一坐标系。"""
    import unicodedata

    proj_dir = _write_project(tmp_path)
    project, _ = _load_project_and_unit(proj_dir, "E1U1")
    name_nfc = unicodedata.normalize("NFC", "Hiếu")
    name_nfd = unicodedata.normalize("NFD", "Hiếu")
    assert name_nfc != name_nfd
    project["characters"][name_nfc] = {"description": "x", "character_sheet": "characters/hieu.png"}
    (proj_dir / "characters" / "hieu.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    refs = [{"type": "character", "name": name_nfd}]
    entries, warnings = _resolve_ad_unit_reference_entries(project, proj_dir, refs)

    assert warnings == []
    assert [e["image"].name for e in entries] == ["hieu.png"]
    assert entries[0]["name"] == name_nfc
    assert name_nfc in entries[0]["label"] and name_nfd not in entries[0]["label"]


@pytest.mark.integration
def test_resolve_ad_unit_reference_entries_no_false_positive_warning_for_nfd_product(tmp_path: Path):
    """产品参考在 unit.references 中以 NFD 编码出现且 sheet 存在：collect_product_references_for_names
    返回的 entries["name"] 已归一为 NFC，membership 检查须同样归一后再比较产品名是否成功注入，
    否则原始 NFD 名永远命中不了归一后的集合，误报 ref_ad_reference_skipped。"""
    import unicodedata

    proj_dir = _write_project(tmp_path)
    project, _ = _load_project_and_unit(proj_dir, "E1U1")
    name_nfd = unicodedata.normalize("NFD", "Hiếu")
    project["products"] = {name_nfd: {"description": "x", "product_sheet": "products/hieu.png"}}
    (proj_dir / "products").mkdir()
    (proj_dir / "products" / "hieu.png").write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x04\x00\x00\x00\x04"
        b"\x08\x02\x00\x00\x00&\x93\t)\x00\x00\x00\x13IDATx\x9cc<\x91b\xc4\x00"
        b"\x03Lp\x16^\x0e\x00E\xf6\x01f\xac\xf5\x15\xfa\x00\x00\x00\x00IEND\xaeB`\x82"
    )

    refs = [{"type": "product", "name": name_nfd}]
    entries, warnings = _resolve_ad_unit_reference_entries(project, proj_dir, refs)
    assert [e["image"].name for e in entries] == ["hieu.png"]
    assert warnings == []


@pytest.mark.unit
def test_render_unit_prompt_rejects_empty_shots():
    """执行层保留一道防御性空检查：提示词源是可变 script、执行期重读，结构校验上移到
    入队守卫点后仍需挡住「入队后被改空 / 在途遗留任务」漏过的空提示词，避免尾词追加后
    被当成有效 prompt 提交给付费 backend。"""
    unit = {
        "shots": [
            {"text": ""},
            {"text": "   "},
        ],
        "references": [{"type": "character", "name": "张三"}],
    }
    with pytest.raises(ValueError, match="empty"):
        _render_unit_prompt(
            unit,
            {},
            voice_consistency="soft",
            requested_generate_audio=True,
            max_reference_audio=0,
            model_id="m",
            audio_ready=set(),
        )


@pytest.mark.unit
def test_render_unit_prompt_binds_subjects_in_reference_order():
    unit = {
        "shots": [
            {"text": "镜头1：@张三 推门"},
            {"text": "镜头2：对面的 @张三 抬眼，背景是 @酒馆"},
        ],
        "references": [
            {"type": "character", "name": "张三"},
            {"type": "scene", "name": "酒馆"},
        ],
    }
    project = {"characters": {"张三": {}}, "scenes": {"酒馆": {}}}
    rendered = _render_unit_prompt(
        unit,
        project,
        voice_consistency="soft",
        requested_generate_audio=True,
        max_reference_audio=0,
        model_id="m",
        audio_ready=set(),
    )
    assert "<张三>@图片1、<酒馆>@图片2。" in rendered.prompt
    assert "@张三" not in rendered.prompt
    # [图N] 对照表已废除
    assert "[图1]" not in rendered.prompt


@pytest.mark.unit
def test_render_unit_prompt_preserves_shot_boundaries_when_shots_lack_headers():
    """经解析预览面板编辑回写的 unit，``shots[*].text`` 不带 ``镜头N：`` header——
    渲染仍须按数组结构切成对应数量的镜头，不能因裸拼接后找不到 header 被折叠成一镜头。"""
    unit = {
        "shots": [
            {"text": "@张三 推门而入。"},
            {"text": "他环顾四周。"},
            {"text": "他坐下。"},
        ],
        "references": [{"type": "character", "name": "张三"}],
    }
    project = {"characters": {"张三": {}}}
    rendered = _render_unit_prompt(
        unit,
        project,
        voice_consistency="soft",
        requested_generate_audio=True,
        max_reference_audio=0,
        model_id="m",
        audio_ready=set(),
    )
    assert "镜头1：\n<张三> 推门而入。" in rendered.prompt
    assert "镜头2：\n他环顾四周。" in rendered.prompt
    assert "镜头3：\n他坐下。" in rendered.prompt


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

    async def fake_caps(_project, *, degraded_to, episode=None):
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


@pytest.mark.unit
async def test_resolve_project_duration_context_skips_resolution_when_no_durations(monkeypatch: pytest.MonkeyPatch):
    """档位不可解析时分辨率也不解析——空档位下分辨率约束无意义，省一趟 IO。"""
    from server.services import reference_video_tasks as rvt

    resolution_calls = 0

    async def fake_caps(_project, *, degraded_to, episode=None):
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
def test_default_unit_duration_narrows_by_references_like_precheck_unit():
    """新建 unit 若已带 references，默认时长要按参考图约束收窄——否则会给出一个立刻被
    precheck_unit 打回、要求用户确认的默认值——两处判据须保持一致。"""
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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_reference_video_task_sends_reference_audio_in_prompt_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A 类 tracer bullet：请求 reference_audio_files 的顺序与第一段 @音频N 指认严格一致。"""
    proj_dir = _write_project(tmp_path)

    project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    project["characters"]["张三"]["voice_style"] = "低沉沙哑的男声"
    project["characters"]["张三"]["reference_audio"] = "characters/refs_audio/张三.wav"
    project["characters"]["李四"] = {
        "description": "x",
        "character_sheet": "characters/张三.png",
        "voice_style": "清亮少女音",
        "reference_audio": "characters/refs_audio/李四.mp3",
    }
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    refs_audio = proj_dir / "characters" / "refs_audio"
    refs_audio.mkdir(parents=True)
    (refs_audio / "张三.wav").write_bytes(b"RIFF")
    (refs_audio / "李四.mp3").write_bytes(b"ID3")

    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["shots"] = [
        {"text": "@[张三] 推门而入。\n@[李四]：{你终于来了。}\n@[张三]：{今晚的酒，我请。}"}
    ]
    script["video_units"][0]["references"] = [{"type": "character", "name": "张三"}]
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = project
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _n, _f: json.loads(script_path.read_text(encoding="utf-8"))
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
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="ark",
        backend_model="doubao-seedance-2-0-260128",
        voice_consistency="native",
        max_reference_audio_count=3,
    )

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    await rvt.execute_reference_video_task("demo", "E1U1", {"script_file": "scripts/episode_1.json"}, user_id="u1")

    prompt = captured["prompt"]
    # speaker 首现顺序 = 音频编号 = 请求字段顺序（李四先开口，尽管张三先入画）
    assert "<李四>的台词音色参考 @音频1，声音特征：清亮少女音。" in prompt
    assert "<张三>的台词音色参考 @音频2，声音特征：低沉沙哑的男声。" in prompt
    assert [p.name for p in captured["reference_audio_files"]] == ["李四.mp3", "张三.wav"]
    # speaker 位不产生参考图：李四没有 @图片N 绑定
    assert "<张三>@图片1。" in prompt
    assert "<李四>@图片" not in prompt


@pytest.mark.asyncio
@pytest.mark.integration
async def test_execute_reference_video_task_omits_reference_audio_when_episode_is_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """无声视频：即便角色配好了音色档案、模型也是 A 类，请求里也不带任何参考音频负载；
    台词文本照常下发（供应商可用作口型参考），并随结果回一条无声知会。"""
    proj_dir = _write_project(tmp_path)

    project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    project["characters"]["张三"]["voice_style"] = "低沉沙哑的男声"
    project["characters"]["张三"]["reference_audio"] = "characters/refs_audio/张三.wav"
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    refs_audio = proj_dir / "characters" / "refs_audio"
    refs_audio.mkdir(parents=True)
    (refs_audio / "张三.wav").write_bytes(b"RIFF")

    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["shots"] = [{"text": "@[张三] 推门而入。\n@[张三]：{今晚的酒，我请。}"}]
    script["video_units"][0]["references"] = [{"type": "character", "name": "张三"}]
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = project
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _n, _f: json.loads(script_path.read_text(encoding="utf-8"))
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
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="ark",
        backend_model="doubao-seedance-2-0-260128",
        voice_consistency="native",
        max_reference_audio_count=3,
        requested_generate_audio=False,
    )

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    result = await rvt.execute_reference_video_task(
        "demo", "E1U1", {"script_file": "scripts/episode_1.json"}, user_id="u1"
    )

    assert captured["reference_audio_files"] is None
    assert captured["reference_audio_targets"] is None
    prompt = captured["prompt"]
    assert "@音频" not in prompt
    # 台词与参考图绑定照常，只有音色参考行消失
    assert "<张三>说 {今晚的酒，我请。}" in prompt
    assert "<张三>@图片1。" in prompt
    assert {"key": "ref_warn_silent_episode", "params": {}} in result["warnings"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_reference_video_task_aligns_reference_audio_targets_for_per_image_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """backend 要求音频逐段挂参考图（如 wan2.7-r2v）时，reference_audio_targets 须按名字
    对齐到正确的参考图下标，纯画外角色（李四）降级不绑定——不能假设两个列表天然同序。"""
    proj_dir = _write_project(tmp_path)

    project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    project["characters"]["张三"]["voice_style"] = "低沉沙哑的男声"
    project["characters"]["张三"]["reference_audio"] = "characters/refs_audio/张三.wav"
    project["characters"]["李四"] = {
        "description": "x",
        "character_sheet": "characters/张三.png",
        "voice_style": "清亮少女音",
        "reference_audio": "characters/refs_audio/李四.mp3",
    }
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    refs_audio = proj_dir / "characters" / "refs_audio"
    refs_audio.mkdir(parents=True)
    (refs_audio / "张三.wav").write_bytes(b"RIFF")
    (refs_audio / "李四.mp3").write_bytes(b"ID3")

    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["shots"] = [
        {"text": "@[张三] 推门而入。\n@[李四]：{你终于来了。}\n@[张三]：{今晚的酒，我请。}"}
    ]
    # 场景排在张三之前：references[0]=酒馆（下标0），references[1]=张三（下标1）——
    # 音频顺序（李四先开口→张三）与图片顺序不同序，位置对齐会把音频错挂到酒馆图上。
    script["video_units"][0]["references"] = [
        {"type": "scene", "name": "酒馆"},
        {"type": "character", "name": "张三"},
    ]
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = project
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _n, _f: json.loads(script_path.read_text(encoding="utf-8"))
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
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="dashscope",
        backend_model="wan2.7-r2v",
        voice_consistency="native",
        max_reference_audio_count=5,
        reference_audio_per_image=True,
    )

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    await rvt.execute_reference_video_task("demo", "E1U1", {"script_file": "scripts/episode_1.json"}, user_id="u1")

    # 李四没有参考图（纯画外），降级不绑定：只剩张三一段音频
    assert [p.name for p in captured["reference_audio_files"]] == ["张三.wav"]
    # 张三在 references 里的 0-based 下标是 1（酒馆占 0）
    assert captured["reference_audio_targets"] == [1]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_reference_video_task_omits_audio_field_for_soft_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """B 类：无音频字段，但第一段仍带 voice_style 声音特征声明。"""
    proj_dir = _write_project(tmp_path)
    project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    project["characters"]["张三"]["voice_style"] = "低沉沙哑的男声"
    project["characters"]["张三"]["reference_audio"] = "characters/refs_audio/张三.wav"
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    refs_audio = proj_dir / "characters" / "refs_audio"
    refs_audio.mkdir(parents=True)
    (refs_audio / "张三.wav").write_bytes(b"RIFF")

    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["shots"] = [{"text": "@[张三] 推门。\n@[张三]：{我回来了。}"}]
    script["video_units"][0]["references"] = [{"type": "character", "name": "张三"}]
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = project
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _n, _f: json.loads(script_path.read_text(encoding="utf-8"))
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
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="ark",
        backend_model="doubao-seedance-1-5",
        voice_consistency="soft",
    )

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    await rvt.execute_reference_video_task("demo", "E1U1", {"script_file": "scripts/episode_1.json"}, user_id="u1")

    assert captured["reference_audio_files"] is None
    assert "<张三>的声音特征：低沉沙哑的男声。" in captured["prompt"]
    assert "@音频" not in captured["prompt"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_reference_video_task_surfaces_render_warnings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """渲染期降级 warning 与既有 result.warnings 通道贯通（超上限截断降级）。"""
    proj_dir = _write_project(tmp_path)
    project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    project["characters"]["张三"]["reference_audio"] = "characters/refs_audio/张三.wav"
    project["characters"]["李四"] = {
        "description": "x",
        "character_sheet": "characters/张三.png",
        "reference_audio": "characters/refs_audio/李四.mp3",
    }
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    refs_audio = proj_dir / "characters" / "refs_audio"
    refs_audio.mkdir(parents=True)
    (refs_audio / "张三.wav").write_bytes(b"RIFF")
    (refs_audio / "李四.mp3").write_bytes(b"ID3")

    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["shots"] = [{"text": "@[张三] 推门。\n@[张三]：{我回来了。}\n@[李四]：{你好。}"}]
    script["video_units"][0]["references"] = [{"type": "character", "name": "张三"}]
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = project
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda _n, _f: json.loads(script_path.read_text(encoding="utf-8"))
    _wire_locked_script(fake_pm)
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    async def _fake_generate_video_async(**kwargs):
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00\x00 ftypmp42")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="ark",
        backend_model="doubao-seedance-2-0-260128",
        voice_consistency="native",
        max_reference_audio_count=1,
    )

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    result = await rvt.execute_reference_video_task(
        "demo", "E1U1", {"script_file": "scripts/episode_1.json"}, user_id="u1"
    )
    assert {"key": "ref_warn_reference_audio_overflow", "params": {"limit": 1, "name": "李四"}} in result["warnings"]


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_reference_video_task_uses_real_media_generator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """executor 必须走真实 MediaGenerator._get_output_path。

    只 mock 最外层的 VideoBackend.generate ——resource_type 未注册到
    lib.resource_paths 时，这条测试会立刻爆 ValueError。
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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_reference_video_task_passes_source_refs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """executor 把**源 sheet 路径**直接交给 generate_video_async（单次调用），压缩下沉
    咽喉层——不预压缩到临时文件，不在 R2V 层做二次压缩重试。
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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_reference_video_task_clamps_via_lane_caps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """executor 的 duration/refs clamp 走 video lane 的 model 粒度 caps。

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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_reference_video_task_prompt_matches_clipped_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """prompt 里的 `@图片N` 索引必须与 backend 收到的 reference_images 对齐：references 裁剪后
    须按 `constrained_refs` 长度重新 slice，用整条 `unit.references` 渲染会让 `@图片N` 越界
    （例如 5 张裁到 1 张，prompt 里仍出现 `@图片5`）。
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
    script["video_units"][0]["shots"] = [{"text": "Shot 1 (4s): @张三 在 @酒馆 拿起 @瓶子"}]
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

    # 3 张裁到 1 张，第一段只能绑出 @图片1，不能出现 @图片2/@图片3
    assert len(captured["reference_images"]) == 1
    prompt = captured["prompt"]
    assert "@图片1" in prompt
    assert "@图片2" not in prompt
    assert "@图片3" not in prompt
    # 被裁掉的 @酒馆 / @瓶子 仍是画面主体（<X> 与图号解耦），只是没有绑定行、没随请求发图
    assert "<酒馆>" in prompt and "<瓶子>" in prompt
    assert "<酒馆>@图片" not in prompt and "<瓶子>@图片" not in prompt


@pytest.mark.integration
@pytest.mark.asyncio
async def test_execute_reference_video_task_prompt_matches_deduped_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """unit.references 携带同一角色的 NFC/NFD 两条记录时，prompt 的 `@图片N` 索引必须与去重后的
    reference_images 对齐：图片列表与渲染用的逻辑引用列表须按同一去重规则计数，否则 `@图片2`
    会错误绑到与 `@图片1` 相同的资产、后面一条真正不同的参考丢失编号。"""
    import unicodedata

    proj_dir = _write_project(tmp_path)
    name_nfc = unicodedata.normalize("NFC", "Hiếu")
    name_nfd = unicodedata.normalize("NFD", "Hiếu")
    assert name_nfc != name_nfd

    project_path = proj_dir / "project.json"
    project = json.loads(project_path.read_text(encoding="utf-8"))
    project["characters"][name_nfc] = {"description": "x", "character_sheet": "characters/hieu.png"}
    (proj_dir / "characters" / "hieu.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    project_path.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")

    script_path = proj_dir / "scripts" / "episode_1.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    script["video_units"][0]["shots"] = [{"text": f"Shot 1 (4s): @{name_nfc} 与 @酒馆"}]
    script["video_units"][0]["duration_seconds"] = 4
    script["video_units"][0]["references"] = [
        {"type": "character", "name": name_nfc},
        {"type": "character", "name": name_nfd},
        {"type": "scene", "name": "酒馆"},
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
    # max_refs=None：不做 provider 裁剪，去重本身是本测试唯一验证的效应。
    _wire_context(
        monkeypatch,
        rvt,
        fake_generator,
        backend_name="grok",
        backend_model="grok-imagine-video",
        max_refs=None,
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

    # 3 条 references（含一对 NFC/NFD 重复）去重后只剩 2 张图，图号必须与之对齐
    assert len(captured["reference_images"]) == 2
    prompt = captured["prompt"]
    assert "@图片1" in prompt
    assert "@图片2" in prompt
    assert "@图片3" not in prompt


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
    script["video_units"][0]["shots"] = [{"text": "@张三 推门"}]
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


@pytest.mark.unit
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
    script["video_units"][0]["shots"] = [{"text": "@张三 推门"}]
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


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
def test_apply_unit_video_assets_stamps_video_generated_at():
    """每次写回 video_clip 都机械戳 video_generated_at（存量过渡横幅的计数依据）。"""
    from server.services.reference_video_tasks import apply_unit_video_assets

    script = {"video_units": [{"unit_id": "E1U1", "generated_assets": {}}]}
    apply_unit_video_assets(script, "E1U1", video_uri=None, thumb_rel=None)
    first_stamp = script["video_units"][0]["generated_assets"]["video_generated_at"]
    assert isinstance(first_stamp, str) and first_stamp

    # 重新生成（第二次写回）必须刷新时间戳，不能沿用旧值
    apply_unit_video_assets(script, "E1U1", video_uri=None, thumb_rel=None)
    second_stamp = script["video_units"][0]["generated_assets"]["video_generated_at"]
    assert isinstance(second_stamp, str) and second_stamp


@pytest.mark.unit
def test_apply_unit_video_assets_honors_explicit_generated_at():
    """版本还原传入被还原版本的原始入库时间，不把旧内容洗成「刚生成」。"""
    from server.services.reference_video_tasks import apply_unit_video_assets

    script = {"video_units": [{"unit_id": "E1U1", "generated_assets": {}}]}
    apply_unit_video_assets(script, "E1U1", video_uri=None, thumb_rel=None)

    restored_at = "2020-01-01T00:00:00+00:00"
    apply_unit_video_assets(script, "E1U1", video_uri=None, thumb_rel=None, generated_at=restored_at)
    assert script["video_units"][0]["generated_assets"]["video_generated_at"] == restored_at
