"""Deletion disconnects old DB records from same-name recreated projects, retaining costs under deleted identities."""

from lib.asset_library.bindings import AssetLibraryScope
from lib.asset_library.links import selected_link
from lib.db.models.asset_library import ProviderAssetLink
from lib.db.repositories.session_repo import SessionRepository
from lib.db.repositories.task_repo import TaskRepository
from lib.db.repositories.usage_repo import UsageFilters, UsageRepository
from lib.project.project_manager import ProjectManager
from tests.integration.server.routers.projects_router_support import build_projects_client, seed_project_records


async def test_same_name_project_after_delete_inherits_no_records(tmp_path, monkeypatch, db_factory):
    manager = ProjectManager(tmp_path)
    manager.create_project("demo")
    old = await seed_project_records(db_factory)
    scope = AssetLibraryScope(
        account_id=1, account_identity="TEST-identity", site="volcengine", region="cn-beijing", project_name="film"
    )
    async with db_factory() as session:
        session.add(
            ProviderAssetLink(
                project_name="demo",
                content_version_id="cv-1",
                content_digest="0" * 64,
                resource_type="characters",
                resource_id="Alice",
                version=1,
                locator_path="versions/characters/Alice_v1.png",
                origin="upload",
                account_id=scope.account_id,
                account_identity=scope.account_identity,
                site=scope.site,
                region=scope.region,
                provider_project_name=scope.project_name,
                state="active",
                provider_asset_id="asset-1",
                submit_name="Alice-arc-1",
                selected=True,
                check_attempts=0,
            )
        )
        await session.commit()

    with build_projects_client(monkeypatch, manager, session_factory=db_factory) as client:
        response = client.delete("/api/v1/projects/demo")
    assert response.status_code == 200
    manager.create_project("demo")

    async with db_factory() as session:
        tasks = TaskRepository(session)
        assert (await tasks.list_tasks(project_name="demo"))["total"] == 0
        assert (await tasks.get(old["queued"]) or {})["status"] == "cancelled"
        # Workers never claim tasks queued before deletion.
        assert await tasks.claim_next("image") is None

        # New same-resource requests create tasks, never merge with old running jobs.
        fresh = await tasks.enqueue(project_name="demo", task_type="video", media_type="video", resource_id="E1S02")
        assert fresh["deduped"] is False
        assert fresh["task_id"] != old["running"]

        assert await SessionRepository(session).list(project_name="demo") == []
        # Same-name imports with identical content-version identities never inherit old provider-material associations.
        assert await selected_link(session, project_name="demo", content_version_id="cv-1", scope=scope) is None

        usage = UsageRepository(session)
        assert await usage.fetch_summary_rows(filters=UsageFilters(project_name="demo")) == []
        [deleted_name] = [name for name in (await usage.fetch_usage_filter_options()).projects if name != "demo"]
        assert deleted_name.startswith("demo#deleted-")
        [row] = await usage.fetch_summary_rows(filters=UsageFilters(project_name=deleted_name))
        assert row.cost_amount == 1.5
        assert (await tasks.get(old["running"]) or {})["project_name"] == deleted_name


async def test_delete_missing_project_is_404_and_leaves_records(tmp_path, monkeypatch, db_factory):
    old = await seed_project_records(db_factory)

    with build_projects_client(monkeypatch, ProjectManager(tmp_path), session_factory=db_factory) as client:
        response = client.delete("/api/v1/projects/demo")
    assert response.status_code == 404

    async with db_factory() as session:
        assert (await TaskRepository(session).get(old["queued"]) or {})["status"] == "queued"
