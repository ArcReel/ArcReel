"""资产文件指纹计算 — 基于 mtime 的内容寻址缓存支持"""

from pathlib import Path

# 扫描的媒体子目录
_MEDIA_SUBDIRS = ("storyboards", "videos", "thumbnails", "characters", "clues")

# 根目录下的已知媒体文件（如风格参考图）
_ROOT_MEDIA_SUFFIXES = frozenset((".png", ".jpg", ".jpeg", ".webp", ".mp4"))


def compute_asset_fingerprints(project_path: Path) -> dict[str, int]:
    """
    扫描项目目录下所有媒体文件，返回 {相对路径: mtime_ns_int} 映射。

    mtime_ns 为纳秒级整数，用作 URL cache-bust 参数，精度高于秒级。
    对约 50 个文件，耗时 <1ms（仅读文件系统元数据）。
    """
    fingerprints: dict[str, int] = {}

    for subdir in _MEDIA_SUBDIRS:
        dir_path = project_path / subdir
        if not dir_path.is_dir():
            continue
        for f in dir_path.iterdir():
            if f.is_file():
                fingerprints[f"{subdir}/{f.name}"] = f.stat().st_mtime_ns

    # 根目录下的媒体文件（如 style_reference.png）
    for f in project_path.iterdir():
        if f.is_file() and f.suffix.lower() in _ROOT_MEDIA_SUFFIXES:
            fingerprints[f.name] = f.stat().st_mtime_ns

    return fingerprints
