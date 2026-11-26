"""模型按提交动作记账；每一步从桶、关系库、版本库和回收站重新观察。"""

import asyncio
import hashlib
from collections import Counter
from contextlib import ExitStack
from contextvars import copy_context
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import pytest
from fastapi import FastAPI
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule, run_state_machine_as_test
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.artifacts.formal_write import project_metadata_lock
from lib.artifacts.version_manager import VersionManager
from lib.db.models.asset import Asset
from lib.db.models.object_storage import FormalObject, FormalObjectRef
from lib.material.formal_objects import (
    collect_unreferenced_objects,
    formal_commit,
    formal_object_key,
    reconcile_and_collect,
    reconcile_project_references,
)
from lib.material.global_library import library_holder
from lib.project import project_trash
from lib.project.project_manager import ProjectManager
from server.routers import assets
from tests.fakes import bind_session_factory, use_assets_router_sessions, use_object_storage_sdk
from tests.integration.server.routers.test_remote_material_storage import BUCKET, MemoryBucket, _remote
from tests.invariant_profiles import is_nightly_profile, state_machine_settings

pytestmark = pytest.mark.invariant
contents = st.sampled_from((b"A", b"B"))


def key(content: bytes) -> str:
    return formal_object_key(hashlib.sha256(content).hexdigest(), ".png")


class PublishFailure(Exception):
    pass


class FailAssetRefresh(AsyncSession):
    async def refresh(self, instance, *args, **kwargs):
        if isinstance(instance, Asset):
            raise PublishFailure
        await super().refresh(instance, *args, **kwargs)


def fail_selection():
    raise PublishFailure


