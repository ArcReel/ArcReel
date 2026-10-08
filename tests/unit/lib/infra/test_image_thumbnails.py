"""按需图片缩略图：宽度吸附、WebP 编码、原图回退与磁盘缓存。"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from lib.infra.image_thumbnails import (
    ensure_image_thumbnail,
    is_thumbnail_source,
    snap_thumbnail_width,
    thumbnail_cache_path,
)

_EXIF_ORIENTATION = 0x0112


def _write_image(path: Path, size: tuple[int, int], *, fmt: str = "PNG", mode: str = "RGB", exif=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    color = (255, 0, 0, 128) if mode == "RGBA" else (255, 0, 0)
    image = Image.new(mode, size, color)
    kwargs = {"exif": exif} if exif is not None else {}
    image.save(path, format=fmt, **kwargs)
    return path


def _thumb(source: Path, cache_dir: Path, width: int, key: str = "storyboards/a.png"):
    return ensure_image_thumbnail(source, source.stat(), cache_dir=cache_dir, source_key=key, width=width)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("160", 160),
        ("320", 320),
        ("640", 640),
        ("1280", 1280),
        ("1", 160),
        ("161", 320),
        ("700", 1280),
        ("5000", 1280),
        ("9" * 5000, 1280),
    ],
)
def test_positive_width_snaps_up_to_allowed_width(raw: str, expected: int) -> None:
    assert snap_thumbnail_width(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "0", "-320", "abc", "320px", "3.5", " 320", "３２０"])
def test_invalid_width_is_ignored(raw: str | None) -> None:
    assert snap_thumbnail_width(raw) is None


@pytest.mark.parametrize(
    ("name", "expected"),
    [("a.png", True), ("a.JPG", True), ("a.jpeg", True), ("a.webp", True), ("a.mp4", False), ("a.wav", False)],
)
def test_only_raster_images_are_thumbnail_sources(name: str, expected: bool) -> None:
    assert is_thumbnail_source(Path(name)) is expected


def test_resizes_to_target_width_as_webp(tmp_path: Path) -> None:
    source = _write_image(tmp_path / "src" / "a.png", (1000, 500))

    result = _thumb(source, tmp_path / "cache", 320)

    assert result is not None
    path, stat_result = result
    assert stat_result.st_size == path.stat().st_size
    with Image.open(path) as thumbnail:
        assert thumbnail.format == "WEBP"
        assert thumbnail.size == (320, 160)


def test_keeps_alpha_channel(tmp_path: Path) -> None:
    source = _write_image(tmp_path / "src" / "a.png", (800, 800), mode="RGBA")

    result = _thumb(source, tmp_path / "cache", 160)

    assert result is not None
    with Image.open(result[0]) as thumbnail:
        assert thumbnail.mode == "RGBA"


def test_jpeg_source_is_resized(tmp_path: Path) -> None:
    source = _write_image(tmp_path / "src" / "a.jpg", (2000, 1000), fmt="JPEG")

    result = _thumb(source, tmp_path / "cache", 640, key="storyboards/a.jpg")

    assert result is not None
    with Image.open(result[0]) as thumbnail:
        assert thumbnail.size == (640, 320)


def test_exif_rotation_is_applied_before_comparing_width(tmp_path: Path) -> None:
    """EXIF 方向 6（顺时针 90°）：存储 1000x400，显示 400x1000。"""
    exif = Image.Exif()
    exif[_EXIF_ORIENTATION] = 6
    source = _write_image(tmp_path / "src" / "a.jpg", (1000, 400), fmt="JPEG", exif=exif.tobytes())

    assert _thumb(source, tmp_path / "cache", 640, key="a.jpg") is None
    result = _thumb(source, tmp_path / "cache", 320, key="a.jpg")
    assert result is not None
    with Image.open(result[0]) as thumbnail:
        assert thumbnail.size == (320, 800)


@pytest.mark.parametrize("size", [(320, 200), (100, 900)])
def test_original_not_wider_than_target_is_not_thumbnailed(tmp_path: Path, size: tuple[int, int]) -> None:
    source = _write_image(tmp_path / "src" / "a.png", size)
    cache_dir = tmp_path / "cache"

    assert _thumb(source, cache_dir, 320) is None
    assert not cache_dir.exists()


def test_undecodable_source_falls_back_to_original(tmp_path: Path) -> None:
    source = tmp_path / "a.png"
    source.write_bytes(b"not-an-image")

    assert _thumb(source, tmp_path / "cache", 320) is None


def test_cache_hit_reuses_file_without_decoding_source(tmp_path: Path) -> None:
    source = _write_image(tmp_path / "src" / "a.png", (1000, 500))
    first = _thumb(source, tmp_path / "cache", 320)
    assert first is not None
    source_stat = source.stat()
    # 源文件字节被换成不可解码的内容但 mtime / size 不变：命中缓存时不应再读源图
    source.write_bytes(b"x" * source_stat.st_size)
    os.utime(source, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))

    second = _thumb(source, tmp_path / "cache", 320)

    assert second is not None
    assert second[0] == first[0]
    assert second[0].read_bytes() == first[0].read_bytes()


def test_thumbnail_mtime_follows_source_so_etag_is_stable(tmp_path: Path) -> None:
    source = _write_image(tmp_path / "src" / "a.png", (1000, 500))
    os.utime(source, ns=(1_700_000_000_000_000_000, 1_700_000_000_123_456_789))

    result = _thumb(source, tmp_path / "cache", 320)

    assert result is not None
    assert result[1].st_mtime_ns == source.stat().st_mtime_ns


def test_new_source_version_replaces_old_cache_entries(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    source = _write_image(tmp_path / "src" / "a.png", (1000, 500))
    os.utime(source, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    old_small = _thumb(source, cache_dir, 160)
    old_large = _thumb(source, cache_dir, 320)
    assert old_small is not None
    assert old_large is not None

    _write_image(source, (1200, 600))
    os.utime(source, ns=(1_700_000_100_000_000_000, 1_700_000_100_000_000_000))
    new = _thumb(source, cache_dir, 320)

    assert new is not None
    assert new[0] != old_large[0]
    assert sorted(path.name for path in new[0].parent.iterdir()) == [new[0].name]


def _replace_source(source: Path, size: tuple[int, int], mtime_ns: int) -> None:
    _write_image(source, size)
    os.utime(source, ns=(mtime_ns, mtime_ns))


def test_worker_holding_a_stale_source_stat_publishes_nothing(tmp_path: Path) -> None:
    # 请求 A 拿到旧 stat 后源图被替换，请求 B 已发布新版本缩略图；A 随后才开始生成
    cache_dir = tmp_path / "cache"
    source = _write_image(tmp_path / "src" / "a.png", (1000, 500))
    os.utime(source, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    stale_stat = source.stat()
    _replace_source(source, (1200, 600), 1_700_000_100_000_000_000)
    current = _thumb(source, cache_dir, 320)
    assert current is not None

    stale = ensure_image_thumbnail(source, stale_stat, cache_dir=cache_dir, source_key="storyboards/a.png", width=320)

    assert stale is None
    assert sorted(path.name for path in current[0].parent.iterdir()) == [current[0].name]


def test_pruning_keeps_the_version_the_source_currently_has(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 请求 A 发布旧版本后、清理前，源图被替换且请求 B 发布了新版本：A 的清理不得删掉 B 的文件
    cache_dir = tmp_path / "cache"
    source = _write_image(tmp_path / "src" / "a.png", (1000, 500))
    os.utime(source, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    stale_stat = source.stat()
    newer: list[Path] = []
    real_replace = os.replace

    def _publish_then_source_changes(src, dst) -> None:
        real_replace(src, dst)
        monkeypatch.setattr(os, "replace", real_replace)
        _replace_source(source, (1200, 600), 1_700_000_100_000_000_000)
        result = _thumb(source, cache_dir, 320)
        assert result is not None
        newer.append(result[0])

    monkeypatch.setattr(os, "replace", _publish_then_source_changes)

    ensure_image_thumbnail(source, stale_stat, cache_dir=cache_dir, source_key="storyboards/a.png", width=320)

    assert newer[0].exists()


def test_widths_of_the_same_version_coexist(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    source = _write_image(tmp_path / "src" / "a.png", (1000, 500))

    small = _thumb(source, cache_dir, 160)
    large = _thumb(source, cache_dir, 640)

    assert small is not None
    assert large is not None
    assert small[0].exists()
    assert large[0].exists()


def test_cache_key_separates_source_paths(tmp_path: Path) -> None:
    source = _write_image(tmp_path / "src" / "a.png", (1000, 500))
    source_stat = source.stat()

    first = thumbnail_cache_path(tmp_path, "storyboards/a.png", source_stat, 320)
    second = thumbnail_cache_path(tmp_path, "scenes/a.png", source_stat, 320)

    assert first != second
    assert first.is_relative_to(tmp_path)
    assert second.is_relative_to(tmp_path)


def test_unwritable_cache_falls_back_to_original(tmp_path: Path) -> None:
    source = _write_image(tmp_path / "src" / "a.png", (1000, 500))
    blocker = tmp_path / "cache"
    blocker.write_bytes(b"")  # 缓存根是一个文件，目录建不出来

    assert _thumb(source, blocker, 320) is None


def test_no_temp_files_left_after_write(tmp_path: Path) -> None:
    source = _write_image(tmp_path / "src" / "a.png", (1000, 500))

    result = _thumb(source, tmp_path / "cache", 320)

    assert result is not None
    assert [path.name for path in result[0].parent.iterdir()] == [result[0].name]


def test_concurrent_generation_of_the_same_key_yields_one_complete_file(tmp_path: Path) -> None:
    source = _write_image(tmp_path / "src" / "a.png", (1000, 500))
    cache_dir = tmp_path / "cache"
    barrier = threading.Barrier(8)

    def _generate(_index: int):
        barrier.wait()
        return _thumb(source, cache_dir, 320)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_generate, range(8)))

    assert all(result is not None for result in results)
    paths = {result[0] for result in results if result is not None}
    assert len(paths) == 1
    (path,) = paths
    assert [entry.name for entry in path.parent.iterdir()] == [path.name]
    with Image.open(path) as thumbnail:
        thumbnail.load()
        assert thumbnail.size == (320, 160)


def test_webp_source_is_resized(tmp_path: Path) -> None:
    buffer = BytesIO()
    Image.new("RGB", (900, 300), (0, 255, 0)).save(buffer, format="WEBP")
    source = tmp_path / "a.webp"
    source.write_bytes(buffer.getvalue())

    result = _thumb(source, tmp_path / "cache", 320, key="a.webp")

    assert result is not None
    with Image.open(result[0]) as thumbnail:
        assert thumbnail.size == (320, 107)
