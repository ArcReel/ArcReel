"""文本与音频统一运行时解析、装配与纯能力内核收口测试（Issue #4015）。"""

from __future__ import annotations

from dataclasses import fields
from datetime import date

import pytest
from sqlalchemy import event, update

from lib.backends.audio_backends.base import AudioCapability
from lib.backends.text_backends.base import TextTaskType
from lib.backends.text_generator import TextGenerator
from lib.capabilities.kernel import CapabilitySnapshot
from lib.catalog import query as catalog
from lib.catalog.errors import ModelDiscontinuedError
from lib.config.resolver import ConfigResolver, ProviderModel, VisionCapabilityError
from lib.config.service import ConfigService
from lib.custom_provider import make_provider_id
from lib.custom_provider.capability_view import (
    OverrideRejected,
    builtin_capability_view,
    check_user_added_overrides,
    custom_capability_view,
)
from lib.custom_provider.endpoints import get_endpoint_spec
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.video_runtime.audio import AudioRuntime, AudioTarget, assemble_audio
from lib.video_runtime.text import TextRuntime, assemble_text
from server.services.tasks import generation_context
from server.services.tasks.generation_context import audio_backend_profile, audio_backend_voice_ids
from tests.catalog_support import install_catalog, with_entry_changes
from tests.fakes import bind_session_factory


def _first_text_model(provider_id: str, *, vision: bool) -> str:
    for model_id, info in catalog.list_models(provider_id).items():
        if info.media_type == "text" and (("vision" in info.capabilities) is vision):
            return model_id
    raise AssertionError(f"no text model with vision={vision} for {provider_id}")


async def _seed_custom_provider(db_factory, *, models: list[dict], api_key: str = "sk-test-4015") -> str:
    async with db_factory() as session:
        provider = await CustomProviderRepository(session).create_provider(
            display_name="Relay 4015",
            discovery_format="openai",
            base_url="https://relay.test/v1",
            api_key=api_key,
            models=models,
        )
        await session.commit()
        return make_provider_id(provider.id)


class TestTextCapabilityKernel:
    def test_builtin_and_custom_text_views_evaluate_via_kernel(self):
        vision_model = _first_text_model("gemini-aistudio", vision=True)
        official_info = catalog.get_model("gemini-aistudio", vision_model)
        assert official_info is not None

        official_view = builtin_capability_view(
            "gemini-aistudio", vision_model, base_model=official_info, official=True, overrides={"image_input": False}
        )
        assert official_view.editable == ()
        assert official_view.overrides == {}
        assert official_view.base["image_input"] is True
        assert official_view.effective["image_input"] is True

        spec = get_endpoint_spec("openai-chat")
        matched_custom = custom_capability_view(
            model_id=vision_model,
            endpoint="openai-chat",
            spec=spec,
            raw_overrides={"image_input": False, "max_output_tokens": 4096, "last_frame": True},
            applied_overrides=None,
            entry_binding=None,
        )
        assert matched_custom.editable == ("image_input", "structured_output", "max_output_tokens")
        assert matched_custom.base["image_input"] is True
        assert matched_custom.overrides == {"image_input": False, "max_output_tokens": 4096}
        assert matched_custom.effective["image_input"] is False
        assert matched_custom.effective["max_output_tokens"] == 4096

        unmatched_custom = custom_capability_view(
            model_id="private-llm-unknown",
            endpoint="openai-chat",
            spec=spec,
            raw_overrides=None,
            applied_overrides=None,
            entry_binding=None,
        )
        assert unmatched_custom.base == {"image_input": None, "structured_output": None, "max_output_tokens": None}
        assert unmatched_custom.effective == unmatched_custom.base

    def test_user_added_builtin_text_override_validation_rejects_invalid_and_official(self):
        vision_model = _first_text_model("gemini-aistudio", vision=True)
        official_view = catalog.provider_model_view("gemini-aistudio", vision_model, media_type="text")
        assert official_view is not None
        with pytest.raises(OverrideRejected) as official_exc:
            check_user_added_overrides("gemini-aistudio", vision_model, official_view, {"image_input": False})
        assert official_exc.value.code == "builtin_model_official_settings"

        catalog.install_user_added_models((catalog.UserAddedModel("openai", "custom-gpt-4015", "text"),))
        try:
            user_view = catalog.provider_model_view("openai", "custom-gpt-4015", media_type="text")
            assert user_view is not None
            assert check_user_added_overrides(
                "openai", "custom-gpt-4015", user_view, {"image_input": True, "max_output_tokens": 2048}
            ) == {"image_input": True, "max_output_tokens": 2048}
            with pytest.raises(OverrideRejected) as bad_val:
                check_user_added_overrides("openai", "custom-gpt-4015", user_view, {"max_output_tokens": 0})
            assert bad_val.value.code == "capability_override_invalid_value"
            assert bad_val.value.params["expected"] == "int > 0"
            with pytest.raises(OverrideRejected) as bad_key:
                check_user_added_overrides("openai", "custom-gpt-4015", user_view, {"last_frame": True})
            assert bad_key.value.code == "capability_override_not_applicable"
            assert bad_key.value.params["media_type"] == "text"
        finally:
            catalog.install_user_added_models(())