class MaterialLifecycle(RuleBasedStateMachine):
    def __init__(self, example_database, tmp_path):
        super().__init__()
        self.resources = ExitStack()
        try:
            self.runner = self.resources.enter_context(asyncio.Runner())
            directory = Path(self.resources.enter_context(TemporaryDirectory(dir=tmp_path)))
            database = example_database(directory / "formal.sqlite3")
            self.sessions = self.runner.run(database.__aenter__())
            self.resources.callback(lambda: self.runner.run(database.__aexit__(None, None, None)))
            patch = self.resources.enter_context(pytest.MonkeyPatch.context())
            self.bucket = MemoryBucket()
            use_object_storage_sdk(patch, lambda config: self.bucket)
            bind_session_factory(patch, self.sessions)
            use_assets_router_sessions(
                patch, async_sessionmaker(self.sessions.kw["bind"], class_=FailAssetRefresh, expire_on_commit=False)
            )
            pm = ProjectManager(directory / "projects")
            patch.setattr(assets, "get_project_manager", lambda: pm)
            pm.create_project("demo")
            self.root = pm.get_project_path("demo")
            self.data_root = pm.data_root
            self.manager = VersionManager(self.root)
            self.manager.enable_sqlite_history()
            self.current_file = self.root / "characters/Alice.png"
            self.runner.run(_remote(self.sessions))
            app = FastAPI()
            app.include_router(assets.router)
            self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
            self.resources.callback(lambda: self.runner.run(self.client.aclose()))
            self.pending = []
            self.library = {}
            self.history = {}
            self.trash = {}
            self.identities = {}
            self.collectible: set[bytes] = set()
            self.current = 0
            self.serial = 0
        except BaseException:
            self.resources.close()
            raise

    def teardown(self):
        try:
            while self.pending:
                self.abort(0)
        finally:
            self.resources.close()

    @rule(content=contents)
    def begin(self, content):
        self.serial += 1
        holder = f"upload-{self.serial}"
        commit = formal_commit(holder, session_factory=self.sessions)
        context = copy_context()
        storage = self.runner.run(commit.__aenter__(), context=context)
        self.collectible.discard(content)
        self.pending.append((holder, content, commit, context))
        file = self.root / "upload.png"
        file.write_bytes(content)
        assert storage is not None
        self.runner.run(asyncio.to_thread(storage.store, file, hashlib.sha256(content).hexdigest()))

    @precondition(lambda self: bool(self.pending))
    @rule(index=st.integers(0, 3))
    def abort(self, index):
        _, _, commit, context = self.pending.pop(index % len(self.pending))
        self.runner.run(commit.__aexit__(None, None, None), context=context)

    @rule(content=contents)
    def publish_failure(self, content):
        self.serial += 1
        name = f"asset-{self.serial}"
        with pytest.raises(PublishFailure):
            self.runner.run(
                self.client.post(
                    "/assets",
                    data={"type": "character", "name": name},
                    files={"image": ("sheet.png", content, "image/png")},
                )
            )
        self.collectible.discard(content)
        self.library[name] = content

    @precondition(lambda self: bool(self.library))
    @rule()
    def release(self):
        name = next(iter(self.library))
        asset = next(a for a in self.runner.run(self.assets()) if a.name == name)
        assert self.runner.run(self.client.delete(f"/assets/{asset.id}")).status_code == 204
        del self.library[name]

    async def assets(self):
        async with self.sessions() as session:
            return list(await session.scalars(select(Asset)))

    @rule(content=contents, fail=st.booleans())
    def paid(self, content, fail):
        self.serial += 1
        prompt = f"paid-{self.serial}"
        version = max(self.history, default=0) + 1
        staged = self.root / "paid.png"
        staged.write_bytes(content)

        async def commit():
            async with formal_commit("demo", session_factory=self.sessions):
                await asyncio.to_thread(
                    self.manager.commit_staged_paid_version,
                    "characters",
                    "Alice",
                    prompt,
                    staged_file=staged,
                    current_file=self.current_file,
                    select_current=True,
                    on_select=fail_selection if fail else None,
                )

        if fail:
            with pytest.raises(PublishFailure):
                self.runner.run(commit())
        else:
            self.runner.run(commit())
            self.current = version
        self.collectible.discard(content)
        self.history[version] = (prompt, content)

    def rows(self):
        return self.manager.get_versions("characters", "Alice")

    @precondition(lambda self: bool(self.history.keys() - {self.current}))
    @rule(index=st.integers(0, 3))
    def delete(self, index):
        choices = sorted(self.history.keys() - {self.current})
        version = choices[index % len(choices)]
        row = next(r for r in self.rows()["versions"] if r["version"] == version)
        with project_metadata_lock(self.root):
            record = self.manager.delete_history_version(row["file"])
        self.trash[record.id] = self.history.pop(version)
        self.reconcile()

    @precondition(lambda self: bool(self.trash))
    @rule()
    def restore(self):
        record = next(iter(self.trash))
        version = max(self.history, default=0) + 1
        project_trash.restore(self.root, record)
        self.history[version] = self.trash.pop(record)
        self.reconcile()

    @precondition(lambda self: bool(self.trash))
    @rule()
    def purge(self):
        record = next(iter(self.trash))
        project_trash.purge(self.root, record)
        _, content = self.trash.pop(record)
        self.runner.run(reconcile_project_references("demo", self.root, self.sessions))
        held = set(self.library.values())
        held.update(value for _, value in (*self.history.values(), *self.trash.values()))
        held.update(value for _, value, _, _ in self.pending)
        if content not in held:
            self.collectible.add(content)

    def reconcile(self):
        self.runner.run(reconcile_and_collect("demo", self.root, self.sessions))

    @rule()
    def collect(self):
        async def run():
            async with self.sessions() as session:
                ids = list(await session.scalars(select(FormalObject.id)))
            assert await collect_unreferenced_objects(ids, self.sessions) == len(self.collectible)
            assert await collect_unreferenced_objects(ids, self.sessions) == 0

        self.runner.run(run())
        self.collectible.clear()

    @invariant()
    def matches_ledger(self):
        refs = Counter()
        data = self.rows()
        assert data["current_version"] == self.current
        assert {r["version"]: r["prompt"] for r in data["versions"]} == {v: p for v, (p, _) in self.history.items()}
        assert self.current_file.is_file() == bool(self.current)
        if self.current:
            assert self.current_file.read_bytes() == self.history[self.current][1]
        for row in data["versions"]:
            prompt, content = self.history[row["version"]]
            assert (self.root / row["file"]).read_bytes() == content
            identity = self.identities.setdefault(prompt, row["content_version_id"])
            assert row["content_version_id"] == identity
            assert row["storage"]["key"] == key(content)
            refs[content, "demo", identity, "active"] += 1
        assert {r.id for r in project_trash.list_records(self.root)} == self.trash.keys()
        for prompt, content in self.trash.values():
            refs[content, "demo", self.identities[prompt], "trashed"] += 1
        for holder, content, _, _ in self.pending:
            refs[content, holder, None, "pending"] += 1
        entries = self.runner.run(self.assets())
        assert {a.name for a in entries} == self.library.keys()
        for asset in entries:
            content = self.library[asset.name]
            assert (self.data_root / asset.image_path).read_bytes() == content
            assert asset.image_storage is not None
            assert asset.image_storage["key"] == key(content)
            refs[content, library_holder(asset.id), asset.image_content_id, "active"] += 1

        stored = self.collectible | {content for content, *_ in refs}

        async def stored_refs():
            async with self.sessions() as session:
                states = (await session.execute(select(FormalObject.object_key, FormalObject.state))).all()
                expected_keys = {key(content) for content in stored}
                assert {k for k, state in states if state == "stored"} == expected_keys
                assert all(state in {"stored", "deleted"} for _, state in states)
                return (
                    await session.execute(
                        select(
                            FormalObject.object_key,
                            FormalObjectRef.project_name,
                            FormalObjectRef.content_version_id,
                            FormalObjectRef.state,
                        ).join(FormalObjectRef, FormalObjectRef.object_id == FormalObject.id)
                    )
                ).all()

        assert Counter(self.runner.run(stored_refs())) == {
            (key(content), holder, identity, state): n for (content, holder, identity, state), n in refs.items()
        }
        assert self.bucket.objects == {(BUCKET, key(content)): content for content in stored}


