"""
Version management module

Manages historical versions of storyboard images, videos, character images, and clue images.
Supports version backup, switching the current version, recording, and querying.
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
    """Version manager"""

    # Supported resource types
    RESOURCE_TYPES = ("storyboards", "videos", "characters", "clues")

    # File extension for each resource type
    EXTENSIONS = {
        "storyboards": ".png",
        "videos": ".mp4",
        "characters": ".png",
        "clues": ".png",
    }

    def __init__(self, project_path: Path):
        """
        Initialise the version manager.

        Args:
            project_path: Project root directory path
        """
        self.project_path = Path(project_path)
        self.versions_dir = self.project_path / "versions"
        self.versions_file = self.versions_dir / "versions.json"
        self._lock = _get_versions_file_lock(self.versions_file)

        # Ensure version directory exists
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """Ensure the version directory structure exists."""
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        for resource_type in self.RESOURCE_TYPES:
            (self.versions_dir / resource_type).mkdir(exist_ok=True)

    def _load_versions(self) -> dict:
        """Load version metadata."""
        if not self.versions_file.exists():
            return {rt: {} for rt in self.RESOURCE_TYPES}

        with open(self.versions_file, encoding="utf-8") as f:
            return json.load(f)

    def _save_versions(self, data: dict) -> None:
        """Save version metadata."""
        with open(self.versions_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _generate_timestamp(self) -> str:
        """Generate a timestamp string (for use in filenames)."""
        return datetime.now().strftime("%Y%m%dT%H%M%S")

    def _generate_iso_timestamp(self) -> str:
        """Generate an ISO-format timestamp (for use in metadata)."""
        return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def get_versions(self, resource_type: str, resource_id: str) -> dict:
        """
        Get all version information for a resource.

        Args:
            resource_type: Resource type (storyboards, videos, characters, clues)
            resource_id: Resource ID (e.g. E1S01, character_name)

        Returns:
            Version information dictionary containing current_version and versions list
        """
        if resource_type not in self.RESOURCE_TYPES:
            raise ValueError(f"Unsupported resource type: {resource_type}")

        with self._lock:
            data = self._load_versions()
            resource_data = data.get(resource_type, {}).get(resource_id)

            if not resource_data:
                return {"current_version": 0, "versions": []}

            # Add is_current and file_url fields
            versions = []
            for v in resource_data.get("versions", []):
                version_info = v.copy()
                version_info["is_current"] = v["version"] == resource_data["current_version"]
                version_info["file_url"] = f"/api/v1/files/{self.project_path.name}/{v['file']}"
                versions.append(version_info)

            return {"current_version": resource_data.get("current_version", 0), "versions": versions}

    def get_current_version(self, resource_type: str, resource_id: str) -> int:
        """
        Get the current version number.

        Args:
            resource_type: Resource type
            resource_id: Resource ID

        Returns:
            Current version number; returns 0 if no versions exist
        """
        info = self.get_versions(resource_type, resource_id)
        return info["current_version"]

    def add_version(
        self, resource_type: str, resource_id: str, prompt: str, source_file: Path | None = None, **metadata
    ) -> int:
        """
        Add a new version record.

        Args:
            resource_type: Resource type
            resource_id: Resource ID
            prompt: The prompt used to generate this version
            source_file: Source file path (copied to the versions directory)
            **metadata: Additional metadata (e.g. aspect_ratio, duration_seconds)

        Returns:
            New version number
        """
        if resource_type not in self.RESOURCE_TYPES:
            raise ValueError(f"Unsupported resource type: {resource_type}")

        with self._lock:
            data = self._load_versions()

            # Ensure the resource type key exists
            if resource_type not in data:
                data[resource_type] = {}

            # Get or create the resource record
            if resource_id not in data[resource_type]:
                data[resource_type][resource_id] = {"current_version": 0, "versions": []}

            resource_data = data[resource_type][resource_id]
            existing_versions = resource_data.get("versions", [])
            max_version = max(
                (item.get("version", 0) for item in existing_versions),
                default=0,
            )
            new_version = max_version + 1

            # Generate version filename and path
            timestamp = self._generate_timestamp()
            ext = self.EXTENSIONS.get(resource_type, ".png")
            version_filename = f"{resource_id}_v{new_version}_{timestamp}{ext}"
            version_rel_path = f"versions/{resource_type}/{version_filename}"
            version_abs_path = self.project_path / version_rel_path

            # If a source file is provided, copy it to the versions directory
            if source_file and Path(source_file).exists():
                shutil.copy2(source_file, version_abs_path)

            # Create the version record
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
        Back up the current file to the versions directory.

        If the current file does not exist, this is a no-op.

        Args:
            resource_type: Resource type
            resource_id: Resource ID
            current_file: Current file path
            prompt: The prompt for the current version
            **metadata: Additional metadata

        Returns:
            The backed-up version number, or None if no backup was performed
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
        “””
        Ensure the current file has at least one version record.

        Used for upgrade/migration scenarios where a current_file already exists on disk
        but versions.json has no record for it yet.
        If the resource already has a version record (current_version > 0) no new entry is written.

        Args:
            resource_type: Resource type
            resource_id: Resource ID
            current_file: Current file path
            prompt: The prompt corresponding to the current file (for record-keeping)
            **metadata: Additional metadata

        Returns:
            The newly added version number; None if no addition was needed or the file does not exist
        “””
        current_file = Path(current_file)
        if not current_file.exists():
            return None

        if resource_type not in self.RESOURCE_TYPES:
            raise ValueError(f”Unsupported resource type: {resource_type}”)

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
        Switch to the specified version.

        Copies the specified version to the current file path and updates current_version to point to it.

        Args:
            resource_type: Resource type
            resource_id: Resource ID
            version: Version number to restore
            current_file: Current file path

        Returns:
            Restore info dict containing restored_version, current_version, and prompt
        """
        if resource_type not in self.RESOURCE_TYPES:
            raise ValueError(f"Unsupported resource type: {resource_type}")

        current_file = Path(current_file)

        with self._lock:
            data = self._load_versions()
            resource_data = data.get(resource_type, {}).get(resource_id)

            if not resource_data:
                raise ValueError(f"Resource not found: {resource_type}/{resource_id}")

            target_version = None
            for v in resource_data["versions"]:
                if v["version"] == version:
                    target_version = v
                    break

            if not target_version:
                raise ValueError(f"Version not found: {version}")

            target_file = self.project_path / target_version["file"]
            if not target_file.exists():
                raise FileNotFoundError(f"Version file not found: {target_file}")

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
        Get the file URL for the specified version.

        Args:
            resource_type: Resource type
            resource_id: Resource ID
            version: Version number

        Returns:
            File URL, or None if not found
        """
        info = self.get_versions(resource_type, resource_id)
        for v in info["versions"]:
            if v["version"] == version:
                return v.get("file_url")
        return None

    def get_version_prompt(self, resource_type: str, resource_id: str, version: int) -> str | None:
        """
        Get the prompt for the specified version.

        Args:
            resource_type: Resource type
            resource_id: Resource ID
            version: Version number

        Returns:
            Prompt text, or None if not found
        """
        info = self.get_versions(resource_type, resource_id)
        for v in info["versions"]:
            if v["version"] == version:
                return v.get("prompt")
        return None

    def has_versions(self, resource_type: str, resource_id: str) -> bool:
        """
        Check whether the resource has any version records.

        Args:
            resource_type: Resource type
            resource_id: Resource ID

        Returns:
            True if at least one version record exists, False otherwise
        """
        return self.get_current_version(resource_type, resource_id) > 0
