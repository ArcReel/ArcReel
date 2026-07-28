"""products 资产路由（spec 工厂自动生成）：CRUD 全通 + 列表字段读写。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.auth import CurrentUserInfo, get_current_user
from server.routers import products


class _FakePM:
    def __init__(self):
        self.projects = {
            "demo": {
                "products": {
                    "保温杯": {
                        "description": "old",
                        "product_sheet": "",
                        "brand": "",
                        "reference_images": [],
                        "selling_points": [],
                    },
                }
            }
        }

    def _add_asset(self, asset_type, project_name, name, entry):
        # 守住路由 → spec 的绑定：products 路由若误传其他资产类型应当即失败
        assert asset_type == "product", f"products 路由应传 asset_type='product'，实际为 {asset_type!r}"
        if project_name not in self.projects:
            raise FileNotFoundError(project_name)
        bucket = self.projects[project_name].setdefault("products", {})
        if name in bucket:
            return False
        bucket[name] = entry
        return True

    def load_project(self, project_name):
        if project_name not in self.projects:
            raise FileNotFoundError(project_name)
        return self.projects[project_name]

    def save_project(self, project_name, project):
        self.projects[project_name] = project

    def update_project(self, project_name, mutate_fn):
        project = self.load_project(project_name)
        mutate_fn(project)
        self.save_project(project_name, project)


def _client(monkeypatch, fake_pm):
    monkeypatch.setattr(products, "get_project_manager", lambda: fake_pm)
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(products.router, prefix="/api/v1")
    return TestClient(app)


class TestProductsRouter:
    def test_add_update_delete(self, monkeypatch):
        fake_pm = _FakePM()
        with _client(monkeypatch, fake_pm) as client:
            add_resp = client.post(
                "/api/v1/projects/demo/products",
                json={"name": "蓝牙耳机", "description": "降噪", "brand": "声学社"},
            )
            assert add_resp.status_code == 200
            created = add_resp.json()["product"]
            assert created["description"] == "降噪"
            assert created["brand"] == "声学社"
            assert created["reference_images"] == []
            assert created["selling_points"] == []

            patch_resp = client.patch(
                "/api/v1/projects/demo/products/保温杯",
                json={"description": "new", "product_sheet": "products/保温杯.png"},
            )
            assert patch_resp.status_code == 200
            assert patch_resp.json()["product"]["product_sheet"].endswith("保温杯.png")

            delete_resp = client.delete("/api/v1/projects/demo/products/蓝牙耳机")
            assert delete_resp.status_code == 200

    def test_patch_list_fields(self, monkeypatch):
        fake_pm = _FakePM()
        with _client(monkeypatch, fake_pm) as client:
            resp = client.patch(
                "/api/v1/projects/demo/products/保温杯",
                json={
                    "selling_points": ["12 小时保温", "一键开盖"],
                    "reference_images": ["products/refs/保温杯_1.jpg"],
                },
            )
            assert resp.status_code == 200
            product = resp.json()["product"]
            assert product["selling_points"] == ["12 小时保温", "一键开盖"]
            assert product["reference_images"] == ["products/refs/保温杯_1.jpg"]

    def test_patch_rejects_invalid_list_field(self, monkeypatch):
        fake_pm = _FakePM()
        with _client(monkeypatch, fake_pm) as client:
            for bad in ("不是列表", [1, 2], [{"a": 1}]):
                resp = client.patch(
                    "/api/v1/projects/demo/products/保温杯",
                    json={"selling_points": bad},
                )
                assert resp.status_code == 422, bad
            # entry 未被污染
            assert fake_pm.projects["demo"]["products"]["保温杯"]["selling_points"] == []

    def test_error_mapping(self, monkeypatch):
        fake_pm = _FakePM()
        with _client(monkeypatch, fake_pm) as client:
            dup_resp = client.post(
                "/api/v1/projects/demo/products",
                json={"name": "保温杯", "description": ""},
            )
            assert dup_resp.status_code == 409

            missing_resp = client.patch(
                "/api/v1/projects/demo/products/不存在",
                json={"description": "x"},
            )
            assert missing_resp.status_code == 404

            missing_del = client.delete("/api/v1/projects/demo/products/不存在")
            assert missing_del.status_code == 404
