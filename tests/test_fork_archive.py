"""ForkProjectArchiveService 归属重写测试。"""

from __future__ import annotations

import pytest

from lib.project_manager import ProjectManager
from server.auth import CurrentUserInfo
from server.fork_archive import ForkProjectArchiveService


def _make_user(sub: str, role: str) -> CurrentUserInfo:
    return CurrentUserInfo(id=sub, sub=sub, role=role)


def _service(tmp_path, user: CurrentUserInfo) -> ForkProjectArchiveService:
    pm = ProjectManager(tmp_path / "projects")
    return ForkProjectArchiveService(pm, current_user=user)


@pytest.mark.unit
class TestForkProjectArchiveOwnerRewrite:
    """fork-only：导入 ZIP 时按当前用户重写 owner 前缀。"""

    def test_user_importing_others_zip_rewrites_owner(self, tmp_path):
        svc = _service(tmp_path, _make_user("bob", "user"))
        target = svc._resolve_target_project_name(
            {"title": "Demo"},
            manifest={"project_name": "alice__demo"},
            root_parts=("alice__demo",),
            uploaded_filename="alice__demo.zip",
        )
        assert target == "bob__demo"

    def test_user_importing_legacy_no_prefix_zip_gets_owner(self, tmp_path):
        svc = _service(tmp_path, _make_user("bob", "user"))
        target = svc._resolve_target_project_name(
            {"title": "Demo"},
            manifest={"project_name": "demo"},
            root_parts=("demo",),
            uploaded_filename="demo.zip",
        )
        assert target == "bob__demo"

    def test_user_importing_own_zip_unchanged(self, tmp_path):
        svc = _service(tmp_path, _make_user("bob", "user"))
        target = svc._resolve_target_project_name(
            {"title": "Demo"},
            manifest={"project_name": "bob__demo"},
            root_parts=("bob__demo",),
            uploaded_filename="bob__demo.zip",
        )
        assert target == "bob__demo"

    def test_admin_preserves_original_owner(self, tmp_path):
        svc = _service(tmp_path, _make_user("admin", "admin"))
        target = svc._resolve_target_project_name(
            {"title": "Demo"},
            manifest={"project_name": "alice__demo"},
            root_parts=("alice__demo",),
            uploaded_filename="alice__demo.zip",
        )
        assert target == "alice__demo"

    def test_admin_legacy_no_prefix_unchanged(self, tmp_path):
        svc = _service(tmp_path, _make_user("admin", "admin"))
        target = svc._resolve_target_project_name(
            {"title": "Demo"},
            manifest={"project_name": "demo"},
            root_parts=("demo",),
            uploaded_filename="demo.zip",
        )
        assert target == "demo"
