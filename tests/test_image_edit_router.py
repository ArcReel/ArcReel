"""图片指令式编辑端点（POST /projects/{name}/edit/image）的请求校验与入队行为。"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.config.resolver import ConfigResolver, ProviderModel
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import generate


class _FakeQueue:
    def __init__(self):
        self.calls = []

    async def enqueue_task(self, **kwargs):
        self.calls.append(kwargs)
        return {"task_id": f"task-{len(self.calls)}", "deduped": False}


class _FakePM:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.project = {
            "content_mode": "narration",
            "characters": {"Alice": {"character_sheet": "characters/Alice.png"}},
            "scenes": {"祠堂": {"scene_sheet": "scenes/祠堂.png"}},
            "props": {"玉佩": {"prop_sheet": "props/玉佩.png"}},
            "products": {"保温杯": {"product_sheet": "products/保温杯.png"}},
        }
        self.script = {
            "content_mode": "narration",
            "segments": [
                {"segment_id": "E1S01", "generated_assets": {}},
                {"segment_id": "E1S02", "generated_assets": {"storyboard_image": "storyboards/scene_E1S02_first.png"}},
                {"segment_id": "E1S03", "generated_assets": {}},
            ],
        }

    def load_project(self, project_name):
        return self.project

    def get_project_path(self, project_name):
        return self.project_path

    def load_script(self, project_name, script_file):
        return self.script


def _prepare_files(tmp_path: Path) -> Path:
    project_path = tmp_path / "projects" / "demo"
    for subdir in ("storyboards", "characters", "scenes", "props", "products"):
        (project_path / subdir).mkdir(parents=True, exist_ok=True)
    (project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"png")
    (project_path / "storyboards" / "scene_E1S02_first.png").write_bytes(b"png")
    (project_path / "characters" / "Alice.png").write_bytes(b"png")
    (project_path / "scenes" / "祠堂.png").write_bytes(b"png")
    (project_path / "props" / "玉佩.png").write_bytes(b"png")
    (project_path / "products" / "保温杯.png").write_bytes(b"png")
    return project_path


def _client(monkeypatch, fake_pm, fake_queue, *, i2i_ready=True):
    monkeypatch.setattr(generate, "get_project_manager", lambda: fake_pm)
    monkeypatch.setattr(generate, "get_generation_queue", lambda: fake_queue)

    async def _resolve(self, project, payload, *, capability):
        assert capability == "i2i"
        if not i2i_ready:
            raise ValueError("未找到可用的 image 供应商")
        return ProviderModel("gemini-aistudio", "gemini-image")

    monkeypatch.setattr(ConfigResolver, "resolve_image_backend", _resolve)

    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(generate.router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


class TestEditImageEnqueue:
    def test_asset_types_enqueue_success(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, _FakePM(project_path), fake_queue)

        cases = [("character", "Alice"), ("scene", "祠堂"), ("prop", "玉佩"), ("product", "保温杯")]
        with client:
            for i, (resource_type, resource_id) in enumerate(cases):
                resp = client.post(
                    "/api/v1/projects/demo/edit/image",
                    json={"resource_type": resource_type, "resource_id": resource_id, "instruction": "把头发改成红色"},
                )
                assert resp.status_code == 200, f"{resource_type}: {resp.text}"
                body = resp.json()
                assert body["success"] is True
                assert body["task_id"] == f"task-{i + 1}"

                call = fake_queue.calls[i]
                assert call["task_type"] == "image_edit"
                assert call["media_type"] == "image"
                assert call["resource_id"] == resource_id
                assert call["source"] == "webui"
                # 顶层 resource_type 纳入 image_edit 去重键，避免不同资产类型同名互相误判去重
                assert call["resource_type"] == resource_type
                assert call["payload"]["resource_type"] == resource_type
                assert call["payload"]["prompt"] == "把头发改成红色"
                # i2i 槽在入队前已解析，provider_id 直接复用（限流池按 i2i 槽精确记账）
                assert call["provider_id"] == "gemini-aistudio"

    def test_storyboard_enqueue_success_carries_script_file(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, _FakePM(project_path), fake_queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/edit/image",
                json={
                    "resource_type": "storyboard",
                    "resource_id": "E1S01",
                    "instruction": "去掉背景里的路人",
                    "script_file": "episode_1.json",
                },
            )
            assert resp.status_code == 200, resp.text
            call = fake_queue.calls[0]
            assert call["task_type"] == "image_edit"
            assert call["script_file"] == "episode_1.json"
            assert call["payload"]["script_file"] == "episode_1.json"
            assert call["payload"]["resource_type"] == "storyboard"

    def test_storyboard_pointer_path_accepted(self, tmp_path, monkeypatch):
        """generated_assets.storyboard_image 指向非 canonical 路径（旧宫格项目）也可编辑。"""
        project_path = _prepare_files(tmp_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, _FakePM(project_path), fake_queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/edit/image",
                json={
                    "resource_type": "storyboard",
                    "resource_id": "E1S02",
                    "instruction": "调亮光线",
                    "script_file": "episode_1.json",
                },
            )
            assert resp.status_code == 200, resp.text


class TestEditImageValidation:
    def test_resource_type_whitelist_400(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, _FakePM(project_path), fake_queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/edit/image",
                json={"resource_type": "grid", "resource_id": "g1", "instruction": "x"},
            )
            assert resp.status_code == 400
            assert fake_queue.calls == []

    def test_blank_instruction_400(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, _FakePM(project_path), fake_queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/edit/image",
                json={"resource_type": "character", "resource_id": "Alice", "instruction": "   "},
            )
            assert resp.status_code == 400
            assert fake_queue.calls == []

    def test_storyboard_requires_script_file_400(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, _FakePM(project_path), fake_queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/edit/image",
                json={"resource_type": "storyboard", "resource_id": "E1S01", "instruction": "x"},
            )
            assert resp.status_code == 400
            assert fake_queue.calls == []

    def test_storyboard_blank_script_file_400(self, tmp_path, monkeypatch):
        """script_file 为纯空白字符串时应等同未提供，不能绕过必填校验。"""
        project_path = _prepare_files(tmp_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, _FakePM(project_path), fake_queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/edit/image",
                json={"resource_type": "storyboard", "resource_id": "E1S01", "instruction": "x", "script_file": "   "},
            )
            assert resp.status_code == 400
            assert fake_queue.calls == []

    def test_i2i_unavailable_400_no_task(self, tmp_path, monkeypatch):
        """i2i 槽解析不出供应商：入队前 fail-fast，不创建任务。"""
        project_path = _prepare_files(tmp_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, _FakePM(project_path), fake_queue, i2i_ready=False)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/edit/image",
                json={"resource_type": "character", "resource_id": "Alice", "instruction": "x"},
            )
            assert resp.status_code == 400, resp.text
            assert fake_queue.calls == []

    def test_no_current_image_400(self, tmp_path, monkeypatch):
        """目标资源无 current 图（sheet 未设置 / 分镜图文件不存在）时拒绝入队。"""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["characters"]["Alice"]["character_sheet"] = ""
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/edit/image",
                json={"resource_type": "character", "resource_id": "Alice", "instruction": "x"},
            )
            assert resp.status_code == 400
            # E1S03 无 generated_assets 指针且 canonical 文件不存在
            resp = client.post(
                "/api/v1/projects/demo/edit/image",
                json={
                    "resource_type": "storyboard",
                    "resource_id": "E1S03",
                    "instruction": "x",
                    "script_file": "episode_1.json",
                },
            )
            assert resp.status_code == 400
            assert fake_queue.calls == []

    def test_sheet_file_missing_on_disk_400(self, tmp_path, monkeypatch):
        """sheet 字段有值但文件不在磁盘上：同样按无 current 图拒绝。"""
        project_path = _prepare_files(tmp_path)
        (project_path / "characters" / "Alice.png").unlink()
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, _FakePM(project_path), fake_queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/edit/image",
                json={"resource_type": "character", "resource_id": "Alice", "instruction": "x"},
            )
            assert resp.status_code == 400
            assert fake_queue.calls == []

    def test_asset_not_found_404(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, _FakePM(project_path), fake_queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/edit/image",
                json={"resource_type": "character", "resource_id": "不存在", "instruction": "x"},
            )
            assert resp.status_code == 404
            assert fake_queue.calls == []

    def test_segment_not_found_404(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, _FakePM(project_path), fake_queue)

        with client:
            resp = client.post(
                "/api/v1/projects/demo/edit/image",
                json={
                    "resource_type": "storyboard",
                    "resource_id": "E9S99",
                    "instruction": "x",
                    "script_file": "episode_1.json",
                },
            )
            assert resp.status_code == 404
            assert fake_queue.calls == []
