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
from lib.i18n import _ as i18n_message
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import grids
from tests.auth_deps import AUTH_DEPENDENCIES

pytestmark = pytest.mark.unit


def _narration_script(count: int = 4):
    """``count`` 个无 segment_break 的分段，凑成单组（默认 4 个，即 grid_4 恰好填满）。"""
    return {
        "content_mode": "narration",
        "segments": [
            {
                "segment_id": f"E1S{i:02d}",
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
            for i in range(1, count + 1)
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
    app.include_router(grids.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
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
        return {"content_mode": "narration", "generation_mode": "storyboard", "grid_storyboard": True}


class _FakePMGridDisabled(_FakePMPathOnly):
    """ProjectManager 替身：路线合法但宫格开关关闭。"""

    def load_project(self, name):
        return {"content_mode": "narration", "generation_mode": "storyboard", "grid_storyboard": False}


class _FakePMReferenceVideo(_FakePMPathOnly):
    """ProjectManager 替身：参考生视频路线，即使残留 grid_storyboard=true 也不激活宫格。"""

    def load_project(self, name):
        return {"content_mode": "narration", "generation_mode": "reference_video", "grid_storyboard": True}


def _assert_grid_switch_rejected(resp, queue) -> None:
    """断言响应是宫格开关专属的拒绝，且拒绝发生在入队之前（不产生计费任务）。"""
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == i18n_message("grid_storyboard_not_enabled")
    assert queue.calls == []


@pytest.mark.parametrize("fake_pm", [_FakePMGridDisabled, _FakePMReferenceVideo])
def test_generate_grid_rejected_when_switch_off(monkeypatch, fake_pm):
    # 宫格开关是入队闸门：未开宫格的项目直接 400，不产生计费任务
    fake_queue = _FakeQueue()
    client = _client(monkeypatch, get_project_manager=fake_pm, get_generation_queue=lambda: fake_queue)
    with client:
        resp = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "episode_1.json"},
        )
        _assert_grid_switch_rejected(resp, fake_queue)


@pytest.mark.parametrize("fake_pm", [_FakePMGridDisabled, _FakePMReferenceVideo])
def test_regenerate_grid_rejected_when_switch_off(monkeypatch, fake_pm):
    # 开关关闭后历史 grid 记录同样不可重新入队
    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=fake_pm,
        GridManager=_FakeGMNotFound,
        get_generation_queue=lambda: fake_queue,
    )
    with client:
        resp = client.post("/api/v1/projects/demo/grids/grid-123/regenerate")
        _assert_grid_switch_rejected(resp, fake_queue)


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
        return {
            "content_mode": "narration",
            "aspect_ratio": "9:16",
            "style": "anime",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
        }

    def load_script(self, name, script_file):
        raise ValueError(f"非法文件名: '{script_file}'")


def test_generate_grid_invalid_script_file(monkeypatch):
    # 非法 script_file（路径穿越等）是坏请求，400 而非落入下方 500 兜底
    client = _client(
        monkeypatch,
        get_project_manager=_FakePMInvalidScriptFile,
    )
    with client:
        resp = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "../../etc/passwd"},
        )
        assert resp.status_code == 400


class _FakePMGenerate:
    """ProjectManager 替身：驱动 generate_grid 成功路径，script/project_path 落 tmp_path。"""

    def __init__(self, project_path):
        self._project_path = project_path

    def load_project(self, name):
        return {
            "content_mode": "narration",
            "aspect_ratio": "9:16",
            "style": "anime",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
        }

    def load_script(self, name, script_file):
        return _narration_script()

    def get_project_path(self, name):
        return self._project_path


class _FakePMUnboundGrid(_FakePMGenerate):
    def load_project(self, name):
        return {
            **super().load_project(name),
            "schema_version": 8,
            "episodes": [],
        }


class _FakePMMismatchedGrid(_FakePMGenerate):
    def load_project(self, name):
        return {
            **super().load_project(name),
            "schema_version": 8,
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        }


def test_generate_grid_rejects_an_unbound_script_before_enqueue(monkeypatch, tmp_path):
    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMUnboundGrid(tmp_path),
        get_generation_queue=lambda: fake_queue,
    )

    with client:
        response = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "episode_1.json"},
        )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == i18n_message("invalid_script_file", name="episode_1.json")
    assert fake_queue.calls == []