class TestTextRuntimeAndVisionGate:
    async def test_vision_gate_agrees_across_builtin_custom_and_overrides(self, db_factory):
        non_vision = _first_text_model("dashscope", vision=False)
        vision_model = _first_text_model("gemini-aistudio", vision=True)
        resolver = ConfigResolver(db_factory)
        runtime = TextRuntime(resolver)

        async with db_factory.begin() as session:
            svc = ConfigService(session)
            await svc.set_provider_config("dashscope", "api_key", "ds-test-key")
            await svc.set_provider_config("gemini-aistudio", "api_key", "g-test-key")
            await svc.set_setting("default_text_backend", f"dashscope/{non_vision}")

        with pytest.raises(VisionCapabilityError):
            await runtime.for_task(TextTaskType.STYLE_ANALYSIS)

        pid = await _seed_custom_provider(
            db_factory,
            models=[
                {
                    "model_id": "relay-no-vision",
                    "display_name": "No Vision",
                    "endpoint": "openai-chat",
                    "is_enabled": True,
                    "capability_overrides": {"image_input": False},
                },
                {
                    "model_id": "relay-forced-vision",
                    "display_name": "Forced Vision",
                    "endpoint": "openai-chat",
                    "is_enabled": True,
                    "entry_binding": f"qwen/{non_vision}",
                    "capability_overrides": {"image_input": True},
                },
                {
                    "model_id": "relay-unmatched",
                    "display_name": "Unmatched",
                    "endpoint": "openai-chat",
                    "is_enabled": True,
                },
            ],
        )

        async with db_factory.begin() as session:
            await ConfigService(session).set_setting("default_text_backend", f"{pid}/relay-no-vision")
        with pytest.raises(VisionCapabilityError):
            await runtime.for_task(TextTaskType.STYLE_ANALYSIS)

        async with db_factory.begin() as session:
            await ConfigService(session).set_setting("default_text_backend", f"{pid}/relay-forced-vision")
        target = await runtime.for_task(TextTaskType.STYLE_ANALYSIS)
        assert target.snapshot.effective["image_input"] is True

        async with db_factory.begin() as session:
            await ConfigService(session).set_setting("default_text_backend", f"{pid}/relay-unmatched")
        unmatched_target = await runtime.for_task(TextTaskType.STYLE_ANALYSIS)
        assert unmatched_target.snapshot.effective.get("image_input") is None

        async with db_factory.begin() as session:
            await ConfigService(session).set_setting("default_text_backend", f"gemini-aistudio/{vision_model}")
        builtin_target = await runtime.for_task(TextTaskType.STYLE_ANALYSIS)
        assert builtin_target.snapshot.effective["image_input"] is True

    async def test_text_assembly_only_consumes_frozen_spec_and_text_generator_reuses_snapshot(
        self, db_factory, db_engine, monkeypatch
    ):
        pid = await _seed_custom_provider(
            db_factory,
            models=[
                {
                    "model_id": "my-text-model",
                    "display_name": "My Text",
                    "endpoint": "openai-chat",
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": {"max_output_tokens": 12345},
                }
            ],
        )
        async with db_factory.begin() as session:
            await ConfigService(session).set_setting("default_text_backend", f"{pid}/my-text-model")

        target = await TextRuntime(ConfigResolver(db_factory)).for_task(TextTaskType.SCRIPT)
        assert target.max_output_tokens == 12345
        assert target.is_custom is True

        async with db_factory.begin() as session:
            await session.execute(update(CustomProvider).values(api_key="", base_url=""))
            await session.execute(update(CustomProviderModel).values(is_enabled=False))

        def unexpected(*args, **kwargs):
            pytest.fail("text assembly must not read database or catalog")

        for attr in ("find_entry", "provider_model_view", "get_model"):
            monkeypatch.setattr(catalog, attr, unexpected)
        event.listen(db_engine.sync_engine, "before_cursor_execute", unexpected)
        try:
            backend = await assemble_text(target.spec)
            assert backend.model == "my-text-model"
        finally:
            event.remove(db_engine.sync_engine, "before_cursor_execute", unexpected)

    async def test_text_generator_create_carries_snapshot_output_limit(self, db_factory, monkeypatch):
        from lib.backends.providers import CallPurpose

        bind_session_factory(monkeypatch, db_factory)
        pid = await _seed_custom_provider(
            db_factory,
            models=[
                {
                    "model_id": "capped-llm",
                    "display_name": "Capped",
                    "endpoint": "openai-chat",
                    "is_enabled": True,
                    "is_default": True,
                    "capability_overrides": {"max_output_tokens": 4096},
                }
            ],
        )
        async with db_factory.begin() as session:
            await ConfigService(session).set_setting("default_text_backend", f"{pid}/capped-llm")

        generator = await TextGenerator.create(TextTaskType.SCRIPT, purpose=CallPurpose.SCRIPT_GENERATION)
        assert generator.model == "capped-llm"
        assert generator.max_output_tokens == 4096


