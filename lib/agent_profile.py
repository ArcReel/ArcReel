"""Resolve the agent_runtime_profile directory and read its shared reference texts.

Default: ``<PROJECT_ROOT>/agent_runtime_profile``. Set ``ARCREEL_PROFILE_DIR``
when the runtime profile lives outside the source tree (e.g. read-only
installations that ship the profile at a fixed path).

``.claude/references/`` 下的规则正文既被子智能体读取，也被服务端 prompt builder
拼进 prompt——两侧读同一个文件，仓库里只有一份文本。
"""

from __future__ import annotations

import os
from pathlib import Path

from lib.env_init import PROJECT_ROOT


def agent_profile_dir() -> Path:
    override = os.getenv("ARCREEL_PROFILE_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return PROJECT_ROOT / "agent_runtime_profile"


def read_profile_reference(file_name: str) -> str:
    """读取 ``.claude/references/<file_name>`` 的正文。

    调用时解析 profile 目录而非导入时：``agent_profile_dir()`` 受 ``ARCREEL_PROFILE_DIR``
    影响，导入期读会把 profile 路径冻结在模块导入那一刻。文件缺失时 fail-loud。
    """
    return (agent_profile_dir() / ".claude" / "references" / file_name).read_text(encoding="utf-8").strip()