def test_generate_grid_rejects_an_episode_path_that_mismatches_the_bound_script(monkeypatch, tmp_path):
    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMMismatchedGrid(tmp_path),
        get_generation_queue=lambda: fake_queue,
    )

    with client:
        response = client.post(
            "/api/v1/projects/demo/generate/grid/2",
            json={"script_file": "episode_1.json"},
        )

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == i18n_message("invalid_script_file", name="episode_1.json")
    assert fake_queue.calls == []


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


class _FakePMScenes(_FakePMGenerate):
    """``_FakePMGenerate`` 的变体：剧本分段数可指定，用于跨档位的阶梯断言。"""

    def __init__(self, project_path, scene_count: int):
        super().__init__(project_path)
        self._scene_count = scene_count

    def load_script(self, name, script_file):
        return _narration_script(self._scene_count)


def _generate_with_gate(monkeypatch, tmp_path, *, scene_count: int, allow_large_grid: bool):
    """跑一次 generate_grid，返回入队 payload 列表。4K 门控结果直接注入。"""

    async def _gate(_project):
        return allow_large_grid

    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMScenes(tmp_path, scene_count),
        get_generation_queue=lambda: fake_queue,
        resolve_large_grid_allowed=_gate,
    )
    with client:
        resp = client.post(
            "/api/v1/projects/demo/generate/grid/1",
            json={"script_file": "episode_1.json"},
        )
        assert resp.status_code == 200
    return [call["payload"] for call in fake_queue.calls]


@pytest.mark.parametrize(
    ("scene_count", "grid_size", "side"),
    [(10, "grid_16", 4), (16, "grid_16", 4), (17, "grid_25", 5), (25, "grid_25", 5)],
)
def test_generate_grid_uses_large_grid_when_4k(monkeypatch, tmp_path, scene_count, grid_size, side):
    payloads = _generate_with_gate(monkeypatch, tmp_path, scene_count=scene_count, allow_large_grid=True)
    assert len(payloads) == 1
    assert payloads[0]["grid_size"] == grid_size
    assert (payloads[0]["rows"], payloads[0]["cols"]) == (side, side)
    # 方形档整图比例即项目视频比例
    assert payloads[0]["grid_aspect_ratio"] == payloads[0]["video_aspect_ratio"] == "9:16"


def test_generate_grid_above_25_chunks_at_25(monkeypatch, tmp_path):
    payloads = _generate_with_gate(monkeypatch, tmp_path, scene_count=30, allow_large_grid=True)
    assert [len(p["scene_ids"]) for p in payloads] == [25, 5]
    assert [p["grid_size"] for p in payloads] == ["grid_25", "grid_9"]


@pytest.mark.parametrize("scene_count", [10, 17, 30])
def test_generate_grid_caps_at_9_without_4k(monkeypatch, tmp_path, scene_count):
    payloads = _generate_with_gate(monkeypatch, tmp_path, scene_count=scene_count, allow_large_grid=False)
    # 门控生效时切块封顶 9：不足一整块的余数落回更小的档位，但不会出现 4×4 / 5×5
    assert all(p["grid_size"] in {"grid_4", "grid_9"} for p in payloads)
    assert all(p["rows"] * p["cols"] <= 9 for p in payloads)
    assert sum(len(p["scene_ids"]) for p in payloads) == scene_count


@pytest.mark.parametrize("scene_count", [5, 6])
def test_generate_grid_5_and_6_scenes_use_grid_9(monkeypatch, tmp_path, scene_count):
    # grid_6 已删除：5~6 场景落 grid_9，不足的格由占位格补齐
    payloads = _generate_with_gate(monkeypatch, tmp_path, scene_count=scene_count, allow_large_grid=False)
    assert len(payloads) == 1
    assert payloads[0]["grid_size"] == "grid_9"
    assert (payloads[0]["rows"], payloads[0]["cols"]) == (3, 3)


def test_grid_capability_reports_gate(monkeypatch, tmp_path):
    async def _gate(_project):
        return True

    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMGenerate(tmp_path),
        resolve_large_grid_allowed=_gate,
    )
    with client:
        resp = client.get("/api/v1/projects/demo/grid-capability")
        assert resp.status_code == 200
        assert resp.json() == {"large_grid_allowed": True, "max_cell_count": 25}


def test_grid_capability_gated_max_cell_count(monkeypatch, tmp_path):
    async def _gate(_project):
        return False

    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMGenerate(tmp_path),
        resolve_large_grid_allowed=_gate,
    )
    with client:
        resp = client.get("/api/v1/projects/demo/grid-capability")
        assert resp.json() == {"large_grid_allowed": False, "max_cell_count": 9}


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
        video_aspect_ratio="9:16",
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
        video_aspect_ratio="9:16",
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
        return {
            "content_mode": "narration",
            "aspect_ratio": "9:16",
            "generation_mode": "storyboard",
            "grid_storyboard": True,
        }

    def load_script(self, name, script_file):
        return _narration_script()


