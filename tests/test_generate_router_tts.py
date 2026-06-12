"""旁白配音（TTS）生成端点测试：单段入队、批量补缺、未配置供应商提示。"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.config.resolver import ConfigResolver, ProviderModel
from server.auth import CurrentUserInfo, get_current_user
from server.routers import generate


class _FakeQueue:
    """记录 enqueue 调用的假队列。"""

    def __init__(self):
        self.calls = []

    async def enqueue_task(self, **kwargs):
        self.calls.append(kwargs)
        return {"task_id": f"task-{len(self.calls)}", "deduped": False}


class _FakePM:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.project = {"content_mode": "narration"}
        self.script = {
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "duration_seconds": 4,
                    "novel_text": "夜色深沉，山道蜿蜒。",
                    "generated_assets": {},
                },
                {
                    "segment_id": "E1S02",
                    "duration_seconds": 4,
                    "novel_text": "他抬头望向远方的灯火。",
                    "generated_assets": {"narration_audio": "audio/segment_E1S02.wav"},
                },
                {
                    "segment_id": "E1S03",
                    "duration_seconds": 4,
                    "novel_text": "",
                    "generated_assets": {},
                },
            ],
        }

    def load_project(self, project_name):
        return self.project

    def get_project_path(self, project_name):
        return self.project_path

    def load_script(self, project_name, script_file):
        return self.script


def _client(monkeypatch, fake_pm, fake_queue, *, audio_provider_ready=True):
    monkeypatch.setattr(generate, "get_project_manager", lambda: fake_pm)
    monkeypatch.setattr(generate, "get_generation_queue", lambda: fake_queue)

    async def _resolve(self, project, payload):
        if not audio_provider_ready:
            raise ValueError("未找到可用的 audio 供应商")
        return ProviderModel("dashscope", "qwen3-tts-flash")

    monkeypatch.setattr(ConfigResolver, "resolve_audio_backend", _resolve)

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(generate.router, prefix="/api/v1")
    return TestClient(app)


class TestGenerateTtsSingle:
    def test_enqueue_success(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            res = client.post(
                "/api/v1/projects/demo/generate/tts/E1S01",
                json={"script_file": "episode_1.json"},
            )
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["success"] is True
            assert body["task_id"] == "task-1"
            assert "message" in body

            call = fake_queue.calls[0]
            assert call["project_name"] == "demo"
            assert call["task_type"] == "tts"
            assert call["media_type"] == "audio"
            assert call["resource_id"] == "E1S01"
            assert call["script_file"] == "episode_1.json"
            assert call["payload"]["script_file"] == "episode_1.json"
            assert call["source"] == "webui"
            # 路由层已解析过一次 provider，入队直接复用，不再逐段重复解析
            assert call["provider_id"] == "dashscope"

    def test_regenerate_allowed_when_audio_exists(self, tmp_path, monkeypatch):
        """已有旁白的段也允许重新生成（换音色/语速迭代）。"""
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            res = client.post(
                "/api/v1/projects/demo/generate/tts/E1S02",
                json={"script_file": "episode_1.json"},
            )
            assert res.status_code == 200, res.text
            assert len(fake_queue.calls) == 1

    def test_segment_not_found_404(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            res = client.post(
                "/api/v1/projects/demo/generate/tts/MISSING",
                json={"script_file": "episode_1.json"},
            )
            assert res.status_code == 404
            assert fake_queue.calls == []

    def test_empty_novel_text_400(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            res = client.post(
                "/api/v1/projects/demo/generate/tts/E1S03",
                json={"script_file": "episode_1.json"},
            )
            assert res.status_code == 400
            assert fake_queue.calls == []

    def test_audio_provider_not_configured_400(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue, audio_provider_ready=False)

        with client:
            res = client.post(
                "/api/v1/projects/demo/generate/tts/E1S01",
                json={"script_file": "episode_1.json"},
            )
            assert res.status_code == 400
            # 提示语明确指向音频供应商配置入口
            assert "音频" in res.json()["detail"]
            assert fake_queue.calls == []


class TestGenerateTtsBatch:
    def test_enqueues_only_missing_segments(self, tmp_path, monkeypatch):
        """批量只补缺：已有旁白（E1S02）与无原文（E1S03）的段都跳过。"""
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            res = client.post(
                "/api/v1/projects/demo/generate/tts",
                json={"script_file": "episode_1.json"},
            )
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["success"] is True
            assert body["task_ids"] == ["task-1"]
            assert "message" in body

            assert len(fake_queue.calls) == 1
            call = fake_queue.calls[0]
            assert call["resource_id"] == "E1S01"
            assert call["task_type"] == "tts"
            assert call["media_type"] == "audio"

    def test_none_missing_returns_empty(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        for seg in fake_pm.script["segments"]:
            seg["generated_assets"] = {"narration_audio": f"audio/segment_{seg['segment_id']}.wav"}
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            res = client.post(
                "/api/v1/projects/demo/generate/tts",
                json={"script_file": "episode_1.json"},
            )
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["success"] is True
            assert body["task_ids"] == []
            assert fake_queue.calls == []

    def test_audio_provider_not_configured_400(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue, audio_provider_ready=False)

        with client:
            res = client.post(
                "/api/v1/projects/demo/generate/tts",
                json={"script_file": "episode_1.json"},
            )
            assert res.status_code == 400
            assert fake_queue.calls == []

    def test_none_missing_skips_provider_check(self, tmp_path, monkeypatch):
        """无缺段时直接返回成功：即使 audio 供应商未配置也不应 400。"""
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        for seg in fake_pm.script["segments"]:
            seg["generated_assets"] = {"narration_audio": f"audio/segment_{seg['segment_id']}.wav"}
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue, audio_provider_ready=False)

        with client:
            res = client.post(
                "/api/v1/projects/demo/generate/tts",
                json={"script_file": "episode_1.json"},
            )
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["success"] is True
            assert body["task_ids"] == []
            assert fake_queue.calls == []
