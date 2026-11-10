"""v19→v20 迁移：只写了内置供应商的模型字段按推荐模型固化；版本守卫与幂等。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lib.project.project_migrations.v19_to_v20_bare_provider_models import migrate_project_dict, migrate_v19_to_v20

_RECOMMENDED = {("ark", "video"): "seedance-x", ("grok", "text"): "grok-x", ("openai", "image"): "image-x"}


def _recommend(provider_id: str, media_type: str) -> str | None:
    return _RECOMMENDED.get((provider_id, media_type))


def test_bare_builtin_providers_take_the_recommended_model() -> None:
    project = {
        "video_backend": "ark",
        "image_provider_t2i": "openai/",
        "text_backend_complex": " grok ",
        "title": "ark",
    }

    assert migrate_project_dict(project, _recommend) == {
        "video_backend": "ark/seedance-x",
        "image_provider_t2i": "openai/image-x",
        "text_backend_complex": "grok/grok-x",
        "title": "ark",
    }


def test_values_without_a_recommendation_or_not_bare_builtin_are_kept() -> None:
    project = {
        # 没有视频推荐：原样保留，运行时与升级前一样报错让用户重新选择
        "video_provider_r2v": "openai",
        # 自定义供应商的裸 id：升级前也不补模型
        "audio_backend": "custom-3",
        "default_image_backend": "openai/gpt-image-2",
        "video_provider_i2v": "no-such-provider",
        "default_text_backend": 3,
        "text_backend_simple": "",
    }

    assert migrate_project_dict(project, _recommend) == project


def test_migration_is_idempotent() -> None:
    once = migrate_project_dict({"video_backend": "ark"}, _recommend)
    assert migrate_project_dict(once, _recommend) == once


def _write(tmp_path: Path, data: dict[str, Any]) -> Path:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    (project_dir / "project.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return project_dir


def _load(project_dir: Path) -> dict[str, Any]:
    return json.loads((project_dir / "project.json").read_text(encoding="utf-8"))


def test_file_migration_bumps_the_schema_then_is_a_noop(tmp_path: Path) -> None:
    project_dir = _write(tmp_path, {"schema_version": 19, "video_backend": "grok"})

    migrate_v19_to_v20(project_dir)
    migrated = _load(project_dir)
    assert migrated["schema_version"] == 20
    assert migrated["video_backend"].startswith("grok/")

    (project_dir / "project.json").write_text(json.dumps({**migrated, "video_backend": "grok"}), encoding="utf-8")
    migrate_v19_to_v20(project_dir)
    assert _load(project_dir)["video_backend"] == "grok"
