"""
版本管理模块

管理分镜图、视频、角色图、场景设计图、道具设计图、宫格图的历史版本。
支持版本备份、切换当前版本、记录和查询。
"""

import json
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from lib.api_errors import BadRequestError, NotFoundError
from lib.resource_paths import RESOURCE_TYPES as _RESOURCE_TYPES
from lib.resource_paths import resource_extension

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

    # 支持的资源类型与扩展名均派生自单一真相源 lib.resource_paths，避免副本漂移。
    RESOURCE_TYPES = _RESOURCE_TYPES
    EXTENSIONS = {rt: resource_extension(rt) for rt in _RESOURCE_TYPES}

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
            raise BadRequestError("unsupported_resource_type", resource_type=resource_type)

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

    def rename_resource(self, resource_type: str, old_id: str, new_id: str, *, dry_run: bool = False) -> int:
        """把资源的版本历史整体迁移到新 id：re-key 元数据、重命名快照文件、改写记录内路径。

        资产重命名的版本时光机迁移入口。resource_id 与快照文件名都以资产名为前缀
        （``{name}_v{n}_{timestamp}{ext}``），名字判等走比对坐标系（NFC）——存量记录
        与文件名可能以 NFD 落盘。返回涉及的快照文件数；旧 id 无版本记录时返回 0
        （幂等：级联事务中途失败后重跑安全）。``dry_run=True`` 只统计、不迁移。
        """
        if resource_type not in self.RESOURCE_TYPES:
            raise ValueError(f"不支持的资源类型: {resource_type}")

        from lib.asset_types import normalize_asset_name, resolve_asset_key

        with self._lock:
            data = self._load_versions()
            bucket = data.get(resource_type)
            if not isinstance(bucket, dict):
                return 0
            key = resolve_asset_key(bucket, old_id)
            if key is None or not isinstance(bucket.get(key), dict):
                return 0
            record = bucket[key]
            versions = [v for v in record.get("versions", []) if isinstance(v, dict) and isinstance(v.get("file"), str)]
            if dry_run:
                return len(versions)

            prefix = f"{normalize_asset_name(old_id)}_v"
            for version in versions:
                basename = normalize_asset_name(PurePosixPath(version["file"].replace("\\", "/")).name)
                if not basename.startswith(prefix):
                    continue
                new_basename = f"{new_id}_v{basename[len(prefix) :]}"
                src = self.project_path / version["file"]
                dst = self.versions_dir / resource_type / new_basename
                if src.exists():
                    src.replace(dst)
                version["file"] = f"versions/{resource_type}/{new_basename}"
            del bucket[key]
            # 重跑场景下新 id 可能已有残缺记录：旧记录是完整历史，覆盖之。按 NFC 解析后再删——
            # 残缺记录的 key 可能是另一种编码形式，精确下标写入会留下两条视觉同名的记录，
            # 而 resolve_asset_key 按遍历顺序取最后一条，可能命中残缺记录而非完整历史。
            existing_new_key = resolve_asset_key(bucket, new_id)
            if existing_new_key is not None:
                del bucket[existing_new_key]
            bucket[new_id] = record
            self._save_versions(data)
            return len(versions)

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
            raise BadRequestError("unsupported_resource_type", resource_type=resource_type)

        current_file = Path(current_file)

        with self._lock:
            data = self._load_versions()
            resource_data = data.get(resource_type, {}).get(resource_id)

            if not resource_data:
                raise NotFoundError("version_resource_not_found", resource_type=resource_type, resource_id=resource_id)

            target_version = None
            for v in resource_data["versions"]:
                if v["version"] == version:
                    target_version = v
                    break

            if not target_version:
                raise NotFoundError("version_not_found", version=version)

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

    def update_version_metadata(self, resource_type: str, resource_id: str, version: int, **metadata) -> bool:
        """为既有版本记录补写元数据键（覆盖同名键）。

        供生成 finalize 在版本入库后回填只有 finalize 阶段才确定的元数据（如参考
        单元产物的来源签名）。目标记录不存在时返回 False 不抛错：档案补写是
        best-effort，缺失只降级为将来还原时拿不到该元数据。

        Args:
            resource_type: 资源类型
            resource_id: 资源 ID
            version: 版本号
            **metadata: 要写入的元数据键值

        Returns:
            是否找到记录并写入
        """
        if resource_type not in self.RESOURCE_TYPES:
            raise ValueError(f"不支持的资源类型: {resource_type}")

        with self._lock:
            data = self._load_versions()
            resource_data = data.get(resource_type, {}).get(resource_id)
            if not resource_data:
                return False
            for record in resource_data.get("versions", []):
                if record.get("version") == version:
                    record.update(metadata)
                    self._save_versions(data)
                    return True
            return False

    def get_version_metadata(self, resource_type: str, resource_id: str, version: int, key: str) -> Any | None:
        """读取指定版本记录上的元数据键，记录或键不存在时返回 None。"""
        info = self.get_versions(resource_type, resource_id)
        for v in info["versions"]:
            if v["version"] == version:
                return v.get(key)
        return None

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

    def get_version_created_at(self, resource_type: str, resource_id: str, version: int) -> str | None:
        """
        获取指定版本的入库时间（ISO8601）

        还原历史版本时用于把该版本的原始生成时间写回 generated_assets，而不是戳成
        「现在」——还原回来的是旧内容，声音等派生判定须按旧时间成立。

        Args:
            resource_type: 资源类型
            resource_id: 资源 ID
            version: 版本号

        Returns:
            ISO8601 时间戳，不存在时返回 None
        """
        value = self.get_version_metadata(resource_type, resource_id, version, "created_at")
        return value if isinstance(value, str) else None

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