class _FakePMRegenerateUnbound(_FakePMRegenerate):
    def load_project(self, name):
        return {
            **super().load_project(name),
            "schema_version": 8,
            "episodes": [],
        }


def test_regenerate_grid_rejects_an_unbound_script_without_mutating_the_record(monkeypatch, tmp_path):
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["a", "b", "c", "d"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="stale-provider",
        model="stale-model",
        video_aspect_ratio="9:16",
    )
    grid.status = "failed"
    grid.error_message = "boom"
    GridManager(tmp_path).save(grid)

    fake_queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMRegenerateUnbound(tmp_path),
        get_generation_queue=lambda: fake_queue,
    )
    with client:
        response = client.post(f"/api/v1/projects/demo/grids/{grid.id}/regenerate")

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == i18n_message("invalid_script_file", name="episode_1.json")
    assert fake_queue.calls == []
    assert GridManager(tmp_path).get(grid.id) == grid


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
        video_aspect_ratio="9:16",
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


def _regenerate_with_frozen_ratio(monkeypatch, tmp_path, frozen: str | None) -> tuple[GridGeneration, dict]:
    """按给定冻结值建档并重生成，返回落盘后的记录与入队 payload。"""
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["a", "b", "c", "d"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="p",
        model="m",
        video_aspect_ratio="16:9",
    )
    grid.video_aspect_ratio = frozen
    grid.status = "completed"
    GridManager(tmp_path).save(grid)

    queue = _FakeQueue()
    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMRegenerate(tmp_path),
        get_generation_queue=lambda: queue,
    )
    with client:
        assert client.post(f"/api/v1/projects/demo/grids/{grid.id}/regenerate").status_code == 200

    saved = GridManager(tmp_path).get(grid.id)
    assert saved is not None
    return saved, queue.calls[0]["payload"]


def test_regenerate_grid_keeps_frozen_aspect_ratio(monkeypatch, tmp_path):
    """重生成沿用记录冻结的比例，不改用项目当前比例。

    prompt 冻结在记录上、其中写死了画布比例；改用项目当前比例（_FakePMRegenerate 为
    9:16）会让下发参数与 prompt 描述的画布矛盾。
    """
    saved, payload = _regenerate_with_frozen_ratio(monkeypatch, tmp_path, "16:9")

    assert saved.video_aspect_ratio == "16:9"
    assert payload["video_aspect_ratio"] == "16:9"
    assert payload["grid_aspect_ratio"] == "16:9"


def test_regenerate_grid_backfills_missing_aspect_ratio(monkeypatch, tmp_path):
    """存量记录没有冻结值，重生成回落到项目当前比例并就地补齐。"""
    saved, payload = _regenerate_with_frozen_ratio(monkeypatch, tmp_path, None)

    assert saved.video_aspect_ratio == "9:16"
    assert payload["video_aspect_ratio"] == "9:16"


# ==================== 切分端点 ====================


def _make_completed_grid(tmp_path, *, with_image: bool = True) -> GridGeneration:
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["a", "b", "c", "d"],
        rows=2,
        cols=2,
        grid_size="grid_4",
        provider="p",
        model="m",
        video_aspect_ratio="9:16",
    )
    grid.status = "completed"
    grid.grid_image_path = f"grids/{grid.id}.png"
    if with_image:
        from PIL import Image

        (tmp_path / "grids").mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (100, 100)).save(tmp_path / "grids" / f"{grid.id}.png")
    GridManager(tmp_path).save(grid)
    return grid


def test_split_grid_success(monkeypatch, tmp_path):
    grid = _make_completed_grid(tmp_path)

    from server.services.grid_split import GridSplitResult

    calls = []

    async def fake_split(project_name, g):
        calls.append((project_name, g.id))
        g.split_at = "2026-01-01T00:00:00+00:00"
        return GridSplitResult(
            updated_scene_ids=["a", "b"],
            missing_scene_ids=["c"],
            asset_fingerprints={"storyboards/scene_a.png": 1},
        )

    client = _client(
        monkeypatch,
        get_project_manager=lambda: _FakePMRegenerate(tmp_path),
        apply_grid_split=fake_split,
    )
    with client:
        resp = client.post(f"/api/v1/projects/demo/grids/{grid.id}/split")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["updated_scene_ids"] == ["a", "b"]
        assert body["missing_scene_ids"] == ["c"]
        assert body["asset_fingerprints"] == {"storyboards/scene_a.png": 1}
        assert body["split_at"] == "2026-01-01T00:00:00+00:00"
    assert calls == [("demo", grid.id)]


