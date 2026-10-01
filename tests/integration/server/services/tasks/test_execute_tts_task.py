"""旁白配音执行使用项目快照，不能付费调用回退模型。"""

import pytest
import respx

from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.project.project_manager import ProjectManager
from lib.speech.narration_config import NarrationConfigError
from server.services.tasks import generation_context, generation_tasks


async def test_disabled_snapshot_model_blocks_tts_before_paid_synthesis(db_factory, tmp_path, monkeypatch):
    async with db_factory() as session:
        provider = CustomProvider(
            display_name="TTS",
            discovery_format="openai",
            base_url="https://tts.example.com/v1",
            api_key="test",
        )
        session.add(provider)
        await session.flush()
        session.add_all(
            [
                CustomProviderModel(
                    provider_id=provider.id,
                    model_id="snapshot-tts",
                    display_name="Snapshot",
                    endpoint="openai-tts",
                    is_enabled=False,
                ),
                CustomProviderModel(
                    provider_id=provider.id,
                    model_id="default-tts",
                    display_name="Default",
                    endpoint="openai-tts",
                    is_default=True,
                ),
            ]
        )
        await session.commit()
        backend = f"{provider.provider_id}/snapshot-tts"

    manager = ProjectManager(tmp_path)
    manager.create_project("demo", publish=False)
    original = manager.create_project_metadata(
        "demo",
        narration={"narration_delivery": "use_tts", "audio_backend": backend, "narration_voice": "alloy"},
    )
    monkeypatch.setattr("lib.db.async_session_factory", db_factory)
    monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: manager)
    monkeypatch.setattr(generation_context, "get_project_manager", lambda: manager)
    generation_context.invalidate_backend_cache()
    try:
        with respx.mock, pytest.raises(NarrationConfigError) as caught:
            await generation_tasks.execute_tts_task("demo", "E1S01", {"text": "旁白"})
        assert caught.value.code == "narration_tts_model_invalid"
        assert manager.load_project("demo") == original
        assert not (manager.get_project_path("demo") / "audio" / "segment_E1S01.wav").exists()
    finally:
        generation_context.invalidate_backend_cache()
