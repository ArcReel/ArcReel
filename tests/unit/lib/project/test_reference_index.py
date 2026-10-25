"""引用索引：按项目数据给任意路径定角色，并判断资源是否仍在项目中。"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from lib.artifacts.artifact_manifest import MANIFEST_FILENAME, encode_artifact_key_parts
from lib.infra.content_digest import HASH_ALGORITHM
from lib.project.reference_index import FileOwner, FileReference, FileRole, ReferenceIndex
from lib.project.reference_library import publish_reference_context, register_pdf_reference

_DIGEST = "sha256-v1:" + "0" * 64


def test_reference_editions_are_protected_without_loading_old_context(tmp_path: Path) -> None:
    project = tmp_path / "project"
    source = tmp_path / "handoff.pdf"
    source.write_bytes(b"%PDF-first")
    old = register_pdf_reference(project, "world", source)
    old = publish_reference_context(project, "world", expected_revision=old.latest, context="Old map")
    source.write_bytes(b"%PDF-second")
    current = register_pdf_reference(project, "world", source)
    current = publish_reference_context(project, "world", expected_revision=current.latest, context="Current map")
    prefix = "source/reference-library"
    index = ReferenceIndex.build(project)

    assert index.role_of(f"{prefix}/index.json") is FileRole.STRUCTURE
    assert index.role_of(f"{prefix}/world/{current.latest}/original.pdf") is FileRole.REFERENCED
    assert index.role_of(f"{prefix}/world/{current.latest}/context-{current.context_digest}.md") is FileRole.REFERENCED
    assert index.role_of(f"{prefix}/world/{old.latest}/original.pdf") is FileRole.HISTORY
    assert index.role_of(f"{prefix}/world/{old.latest}/context-{old.context_digest}.md") is FileRole.HISTORY
    assert index.role_of(f"{prefix}/unregistered/original.pdf") is FileRole.UNREFERENCED


def test_asset_descriptive_strings_are_not_file_references(tmp_path: Path) -> None:
    _write(
        tmp_path / "project.json",
        {
            "characters": {
                "Narrator": {
                    "voice_style": "A natural conversational voice. " * 20,
                    "reference_audio": "characters/voice.wav",
                    "voice_notice_dismissed_at": "unused.txt",
                }
            },
            "products": {"Book": {"brand": "unused.txt", "reference_images": ["products/book.png"]}},
        },
    )
    index = ReferenceIndex.build(tmp_path)

    assert index.role_of("characters/voice.wav") is FileRole.REFERENCED
    assert index.role_of("products/book.png") is FileRole.REFERENCED
    assert index.role_of("unused.txt") is FileRole.UNREFERENCED


def _write(path: Path, content: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def indexed_project_dir(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    _write(
        root / "project.json",
        {
            "title": "demo",
            "characters": {
                "阿青": {
                    "character_sheet": "characters/阿青.png",
                    "reference_audio": "characters/refs_audio/阿青.wav",
                    "derivatives": {"少年": {"character_sheet": "characters/derivatives/阿青/少年.png"}},
                }
            },
            "scenes": {"古庙": {"scene_sheet": "scenes/古庙.png"}},
            "episodes": [
                {"episode": 1, "script_file": "scripts/episode_1.json", "source_origin": "whole_source"},
                {"episode": 2, "script_file": "scripts/episode_2.json", "source_origin": "own"},
            ],
            "whole_source_files": [{"source_file": "source/novel.txt"}],
        },
    )
    _write(
        root / "scripts/episode_1.json",
        {
            "segments": [
                {
                    "segment_id": "E1S01",
                    "end_frame_image": "end_frames/scene_E1S01.png",
                    "generated_assets": {
                        "storyboard_image": "storyboards/scene_E1S01.png",
                        "video_clip": "videos/scene_E1S01.mp4",
                        "video_thumbnail": "thumbnails/scene_E1S01.jpg",
                        "video_uri": "https://example.com/v.mp4",
                        "grid_id": "grid_0123456789ab",
                    },
                }
            ],
        },
    )
    _write(root / "scripts/episode_2.json", {"video_units": [{"unit_id": "E2U1", "generated_assets": {}}]})
    _write(
        root / "versions/versions.json",
        {
            "storyboards": {
                "E1S01": {
                    "current_version": 2,
                    "versions": [
                        {"version": 1, "file": "versions/storyboards/E1S01_v1_20260101T000000.png"},
                        {"version": 2, "file": "versions/storyboards/E1S01_v2_20260102T000000.png"},
                    ],
                }
            },
            "characters": {
                "旧人": {
                    "current_version": 1,
                    "versions": [{"version": 1, "file": "versions/characters/旧人_v1_x.png"}],
                }
            },
        },
    )
    subtitle_key = encode_artifact_key_parts("episode-subtitle", [1, "E1S01", "post_production"])
    _write(
        root / MANIFEST_FILENAME,
        {
            "entries": {
                subtitle_key: {
                    "artifact_path": "subtitles/episode_1/E1S01.post_production.json",
                    "basis_digest": _DIGEST,
                }
            },
            "hash_algorithm": HASH_ALGORITHM,
            "schema_version": 1,
        },
    )
    return root


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        ("project.json", FileReference(FileRole.STRUCTURE, FileOwner("project"))),
        ("scripts/episode_1.json", FileReference(FileRole.STRUCTURE, FileOwner("episode", episode=1))),
        ("versions/versions.json", FileReference(FileRole.STRUCTURE)),
        (
            "grids/grid_0123456789ab.json",
            FileReference(FileRole.STRUCTURE, FileOwner("grid", id="grid_0123456789ab", episode=1)),
        ),
        (
            "edit_timelines/episode_1/tl-0123abcd.json",
            FileReference(FileRole.STRUCTURE, FileOwner("timeline", id="tl-0123abcd", episode=1)),
        ),
        (
            "characters/阿青.png",
            FileReference(FileRole.REFERENCED, FileOwner("asset", id="阿青", asset_type="character")),
        ),
        (
            "characters/refs_audio/阿青.wav",
            FileReference(FileRole.REFERENCED, FileOwner("asset", id="阿青", asset_type="character")),
        ),
        (
            "characters/derivatives/阿青/少年.png",
            FileReference(FileRole.REFERENCED, FileOwner("asset", id="阿青", asset_type="character")),
        ),
        (
            "storyboards/scene_E1S01.png",
            FileReference(FileRole.REFERENCED, FileOwner("segment", id="E1S01", episode=1)),
        ),
        ("end_frames/scene_E1S01.png", FileReference(FileRole.REFERENCED, FileOwner("segment", id="E1S01", episode=1))),
        (
            "subtitles/episode_1/E1S01.post_production.json",
            FileReference(FileRole.REFERENCED, FileOwner("episode", episode=1)),
        ),
        ("thumbnails/scene_E1S01.jpg", FileReference(FileRole.DERIVED, FileOwner("segment", id="E1S01", episode=1))),
        # 历史版本按版本历史判定，current 版本的快照同样在版本历史里并带当前标识；被删资产留下的版本仍是历史版本。
        (
            "versions/storyboards/E1S01_v1_20260101T000000.png",
            FileReference(FileRole.HISTORY, FileOwner("segment", id="E1S01", episode=1)),
        ),
        (
            "versions/storyboards/E1S01_v2_20260102T000000.png",
            FileReference(FileRole.HISTORY, FileOwner("segment", id="E1S01", episode=1), current=True),
        ),
        (
            "versions/characters/旧人_v1_x.png",
            FileReference(FileRole.HISTORY, FileOwner("asset", id="旧人", asset_type="character"), current=True),
        ),
        ("source/novel.txt", FileReference(FileRole.STRUCTURE, FileOwner("source"))),
        ("source/raw/novel.epub", FileReference(FileRole.STRUCTURE, FileOwner("source"))),
        ("source/snapshots/novel.txt", FileReference(FileRole.DERIVED, FileOwner("source"))),
        ("source/episode_1.txt", FileReference(FileRole.DERIVED, FileOwner("episode", episode=1))),
        ("source/episode_2.txt", FileReference(FileRole.STRUCTURE, FileOwner("episode", episode=2))),
        # 未纳入整本源文的源文件与它的原始上传件交给分集视图。
        ("source/draft.txt", FileReference(FileRole.UNREFERENCED, FileOwner("source"))),
        ("source/raw/draft.docx", FileReference(FileRole.UNREFERENCED, FileOwner("source"))),
        ("storyboards/scene_E1S09.png", FileReference(FileRole.UNREFERENCED)),
        ("run_video_gen.py", FileReference(FileRole.UNREFERENCED)),
        ("CLAUDE.md", FileReference(FileRole.AGENT_CONFIG)),
        (".claude/skills/own-skill/SKILL.md", FileReference(FileRole.AGENT_CONFIG)),
        (".arcreel/memory/MEMORY.md", FileReference(FileRole.MEMORY, FileOwner("memory", id="MEMORY.md"))),
        (".arcreel/source_encoding_migrated", FileReference(FileRole.INFRASTRUCTURE)),
        (".arcreel/trash/x.json", FileReference(FileRole.INFRASTRUCTURE)),
        (".project.json.lock", FileReference(FileRole.INFRASTRUCTURE)),
        (MANIFEST_FILENAME, FileReference(FileRole.INFRASTRUCTURE)),
        ("project.json.bak.v12-1790943189", FileReference(FileRole.INFRASTRUCTURE)),
        ("scripts/.episode_1.json.lock", FileReference(FileRole.INFRASTRUCTURE)),
    ],
)
def test_role_comes_from_project_references(indexed_project_dir: Path, rel: str, expected: FileReference) -> None:
    assert ReferenceIndex.build(indexed_project_dir).reference_of(rel) == expected


def test_contains_resource_tracks_project_data(indexed_project_dir: Path) -> None:
    index = ReferenceIndex.build(indexed_project_dir)

    assert index.contains_resource("characters", "阿青")
    assert index.contains_resource("character_derivatives", "阿青/少年")
    assert index.contains_resource("storyboards", "E1S01")
    assert index.contains_resource("videos", "E1S01")
    assert index.contains_resource("reference_videos", "E2U1")
    # 视频单元的旁白配音同样按单元 ID 登记。
    assert index.contains_resource("audio", "E2U1")
    assert index.contains_resource("grids", "grid_0123456789ab")
    # 只剩版本历史的资产、已移除的分镜都不在项目中。
    assert not index.contains_resource("characters", "旧人")
    assert not index.contains_resource("storyboards", "E1S09")
    assert not index.contains_resource("reference_videos", "E1S01")


def test_contains_resource_compares_asset_names_in_nfc(tmp_path: Path) -> None:
    nfd = unicodedata.normalize("NFD", "Thảo")
    (tmp_path / "project.json").write_text(
        json.dumps({"characters": {nfd: {"derivatives": {nfd: {}}}}}), encoding="utf-8"
    )
    index = ReferenceIndex.build(tmp_path)

    nfc = unicodedata.normalize("NFC", "Thảo")
    assert index.contains_resource("characters", nfc)
    assert index.contains_resource("character_derivatives", f"{nfc}/{nfc}")


def test_pending_grid_record_is_structure_before_any_reference(indexed_project_dir: Path) -> None:
    # 排队中的宫格只有 grids/<id>.json，还没有产物登记或剧本 grid_id。
    _write(indexed_project_dir / "grids/grid_fedcba987654.json", {"id": "grid_fedcba987654", "status": "pending"})

    index = ReferenceIndex.build(indexed_project_dir)

    assert index.reference_of("grids/grid_fedcba987654.json") == FileReference(
        FileRole.STRUCTURE, FileOwner("grid", id="grid_fedcba987654")
    )
    # 已有剧本引用的宫格仍带所属集。
    assert index.reference_of("grids/grid_0123456789ab.json").owner == FileOwner(
        "grid", id="grid_0123456789ab", episode=1
    )


def test_legacy_script_plan_is_structure_of_its_episode(indexed_project_dir: Path) -> None:
    # 只剩旧版 .md 规划时，分集视图仍读取它作为该集的脚本规划。
    _write(indexed_project_dir / "drafts/episode_1/script_plan_normalized_script.md", "# 规划")

    assert ReferenceIndex.build(indexed_project_dir).reference_of(
        "drafts/episode_1/script_plan_normalized_script.md"
    ) == FileReference(FileRole.STRUCTURE, FileOwner("episode", episode=1))


def test_corrupt_project_data_fails_instead_of_reporting_unreferenced(indexed_project_dir: Path) -> None:
    (indexed_project_dir / "scripts/episode_1.json").write_text("{", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        ReferenceIndex.build(indexed_project_dir)
