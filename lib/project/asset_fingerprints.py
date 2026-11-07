"""Asset-file fingerprints: mtime-based content-addressed cache support."""

from pathlib import Path

# Scanned media subdirectories match public endpoints (``server.routers.files.PUBLIC_MEDIA_DIRS``),
# except ``renders/``: finished cuts use version keys, without cache-busting.
_MEDIA_SUBDIRS = (
    "storyboards",
    "end_frames",
    "videos",
    "thumbnails",
    "characters",
    "scenes",
    "props",
    "products",
    "grids",
    "reference_videos",
    "audio",
    "bgm",
)

# Known root-level media (e.g. style reference).
_ROOT_MEDIA_SUFFIXES = frozenset((".png", ".jpg", ".jpeg", ".webp", ".mp4"))

#: Snapshot bucket name. Snapshots already live outside ``_MEDIA_SUBDIRS`` at root ``versions/{subdir}/``
#: (``lib.project.resource_paths.version_snapshot_dir``); this exclusion
#: covers same-named directories within media. Version-addressed snapshots need no cache-busting; scanning wastes work.
_VERSIONS_DIR = "versions"


def _scan_media_tree(prefix: str, dir_path: Path, fingerprints: dict[str, int]) -> None:
    """Recursively scan all files in a media subtree, skipping ``versions/`` and directory symlinks at every depth.

    Unlimited depth covers third-level derivative sheets such as ``characters/derivatives/{本体}/{衍生}.png``.
    Do not descend directory links: targets are not project-owned media, and ancestor links would recurse
    until OS symlink-resolution limits. Match ``lib.agent.profile_manifest`` ``os.walk(followlinks=False)``."""
    for entry in dir_path.iterdir():
        if entry.is_file():
            fingerprints[f"{prefix}/{entry.name}"] = entry.stat().st_mtime_ns
        elif entry.is_dir() and not entry.is_symlink() and entry.name != _VERSIONS_DIR:
            _scan_media_tree(f"{prefix}/{entry.name}", entry, fingerprints)


def compute_asset_fingerprints(project_path: Path) -> dict[str, int]:
    """Scan project media, returning {relative path: mtime_ns_int}.

    Nanosecond integer mtime_ns provides finer URL cache-busting precision than seconds.
    About 50 files take <1ms, reading filesystem metadata only."""
    fingerprints: dict[str, int] = {}

    for subdir in _MEDIA_SUBDIRS:
        dir_path = project_path / subdir
        # Skip symlinks here like nested levels: `is_dir()` follows links, so a symlinked media directory would
        # register external files as project fingerprints and traverse external trees.
        if dir_path.is_dir() and not dir_path.is_symlink():
            _scan_media_tree(subdir, dir_path, fingerprints)

    # Root-level media (e.g. style_reference.png).
    for f in project_path.iterdir():
        if f.is_file() and f.suffix.lower() in _ROOT_MEDIA_SUFFIXES:
            fingerprints[f.name] = f.stat().st_mtime_ns

    return fingerprints
