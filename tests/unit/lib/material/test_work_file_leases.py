"""工作文件的使用登记按真实路径比对：数据目录经过符号链接时，登记方与清理方的路径写法不同也认得出同一个文件。"""

from __future__ import annotations

import os
import time
from pathlib import Path

from lib.material.work_files import evict_idle_work_file, hold_work_file


def test_held_file_is_kept_when_cleanup_spells_its_path_through_a_symlink(tmp_path: Path) -> None:
    real_root = tmp_path / "data"
    (real_root / "projects" / "demo" / "characters").mkdir(parents=True)
    linked_root = tmp_path / "linked-data"
    linked_root.symlink_to(real_root, target_is_directory=True)
    real_file = real_root / "projects" / "demo" / "characters" / "Alice.png"
    real_file.write_bytes(b"image")
    linked_file = linked_root / "projects" / "demo" / "characters" / "Alice.png"
    idle_after_write = time.time() + 60

    # 导出经 get_project_path 拿到真实路径并登记；清理按数据目录的原路径枚举到同一个文件
    with hold_work_file(real_file):
        assert evict_idle_work_file(linked_file, os.stat(linked_file), idle_after_write) == (0, 0, 1)
    assert real_file.is_file()
