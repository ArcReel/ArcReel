"""_load_project_env 加载后白名单过滤行为。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from server.agent_runtime.service import AssistantService


def test_load_project_env_drops_provider_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "AUTH_PASSWORD=admin\n"
        "DATABASE_URL=sqlite:///x.db\n"
        "ANTHROPIC_API_KEY=should-be-dropped\n"
        "ARK_API_KEY=also-dropped\n"
        "GEMINI_API_KEY=dropped-too\n"
        "VIDU_API_KEY=dropped\n"
        "RANDOM_VAR=also-dropped\n"
    )
    for k in (
        "AUTH_PASSWORD",
        "DATABASE_URL",
        "ANTHROPIC_API_KEY",
        "ARK_API_KEY",
        "GEMINI_API_KEY",
        "VIDU_API_KEY",
        "RANDOM_VAR",
    ):
        monkeypatch.delenv(k, raising=False)

    AssistantService._load_project_env(tmp_path)

    assert os.environ.get("AUTH_PASSWORD") == "admin"
    assert os.environ.get("DATABASE_URL") == "sqlite:///x.db"

    # provider keys 被精确删除
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert "ARK_API_KEY" not in os.environ
    assert "GEMINI_API_KEY" not in os.environ
    assert "VIDU_API_KEY" not in os.environ

    # 保守版：未列入 provider 名单的 RANDOM_VAR **保留**
    assert os.environ.get("RANDOM_VAR") == "also-dropped"


def test_missing_env_file_is_noop(tmp_path: Path) -> None:
    """目录里没有 .env 时不报错。"""
    AssistantService._load_project_env(tmp_path)  # no raise
