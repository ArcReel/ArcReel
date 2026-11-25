from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import respx
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.orm import Session

from alembic import command
from arcreel_market_core.video_backend_contract import VideoCapabilityError, VideoGenerationRequest
from lib.custom_provider.backends import CustomVideoBackend
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from tests.custom_backend_support import create_custom_backend
from tests.factories import image_bytes

_REFERENCE = "vidu-video-q3-reference"
_MIX = "vidu-video-q3-mix"


def _insert_custom(
    conn: sa.Connection,
    provider_id: int,
    base_url: str,
    *,
    model_id: str = "viduq3-mix",
    endpoint: str = _REFERENCE,
    binding: str | None = None,
    path_overrides: str | None = None,
    capability_overrides: str | None = None,
) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO custom_provider (id, display_name, discovery_format, base_url, api_key, created_at, updated_at) "
            "VALUES (:id, 'Vidu', 'openai', :url, 'local-test-key', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {"id": provider_id, "url": base_url},
    )
    conn.execute(
        sa.text(
            "INSERT INTO custom_provider_model (provider_id, model_id, display_name, endpoint, entry_binding, "
            "is_default, is_enabled, path_overrides, capability_overrides, created_at, updated_at) "
            "VALUES (:id, :model, 'Vidu Q3 Mix', :endpoint, :binding, 0, 1, :paths, :caps, "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {
            "id": provider_id,
            "model": model_id,
            "endpoint": endpoint,
            "binding": binding,
            "paths": path_overrides,
            "caps": capability_overrides,
        },
    )


def test_legacy_official_mix_upgrade_uses_the_existing_mix_request_contract(
    alembic_cfg: tuple[Config, Path],
    migration_revisions: Callable[[str], tuple[str, str]],
    tmp_path: Path,
) -> None:
    cfg, db_path = alembic_cfg
    split_revision, split_parent = migration_revisions("*_split_video_request_profiles.py")
    command.upgrade(cfg, split_parent)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            _insert_custom(conn, 1, "https://api.vidu.cn", endpoint="vidu-video")
            _insert_custom(conn, 2, "api.vidu.cn/ent/v2/", endpoint=_MIX)
        command.upgrade(cfg, split_revision)
        with Session(engine) as session:
            assert session.scalars(
                sa.select(CustomProviderModel.endpoint).order_by(CustomProviderModel.provider_id)
            ).all() == [_REFERENCE, _MIX]
        command.upgrade(cfg, "head")
        with Session(engine) as session:
            models = session.scalars(sa.select(CustomProviderModel).order_by(CustomProviderModel.provider_id)).all()
            assert [model.endpoint for model in models] == [_MIX, _MIX]
            backends = [
                create_custom_backend(
                    provider=session.get_one(CustomProvider, model.provider_id),
                    model_id=model.model_id,
                    endpoint=model.endpoint,
                )
                for model in models
            ]
    finally:
        engine.dispose()

    reference = tmp_path / "reference.png"
    reference.write_bytes(image_bytes())
    request = VideoGenerationRequest(
        prompt="字" * 20_000,
        output_path=tmp_path / "out.mp4",
        duration_seconds=16,
        generate_audio=True,
        reference_images=[reference],
    )
    with respx.mock(assert_all_mocked=True) as router:
        submit = router.post("https://api.vidu.cn/ent/v2/reference2video").mock(
            return_value=httpx.Response(200, json={"task_id": "local-job"})
        )
        router.get("https://api.vidu.cn/ent/v2/tasks/local-job/creations").mock(
            return_value=httpx.Response(
                200, json={"state": "success", "creations": [{"url": "https://media.test/out.mp4"}]}
            )
        )
        router.get("https://media.test/out.mp4").mock(return_value=httpx.Response(200, content=b"local video"))

        async def generate() -> None:
            for backend in backends:
                assert isinstance(backend, CustomVideoBackend)
                result = await backend.generate(request)
                assert result.duration_seconds == 15
                with pytest.raises(VideoCapabilityError) as exc:
                    await backend.generate(replace(request, prompt=request.prompt + "字"))
                assert exc.value.code == "video_prompt_too_long"
                assert exc.value.params["limit"] == 20_000

        asyncio.run(generate())

    migrated_body, existing_body = [json.loads(call.request.content) for call in submit.calls]
    assert migrated_body == existing_body
    assert migrated_body["model"] == "viduq3-mix"
    assert migrated_body["duration"] == 15
    assert migrated_body["prompt"] == request.prompt
    assert "audio" not in migrated_body


def test_mix_upgrade_changes_only_standard_official_bindings_and_is_idempotent(
    alembic_cfg: tuple[Config, Path],
    migration_revisions: Callable[[str], tuple[str, str]],
) -> None:
    cfg, db_path = alembic_cfg
    revision, parent = migration_revisions("*_converge_vidu_q3_mix.py")
    command.upgrade(cfg, parent)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            _insert_custom(
                conn,
                1,
                "API.VIDU.COM/ent/v2/",
                binding="shengshu/viduq3-mix",
                path_overrides="{}",
                capability_overrides="null",
            )
            _insert_custom(conn, 2, "https://relay.test/ent/v2")
            _insert_custom(conn, 3, "")
            _insert_custom(conn, 4, "https://api.vidu.cn/tenant/ent/v2")
            _insert_custom(conn, 5, "https://api.vidu.cn", binding="shengshu/viduq3")
            _insert_custom(conn, 6, "https://api.vidu.cn", model_id="viduq3", binding="shengshu/viduq3-mix")
            _insert_custom(conn, 7, "https://api.vidu.cn", model_id="mix-alias", binding="shengshu/viduq3-mix")
            _insert_custom(conn, 8, "https://api.vidu.cn", path_overrides='{"poll":"/relay/tasks/{{ task_id }}"}')
            _insert_custom(conn, 9, "https://api.vidu.cn", capability_overrides='{"max_prompt_chars":1500}')
            _insert_custom(conn, 10, "https://api.vidu.cn", endpoint="vidu-video-generic")
            _insert_custom(conn, 11, "https://api.vidu.cn:8443/ent/v2")
            _insert_custom(conn, 12, "https://api.vidu.cn.relay.test/ent/v2")
            _insert_custom(conn, 13, "https://[malformed")
            _insert_custom(conn, 14, "https://api.vidu.cn?relay=true")
            _insert_custom(conn, 15, "https://api.vidu.cn", model_id="viduq3")
            _insert_custom(conn, 16, "https://api.vidu.cn", endpoint=_MIX)
            conn.execute(
                sa.text(
                    "INSERT INTO tasks (task_id, project_name, task_type, media_type, resource_id, status, "
                    "source, queued_at, updated_at, provider_endpoint, submitted_base_url) "
                    "VALUES ('submitted', 'demo', 'reference_video', 'video', 'r1', 'running', 'webui', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :endpoint, 'https://api.vidu.com/ent/v2')"
                ),
                {"endpoint": _REFERENCE},
            )
            models = sa.Table("custom_provider_model", sa.MetaData(), autoload_with=conn)
            before = [dict(row) for row in conn.execute(models.select().order_by(models.c.provider_id)).mappings()]
            tasks_before = conn.execute(sa.text("SELECT * FROM tasks")).all()

        expected = [before[0] | {"endpoint": _MIX}, *before[1:]]
        command.upgrade(cfg, revision)
        with engine.connect() as conn:
            assert conn.execute(models.select().order_by(models.c.provider_id)).mappings().all() == expected
            assert conn.execute(sa.text("SELECT * FROM tasks")).all() == tasks_before
        command.downgrade(cfg, parent)
        with engine.connect() as conn:
            assert conn.execute(models.select().order_by(models.c.provider_id)).mappings().all() == expected
        command.upgrade(cfg, revision)
        with engine.connect() as conn:
            assert conn.execute(models.select().order_by(models.c.provider_id)).mappings().all() == expected
            assert conn.execute(sa.text("SELECT * FROM tasks")).all() == tasks_before
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("config_url", "active_url", "expected"),
    [
        (None, None, _MIX),
        ("https://api.vidu.cn/ent/v2", "https://relay.test/ent/v2", _REFERENCE),
        ("https://relay.test/ent/v2", "https://api.vidu.com/ent/v2/", _MIX),
        ("https://api.vidu.cn", None, _REFERENCE),
        ("https://api.vidu.cn/ent/v2//", None, _REFERENCE),
        ("https://api.vidu.com/ent/v2", None, _MIX),
    ],
    ids=["default", "active-relay", "active-official", "missing-mount", "double-slash", "provider-config"],
)
def test_builtin_mix_upgrade_resolves_the_effective_base_url(
    alembic_cfg: tuple[Config, Path],
    migration_revisions: Callable[[str], tuple[str, str]],
    config_url: str | None,
    active_url: str | None,
    expected: str,
) -> None:
    cfg, db_path = alembic_cfg
    revision, parent = migration_revisions("*_converge_vidu_q3_mix.py")
    command.upgrade(cfg, parent)
    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO builtin_provider_model "
                    "(provider_id, model_id, enabled, user_added, media_type, endpoint, created_at, updated_at) VALUES "
                    "('vidu', 'viduq3-mix', 1, 1, 'video', :endpoint, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                    "('vidu', 'viduq3', 1, 1, 'video', :endpoint, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"endpoint": _REFERENCE},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO provider_credential (provider, name, base_url, is_active, created_at, updated_at) VALUES "
                    "('vidu', 'inactive', 'https://inactive.test/ent/v2', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                    "('vidu', 'active', :url, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"url": active_url},
            )
            if config_url is not None:
                conn.execute(
                    sa.text(
                        "INSERT INTO provider_config (provider, key, value, is_secret, updated_at) "
                        "VALUES ('vidu', 'base_url', :url, 0, CURRENT_TIMESTAMP)"
                    ),
                    {"url": config_url},
                )
        command.upgrade(cfg, revision)
        with engine.connect() as conn:
            assert conn.execute(
                sa.text(
                    "SELECT model_id, endpoint FROM builtin_provider_model WHERE provider_id = 'vidu' ORDER BY model_id"
                )
            ).all() == [("viduq3", _REFERENCE), ("viduq3-mix", expected)]
    finally:
        engine.dispose()
