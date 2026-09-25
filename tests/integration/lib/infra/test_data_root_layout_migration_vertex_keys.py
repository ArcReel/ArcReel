"""数据根布局迁移入口：存量 Vertex 凭证迁移后收进数据根并照常可加载，旧位置被清空。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.config.resolver import ConfigResolver
from lib.config.system_config import resolve_vertex_credentials_path
from lib.db.repositories.credential_repository import CredentialRepository
from lib.infra.app_data_dir import reset_for_tests
from lib.infra.data_root_layout import DataRootLayout
from lib.infra.data_root_layout_migration import migrate_data_root_layout
from server.agent_runtime.agent_access_policy import AgentAccessPolicy


@pytest.fixture
def deployed_data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "deploy" / "projects"
    root.mkdir(parents=True)
    monkeypatch.setenv("ARCREEL_DATA_DIR", str(root))
    # 建库夹具可能已按默认数据根解析过一次。
    reset_for_tests()
    return root


@pytest.fixture
def legacy_keys_dir(deployed_data_root: Path) -> Path:
    """旧布局的凭证目录：数据根的上一级（Docker 部署里是单独挂载的卷）。"""
    return deployed_data_root.parent / "vertex_keys"


def _write_service_account(path: Path, project_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"project_id": project_id}), encoding="utf-8")


def _project_id_in(path: Path) -> str:
    return json.loads(path.read_text(encoding="utf-8"))["project_id"]


def _is_emptied(directory: Path) -> bool:
    return not any(directory.iterdir())


async def _stored_vertex_credential(
    session_factory: async_sessionmaker[AsyncSession], *, recorded_name: str, recorded_dir: Path
) -> int:
    """登记一条旧布局下的凭证：记录上存着凭证文件的绝对路径。"""
    async with session_factory() as session:
        repo = CredentialRepository(session)
        cred = await repo.create(provider="gemini-vertex", name="Vertex")
        await repo.update(cred.id, credentials_path=str(recorded_dir / recorded_name.format(id=cred.id)))
        await repo.activate(cred.id, "gemini-vertex")
        await session.commit()
        return cred.id


async def _loaded_credential(session_factory: async_sessionmaker[AsyncSession]) -> tuple[Path, str]:
    config = await ConfigResolver(session_factory).provider_config("gemini-vertex")
    path = Path(config["credentials_path"])
    return path, _project_id_in(path)


async def _migrate(deployed_data_root: Path, session_factory: async_sessionmaker[AsyncSession], tmp_path: Path) -> None:
    await migrate_data_root_layout(
        deployed_data_root, session_factory=session_factory, sdk_config_dir=tmp_path / "claude"
    )


@pytest.mark.parametrize(
    "recorded_in",
    [
        pytest.param("legacy", id="recorded-path-is-legacy-location"),
        # Docker 部署里记录的是容器内路径，在当前进程看来不存在；文件在旧卷里、同名。
        pytest.param("container", id="recorded-path-is-stale-container-path"),
    ],
)
async def test_existing_credential_loads_from_data_root_after_migration(
    tmp_path: Path,
    deployed_data_root: Path,
    legacy_keys_dir: Path,
    session_factory: async_sessionmaker[AsyncSession],
    recorded_in: str,
) -> None:
    recorded_dir = legacy_keys_dir if recorded_in == "legacy" else tmp_path / "app" / "vertex_keys"
    cred_id = await _stored_vertex_credential(
        session_factory, recorded_name="vertex_cred_{id}.json", recorded_dir=recorded_dir
    )
    _write_service_account(legacy_keys_dir / f"vertex_cred_{cred_id}.json", "existing")

    await _migrate(deployed_data_root, session_factory, tmp_path)

    path, project_id = await _loaded_credential(session_factory)
    assert project_id == "existing"
    assert path.is_relative_to(deployed_data_root)
    assert _is_emptied(legacy_keys_dir)


async def test_credential_recorded_outside_legacy_dir_is_copied_into_data_root_and_original_kept(
    tmp_path: Path, deployed_data_root: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    elsewhere = tmp_path / "elsewhere"
    await _stored_vertex_credential(session_factory, recorded_name="service-account.json", recorded_dir=elsewhere)
    _write_service_account(elsewhere / "service-account.json", "elsewhere")

    await _migrate(deployed_data_root, session_factory, tmp_path)

    path, project_id = await _loaded_credential(session_factory)
    assert project_id == "elsewhere"
    assert path.is_relative_to(deployed_data_root)
    assert _project_id_in(elsewhere / "service-account.json") == "elsewhere"


async def test_legacy_file_sharing_a_name_with_a_recorded_file_elsewhere_moves_in_by_name(
    tmp_path: Path, deployed_data_root: Path, legacy_keys_dir: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    elsewhere = tmp_path / "elsewhere"
    await _stored_vertex_credential(session_factory, recorded_name="service-account.json", recorded_dir=elsewhere)
    _write_service_account(elsewhere / "service-account.json", "recorded")
    _write_service_account(legacy_keys_dir / "service-account.json", "unregistered")

    await _migrate(deployed_data_root, session_factory, tmp_path)

    assert (await _loaded_credential(session_factory))[1] == "recorded"
    assert _project_id_in(DataRootLayout(deployed_data_root).vertex_keys_dir / "service-account.json") == "unregistered"
    assert _is_emptied(legacy_keys_dir)


async def test_unregistered_key_files_in_legacy_dir_move_into_data_root(
    tmp_path: Path, deployed_data_root: Path, legacy_keys_dir: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _write_service_account(legacy_keys_dir / "vertex_credentials.json", "dropped-in")

    await _migrate(deployed_data_root, session_factory, tmp_path)

    found = resolve_vertex_credentials_path()
    assert found is not None
    assert found.is_relative_to(deployed_data_root)
    assert _project_id_in(found) == "dropped-in"
    assert _is_emptied(legacy_keys_dir)


async def test_rerunning_migration_keeps_credential_loadable(
    tmp_path: Path, deployed_data_root: Path, legacy_keys_dir: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    cred_id = await _stored_vertex_credential(
        session_factory, recorded_name="vertex_cred_{id}.json", recorded_dir=legacy_keys_dir
    )
    _write_service_account(legacy_keys_dir / f"vertex_cred_{cred_id}.json", "existing")

    await _migrate(deployed_data_root, session_factory, tmp_path)
    first = await _loaded_credential(session_factory)
    await _migrate(deployed_data_root, session_factory, tmp_path)

    assert await _loaded_credential(session_factory) == first


async def test_resumes_after_file_was_copied_but_source_and_record_were_not_cleared(
    tmp_path: Path, deployed_data_root: Path, legacy_keys_dir: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    cred_id = await _stored_vertex_credential(
        session_factory, recorded_name="vertex_cred_{id}.json", recorded_dir=legacy_keys_dir
    )
    _write_service_account(legacy_keys_dir / f"vertex_cred_{cred_id}.json", "existing")
    _write_service_account(DataRootLayout(deployed_data_root).vertex_credential_path(cred_id), "existing")

    await _migrate(deployed_data_root, session_factory, tmp_path)

    path, project_id = await _loaded_credential(session_factory)
    assert project_id == "existing"
    assert path.is_relative_to(deployed_data_root)
    assert _is_emptied(legacy_keys_dir)


async def test_credential_whose_file_is_missing_stays_loadable_from_its_record_once_the_file_reappears(
    tmp_path: Path, deployed_data_root: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    unmounted = tmp_path / "unmounted"
    cred_id = await _stored_vertex_credential(
        session_factory, recorded_name="vertex_cred_{id}.json", recorded_dir=unmounted
    )

    await _migrate(deployed_data_root, session_factory, tmp_path)
    recorded = unmounted / f"vertex_cred_{cred_id}.json"
    _write_service_account(recorded, "remounted")
    await _migrate(deployed_data_root, session_factory, tmp_path)

    assert await _loaded_credential(session_factory) == (recorded, "remounted")


async def test_credentials_sharing_one_file_all_stay_loadable(
    tmp_path: Path, deployed_data_root: Path, legacy_keys_dir: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    first = await _stored_vertex_credential(
        session_factory, recorded_name="vertex_credentials.json", recorded_dir=legacy_keys_dir
    )
    second = await _stored_vertex_credential(
        session_factory, recorded_name="vertex_credentials.json", recorded_dir=legacy_keys_dir
    )
    _write_service_account(legacy_keys_dir / "vertex_credentials.json", "shared")

    await _migrate(deployed_data_root, session_factory, tmp_path)

    for cred_id in (first, second):
        async with session_factory() as session:
            await CredentialRepository(session).activate(cred_id, "gemini-vertex")
            await session.commit()
        path, project_id = await _loaded_credential(session_factory)
        assert project_id == "shared"
        assert path.is_relative_to(deployed_data_root)
    assert _is_emptied(legacy_keys_dir)


async def test_read_only_legacy_dir_does_not_block_migration(
    tmp_path: Path, deployed_data_root: Path, legacy_keys_dir: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    if os.name != "posix" or os.geteuid() == 0:
        pytest.skip("需要 POSIX 权限位且非 root 才能让旧目录删不掉文件")
    cred_id = await _stored_vertex_credential(
        session_factory, recorded_name="vertex_cred_{id}.json", recorded_dir=legacy_keys_dir
    )
    _write_service_account(legacy_keys_dir / f"vertex_cred_{cred_id}.json", "existing")
    _write_service_account(legacy_keys_dir / "vertex_credentials.json", "dropped-in")
    os.chmod(legacy_keys_dir, 0o555)

    try:
        await _migrate(deployed_data_root, session_factory, tmp_path)
        await _migrate(deployed_data_root, session_factory, tmp_path)
    finally:
        os.chmod(legacy_keys_dir, 0o755)

    path, project_id = await _loaded_credential(session_factory)
    assert project_id == "existing"
    assert path.is_relative_to(deployed_data_root)
    assert (
        _project_id_in(DataRootLayout(deployed_data_root).vertex_keys_dir / "vertex_credentials.json") == "dropped-in"
    )


async def test_bash_sandbox_denies_credential_dir_on_fresh_install(
    tmp_path: Path, deployed_data_root: Path, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _migrate(deployed_data_root, session_factory, tmp_path)

    repo = tmp_path / "repo"
    policy = AgentAccessPolicy(
        project_root=repo,
        data_root=deployed_data_root,
        agent_profile_root=repo / "agent_runtime_profile",
    )
    sandbox = policy.build_sandbox_settings(deployed_data_root / "demo", user_id="u1")

    assert str(DataRootLayout(deployed_data_root).vertex_keys_dir) in sandbox["filesystem"]["denyRead"]
