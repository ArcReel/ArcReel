"""宫格图路由测试：成功路径 + 「未预期异常 → 通用 500 且不泄露内部细节」回归测试。

未预期异常场景：每个端点内最早调用 get_project_manager()，把它 monkeypatch 成抛
RuntimeError（带唯一哨兵串），异常沿 app 级 exception handler 统一收口为通用 500。
断言响应 500 且哨兵串不出现在响应体里——验证内部异常细节仅落服务端日志、不泄露给客户端。
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.grid.models import GridGeneration
from lib.grid_manager import GridManager
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import grids


def _narration_script():
    """四个无 segment_break 的分段，凑成单组 grid_4（cell_count=4）。"""
    return {
        "content_mode": "narration",
        "segments": [
            {
                "segment_id": f"E1S0{i}",
                "episode": 1,
                "segment_break": False,
                "duration_seconds": 4,
                "novel_text": "text",
                "characters_in_segment": [],
                "scenes": [],
                "props": [],
                "image_prompt": {
                    "scene": f"scene{i}",
                    "composition": {"shot_type": "medium", "lighting": "natural", "ambiance": "calm"},
                },
                "video_prompt": {
                    "action": f"action{i}",
                    "camera_motion": "static",
                    "ambiance_audio": "quiet",
                    "dialogue": [],
                },
                "transition_to_next": "cut",
                "generated_assets": {"storyboard_image": None, "video_clip": None, "status": "pending"},
            }
            for i in range(1, 5)
        ],
    }


class _FakeQueue:
    """记录入队调用的假队列。"""

    def __init__(self):
        self.calls = []

    async def enqueue_task(self, **kwargs):
        self.calls.append(kwargs)
        return {"task_id": f"task-{len(self.calls)}", "deduped": False}


def _client(monkeypatch, **patches):
    for name, fn in patches.items():
        monkeypatch.setattr(grids, name, fn)
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(grids.router, prefix="/api/v1")
    register_error_handlers(app)
    # app 级 Exception handler 已把未预期异常收口为 500；关闭 TestClient 的默认重抛，
    # 以便断言收口后的响应体（而非让异常穿透到测试栈）。
    return TestClient(app, raise_server_exceptions=False)


def test_generate_grid_unexpected_error_no_leak(monkeypatch):
    # generate_grid 末端 catch-all：load_project 抛非预期异常时不泄露内部细节
    client = _client(
        monkeypatch,
        get_project_manager=lambda: (_ for _ in ()).throw(RuntimeError("LEAK_generate")),
    )
    with client:
        resp = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "episode_1.json"},
        )
        assert resp.status_code == 500
        assert "LEAK_generate" not in resp.text


def test_list_grids_unexpected_error_no_leak(monkeypatch):
    # list_grids 末端 catch-all：get_project_path 抛非预期异常时不泄露内部细节
    client = _client(
        monkeypatch,
        get_project_manager=lambda: (_ for _ in ()).throw(RuntimeError("LEAK_list")),
    )
    with client:
        resp = client.get("/api/v1/projects/demo/grids")
        assert resp.status_code == 500
        assert "LEAK_list" not in resp.text


def test_get_grid_unexpected_error_no_leak(monkeypatch):
    # get_grid 末端 catch-all：get_project_path 抛非预期异常时不泄露内部细节
    client = _client(
        monkeypatch,
        get_project_manager=lambda: (_ for _ in ()).throw(RuntimeError("LEAK_get")),
    )
    with client:
        resp = client.get("/api/v1/projects/demo/grids/grid-123")
        assert resp.status_code == 500
        assert "LEAK_get" not in resp.text


def test_regenerate_grid_unexpected_error_no_leak(monkeypatch):
    # regenerate_grid 末端 catch-all：load_project 抛非预期异常时不泄露内部细节
    client = _client(
        monkeypatch,
        get_project_manager=lambda: (_ for _ in ()).throw(RuntimeError("LEAK_regen")),
    )
    with client:
        resp = client.post("/api/v1/projects/demo/grids/grid-123/regenerate")
        assert resp.status_code == 500
        assert "LEAK_regen" not in resp.text


class _FakeGMNotFound:
    """GridManager 替身：get() 恒返回 None，模拟 grid_id 不存在。"""

    def __init__(self, project_path):
        pass

    def get(self, grid_id):
        return None


class _FakePMPathOnly:
    """ProjectManager 替身：仅提供 get_project_path，用于 grid_id 不存在场景。"""

    def get_project_path(self, name):
        return "/fake/path"


class _FakePMNarration(_FakePMPathOnly):
    """ProjectManager 替身：额外提供 load_project，用于 regenerate 的项目校验通过场景。"""

    def load_project(self, name):
        return {"content_mode": "narration"}


def test_get_grid_not_found(monkeypatch):
    # gm.get() 返回 None 时：raise NotFoundError("grid_not_found", ...) -> 404
    client = _client(
        monkeypatch,
        get_project_manager=_FakePMPathOnly,
        GridManager=_FakeGMNotFound,
    )
    with client:
        resp = client.get("/api/v1/projects/demo/grids/grid-missing")
        assert resp.status_code == 404


def test_regenerate_grid_not_found(monkeypatch):
    # ad 项目校验通过后 gm.get() 返回 None：raise NotFoundError("grid_not_found", ...) -> 404
    client = _client(
        monkeypatch,
        get_project_manager=_FakePMNarration,
        GridManager=_FakeGMNotFound,
    )
    with client:
        resp = client.post("/api/v1/projects/demo/grids/grid-missing/regenerate")
        assert resp.status_code == 404


class _FakePMInvalidName:
    """ProjectManager 替身：load_project / get_project_path 均模拟非法项目名（路径穿越等）。"""

    def load_project(self, name):
        raise ValueError(f"非法项目名称: '{name}'")

    def get_project_path(self, name):
        raise ValueError(f"非法项目名称: '{name}'")


def test_generate_grid_invalid_project_name(monkeypatch):
    # load_project 抛 ValueError：非法项目名是坏请求，不是「不存在」-> 400
    client = _client(
        monkeypatch,
        get_project_manager=_FakePMInvalidName,
    )
    with client:
        resp = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "episode_1.json"},
        )
        assert resp.status_code == 400


def test_list_grids_invalid_project_name(monkeypatch):
    client = _client(
        monkeypatch,
        get_project_manager=_FakePMInvalidName,
    )
    with client:
        resp = client.get("/api/v1/projects/demo/grids")
        assert resp.status_code == 400


def test_get_grid_invalid_project_name(monkeypatch):
    client = _client(
        monkeypatch,
        get_project_manager=_FakePMInvalidName,
    )
    with client:
        resp = client.get("/api/v1/projects/demo/grids/grid-123")
        assert resp.status_code == 400


def test_regenerate_grid_invalid_project_name(monkeypatch):
    client = _client(
        monkeypatch,
        get_project_manager=_FakePMInvalidName,
    )
    with client:
        resp = client.post("/api/v1/projects/demo/grids/grid-123/regenerate")
        assert resp.status_code == 400


class _FakePMCorrupted:
    """ProjectManager 替身：load_project 模拟 project.json 损坏（JSONDecodeError）。"""

    def load_project(self, name):
        raise json.JSONDecodeError("Expecting value", "", 0)


def test_generate_grid_corrupted_project_maps_to_500_not_invalid_name(monkeypatch):
    # JSONDecodeError 是 ValueError 子类：损坏的 project.json 不能被 except ValueError
    # 误判为「非法项目名」，须先于其拦截并映射为通用 500
    client = _client(
        monkeypatch,
        get_project_manager=_FakePMCorrupted,
    )
    with client:
        resp = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "episode_1.json"},
        )
        assert resp.status_code == 500
        assert "非法项目名称" not in resp.text


def test_regenerate_grid_corrupted_project_maps_to_500_not_invalid_name(monkeypatch):
    client = _client(
        monkeypatch,
        get_project_manager=_FakePMCorrupted,
    )
    with client:
        resp = client.post("/api/v1/projects/demo/grids/grid-123/regenerate")
        assert resp.status_code == 500
        assert "非法项目名称" not in resp.text


class _FakePMInvalidScriptFile:
    """ProjectManager 替身：load_script 模拟非法 script_file（路径穿越）。"""

    def load_project(self, name):
        return {"content_mode": "narration", "aspect_ratio": "9:16", "style": "anime"}

    def load_script(self, name, script_file):
        raise ValueError(f"非法文件名: '{script_file}'")


def test_generate_grid_invalid_script_file(monkeypatch):
    # 非法 script_file（路径穿越等）是坏请求，422 而非落入下方 500 兜底
    client = _client(
        monkeypatch,
        get_project_manager=_FakePMInvalidScriptFile,
    )
    with client:
        resp = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "../../etc/passwd"},
        )
        assert resp.status_code == 422


class _FakePMGenerate:
    """ProjectManager 替身：驱动 generate_grid 成功路径，script/project_path 落 tmp_path。"""

    def __init__(self, project_path):
        self._project_path = project_path

    def load_project(self, name):
        return {"content_mode": "narration", "aspect_ratio": "9:16", "style": "anime"}

    def load_script(self, name, script_file):
        return _narration_script()

    def get_project_path(self, name):
        return self._project_path


def test_generate_grid_success(monkeypatch, tmp_path):
    # 完整走一遍分组 -> 布局 -> prompt -> 入队，断言 200 且每组产出一个 grid_id/task_id
    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMGenerate(tmp_path),
        get_generation_queue=lambda: fake_queue,
    )
    with client:
        resp = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "episode_1.json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert len(body["grid_ids"]) == 1
        assert len(body["task_ids"]) == 1
        assert body["deduped"] is False
        # message 走 i18n（默认中文），不再硬编码
        assert body["message"] == "已提交 1 个宫格生成任务"
    assert len(fake_queue.calls) == 1
    saved = json.loads((tmp_path / "grids" / f"{body['grid_ids'][0]}.json").read_text(encoding="utf-8"))
    assert saved["scene_ids"] == ["E1S01", "E1S02", "E1S03", "E1S04"]


def test_generate_grid_success_message_localized_en(monkeypatch, tmp_path):
    # Accept-Language=en 时 message 按英文渲染，验证成功文案已接入 Translator
    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMGenerate(tmp_path),
        get_generation_queue=lambda: fake_queue,
    )
    with client:
        resp = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "episode_1.json"},
            headers={"Accept-Language": "en"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["message"] == "Submitted 1 grid generation tasks"


class _FakePMPath:
    """ProjectManager 替身：仅提供 get_project_path，指向 tmp_path。"""

    def __init__(self, project_path):
        self._project_path = project_path

    def get_project_path(self, name):
        return self._project_path


def test_list_grids_success(monkeypatch, tmp_path):
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["a", "b"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="",
        model="",
    )
    GridManager(tmp_path).save(grid)
    client = _client(monkeypatch, get_project_manager=lambda: _FakePMPath(tmp_path))
    with client:
        resp = client.get("/api/v1/projects/demo/grids")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["id"] == grid.id


def test_get_grid_success(monkeypatch, tmp_path):
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["a", "b"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="",
        model="",
    )
    GridManager(tmp_path).save(grid)
    client = _client(monkeypatch, get_project_manager=lambda: _FakePMPath(tmp_path))
    with client:
        resp = client.get(f"/api/v1/projects/demo/grids/{grid.id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == grid.id


@pytest.mark.parametrize(
    "bad_id",
    [
        "..%2F..%2Fetc%2Fpasswd",  # URL 编码的 ../../etc/passwd
        "grid_..%2F..%2Fsecret",  # 前缀合法但含穿越段
        "grid_ABCDEF123456",  # 大写十六进制不匹配白名单
        "not-a-grid-id",
    ],
)
def test_get_grid_malformed_id_returns_404(monkeypatch, tmp_path, bad_id):
    """grid_id 直接来自 URL 路径参数：格式非法一律 404，不落到文件系统读越界文件。"""
    outside = tmp_path.parent / "secret.json"
    outside.write_text('{"leak": true}', encoding="utf-8")
    client = _client(monkeypatch, get_project_manager=lambda: _FakePMPath(tmp_path))
    with client:
        resp = client.get(f"/api/v1/projects/demo/grids/{bad_id}")
        assert resp.status_code == 404
        assert "leak" not in resp.text


class _FakePMRegenerate(_FakePMPath):
    """ProjectManager 替身：驱动 regenerate_grid 成功路径。"""

    def load_project(self, name):
        return {"content_mode": "narration", "aspect_ratio": "9:16"}


def test_regenerate_grid_success(monkeypatch, tmp_path):
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["a", "b", "c", "d"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="stale-provider",
        model="stale-model",
    )
    grid.status = "failed"
    grid.error_message = "boom"
    GridManager(tmp_path).save(grid)

    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMRegenerate(tmp_path),
        get_generation_queue=lambda: fake_queue,
    )
    with client:
        resp = client.post(f"/api/v1/projects/demo/grids/{grid.id}/regenerate")
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["task_id"] == "task-1"
    assert len(fake_queue.calls) == 1
    saved = GridManager(tmp_path).get(grid.id)
    assert saved is not None
    assert saved.status == "pending"
    assert saved.error_message is None
    assert saved.provider == ""