def test_split_grid_rejected_when_switch_off(monkeypatch, tmp_path):
    grid = _make_completed_grid(tmp_path)
    client = _client(monkeypatch, get_project_manager=_FakePMGridDisabled)
    with client:
        resp = client.post(f"/api/v1/projects/demo/grids/{grid.id}/split")
        assert resp.status_code == 400
        assert resp.json()["detail"] == i18n_message("grid_storyboard_not_enabled")


def test_split_grid_conflict_while_generating(monkeypatch, tmp_path):
    # 生成在途的宫格拒绝切分：worker 完成时会覆写联合图，按旧图切分会被踩踏
    grid = _make_completed_grid(tmp_path)
    grid.status = "generating"
    GridManager(tmp_path).save(grid)

    client = _client(monkeypatch, get_project_manager=lambda: _FakePMRegenerate(tmp_path))
    with client:
        resp = client.post(f"/api/v1/projects/demo/grids/{grid.id}/split")
        assert resp.status_code == 409


def test_split_grid_image_not_ready(monkeypatch, tmp_path):
    # 联合图缺失（未生成完成且未上传）→ 400，业务语义明确不落通用 500
    grid = _make_completed_grid(tmp_path, with_image=False)

    client = _client(monkeypatch, get_project_manager=lambda: _FakePMRegenerate(tmp_path))
    with client:
        resp = client.post(f"/api/v1/projects/demo/grids/{grid.id}/split")
        assert resp.status_code == 400
        assert resp.json()["detail"] == i18n_message("grid_image_not_ready", grid_id=grid.id)


def test_split_grid_not_found(monkeypatch, tmp_path):
    client = _client(
        monkeypatch,
        get_project_manager=_FakePMNarration,
        GridManager=_FakeGMNotFound,
    )
    with client:
        resp = client.post("/api/v1/projects/demo/grids/grid-missing/split")
        assert resp.status_code == 404


# ==================== 联合图上传 ====================


def _png_bytes(size=(64, 64), color=(1, 2, 3)) -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_bytes(size=(64, 64), color=(9, 9, 9)) -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def test_upload_grid_image_normalizes_to_png_and_versions(monkeypatch, tmp_path):
    """非 PNG 输入归一化为 PNG 并登记新版本；宫格记录复位为「联合图就绪、待切分」。"""
    from lib.version_manager import VersionManager

    grid = _make_completed_grid(tmp_path)
    grid.status = "failed"
    grid.error_message = "boom"
    grid.split_at = "2026-01-01T00:00:00+00:00"
    GridManager(tmp_path).save(grid)

    monkeypatch.setattr(
        "server.services.generation_tasks.emit_generation_success_batch",
        lambda **kw: {f"grids/{grid.id}.png": 123},
    )
    client = _client(monkeypatch, get_project_manager=lambda: _FakePMRegenerate(tmp_path))
    with client:
        resp = client.post(
            f"/api/v1/projects/demo/grids/{grid.id}/upload",
            files={"file": ("photo.jpg", _jpeg_bytes(), "image/jpeg")},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["path"] == f"grids/{grid.id}.png"
        assert body["asset_fingerprints"] == {f"grids/{grid.id}.png": 123}

    # 落盘为合法 PNG
    from io import BytesIO

    from PIL import Image

    saved_bytes = (tmp_path / "grids" / f"{grid.id}.png").read_bytes()
    with Image.open(BytesIO(saved_bytes)) as img:
        assert img.format == "PNG"

    # 旧联合图补登 + 新版本登记（source=upload）
    versions = VersionManager(tmp_path).get_versions("grids", grid.id)
    assert len(versions["versions"]) >= 2

    saved = GridManager(tmp_path).get(grid.id)
    assert saved is not None
    assert saved.status == "completed"
    assert saved.error_message is None
    assert saved.split_at is None
    assert saved.grid_image_path == f"grids/{grid.id}.png"


def test_upload_grid_image_refreshes_frozen_aspect_ratio(monkeypatch, tmp_path):
    """手动补图按项目当前比例排布，记录上冻结的单格比例随之改写。

    沿用旧冻结值会让改过项目比例后补的图被按旧比例中心裁切。
    """
    grid = _make_completed_grid(tmp_path)
    grid.video_aspect_ratio = "16:9"
    GridManager(tmp_path).save(grid)

    monkeypatch.setattr(
        "server.services.generation_tasks.emit_generation_success_batch",
        lambda **kw: {},
    )
    # _FakePMRegenerate 的项目比例为 9:16，与记录冻结的 16:9 不同
    client = _client(monkeypatch, get_project_manager=lambda: _FakePMRegenerate(tmp_path))
    with client:
        resp = client.post(
            f"/api/v1/projects/demo/grids/{grid.id}/upload",
            files={"file": ("a.png", _png_bytes(), "image/png")},
        )
        assert resp.status_code == 200, resp.text

    saved = GridManager(tmp_path).get(grid.id)
    assert saved is not None
    assert saved.video_aspect_ratio == "9:16"


def test_upload_grid_image_registration_failure_restores_file_version_and_record(monkeypatch, tmp_path):
    from lib.version_manager import VersionManager

    grid = _make_completed_grid(tmp_path)
    grid.split_at = "2026-01-01T00:00:00+00:00"
    GridManager(tmp_path).save(grid)
    target = tmp_path / "grids" / f"{grid.id}.png"
    old_bytes = target.read_bytes()
    versions = VersionManager(tmp_path)
    versions.add_version("grids", grid.id, "old", source_file=target)
    versions_bytes = versions.versions_file.read_bytes()
    record = tmp_path / "grids" / f"{grid.id}.json"
    record_bytes = record.read_bytes()

    monkeypatch.setattr(
        grids,
        "register_current_resource_artifact",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("manifest commit failed")),
    )
    client = _client(monkeypatch, get_project_manager=lambda: _FakePMRegenerate(tmp_path))
    with client:
        resp = client.post(
            f"/api/v1/projects/demo/grids/{grid.id}/upload",
            files={"file": ("replacement.png", _png_bytes(color=(200, 1, 2)), "image/png")},
        )

    assert resp.status_code == 500
    assert target.read_bytes() == old_bytes
    assert versions.versions_file.read_bytes() == versions_bytes
    assert record.read_bytes() == record_bytes


