"""ad 模式参考直出执行层（execute_reference_video_task 的 ad 分支）单测。

ad 剧本骨架唯一：unit 是 reference_units 轻量索引条目，成员镜头执行期从
shots（内容唯一真相）水合；产品参考按注入二元规则全量绝对优先。
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x04\x00\x00\x00\x04"
    b"\x08\x02\x00\x00\x00&\x93\t)\x00\x00\x00\x13IDATx\x9cc<\x91b\xc4\x00"
    b"\x03Lp\x16^\x0e\x00E\xf6\x01f\xac\xf5\x15\xfa\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _shot(shot_id: str, duration: int, **overrides) -> dict:
    base = {
        "shot_id": shot_id,
        "section": "hook",
        "duration_seconds": duration,
        "voiceover_text": "口播文案",
        "characters_in_shot": [],
        "scenes": [],
        "props": [],
        "products_in_shot": [],
        "image_prompt": {
            "scene": f"{shot_id} 画面",
            "composition": {"shot_type": "Close-up", "lighting": "自然光", "ambiance": "明亮"},
        },
        "video_prompt": {
            "action": f"{shot_id} 动作",
            "camera_motion": "Static",
            "ambiance_audio": "",
            "dialogue": [],
        },
        "transition_to_next": "cut",
        "generated_assets": {"status": "pending"},
    }
    base.update(overrides)
    return base


def _write_ad_project(tmp_path: Path) -> Path:
    project = {
        "title": "带货短片",
        "content_mode": "ad",
        "generation_mode": "reference_video",
        "style": "明亮写实",
        "target_duration": 30,
        "brief": "卖按摩仪",
        "characters": {"小美": {"description": "x", "character_sheet": "characters/小美.png"}},
        "scenes": {},
        "props": {},
        "products": {
            "按摩仪": {
                "description": "颈部按摩仪",
                "product_sheet": "products/按摩仪.png",
                "reference_images": ["products/按摩仪_原图.jpg"],
            }
        },
        "episodes": [{"episode": 1, "title": "短片", "script_file": "scripts/episode_1.json"}],
    }
    script = {
        "episode": 1,
        "title": "短片",
        "content_mode": "ad",
        "shots": [
            _shot("E1S1", 3, products_in_shot=["按摩仪"]),
            _shot("E1S2", 2, characters_in_shot=["小美"]),
        ],
        "reference_units": [
            {
                "unit_id": "E1U1",
                "shot_ids": ["E1S1", "E1S2"],
                "references": [
                    {"type": "product", "name": "按摩仪"},
                    {"type": "character", "name": "小美"},
                ],
                "generated_assets": {"status": "pending"},
            }
        ],
    }
    proj_dir = tmp_path / "ad-demo"
    (proj_dir / "scripts").mkdir(parents=True)
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    (proj_dir / "scripts" / "episode_1.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    (proj_dir / "characters").mkdir()
    (proj_dir / "characters" / "小美.png").write_bytes(_TINY_PNG)
    (proj_dir / "products").mkdir()
    (proj_dir / "products" / "按摩仪.png").write_bytes(_TINY_PNG)
    (proj_dir / "products" / "按摩仪_原图.jpg").write_bytes(_TINY_PNG)
    return proj_dir


def _wire_executor(proj_dir: Path, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """挂上 fake pm + fake generator，locked_script 写回磁盘以便断言 finalize 结果。"""
    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()

    def _load_project(_name):
        return json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))

    fake_pm.load_project.side_effect = _load_project
    fake_pm.get_project_path.return_value = proj_dir

    def _load_script(_name, filename):
        filename = filename.removeprefix("scripts/")
        return json.loads((proj_dir / "scripts" / filename).read_text(encoding="utf-8"))

    fake_pm.load_script.side_effect = _load_script

    @contextmanager
    def _locked(_name, script_file, *, validate=True):
        path = proj_dir / "scripts" / script_file.removeprefix("scripts/")
        script = json.loads(path.read_text(encoding="utf-8"))
        yield script
        path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    fake_pm.locked_script.side_effect = _locked
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    async def _fake_generate_video_async(**kwargs):
        resource_id = kwargs["resource_id"]
        out = proj_dir / "reference_videos" / f"{resource_id}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00\x00\x00 ftypmp42")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-06-12T10:00:00"}]}
    fake_backend = MagicMock()
    fake_backend.name = "ark"
    fake_backend.model = "seedance"
    fake_generator._video_backend = fake_backend

    async def _fake_get_media_generator(*_a, **_kw):
        return fake_generator

    monkeypatch.setattr(rvt, "get_media_generator", _fake_get_media_generator)

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)
    return fake_generator


@pytest.mark.asyncio
async def test_ad_unit_generates_video_with_inherited_references(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from server.services import reference_video_tasks as rvt

    proj_dir = _write_ad_project(tmp_path)
    fake_generator = _wire_executor(proj_dir, monkeypatch)

    result = await rvt.execute_reference_video_task(
        "ad-demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    assert result["resource_id"] == "E1U1"
    assert result["file_path"] == "reference_videos/E1U1.mp4"

    kwargs = fake_generator.generate_video_async.call_args.kwargs
    # duration = 成员镜头时长之和（2-3 秒短镜头在该路径合法）
    assert kwargs["duration_seconds"] == 5
    # 产品参考全量且绝对优先：sheet 在前、原图压阵，然后才是角色 sheet
    ref_names = [p.name for p in kwargs["reference_images"]]
    assert ref_names == ["按摩仪.png", "按摩仪_原图.jpg", "小美.png"]
    # prompt 含切镜结构与高保真指令，不含口播文案
    prompt = kwargs["prompt"]
    assert "Shot 1 (3s):" in prompt
    assert "Shot 2 (2s):" in prompt
    assert "产品高保真还原" in prompt
    assert "「按摩仪」" in prompt
    assert "口播文案" not in prompt

    # finalize 把产物写回 reference_units 索引条目
    script = json.loads((proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
    unit = script["reference_units"][0]
    assert unit["generated_assets"]["video_clip"] == "reference_videos/E1U1.mp4"
    assert unit["generated_assets"]["status"] == "completed"
    # shots 内容不被 finalize 触碰
    assert script["shots"][0]["generated_assets"]["status"] == "pending"


@pytest.mark.asyncio
async def test_ad_missing_asset_sheet_skipped_with_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """ad 参考集由分组器自动继承，缺图软跳过 + warning，不像 narration/drama 那样硬失败。"""
    from server.services import reference_video_tasks as rvt

    proj_dir = _write_ad_project(tmp_path)
    (proj_dir / "characters" / "小美.png").unlink()
    fake_generator = _wire_executor(proj_dir, monkeypatch)

    result = await rvt.execute_reference_video_task(
        "ad-demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    kwargs = fake_generator.generate_video_async.call_args.kwargs
    ref_names = [p.name for p in kwargs["reference_images"]]
    assert ref_names == ["按摩仪.png", "按摩仪_原图.jpg"]
    assert any(w["key"] == "ref_ad_reference_skipped" and w["params"]["name"] == "小美" for w in result["warnings"])


@pytest.mark.asyncio
async def test_ad_product_without_sheet_injects_originals_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from server.services import reference_video_tasks as rvt

    proj_dir = _write_ad_project(tmp_path)
    project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    del project["products"]["按摩仪"]["product_sheet"]
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    fake_generator = _wire_executor(proj_dir, monkeypatch)

    await rvt.execute_reference_video_task(
        "ad-demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    kwargs = fake_generator.generate_video_async.call_args.kwargs
    ref_names = [p.name for p in kwargs["reference_images"]]
    assert ref_names == ["按摩仪_原图.jpg", "小美.png"]


@pytest.mark.asyncio
async def test_ad_reference_clamp_keeps_product_sheets_alive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """超后端参考上限时产品 sheet 跨产品稳定前置存活，[图N] 对照表与实收列表对齐。"""
    from server.services import reference_video_tasks as rvt

    proj_dir = _write_ad_project(tmp_path)
    project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    project["products"]["精华液"] = {
        "description": "精华液",
        "product_sheet": "products/精华液.png",
        "reference_images": ["products/精华液_原图.jpg"],
    }
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    (proj_dir / "products" / "精华液.png").write_bytes(_TINY_PNG)
    (proj_dir / "products" / "精华液_原图.jpg").write_bytes(_TINY_PNG)
    script = json.loads((proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
    script["shots"][0]["products_in_shot"] = ["按摩仪", "精华液"]
    script["reference_units"][0]["references"] = [
        {"type": "product", "name": "按摩仪"},
        {"type": "product", "name": "精华液"},
        {"type": "character", "name": "小美"},
    ]
    (proj_dir / "scripts" / "episode_1.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    fake_generator = _wire_executor(proj_dir, monkeypatch)

    from server.services import reference_video_tasks as rvt_mod

    async def _fake_caps(_project):
        return {"model": "seedance", "max_reference_images": 3, "max_duration": None, "supported_durations": []}

    fake_resolver = MagicMock()
    fake_resolver.video_capabilities_for_project = AsyncMock(side_effect=_fake_caps)
    monkeypatch.setattr(rvt_mod, "ConfigResolver", lambda *_a, **_kw: fake_resolver)

    result = await rvt.execute_reference_video_task(
        "ad-demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    kwargs = fake_generator.generate_video_async.call_args.kwargs
    ref_names = [p.name for p in kwargs["reference_images"]]
    # 两个产品的 sheet 优先存活，原图与角色 sheet 被裁
    assert ref_names == ["按摩仪.png", "精华液.png", "按摩仪_原图.jpg"]
    # [图N] 对照表与裁剪后的实收列表对齐
    prompt = kwargs["prompt"]
    assert "[图1] 产品「按摩仪」标准多角度参考图" in prompt
    assert "[图3] 产品「按摩仪」实拍原图（保真锚点）" in prompt
    assert "[图4]" not in prompt
    assert any(w["key"] == "ref_too_many_images" for w in result["warnings"])


def test_clamp_zero_max_refs_drops_all_entries():
    """max_refs == 0（模型不支持参考图）裁到空集 + warning，不得当作「无上限」放行。"""
    from server.services.reference_video_tasks import _clamp_ad_reference_entries

    entries = [{"image": Path("a.png"), "label": "产品「按摩仪」标准多角度参考图", "name": "按摩仪", "kind": "sheet"}]
    clamped, warnings = _clamp_ad_reference_entries(entries, 0, provider="ark", model="seedance")

    assert clamped == []
    assert [w["key"] for w in warnings] == ["ref_too_many_images"]


def test_clamp_none_max_refs_keeps_all_entries():
    """max_refs is None（能力未解析）不裁剪，交由 backend 自行报错。"""
    from server.services.reference_video_tasks import _clamp_ad_reference_entries

    entries = [{"image": Path("a.png"), "label": "x", "name": "按摩仪", "kind": "sheet"}]
    clamped, warnings = _clamp_ad_reference_entries(entries, None, provider="ark", model="seedance")

    assert clamped == entries
    assert warnings == []


@pytest.mark.asyncio
async def test_ad_dirty_asset_bucket_skips_with_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """project.json 资产 bucket 形状损坏（非 dict）时软跳过该参考 + warning，不抛 AttributeError。"""
    from server.services import reference_video_tasks as rvt

    proj_dir = _write_ad_project(tmp_path)
    project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    project["characters"] = []
    (proj_dir / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    fake_generator = _wire_executor(proj_dir, monkeypatch)

    result = await rvt.execute_reference_video_task(
        "ad-demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    kwargs = fake_generator.generate_video_async.call_args.kwargs
    ref_names = [p.name for p in kwargs["reference_images"]]
    assert ref_names == ["按摩仪.png", "按摩仪_原图.jpg"]
    assert any(w["key"] == "ref_ad_reference_skipped" and w["params"]["name"] == "小美" for w in result["warnings"])


@pytest.mark.asyncio
async def test_ad_stale_index_fails_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """镜头被删后索引悬空 → fail-loud 提示重新派生，不静默生成残缺视频。"""
    from server.services import reference_video_tasks as rvt

    proj_dir = _write_ad_project(tmp_path)
    script = json.loads((proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
    script["shots"] = [s for s in script["shots"] if s["shot_id"] != "E1S2"]
    (proj_dir / "scripts" / "episode_1.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    _wire_executor(proj_dir, monkeypatch)

    with pytest.raises(ValueError, match="E1S2"):
        await rvt.execute_reference_video_task(
            "ad-demo",
            "E1U1",
            {"script_file": "scripts/episode_1.json"},
            user_id="u1",
        )
