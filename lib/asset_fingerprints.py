"""资产文件指纹计算 — 基于 mtime 的内容寻址缓存支持"""

from pathlib import Path

# 扫描的媒体子目录
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
)

# 根目录下的已知媒体文件（如风格参考图）
_ROOT_MEDIA_SUFFIXES = frozenset((".png", ".jpg", ".jpeg", ".webp", ".mp4"))

#: 版本快照桶的目录名。快照落在项目根的 ``versions/{subdir}/``（见
#: ``lib.resource_paths.version_snapshot_dir``），本就不在 ``_MEDIA_SUBDIRS`` 里；这条跳过
#: 管的是媒体子目录内的同名目录。快照按版本号定址、不参与 cache-bust，扫到只是白算。
_VERSIONS_DIR = "versions"


def _scan_media_tree(prefix: str, dir_path: Path, fingerprints: dict[str, int]) -> None:
    """递归扫描一个媒体子目录下的全部文件，跳过任意层级的 ``versions/`` 与目录软链。

    深度不设限：角色衍生资产图落在 ``characters/derivatives/{本体}/{衍生}.png`` 这样的第三级。
    目录软链不下探——链接目标不是项目自己的媒体，且指向祖先时会一路递归到操作系统的软链
    解析上限才停；与 ``lib.profile_manifest`` 的 ``os.walk(followlinks=False)`` 同口径。
    """
    for entry in dir_path.iterdir():
        if entry.is_file():
            fingerprints[f"{prefix}/{entry.name}"] = entry.stat().st_mtime_ns
        elif entry.is_dir() and not entry.is_symlink() and entry.name != _VERSIONS_DIR:
            _scan_media_tree(f"{prefix}/{entry.name}", entry, fingerprints)


def compute_asset_fingerprints(project_path: Path) -> dict[str, int]:
    """
    扫描项目目录下所有媒体文件，返回 {相对路径: mtime_ns_int} 映射。

    mtime_ns 为纳秒级整数，用作 URL cache-bust 参数，精度高于秒级。
    对约 50 个文件，耗时 <1ms（仅读文件系统元数据）。
    """
    fingerprints: dict[str, int] = {}

    for subdir in _MEDIA_SUBDIRS:
        dir_path = project_path / subdir
        # 软链在这一层与树内各层同口径跳过：`is_dir()` 跟随软链，媒体子目录自身是软链时
        # 会把链接目标的文件写成本项目的指纹键并遍历整棵外部目录。
        if dir_path.is_dir() and not dir_path.is_symlink():
            _scan_media_tree(subdir, dir_path, fingerprints)

    # 根目录下的媒体文件（如 style_reference.png）
    for f in project_path.iterdir():
        if f.is_file() and f.suffix.lower() in _ROOT_MEDIA_SUFFIXES:
            fingerprints[f.name] = f.stat().st_mtime_ns

    return fingerprints
