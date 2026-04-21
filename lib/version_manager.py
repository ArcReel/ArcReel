"""
版本管理模块

管理分镜图、视频、角色图、场景设计图、道具设计图、宫格图的历史版本。
支持版本备份、切换当前版本、记录和查询。
"""

import json
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path

_LOCKS_GUARD = threading.Lock()
_LOCKS_BY_VERSIONS_FILE: dict[str, threading.RLock] = {}


def _get_versions_file_lock(versions_file: Path) -> threading.RLock:
    key = str(Path(versions_file).resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS_BY_VERSIONS_FILE.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS_BY_VERSIONS_FILE[key] = lock
        return lock


class VersionManager:
    """版本管理器"""

    # 支持的资源类型
    RESOURCE_TYPES = ("storyboards", "videos", "characters", "scenes", "props", "grids", "reference_videos")

    # 资源类型对应的文件扩展名
    EXTENSIONS = {
        "storyboards": ".png",
        "videos": ".mp4",
        "characters": ".png",
        "scenes": ".png",
        "props": ".png",
        "grids": ".png",
        "reference_videos": ".mp4",
    }

    def __init__(self, project_path: Path):
        """
        初始化版本管理器

        Args:
            project_path: 项目根目录路径
        """
        self.project_path = Path(project_path)
        self.versions_dir = self.project_path / "versions"
        self.versions_file = self.versions_dir / "versions.json"
        self._lock = _get_versions_file_lock(self.versions_file)

        # 确保版本目录存在
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """确保版本目录结构存在"""
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        for resource_type in self.RESOURCE_TYPES:
            (self.versions_dir / resource_type).mkdir(exist_ok=True)

    def _load_versions(self) -> dict:
        """加载版本元数据"""
        if not self.versions_file.exists():
            return {rt: {} for rt in self.RESOURCE_TYPES}

        with open(self.versions_file, encoding="utf-8") as f:
            return json.load(f)

    def _save_versions(self, data: dict) -> None:
        """保存版本元数据"""
        with open(self.versions_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _generate_timestamp(self) -> str:
        """生成时间戳字符串（用于文件名）"""
        return datetime.now().strftime("%Y%m%dT%H%M%S")

    def _generate_iso_timestamp(self) -> str:
        """生成 ISO 格式时间戳（用于元数据）"""
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def get_versions(self, resource_type: str, resource_id: str) -> dict:
        """
        获取资源的所有版本信息

        Args:
            resource_type: 资源类型 (storyboards, videos, characters, clues)
            resource_id: 资源 ID (如 E1S01, 姜月茴)

        Returns:
            版本信息字典，包含 current_version 和 versions 列表
        """
        if resource_type not in self.RESOURCE_TYPES:
            raise ValueError(f"不支持的资源类型: {resource_type}")

        with self._lock:
            data = self._load_versions()
            resource_data = data.get(resource_type, {}).get(resource_id)

            if not resource_data:
                return {"current_version": 0, "versions": []}

            # 添加 is_current 和 file_url 字段
            versions = []
            for v in resource_data.get("versions", []):
                version_info = v.copy()
                version_info["is_current"] = v["version"] == resource_data["current_version"]
                version_info["file_url"] = f"/api/v1/files/{self.project_path.name}/{v['file']}"
                versions.append(version_info)

            return {"current_version": resource_data.get("current_version", 0), "versions": versions}

    def get_current_version(self, resource_type: str, resource_id: str) -> int:
        """
        获取当前版本号

        Args:
            resource_type: 资源类型
            resource_id: 资源 ID

        Returns:
            当前版本号，无版本时返回 0
        """
        info = self.get_versions(resource_type, resource_id)
        return info["current_version"]

    def add_version(
        self, resource_type: str, resource_id: str, prompt: str, source_file: Path | None = None, **metadata
    ) -> int:
        """
        添加新版本记录

        Args:
            resource_type: 资源类型
            resource_id: 资源 ID
            prompt: 生成该版本使用的 prompt
            source_file: 源文件路径（用于复制到版本目录）
            **metadata: 额外的元数据（如 aspect_ratio, duration_seconds）

        Returns:
            新版本号
        """
        if resource_type not in self.RESOURCE_TYPES:
            raise ValueError(f"不支持的资源类型: {resource_type}")

        with self._lock:
            data = self._load_versions()

            # 确保资源类型存在
            if resource_type not in data:
                data[resource_type] = {}

            # 获取或创建资源记录
            if resource_id not in data[resource_type]:
                data[resource_type][resource_id] = {"current_version": 0, "versions": []}

            resource_data = data[resource_type][resource_id]
            existing_versions = resource_data.get("versions", [])
            max_version = max(
                (item.get("version", 0) for item in existing_versions),
                default=0,
            )
            new_version = max_version + 1

            # 生成版本文件名和路径
            timestamp = self._generate_timestamp()
            ext = self.EXTENSIONS.get(resource_type, ".png")
            version_filename = f"{resource_id}_v{new_version}_{timestamp}{ext}"
            version_rel_path = f"versions/{resource_type}/{version_filename}"
            version_abs_path = self.project_path / version_rel_path

            # 如果有源文件，复制到版本目录
            if source_file and Path(source_file).exists():
                shutil.copy2(source_file, version_abs_path)

            # 创建版本记录
            version_record = {
                "version": new_version,
                "file": version_rel_path,
                "prompt": prompt,
                "created_at": self._generate_iso_timestamp(),
                **metadata,
            }

            resource_data["versions"].append(version_record)
            resource_data["current_version"] = new_version

            self._save_versions(data)
            return new_version

    def backup_current(
        self, resource_type: str, resource_id: str, current_file: Path, prompt: str, **metadata
    ) -> int | None:
        """
        将当前文件备份到版本目录

        如果当前文件不存在，不执行任何操作。

        Args:
            resource_type: 资源类型
            resource_id: 资源 ID
            current_file: 当前文件路径
            prompt: 当前版本的 prompt
            **metadata: 额外的元数据

        Returns:
            备份的版本号，如果未备份则返回 None
        """
        current_file = Path(current_file)
        if not current_file.exists():
            return None

        return self.add_version(
            resource_type=resource_type, resource_id=resource_id, prompt=prompt, source_file=current_file, **metadata
        )

    def ensure_current_tracked(
        self, resource_type: str, resource_id: str, current_file: Path, prompt: str, **metadata
    ) -> int | None:
        """
        确保“当前文件”至少有一个版本记录

        用于升级/迁移场景：磁盘上已有 current_file，但 versions.json 还没有记录。
        若该资源已存在版本记录（current_version > 0）则不会重复写入。

        Args:
            resource_type: 资源类型
            resource_id: 资源 ID
            current_file: 当前文件路径
            prompt: 当前文件对应的 prompt（用于记录）
            **metadata: 额外元数据

        Returns:
            新增的版本号；若无需新增或文件不存在则返回 None
        """
        current_file = Path(current_file)
        if not current_file.exists():
            return None

        if resource_type not in self.RESOURCE_TYPES:
            raise ValueError(f"不支持的资源类型: {resource_type}")

        with self._lock:
            if self.get_current_version(resource_type, resource_id) > 0:
                return None
            return self.add_version(
                resource_type=resource_type,
                resource_id=resource_id,
                prompt=prompt,
                source_file=current_file,
                **metadata,
            )

    def restore_version(self, resource_type: str, resource_id: str, version: int, current_file: Path) -> dict:
        """
        切换到指定版本

        将指定版本复制到当前路径，并将 current_version 指向该版本。

        Args:
            resource_type: 资源类型
            resource_id: 资源 ID
            version: 要还原的版本号
            current_file: 当前文件路径

        Returns:
            切换信息，包含 restored_version, current_version, prompt
        """
        if resource_type not in self.RESOURCE_TYPES:
            raise ValueError(f"不支持的资源类型: {resource_type}")

        current_file = Path(current_file)

        with self._lock:
            data = self._load_versions()
            resource_data = data.get(resource_type, {}).get(resource_id)

            if not resource_data:
                raise ValueError(f"资源不存在: {resource_type}/{resource_id}")

            target_version = None
            for v in resource_data["versions"]:
                if v["version"] == version:
                    target_version = v
                    break

            if not target_version:
                raise ValueError(f"版本不存在: {version}")

            target_file = self.project_path / target_version["file"]
            if not target_file.exists():
                raise FileNotFoundError(f"版本文件不存在: {target_file}")

            current_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target_file, current_file)

            resource_data["current_version"] = version
            self._save_versions(data)

        restored_prompt = target_version.get("prompt", "")
        return {
            "restored_version": version,
            "current_version": version,
            "prompt": restored_prompt,
        }

    def get_version_file_url(self, resource_type: str, resource_id: str, version: int) -> str | None:
        """
        获取指定版本的文件 URL

        Args:
            resource_type: 资源类型
            resource_id: 资源 ID
            version: 版本号

        Returns:
            文件 URL，不存在时返回 None
        """
        info = self.get_versions(resource_type, resource_id)
        for v in info["versions"]:
            if v["version"] == version:
                return v.get("file_url")
        return None

    def get_version_prompt(self, resource_type: str, resource_id: str, version: int) -> str | None:
        """
        获取指定版本的 prompt

        Args:
            resource_type: 资源类型
            resource_id: 资源 ID
            version: 版本号

        Returns:
            prompt 文本，不存在时返回 None
        """
        info = self.get_versions(resource_type, resource_id)
        for v in info["versions"]:
            if v["version"] == version:
                return v.get("prompt")
        return None

    def has_versions(self, resource_type: str, resource_id: str) -> bool:
        """
        检查资源是否有版本记录

        Args:
            resource_type: 资源类型
            resource_id: 资源 ID

        Returns:
            是否有版本记录
        """
        return self.get_current_version(resource_type, resource_id) > 0
