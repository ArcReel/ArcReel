"""两根 Agent 记忆路由（用户记忆 / 项目记忆）的对称行为。

用真实 ``ProjectManager`` 与 ``tmp_path`` 数据根穿过完整的 FastAPI 栈，断言的是 HTTP
可观察结果：状态码、JSON 形状、正文字节与磁盘落点。
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from lib.agent_memory_paths import project_memory_dir, user_memory_dir
from lib.agent_memory_store import INDEX_FILENAME, MAX_FILE_BYTES
from lib.i18n.zh import errors as zh_errors
from lib.project_manager import ProjectManager
from lib.project_migration_failure import record_migration_failure
from server.dependencies import require_project_migration_ok
from server.error_handlers import register_error_handlers
from server.routers import agent_memory
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth

USER_BASE = "/api/v1/agent/memory"
PROJECT_BASE = "/api/v1/projects/demo/agent-memory"


@pytest.fixture
def projects_root(tmp_path):
    root = tmp_path / "projects"
    (root / "demo").mkdir(parents=True)
    return root


@pytest.fixture
def client(projects_root, monkeypatch):
    monkeypatch.setattr(agent_memory, "get_project_manager", lambda: ProjectManager(str(projects_root)))

    app = FastAPI()
    app.include_router(agent_memory.user_router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.include_router(
        agent_memory.project_router,
        prefix="/api/v1",
        dependencies=[*AUTH_DEPENDENCIES, Depends(require_project_migration_ok)],
    )
    register_error_handlers(app)
    override_auth(app)
    return TestClient(app)


@pytest.fixture(params=[USER_BASE, PROJECT_BASE], ids=["user", "project"])
def base(request):
    """两级记忆共用一套断言：接口形状对称是本票的核心承诺。"""
    return request.param


def memory_dir(base: str, projects_root):
    if base == USER_BASE:
        return user_memory_dir(projects_root, "default")
    return project_memory_dir(projects_root / "demo")


class TestListing:
    def test_missing_directory_lists_as_empty(self, client, base, projects_root):
        response = client.get(base)

        assert response.status_code == 200
        assert response.json() == {
            "path": str(memory_dir(base, projects_root)),
            "index": {"exists": False, "line_count": 0, "byte_size": 0, "over_limit": False},
            "files": [],
        }

    def test_lists_entries_with_frontmatter_and_index_stats(self, client, base, projects_root):
        directory = memory_dir(base, projects_root)
        directory.mkdir(parents=True)
        (directory / INDEX_FILENAME).write_text("- tone.md\n", encoding="utf-8")
        (directory / "tone.md").write_text(
            "---\nname: Tone\ndescription: Short lines\ntype: user\n---\nbody\n", encoding="utf-8"
        )
        (directory / "raw.md").write_text("no frontmatter\n", encoding="utf-8")
        (directory / "notes.txt").write_text("invisible\n", encoding="utf-8")
        (directory / "nested").mkdir()
        (directory / "nested" / "deep.md").write_text("invisible\n", encoding="utf-8")

        payload = client.get(base).json()

        assert payload["index"] == {
            "exists": True,
            "line_count": 1,
            "byte_size": len("- tone.md"),
            "over_limit": False,
        }
        assert [(entry["name"], entry["frontmatter"]) for entry in payload["files"]] == [
            ("raw.md", None),
            ("tone.md", {"name": "Tone", "description": "Short lines", "type": "user"}),
        ]

    def test_oversized_index_is_flagged_but_still_writable(self, client, base):
        write = client.put(f"{base}/files/{INDEX_FILENAME}", content=b"line\n" * 201)

        assert write.status_code == 200
        assert client.get(base).json()["index"]["over_limit"] is True

    def test_invalid_frontmatter_type_leaves_the_entry_untagged(self, client, base):
        client.put(f"{base}/files/tone.md", content=b"---\nname: Tone\ntype: unknown\n---\n")

        assert client.get(base).json()["files"][0]["frontmatter"] is None


class TestReadWriteDelete:
    def test_upsert_read_and_delete_round_trip(self, client, base, projects_root):
        assert client.put(f"{base}/files/tone.md", content=b"first").status_code == 200
        assert client.put(f"{base}/files/tone.md", content=b"second").status_code == 200

        read = client.get(f"{base}/files/tone.md")
        assert read.status_code == 200
        assert read.content == b"second"
        assert read.headers["content-type"] == "text/plain; charset=utf-8"
        assert (memory_dir(base, projects_root) / "tone.md").read_bytes() == b"second"

        assert client.delete(f"{base}/files/tone.md").status_code == 200
        assert client.get(f"{base}/files/tone.md").status_code == 404

    def test_reserved_index_is_deletable(self, client, base):
        client.put(f"{base}/files/{INDEX_FILENAME}", content=b"- tone.md\n")

        assert client.delete(f"{base}/files/{INDEX_FILENAME}").status_code == 200
        assert client.get(base).json()["index"]["exists"] is False

    def test_missing_file_reads_404_with_its_error_key(self, client, base):
        response = client.get(f"{base}/files/absent.md")

        assert response.status_code == 404
        assert response.json()["detail"] == zh_errors.MESSAGES["memory_file_not_found"].format(filename="absent.md")

    def test_oversized_body_is_refused(self, client, base, projects_root):
        response = client.put(f"{base}/files/tone.md", content=b"x" * (MAX_FILE_BYTES + 1))

        assert response.status_code == 400
        assert response.json()["detail"] == zh_errors.MESSAGES["memory_file_too_large"].format(
            filename="tone.md", limit_kib=256
        )
        assert not (memory_dir(base, projects_root) / "tone.md").exists()

    def test_body_at_the_limit_is_accepted(self, client, base):
        assert client.put(f"{base}/files/tone.md", content=b"x" * MAX_FILE_BYTES).status_code == 200

    @pytest.mark.parametrize(
        ("encoded", "decoded"),
        [
            ("notes.txt", "notes.txt"),
            (".hidden.md", ".hidden.md"),
            ("-lead.md", "-lead.md"),
            # 反斜杠不是 URL 路径分隔符，穿越形态的文件名因此能走到处理函数里被规则挡下；
            # 它在 Windows 上是真正的分隔符，正是 safe_join 兜底的那一类。
            ("..%5Cescape.md", "..\\escape.md"),
        ],
    )
    def test_illegal_names_are_refused_on_every_method(self, client, base, encoded, decoded):
        for response in (
            client.get(f"{base}/files/{encoded}"),
            client.put(f"{base}/files/{encoded}", content=b"body"),
            client.delete(f"{base}/files/{encoded}"),
        ):
            assert response.status_code == 400
            assert response.json()["detail"] == zh_errors.MESSAGES["memory_invalid_filename"].format(filename=decoded)

    def test_traversal_never_writes_outside_the_memory_directory(self, client, base, projects_root):
        # 正斜杠形态在路由匹配阶段就落空（单段路径参数匹配不到带分隔符的 URL），
        # 拒绝发生在处理函数之前，因此这里只断言「被拒且没有任何东西落在目录外」。
        response = client.put(f"{base}/files/..%2F..%2Fescape.md", content=b"leak")

        assert response.status_code in {400, 404}
        assert not (projects_root / "escape.md").exists()
        assert not (projects_root.parent / "escape.md").exists()


class TestClear:
    def test_clear_empties_the_directory_without_an_index(self, client, base, projects_root):
        client.put(f"{base}/files/tone.md", content=b"body")
        client.put(f"{base}/files/{INDEX_FILENAME}", content=b"- tone.md\n")

        response = client.post(f"{base}/clear")

        assert response.status_code == 200
        directory = memory_dir(base, projects_root)
        assert directory.is_dir()
        assert list(directory.iterdir()) == []
        assert client.get(base).json() == {
            "path": str(directory),
            "index": {"exists": False, "line_count": 0, "byte_size": 0, "over_limit": False},
            "files": [],
        }


class TestProjectScoping:
    def test_unknown_project_is_404(self, client):
        assert client.get("/api/v1/projects/absent/agent-memory").status_code == 404

    def test_project_memory_lands_under_the_project_directory(self, client, projects_root):
        client.put(f"{PROJECT_BASE}/files/tone.md", content=b"body")

        assert (project_memory_dir(projects_root / "demo") / "tone.md").read_bytes() == b"body"
        assert not user_memory_dir(projects_root, "default").exists()

    def test_user_memory_is_not_visible_from_the_project_route(self, client):
        client.put(f"{USER_BASE}/files/tone.md", content=b"body")

        assert client.get(PROJECT_BASE).json()["files"] == []

    def test_blocked_migration_freezes_project_writes_but_not_reads(self, client, projects_root, monkeypatch):
        import lib.project_migration_guard as guard

        client.put(f"{PROJECT_BASE}/files/tone.md", content=b"body")
        record_migration_failure(projects_root / "demo", RuntimeError("broken chain"), schema_version=1)
        monkeypatch.setattr(guard, "get_project_manager", lambda: ProjectManager(str(projects_root)))

        assert client.get(PROJECT_BASE).status_code == 200
        assert client.get(f"{PROJECT_BASE}/files/tone.md").status_code == 200
        assert client.put(f"{PROJECT_BASE}/files/tone.md", content=b"next").status_code == 409
        assert client.delete(f"{PROJECT_BASE}/files/tone.md").status_code == 409
        assert client.post(f"{PROJECT_BASE}/clear").status_code == 409
        # 用户记忆不挂该守卫：它与任何项目的迁移状态无关。
        assert client.put(f"{USER_BASE}/files/tone.md", content=b"body").status_code == 200


class TestAuthentication:
    def test_routes_require_login(self, projects_root, monkeypatch):
        monkeypatch.setattr(agent_memory, "get_project_manager", lambda: ProjectManager(str(projects_root)))

        app = FastAPI()
        app.include_router(agent_memory.user_router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        register_error_handlers(app)

        with TestClient(app) as client:
            assert client.get(USER_BASE).status_code == 401
