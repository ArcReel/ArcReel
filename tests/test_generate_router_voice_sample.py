"""角色 TTS 参考音频试听样本端点测试：音色列表 / 生成入队 / confirm 落资产。"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import lib.project_manager as project_manager_module
from lib.audio_backends.base import VoiceOption
from lib.config.resolver import ConfigResolver, ProviderModel
from lib.resource_paths import resource_relative_path
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import generate
from server.services.generation_context import AudioLaneResult, GenerationContext
from tests.auth_deps import AUTH_DEPENDENCIES

pytestmark = pytest.mark.unit


class _FakeQueue:
    def __init__(self, tasks: dict[str, dict] | None = None):
        self.calls = []
        self._tasks = tasks or {}

    async def enqueue_task(self, **kwargs):
        self.calls.append(kwargs)
        return {"task_id": f"task-{len(self.calls)}", "deduped": False}

    async def get_task(self, task_id):
        return self._tasks.get(task_id)


class _FakePM:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.project = {"characters": {"艾莉": {"description": "x"}}}
        self.updated_audio_refs = []

    def load_project(self, project_name):
        return self.project

    def get_project_path(self, project_name):
        return self.project_path

    def update_character_reference_audio(self, project_name, char_name, ref_path):
        if char_name not in self.project["characters"]:
            raise KeyError(char_name)
        self.project["characters"][char_name]["reference_audio"] = ref_path
        self.updated_audio_refs.append((char_name, ref_path))
        return self.project

    def install_character_reference_audio(self, project_name, char_name, ref_path, content):
        old_ref = self.project["characters"].get(char_name, {}).get("reference_audio")
        project = self.update_character_reference_audio(project_name, char_name, ref_path)
        target = self.project_path / ref_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        if old_ref and old_ref != ref_path:
            (self.project_path / old_ref).unlink(missing_ok=True)
        return project


def _app(monkeypatch, fake_pm, fake_queue, *, audio_provider_ready=True):
    monkeypatch.setattr(generate, "get_project_manager", lambda: fake_pm)
    monkeypatch.setattr(generate, "get_generation_queue", lambda: fake_queue)

    async def _resolve(self, project, payload):
        if not audio_provider_ready:
            raise ValueError("未找到可用的 audio 供应商")
        return ProviderModel("dashscope", "qwen3-tts-flash")

    monkeypatch.setattr(ConfigResolver, "resolve_audio_backend", _resolve)

    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.include_router(generate.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return app


def _client(monkeypatch, fake_pm, fake_queue, **kwargs):
    return TestClient(_app(monkeypatch, fake_pm, fake_queue, **kwargs), raise_server_exceptions=False)


class TestAudioBackendVoices:
    def test_not_configured_returns_empty(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        client = _client(monkeypatch, fake_pm, _FakeQueue(), audio_provider_ready=False)

        with client:
            res = client.get("/api/v1/projects/demo/audio-backend/voices")

        assert res.status_code == 200
        body = res.json()
        assert body == {"configured": False, "provider_id": None, "model": None, "voices": []}

    def _voices_ctx(self):
        return GenerationContext(
            generator=object(),
            audio_lane=AudioLaneResult(
                provider_model=ProviderModel("dashscope", "qwen3-tts-flash"),
                backend_name="dashscope",
                backend_model="qwen3-tts-flash",
                narration_voice="Cherry",
                narration_speed=None,
                # 生产口径：label 是 lib/i18n 翻译 key，不是直出文案（见
                # lib/audio_backends/dashscope.py 的 _VOICE_CATALOG 注释）。
                voices=(VoiceOption(id="Cherry", label="voice_label_dashscope_cherry"),),
            ),
        )

    def test_configured_returns_backend_catalog(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        client = _client(monkeypatch, fake_pm, _FakeQueue())
        ctx = self._voices_ctx()

        async def _resolve_ctx(*args, **kwargs):
            return ctx

        monkeypatch.setattr(generate, "resolve_generation_context", _resolve_ctx)

        with client:
            res = client.get("/api/v1/projects/demo/audio-backend/voices")

        assert res.status_code == 200
        body = res.json()
        assert body["configured"] is True
        assert body["provider_id"] == "dashscope"
        assert body["model"] == "qwen3-tts-flash"
        # 未带 Accept-Language 时按 DEFAULT_LOCALE（zh）渲染。
        assert body["voices"] == [{"id": "Cherry", "label": "芊悦 · 阳光正向的自然年轻女声"}]

    def test_configured_localizes_label_by_accept_language(self, tmp_path, monkeypatch):
        # DashScope 的 label 是翻译 key，未随请求 locale 渲染就是本条要防住的回归——
        # en/vi 用户看到的下拉会是硬编码中文描述而非本地化文案。
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        client = _client(monkeypatch, fake_pm, _FakeQueue())
        ctx = self._voices_ctx()

        async def _resolve_ctx(*args, **kwargs):
            return ctx

        monkeypatch.setattr(generate, "resolve_generation_context", _resolve_ctx)

        with client:
            res = client.get(
                "/api/v1/projects/demo/audio-backend/voices",
                headers={"Accept-Language": "en"},
            )

        assert res.status_code == 200
        body = res.json()
        assert body["voices"] == [{"id": "Cherry", "label": "Cherry — Warm, upbeat natural young female voice"}]


class TestGenerateCharacterVoiceSample:
    def test_enqueue_success(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            res = client.post(
                "/api/v1/projects/demo/characters/艾莉/voice-sample",
                json={"text": "你好，这是一段声音示例。", "voice": "Cherry"},
            )

        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        assert body["task_id"] == "task-1"
        assert len(fake_queue.calls) == 1
        call = fake_queue.calls[0]
        assert call["task_type"] == "voice_sample"
        assert call["media_type"] == "audio"
        assert call["resource_id"] == "艾莉"
        assert call["payload"]["prompt"] == "你好，这是一段声音示例。"
        assert call["payload"]["voice"] == "Cherry"

    def test_character_not_found(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        client = _client(monkeypatch, fake_pm, _FakeQueue())

        with client:
            res = client.post(
                "/api/v1/projects/demo/characters/不存在/voice-sample",
                json={"text": "你好", "voice": "Cherry"},
            )

        assert res.status_code == 404

    def test_invalid_character_name_400_not_500(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        client = _client(monkeypatch, fake_pm, _FakeQueue())

        with client:
            # 冒号是 Windows 保留字符，validate_asset_name 拒绝，但作为单段路径值本身合法，
            # 不会在到达路由处理函数之前就被 ASGI 路由层拦成 404。
            res = client.post(
                "/api/v1/projects/demo/characters/a%3Ab/voice-sample",
                json={"text": "你好", "voice": "Cherry"},
            )

        assert res.status_code == 400

    def test_audio_provider_not_configured(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        client = _client(monkeypatch, fake_pm, _FakeQueue(), audio_provider_ready=False)

        with client:
            res = client.post(
                "/api/v1/projects/demo/characters/艾莉/voice-sample",
                json={"text": "你好", "voice": "Cherry"},
            )

        assert res.status_code == 400

    def test_empty_text_rejected(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        client = _client(monkeypatch, fake_pm, _FakeQueue())

        with client:
            res = client.post(
                "/api/v1/projects/demo/characters/艾莉/voice-sample",
                json={"text": "   ", "voice": "Cherry"},
            )

        assert res.status_code == 400

    def test_overlong_text_rejected(self, tmp_path, monkeypatch):
        """整段粘贴的长文案在入队前就被挡住，不产生「先计费合成、再因超时长落 failed」。"""
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        fake_queue = _FakeQueue()
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            res = client.post(
                "/api/v1/projects/demo/characters/艾莉/voice-sample",
                json={"text": "长" * (generate.VOICE_SAMPLE_TEXT_MAX_LENGTH + 1), "voice": "Cherry"},
            )

        assert res.status_code == 400
        assert fake_queue.calls == []

    def test_empty_voice_rejected(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        client = _client(monkeypatch, fake_pm, _FakeQueue())

        with client:
            res = client.post(
                "/api/v1/projects/demo/characters/艾莉/voice-sample",
                json={"text": "你好", "voice": "  "},
            )

        assert res.status_code == 400


class TestConfirmCharacterVoiceSample:
    def _write_sample(self, project_path: Path, char_name: str, content: bytes = b"fake-wav") -> str:
        rel = resource_relative_path("audio", f"voice_sample__{char_name}")
        abs_path = project_path / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(content)
        return rel

    def test_confirm_success_writes_reference_audio(self, tmp_path, monkeypatch):
        project_path = tmp_path / "projects" / "demo"
        fake_pm = _FakePM(project_path)
        rel = self._write_sample(project_path, "艾莉")
        fake_queue = _FakeQueue(
            {
                "task-1": {
                    "project_name": "demo",
                    "task_type": "voice_sample",
                    "resource_id": "艾莉",
                    "status": "succeeded",
                    "result": {"file_path": rel},
                }
            }
        )
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            res = client.post(
                "/api/v1/projects/demo/characters/艾莉/voice-sample/confirm",
                json={"task_id": "task-1"},
            )

        assert res.status_code == 200
        body = res.json()
        assert body["success"] is True
        assert body["path"] == "characters/refs_audio/艾莉.wav"
        assert fake_pm.updated_audio_refs == [("艾莉", "characters/refs_audio/艾莉.wav")]
        assert (project_path / "characters" / "refs_audio" / "艾莉.wav").read_bytes() == b"fake-wav"

    def test_confirm_waits_for_video_selection_before_replacing_reference_audio(self, tmp_path, monkeypatch):
        pm = project_manager_module.ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        pm.add_character("demo", "艾莉", "desc")
        project_path = pm.get_project_path("demo")
        script_file = project_path / "scripts" / "episode_1.json"
        script_file.write_text(
            '{"episode": 1, "content_mode": "narration", "segments": []}',
            encoding="utf-8",
        )
        target = project_path / "characters" / "refs_audio" / "艾莉.wav"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"old-reference-audio")
        pm.update_character_reference_audio("demo", "艾莉", "characters/refs_audio/艾莉.wav")
        sample_rel = self._write_sample(project_path, "艾莉", b"new-reference-audio")
        fake_queue = _FakeQueue(
            {
                "task-1": {
                    "project_name": "demo",
                    "task_type": "voice_sample",
                    "resource_id": "艾莉",
                    "status": "succeeded",
                    "result": {"file_path": sample_rel},
                }
            }
        )
        client = _client(monkeypatch, pm, fake_queue)

        sample = project_path / sample_rel
        sample_read = Event()
        continue_confirm = Event()
        target_install_started = Event()
        original_read_bytes = Path.read_bytes
        original_replace = project_manager_module.os.replace

        def _paused_sample_read(path: Path) -> bytes:
            content = original_read_bytes(path)
            if path == sample:
                sample_read.set()
                assert continue_confirm.wait(timeout=2)
            return content

        def _observe_target_install(source, destination) -> None:
            if Path(destination) == target:
                target_install_started.set()
            original_replace(source, destination)

        monkeypatch.setattr(Path, "read_bytes", _paused_sample_read)
        monkeypatch.setattr(project_manager_module.os, "replace", _observe_target_install)

        with client, ThreadPoolExecutor(max_workers=1) as executor:
            request = executor.submit(
                client.post,
                "/api/v1/projects/demo/characters/艾莉/voice-sample/confirm",
                json={"task_id": "task-1"},
            )
            assert sample_read.wait(timeout=2)
            with pm.locked_project_script_snapshot("demo", "episode_1.json"):
                continue_confirm.set()
                assert not target_install_started.wait(timeout=0.5)
                assert original_read_bytes(target) == b"old-reference-audio"

            response = request.result(timeout=2)

        assert response.status_code == 200
        assert target_install_started.is_set()
        assert original_read_bytes(target) == b"new-reference-audio"

    def test_confirm_invalid_character_name_400_not_500(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        client = _client(monkeypatch, fake_pm, _FakeQueue())

        with client:
            res = client.post(
                "/api/v1/projects/demo/characters/a%3Ab/voice-sample/confirm",
                json={"task_id": "task-1"},
            )

        assert res.status_code == 400

    def test_confirm_unknown_task_404(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        client = _client(monkeypatch, fake_pm, _FakeQueue())

        with client:
            res = client.post(
                "/api/v1/projects/demo/characters/艾莉/voice-sample/confirm",
                json={"task_id": "missing"},
            )

        assert res.status_code == 404

    def test_confirm_task_for_different_character_404(self, tmp_path, monkeypatch):
        project_path = tmp_path / "projects" / "demo"
        fake_pm = _FakePM(project_path)
        rel = self._write_sample(project_path, "另一个")
        fake_queue = _FakeQueue(
            {
                "task-1": {
                    "project_name": "demo",
                    "task_type": "voice_sample",
                    "resource_id": "另一个",
                    "status": "succeeded",
                    "result": {"file_path": rel},
                }
            }
        )
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            res = client.post(
                "/api/v1/projects/demo/characters/艾莉/voice-sample/confirm",
                json={"task_id": "task-1"},
            )

        assert res.status_code == 404

    def test_confirm_not_succeeded_400(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        fake_queue = _FakeQueue(
            {
                "task-1": {
                    "project_name": "demo",
                    "task_type": "voice_sample",
                    "resource_id": "艾莉",
                    "status": "running",
                    "result": None,
                }
            }
        )
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            res = client.post(
                "/api/v1/projects/demo/characters/艾莉/voice-sample/confirm",
                json={"task_id": "task-1"},
            )

        assert res.status_code == 400

    def test_confirm_missing_result_file_path_400(self, tmp_path, monkeypatch):
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        fake_queue = _FakeQueue(
            {
                "task-1": {
                    "project_name": "demo",
                    "task_type": "voice_sample",
                    "resource_id": "艾莉",
                    "status": "succeeded",
                    "result": {},
                }
            }
        )
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            res = client.post(
                "/api/v1/projects/demo/characters/艾莉/voice-sample/confirm",
                json={"task_id": "task-1"},
            )

        assert res.status_code == 400

    def test_confirm_missing_sample_file_404(self, tmp_path, monkeypatch):
        """任务已 succeeded 且 result.file_path 非空，但样本文件在磁盘上不存在。"""
        fake_pm = _FakePM(tmp_path / "projects" / "demo")
        fake_queue = _FakeQueue(
            {
                "task-1": {
                    "project_name": "demo",
                    "task_type": "voice_sample",
                    "resource_id": "艾莉",
                    "status": "succeeded",
                    "result": {"file_path": "audio/voice_sample__艾莉__task-1.wav"},
                }
            }
        )
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            res = client.post(
                "/api/v1/projects/demo/characters/艾莉/voice-sample/confirm",
                json={"task_id": "task-1"},
            )

        assert res.status_code == 404

    def test_confirm_replaces_stale_reference_audio_with_different_extension(self, tmp_path, monkeypatch):
        """角色已有 reference_audio 指向不同扩展名的旧文件：确认后新文件写入且旧文件被清理。"""
        project_path = tmp_path / "projects" / "demo"
        fake_pm = _FakePM(project_path)
        fake_pm.project["characters"]["艾莉"]["reference_audio"] = "characters/refs_audio/艾莉.mp3"
        old_audio_path = project_path / "characters" / "refs_audio" / "艾莉.mp3"
        old_audio_path.parent.mkdir(parents=True, exist_ok=True)
        old_audio_path.write_bytes(b"old-mp3-bytes")

        rel = self._write_sample(project_path, "艾莉")
        fake_queue = _FakeQueue(
            {
                "task-1": {
                    "project_name": "demo",
                    "task_type": "voice_sample",
                    "resource_id": "艾莉",
                    "status": "succeeded",
                    "result": {"file_path": rel},
                }
            }
        )
        client = _client(monkeypatch, fake_pm, fake_queue)

        with client:
            res = client.post(
                "/api/v1/projects/demo/characters/艾莉/voice-sample/confirm",
                json={"task_id": "task-1"},
            )

        assert res.status_code == 200
        assert (project_path / "characters" / "refs_audio" / "艾莉.wav").exists()
        assert not old_audio_path.exists()
        assert fake_pm.updated_audio_refs == [("艾莉", "characters/refs_audio/艾莉.wav")]
