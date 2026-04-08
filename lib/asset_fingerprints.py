"""Asset file fingerprinting — mtime-based content-addressed cache support"""

from pathlib import Path

# Media subdirectories to scan
_MEDIA_SUBDIRS = ("storyboards", "videos", "thumbnails", "characters", "clues")

# Known media file suffixes in the root directory (e.g. style reference images)
_ROOT_MEDIA_SUFFIXES = frozenset((".png", ".jpg", ".jpeg", ".webp", ".mp4"))


def _scan_subdir(prefix: str, dir_path: Path, fingerprints: dict[str, int]) -> None:
    """Scan a single media subdirectory and its first-level subdirectories (skipping versions/ directory)."""
    for entry in dir_path.iterdir():
        if entry.is_file():
            fingerprints[f"{prefix}/{entry.name}"] = entry.stat().st_mtime_ns
        elif entry.is_dir() and entry.name != "versions":
            sub_prefix = f"{prefix}/{entry.name}"
            for sub_entry in entry.iterdir():
                if sub_entry.is_file():
                    fingerprints[f"{sub_prefix}/{sub_entry.name}"] = sub_entry.stat().st_mtime_ns


def compute_asset_fingerprints(project_path: Path) -> dict[str, int]:
    """
    Scan all media files under the project directory and return a {relative_path: mtime_ns_int} mapping.

    mtime_ns is a nanosecond-precision integer used as URL cache-bust parameter, more precise than seconds.
    For ~50 files, runtime is <1ms (reads only filesystem metadata).
    """
    fingerprints: dict[str, int] = {}

    for subdir in _MEDIA_SUBDIRS:
        dir_path = project_path / subdir
        if dir_path.is_dir():
            _scan_subdir(subdir, dir_path, fingerprints)

    # Media files in the root directory (e.g. style_reference.png)
    for f in project_path.iterdir():
        if f.is_file() and f.suffix.lower() in _ROOT_MEDIA_SUFFIXES:
            fingerprints[f.name] = f.stat().st_mtime_ns

    return fingerprints