def test_upload_grid_image_does_not_downscale(monkeypatch, tmp_path):
    """联合图上传不缩放：超过分镜图 2048 上限的大图原尺寸保留（4K 联合图切格不失真）。"""
    grid = _make_completed_grid(tmp_path)
    monkeypatch.setattr(
        "server.services.generation_tasks.emit_generation_success_batch",
        lambda **kw: {},
    )
    client = _client(monkeypatch, get_project_manager=lambda: _FakePMRegenerate(tmp_path))
    with client:
        resp = client.post(
            f"/api/v1/projects/demo/grids/{grid.id}/upload",
            files={"file": ("big.png", _png_bytes(size=(4096, 64)), "image/png")},
        )
        assert resp.status_code == 200, resp.text

    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO((tmp_path / "grids" / f"{grid.id}.png").read_bytes())) as img:
        assert img.size == (4096, 64)


def test_upload_grid_image_rejects_invalid_image(monkeypatch, tmp_path):
    grid = _make_completed_grid(tmp_path)
    client = _client(monkeypatch, get_project_manager=lambda: _FakePMRegenerate(tmp_path))
    with client:
        resp = client.post(
            f"/api/v1/projects/demo/grids/{grid.id}/upload",
            files={"file": ("bad.png", b"not-an-image", "image/png")},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == i18n_message("invalid_image_file")


def test_upload_grid_image_conflict_while_generating(monkeypatch, tmp_path):
    grid = _make_completed_grid(tmp_path)
    grid.status = "pending"
    GridManager(tmp_path).save(grid)
    client = _client(monkeypatch, get_project_manager=lambda: _FakePMRegenerate(tmp_path))
    with client:
        resp = client.post(
            f"/api/v1/projects/demo/grids/{grid.id}/upload",
            files={"file": ("a.png", _png_bytes(), "image/png")},
        )
        assert resp.status_code == 409


def test_upload_grid_image_rejected_when_switch_off(monkeypatch, tmp_path):
    grid = _make_completed_grid(tmp_path)
    client = _client(monkeypatch, get_project_manager=_FakePMGridDisabled)
    with client:
        resp = client.post(
            f"/api/v1/projects/demo/grids/{grid.id}/upload",
            files={"file": ("a.png", _png_bytes(), "image/png")},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == i18n_message("grid_storyboard_not_enabled")