@pytest.mark.timeout(600 if is_nightly_profile() else 120)
def test_material_lifecycle(example_database, tmp_path):
    run_state_machine_as_test(lambda: MaterialLifecycle(example_database, tmp_path), settings=state_machine_settings())
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def lifecycle(example_database, tmp_path):
    machine = MaterialLifecycle(example_database, tmp_path)
    try:
        yield machine
    finally:
        machine.teardown()


def test_other_pending_upload_survives_settlement(lifecycle):
    lifecycle.begin(b"A")
    lifecycle.begin(b"A")
    lifecycle.abort(0)
    assert lifecycle.bucket.objects == {(BUCKET, key(b"A")): b"A"}


def test_committed_library_image_survives_failed_response(lifecycle):
    lifecycle.publish_failure(b"A")
    [asset] = lifecycle.runner.run(lifecycle.assets())
    assert (lifecycle.data_root / asset.image_path).read_bytes() == b"A"
    lifecycle.matches_ledger()


def test_paid_history_survives_failed_selection(lifecycle):
    lifecycle.paid(b"A", True)
    [row] = lifecycle.rows()["versions"]
    assert (lifecycle.root / row["file"]).read_bytes() == b"A"
    assert lifecycle.rows()["current_version"] == 0


def test_restored_history_preserves_selection(lifecycle):
    lifecycle.paid(b"A", True)
    lifecycle.matches_ledger()
    lifecycle.delete(0)
    lifecycle.restore()
    assert lifecycle.rows()["current_version"] == 0
    lifecycle.matches_ledger()