class TestAudioRuntimeAndAssembly:
    def test_audio_target_has_no_empty_capability_snapshot_field(self):
        field_names = {f.name for f in fields(AudioTarget)}
        assert "snapshot" not in field_names
        for f in fields(AudioTarget):
            assert f.type is not CapabilitySnapshot

    async def test_audio_assembly_only_consumes_frozen_spec_and_preserves_voices_and_capabilities(
        self, db_factory, db_engine, monkeypatch
    ):
        audio_model = next(mid for mid, info in catalog.list_models("dashscope").items() if info.media_type == "audio")
        async with db_factory.begin() as session:
            await ConfigService(session).set_provider_config("dashscope", "api_key", "ds-audio-key")

        reads: list[str] = []

        def observe(*args):
            reads.append("sql")

        def capture_gen() -> int:
            assert not reads
            return 9

        event.listen(db_engine.sync_engine, "before_cursor_execute", observe)
        try:
            target = await AudioRuntime(ConfigResolver(db_factory), capture_generation=capture_gen).resolve(
                {"audio_backend": f"dashscope/{audio_model}"}
            )
        finally:
            event.remove(db_engine.sync_engine, "before_cursor_execute", observe)

        assert target.generation == 9
        assert target.provider_id == "dashscope"
        assert target.model_id == audio_model

        def unexpected(*args, **kwargs):
            pytest.fail("audio assembly must not read database or catalog")

        for attr in ("find_entry", "provider_model_view", "get_model"):
            monkeypatch.setattr(catalog, attr, unexpected)
        event.listen(db_engine.sync_engine, "before_cursor_execute", unexpected)
        try:
            backend = await assemble_audio(target.spec)
            assert backend.model == audio_model
            assert AudioCapability.TEXT_TO_SPEECH in backend.capabilities
            assert len(backend.list_voices()) > 0
        finally:
            event.remove(db_engine.sync_engine, "before_cursor_execute", unexpected)

    async def test_custom_audio_fallback_discontinued_and_profile_preserve_voices(self, db_factory, monkeypatch):
        bind_session_factory(monkeypatch, db_factory)
        generation_context.reset_backend_cache_for_tests()
        audio_model = next(mid for mid, info in catalog.list_models("dashscope").items() if info.media_type == "audio")
        pid = await _seed_custom_provider(
            db_factory,
            models=[
                {
                    "model_id": "tts-disabled",
                    "display_name": "Disabled",
                    "endpoint": "openai-tts",
                    "is_enabled": False,
                    "is_default": False,
                },
                {
                    "model_id": "tts-1",
                    "display_name": "Default",
                    "endpoint": "openai-tts",
                    "is_enabled": True,
                    "is_default": True,
                },
                {
                    "model_id": audio_model,
                    "display_name": "Retired TTS",
                    "endpoint": "dashscope-tts",
                    "is_enabled": True,
                    "is_default": False,
                },
            ],
        )

        runtime = AudioRuntime(ConfigResolver(db_factory))
        fallback = await runtime.resolve({"audio_backend": f"{pid}/tts-disabled"})
        assert fallback.model_id == "tts-1"
        assert await runtime.read_identity({"audio_backend": f"{pid}/tts-disabled"}) == ProviderModel(pid, "tts-1")

        no_default_pid = await _seed_custom_provider(
            db_factory,
            models=[
                {
                    "model_id": "tts-only-disabled",
                    "display_name": "Only Disabled",
                    "endpoint": "openai-tts",
                    "is_enabled": False,
                    "is_default": False,
                }
            ],
        )
        assert await runtime.read_identity({"audio_backend": f"{no_default_pid}/tts-only-disabled"}) == ProviderModel(
            no_default_pid, "tts-only-disabled"
        )

        profile = await audio_backend_profile(pid, audio_model)
        assert AudioCapability.TEXT_TO_SPEECH in profile.capabilities
        assert len(profile.voices) > 0

        voice_ids = await audio_backend_voice_ids(pid, audio_model, ConfigResolver(db_factory))
        assert voice_ids == tuple(v.id for v in profile.voices)

        openai_profile = await audio_backend_profile(pid, "tts-1")
        assert AudioCapability.SPEECH_SPEED in openai_profile.capabilities

        install_catalog(
            monkeypatch,
            with_entry_changes(catalog.factory_snapshot(), "dashscope", audio_model, shutdown_date=date(2000, 1, 1)),
        )
        with pytest.raises(ModelDiscontinuedError):
            await runtime.resolve({"audio_backend": f"{pid}/{audio_model}"})
        with pytest.raises(ModelDiscontinuedError):
            await runtime.read_identity({"audio_backend": f"{pid}/{audio_model}"})
