"""Vertex 凭证文件位置：经 ConfigResolver 的供应商配置取到的凭证文件能被加载。

位置由凭证 id 经数据根布局推导；推导位置没有文件时回退到凭证记录上的路径（存量凭证）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.config.resolver import ConfigResolver
from lib.db.repositories.credential_repository import CredentialRepository
from lib.infra.data_root_layout import DataRootLayout


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DataRootLayout:
    data_root = tmp_path / "data"
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(data_root))
    return DataRootLayout.current()


@pytest.fixture
async def bound_resolver(db_session: AsyncSession, db_factory: async_sessionmaker[AsyncSession]) -> ConfigResolver:
    return ConfigResolver(db_factory, _bound_session=db_session)


def _write_service_account(path: Path, project_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"project_id": project_id}), encoding="utf-8")


async def _active_vertex_credential(db_session: AsyncSession, *, credentials_path: str | None) -> int:
    repo = CredentialRepository(db_session)
    cred = await repo.create(provider="gemini-vertex", name="Vertex", credentials_path=credentials_path)
    await repo.activate(cred.id, "gemini-vertex")
    await db_session.flush()
    return cred.id


def _loaded_project_id(config: dict[str, str]) -> str:
    return json.loads(Path(config["credentials_path"]).read_text(encoding="utf-8"))["project_id"]


async def test_credential_file_is_found_by_id_even_when_recorded_path_is_stale(
    tmp_path: Path, layout: DataRootLayout, db_session: AsyncSession, bound_resolver: ConfigResolver
) -> None:
    cred_id = await _active_vertex_credential(
        db_session, credentials_path=str(tmp_path / "moved-away" / "vertex_keys" / "vertex_cred_1.json")
    )
    _write_service_account(layout.vertex_credential_path(cred_id), "by-id")

    config = await bound_resolver.provider_config("gemini-vertex")

    assert _loaded_project_id(config) == "by-id"


async def test_existing_credential_without_file_at_derived_location_uses_recorded_path(
    tmp_path: Path, layout: DataRootLayout, db_session: AsyncSession, bound_resolver: ConfigResolver
) -> None:
    recorded = tmp_path / "legacy" / "vertex_credentials.json"
    _write_service_account(recorded, "recorded")
    await _active_vertex_credential(db_session, credentials_path=str(recorded))

    config = await bound_resolver.provider_config("gemini-vertex")

    assert _loaded_project_id(config) == "recorded"
