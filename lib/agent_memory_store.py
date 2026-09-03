"""Agent 记忆目录的存储服务：列表 / 读 / upsert / 删 / 清空的单一实现。

按目录参数化，项目记忆与用户记忆共用同一个类——两级记忆的文件名规则、大小上限、
索引超限口径与 frontmatter 解析完全相同，各写一份会让两根 REST 路由的行为随时间分叉。

目录不存在一律视同空目录：记忆是 Agent 按需创建的，创作者在 Agent 写下第一条记忆
之前打开记忆页不应看到错误。

无锁、无冲突检测：单文件写入走同目录临时文件 + rename 原子落盘，并发的两次 PUT
后写胜出，读到的永远是某一次完整写入的内容而不是半截文件。
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from lib.api_errors import BadRequestError, NotFoundError
from lib.json_io import atomic_write_bytes
from lib.path_safety import PathTraversalError, safe_join, try_safe_join

#: 保留的索引文件名：它是记忆的目录页，列表接口单列其统计而不混进普通记忆条目。
#: 允许删除——索引失真时重建比修补便宜。
INDEX_FILENAME = "MEMORY.md"

#: 单个记忆文件的正文上限。超限拒绝写入，让创作者拆分而不是让 Agent 每轮都读进一个大文件。
MAX_FILE_BYTES = 256 * 1024

#: 索引的软上限：只在列表响应里标 ``over_limit``，不拦截写入。
INDEX_MAX_LINES = 200
INDEX_MAX_BYTES = 25_000

#: frontmatter ``type`` 的合法取值。取值之外的一律当作没有标签，不报错。
MEMORY_TYPES = frozenset({"user", "feedback", "project", "reference"})

#: 文件名上限：与 ``_FILENAME_PATTERN`` 一起构成可见性与可读写性的同一把尺子。
FILENAME_MAX_LENGTH = 100

_FILENAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*\.md$"


def is_valid_memory_filename(filename: str) -> bool:
    """``filename`` 是否为合法的顶层记忆文件名。

    首字符限定字母数字，因此 ``.`` 开头的隐藏文件与原子写入的临时文件都不合法：
    临时文件既不会出现在列表里，也不会被当成记忆读写。
    """
    return len(filename) <= FILENAME_MAX_LENGTH and re.fullmatch(_FILENAME_PATTERN, filename) is not None


def parse_memory_frontmatter(raw: bytes) -> dict[str, Any] | None:
    """解析记忆文件的 YAML frontmatter，返回 ``{name, description, type}``。

    这是记忆域唯一的 YAML 解析点：解析发生在服务端，前端只消费已判定合法的结果。
    缺 frontmatter、YAML 语法错、不是对象、``type`` 不在 :data:`MEMORY_TYPES` 内
    一律返回 ``None``——记忆文件由 Agent 自由书写，元数据不全是常态而非错误，
    该文件只是不带标签，不该让整个列表请求失败。
    """
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None

    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = lines.index("---", 1)
    except ValueError:
        return None
    try:
        loaded: Any = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError:
        return None
    if not isinstance(loaded, dict):
        return None

    memory_type = loaded.get("type")
    if not isinstance(memory_type, str) or memory_type not in MEMORY_TYPES:
        return None
    return {
        "name": _optional_text(loaded.get("name")),
        "description": _optional_text(loaded.get("description")),
        "type": memory_type,
    }


def _optional_text(value: object) -> str | None:
    """frontmatter 里的可选文本字段：非字符串或空白一律当作没写。"""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


@dataclass(frozen=True)
class AgentMemoryStore:
    """一个记忆目录的读写门面。``directory`` 由 ``lib.agent_memory_paths`` 派生。"""

    directory: Path

    def overview(self) -> dict[str, Any]:
        """列表响应：目录路径、索引统计与全部可见记忆条目。

        ``path`` 是服务端绝对路径，供创作者在文件管理器里直接打开记忆目录；它是路径
        而不是提示文案，因此不进 i18n。
        """
        index = {"exists": False, "line_count": 0, "byte_size": 0, "over_limit": False}
        files: list[dict[str, Any]] = []
        for path in self._visible_paths():
            stat = path.stat()
            if path.name == INDEX_FILENAME:
                index = _index_stats(path, stat.st_size)
                continue
            files.append(
                {
                    "name": path.name,
                    "size": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
                    "frontmatter": parse_memory_frontmatter(_read_bytes_or_empty(path)),
                }
            )
        return {"path": str(self.directory), "index": index, "files": files}

    def read(self, filename: str) -> bytes:
        """读取单个记忆文件的原始字节。

        原样回字节而不先解码：记忆文件由 Agent 自由书写，混入非 UTF-8 字节时正文照样
        能回给创作者去修，而不是整条读路径退化成 500。
        """
        path = self._resolve(filename)
        if not path.is_file():
            raise NotFoundError("memory_file_not_found", filename=filename)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise NotFoundError("memory_file_not_found", filename=filename) from exc

    def write(self, filename: str, content: bytes) -> None:
        """幂等 upsert：目录不存在则创建，正文超过 :data:`MAX_FILE_BYTES` 时拒绝。

        目标名被目录占用时按非法文件名拒绝，与读、删、列表对同名目录的处置一致：
        记忆目录对 Agent 可写，落一个 ``notes.md/`` 子目录后 ``os.replace`` 会抛
        ``IsADirectoryError``，创作者在文件柜里既存不进也删不掉，只剩清空整个目录。
        """
        path = self._resolve(filename, ensure_directory=True)
        if path.is_dir():
            raise BadRequestError("memory_invalid_filename", filename=filename)
        if len(content) > MAX_FILE_BYTES:
            raise BadRequestError("memory_file_too_large", filename=filename, limit_kib=MAX_FILE_BYTES // 1024)
        atomic_write_bytes(path, content)

    def delete(self, filename: str) -> None:
        """删除单个记忆文件；索引 ``MEMORY.md`` 同样可删。"""
        path = self._resolve(filename)
        if not path.is_file():
            raise NotFoundError("memory_file_not_found", filename=filename)
        try:
            path.unlink()
        except FileNotFoundError as exc:
            raise NotFoundError("memory_file_not_found", filename=filename) from exc

    def clear(self) -> None:
        """清空整个记忆目录后重建空目录，不生成空索引。

        索引由 Agent 在写第一条记忆时重建；预置一个空 ``MEMORY.md`` 只会让列表显示
        一个 0 行的索引，与「什么都没有」不可区分。
        """
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _visible_paths(self) -> list[Path]:
        """目录内按名称排序的可见记忆文件：顶层、合法文件名、未逃出目录的真实文件。

        逐项过 ``try_safe_join``（realpath 语义）而不是只判 ``is_file()``：指向目录外的
        symlink 在 ``is_file()`` 下为真、却读不出来，列出它等于给创作者一个点开必然
        404 的条目。子目录与非 ``.md`` 条目由文件名规则挡在外面。
        """
        try:
            names = sorted(entry.name for entry in self.directory.iterdir())
        except OSError:
            return []
        resolved: list[Path] = []
        for name in names:
            if not is_valid_memory_filename(name):
                continue
            path = try_safe_join(self.directory, name, require_file=True)
            if path is not None:
                resolved.append(path)
        return resolved

    def _resolve(self, filename: str, *, ensure_directory: bool = False) -> Path:
        """把不可信的 ``filename`` 解析为目录内的绝对路径。

        文件名规则先判，``safe_join`` 再判：前者是产品规则（顶层 ``*.md``），后者是
        越界兜底，两者都归到同一个 ``memory_invalid_filename``——对创作者而言
        「这个文件名不能用」是同一件事。
        """
        if not is_valid_memory_filename(filename):
            raise BadRequestError("memory_invalid_filename", filename=filename)
        if ensure_directory:
            self.directory.mkdir(parents=True, exist_ok=True)
        try:
            return safe_join(self.directory, filename)
        except PathTraversalError as exc:
            raise BadRequestError("memory_invalid_filename", filename=filename) from exc


def _index_stats(path: Path, byte_size: int) -> dict[str, Any]:
    """索引的行数 / 字节数与超限判定。超限只是提示，写入与读取都不受影响。"""
    line_count = len(_read_bytes_or_empty(path).splitlines())
    return {
        "exists": True,
        "line_count": line_count,
        "byte_size": byte_size,
        "over_limit": line_count > INDEX_MAX_LINES or byte_size > INDEX_MAX_BYTES,
    }


def _read_bytes_or_empty(path: Path) -> bytes:
    """读不出来的文件按空内容处理：列表接口不因单个文件的 I/O 故障整体失败。"""
    try:
        return path.read_bytes()
    except OSError:
        return b""
