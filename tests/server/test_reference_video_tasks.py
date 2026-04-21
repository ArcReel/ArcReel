from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from lib.reference_video.errors import MissingReferenceError, RequestPayloadTooLargeError
from server.services.reference_video_tasks import (
    _apply_provider_constraints,
    _compress_references_to_tempfiles,
    _render_unit_prompt,
    _resolve_unit_references,
)


def _load_project_and_unit(proj_dir: Path, unit_id: str) -> tuple[dict, dict]:
    project = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    script = json.loads((proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
    unit = next(u for u in script["video_units"] if u["unit_id"] == unit_id)
    return project, unit


def _write_project(tmp_path: Path) -> Path:
    project = {
        "title": "T",
        "content_mode": "reference_video",
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
        "content_mode": "reference_video",
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


def _make_png_bytes() -> bytes:
    import io

    from PIL import Image

    img = Image.new("RGB", (3000, 2000), color=(200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_compress_references_returns_temp_paths(tmp_path: Path):
    src = tmp_path / "big.png"
    src.write_bytes(_make_png_bytes())
    temps = _compress_references_to_tempfiles([src, src])
    try:
        assert len(temps) == 2
        for p in temps:
            assert p.exists()
            assert p.stat().st_size > 0
    finally:
        for p in temps:
            p.unlink(missing_ok=True)


def test_compress_references_empty_input(tmp_path: Path):
    assert _compress_references_to_tempfiles([]) == []


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


def test_apply_provider_constraints_veo_clamps_duration_and_refs():
    refs = [Path(f"/tmp/ref{i}.png") for i in range(5)]
    new_refs, new_duration, warnings = _apply_provider_constraints(
        provider="gemini",
        model="veo-3.1-generate-preview",
        references=refs,
        duration_seconds=12,
    )
    assert len(new_refs) == 3
    assert new_duration == 8
    assert any("ref_duration_exceeded" in w["key"] for w in warnings)
    assert any("ref_too_many_images" in w["key"] for w in warnings)


def test_apply_provider_constraints_sora_single_ref():
    refs = [Path(f"/tmp/ref{i}.png") for i in range(3)]
    new_refs, _, warnings = _apply_provider_constraints(
        provider="openai",
        model="sora-2",
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
        references=refs,
        duration_seconds=12,
    )
    assert len(new_refs) == 9
    assert new_duration == 12
    assert warnings == []


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
    fake_video_backend = MagicMock()
    fake_video_backend.name = "ark"
    fake_video_backend.model = "doubao-seedance-2-0-260128"
    fake_generator._video_backend = fake_video_backend

    async def _fake_get_media_generator(*_args, **_kwargs):
        return fake_generator

    monkeypatch.setattr(rvt, "get_media_generator", _fake_get_media_generator)

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
    """回归守门：executor 必须走真实 MediaGenerator._get_output_path 白名单。

    只 mock 最外层的 VideoBackend.generate — 若未来哪次又漏注册新 resource_type
    到 OUTPUT_PATTERNS，这条测试会立刻爆 ValueError。
    参见 issue #364。
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
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    # 只 mock 最外层：VideoBackend（唯一的真外部依赖）+ UsageTracker/ConfigResolver
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
            return VideoCapabilities(reference_images=True, max_reference_images=9)

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

    class _FakeUsage:
        async def start_call(self, **_kwargs):
            return 1

        async def finish_call(self, **_kwargs):
            pass

    class _FakeConfigResolver:
        async def video_generate_audio(self, _project_name=None):
            return False

    # object.__new__ 绕过 MediaGenerator.__init__（避开 __init__ 里的 UsageTracker 对 DB 的初始化）
    real_gen = object.__new__(MediaGenerator)
    real_gen.project_path = proj_dir
    real_gen.project_name = "demo"
    real_gen._rate_limiter = None
    real_gen._image_backend = None
    real_gen._video_backend = _FakeVideoBackend()
    real_gen._user_id = "u1"
    real_gen._config = _FakeConfigResolver()
    real_gen.versions = VersionManager(proj_dir)
    real_gen.usage_tracker = _FakeUsage()

    async def _fake_get_media_generator(*_a, **_kw):
        return real_gen

    monkeypatch.setattr(rvt, "get_media_generator", _fake_get_media_generator)

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    result = await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )

    # Backend 被真实调用一次，且 output_path 走 OUTPUT_PATTERNS["reference_videos"] 模板
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
async def test_execute_reference_video_task_payload_too_large_retries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    proj_dir = _write_project(tmp_path)

    from server.services import reference_video_tasks as rvt

    fake_pm = MagicMock()
    fake_pm.load_project.return_value = json.loads((proj_dir / "project.json").read_text(encoding="utf-8"))
    fake_pm.get_project_path.return_value = proj_dir
    fake_pm.load_script.side_effect = lambda *_a: json.loads(
        (proj_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8")
    )
    monkeypatch.setattr(rvt, "get_project_manager", lambda: fake_pm)

    call_count = {"n": 0}

    async def _fake_generate_video_async(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RequestPayloadTooLargeError()
        out = proj_dir / "reference_videos" / "E1U1.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"\x00")
        return out, 1, None, None

    fake_generator = MagicMock()
    fake_generator.generate_video_async = AsyncMock(side_effect=_fake_generate_video_async)
    fake_generator.versions.get_versions.return_value = {"versions": [{"created_at": "2026-04-17T10:00:00"}]}
    fake_video_backend = MagicMock()
    fake_video_backend.name = "grok"
    fake_video_backend.model = "grok-imagine-video"
    fake_generator._video_backend = fake_video_backend

    async def _fake_get_media_generator(*_a, **_kw):
        return fake_generator

    monkeypatch.setattr(rvt, "get_media_generator", _fake_get_media_generator)

    async def _fake_extract(*_a, **_k):
        return True

    monkeypatch.setattr(rvt, "extract_video_thumbnail", _fake_extract)

    result = await rvt.execute_reference_video_task(
        "demo",
        "E1U1",
        {"script_file": "scripts/episode_1.json"},
        user_id="u1",
    )
    assert call_count["n"] == 2
    assert result["resource_id"] == "E1U1"
