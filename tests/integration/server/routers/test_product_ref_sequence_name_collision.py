"""商品 Cup 与 Cup_1 同时存在时，经真实 REST 入口改名 / 删除 / 恢复 Cup_1，不得带走 Cup 的原图（#3431）。"""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from lib.project.project_manager import ProjectManager
from server.error_handlers import register_error_handlers
from server.routers import files, products
from server.routers import project_trash as trash_router
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.fakes import use_project_manager

PRODUCTS = "/api/v1/projects/demo/products"
TRASH = "/api/v1/projects/demo/trash"


def _img(color=(255, 0, 0)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (8, 8), color).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    pm = ProjectManager(tmp_path / "data")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    use_project_manager(monkeypatch, pm)
    monkeypatch.setattr(files, "get_project_manager", lambda: pm)
    monkeypatch.setattr(products, "get_project_manager", lambda: pm)
    app = FastAPI()
    for module in (files, products, trash_router):
        app.include_router(module.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)
    override_auth(app)
    client = TestClient(app)
    for name in ("Cup", "Cup_1"):
        assert client.post(PRODUCTS, json={"name": name, "description": name}).status_code == 200
    uploads = {}
    for name, color in (("Cup", (1, 0, 0)), ("Cup", (2, 0, 0)), ("Cup_1", (3, 0, 0))):
        resp = client.post(
            f"/api/v1/projects/demo/upload/product_ref?name={name}", files={"file": ("a.png", _img(color), "image/png")}
        )
        assert resp.status_code == 200, resp.text
        uploads.setdefault(name, []).append(resp.json()["path"])
    project_dir = pm.get_project_path("demo")
    return client, pm, project_dir, uploads


def _products(project_dir: Path) -> dict:
    return json.loads((project_dir / "project.json").read_text(encoding="utf-8"))["products"]


def test_upload_names(setup):
    _client, _pm, _dir, uploads = setup
    print("uploads:", uploads)
    assert uploads == {
        "Cup": ["products/refs/Cup_1.png", "products/refs/Cup_2.png"],
        "Cup_1": ["products/refs/Cup_1_1.png"],
    }


def test_rename_cup_1_leaves_cup_untouched(setup):
    client, _pm, project_dir, uploads = setup
    cup_first = (project_dir / "products/refs/Cup_1.png").read_bytes()
    resp = client.post(f"{PRODUCTS}/Cup_1/rename", json={"new_name": "X"})
    print("rename:", resp.status_code, resp.text)
    print("refs dir after rename:", sorted(p.name for p in (project_dir / "products/refs").iterdir()))
    print("products after rename:", {k: v.get("reference_images") for k, v in _products(project_dir).items()})
    assert resp.status_code == 200
    assert _products(project_dir)["Cup"]["reference_images"] == uploads["Cup"]
    assert (project_dir / "products/refs/Cup_1.png").read_bytes() == cup_first


def test_delete_cup_1_leaves_cup_untouched(setup):
    client, _pm, project_dir, _uploads = setup
    resp = client.delete(f"{PRODUCTS}/Cup_1")
    assert resp.status_code == 200, resp.text
    record = client.get(TRASH).json()["records"][0]
    print("trash record files:", record["files"])
    print("refs dir after delete:", sorted(p.name for p in (project_dir / "products/refs").iterdir()))
    assert "products/refs/Cup_1.png" not in record["files"]
    assert (project_dir / "products/refs/Cup_1.png").is_file()


def test_delete_then_cup_uploads_again_then_restore_cup_1(setup):
    """删除期间 Cup 再传一张图：序号 1 已空出，新图拿到 Cup_1.png；再恢复 Cup_1。"""
    client, _pm, project_dir, _uploads = setup
    original_first = (project_dir / "products/refs/Cup_1.png").read_bytes()
    assert client.delete(f"{PRODUCTS}/Cup_1").status_code == 200
    resp = client.post(
        "/api/v1/projects/demo/upload/product_ref?name=Cup", files={"file": ("b.png", _img((9, 9, 9)), "image/png")}
    )
    print("re-upload path:", resp.json()["path"])
    record = client.get(TRASH).json()["records"][0]
    restored = client.post(f"{TRASH}/{record['id']}/restore")
    print("restore:", restored.status_code, restored.text)
    print("refs dir after restore:", sorted(p.name for p in (project_dir / "products/refs").iterdir()))
    print("products after restore:", {k: v.get("reference_images") for k, v in _products(project_dir).items()})
    # Cup 的原第 1 张图应当仍在 Cup 的 reference_images 指向的某个文件里
    cup_files = [
        (project_dir / rel).read_bytes()
        for rel in _products(project_dir)["Cup"]["reference_images"]
        if (project_dir / rel).is_file()
    ]
    assert original_first in cup_files


def test_restore_after_delete_returns_cup_image(setup):
    client, _pm, project_dir, _uploads = setup
    original_first = (project_dir / "products/refs/Cup_1.png").read_bytes()
    assert client.delete(f"{PRODUCTS}/Cup_1").status_code == 200
    record = client.get(TRASH).json()["records"][0]
    restored = client.post(f"{TRASH}/{record['id']}/restore")
    print("restore:", restored.status_code, restored.json().get("object"))
    assert restored.status_code == 200
    assert (project_dir / "products/refs/Cup_1.png").read_bytes() == original_first
