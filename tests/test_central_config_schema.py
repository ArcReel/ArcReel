"""Guard the cloud/data-center schema against ArcReel configuration drift."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.agent_provider_catalog import CUSTOM_SENTINEL_ID, list_presets
from lib.config.registry import PROVIDER_REGISTRY

pytestmark = pytest.mark.unit

_CREDENTIAL_KEYS = {"api_key", "access_key", "secret_key", "credentials_path", "base_url"}
_SUPPORTED_SECRET_KEYS = {"api_key", "access_key", "secret_key"}


def _schema() -> dict:
    path = Path(__file__).parents[1] / "supabase" / "functions" / "_shared" / "arcreel-config-schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_every_builtin_provider_and_field_is_exposed_to_data_center() -> None:
    providers = {item["id"]: item for item in _schema()["providers"]}
    assert set(providers) == set(PROVIDER_REGISTRY)

    for provider_id, meta in PROVIDER_REGISTRY.items():
        exposed = providers[provider_id]
        expected_secrets = [
            key for key in meta.required_keys if key in meta.secret_keys and key in _SUPPORTED_SECRET_KEYS
        ]
        assert [field["key"] for field in exposed["secret_fields"]] == expected_secrets
        expected_groups = meta.credential_groups or ([expected_secrets] if expected_secrets else [])
        assert exposed["secret_field_groups"] == expected_groups
        assert exposed["supports_base_url"] is ("base_url" in meta.optional_keys)
        expected_config = [key for key in (*meta.required_keys, *meta.optional_keys) if key not in _CREDENTIAL_KEYS]
        assert [field["key"] for field in exposed.get("fields", [])] == expected_config
        assert bool(exposed.get("credential_file")) is ("credentials_path" in meta.required_keys)


def test_every_agent_preset_and_advanced_routing_field_is_exposed() -> None:
    schema = _schema()
    exposed = {item["id"]: item for item in schema["agent_providers"]}
    presets = {item.id: item for item in list_presets()}
    assert set(exposed) == set(presets) | {CUSTOM_SENTINEL_ID}
    for preset_id, preset in presets.items():
        item = exposed[preset_id]
        assert item["name"] == preset.display_name
        assert item["icon_key"] == preset.icon_key
        assert item["base_url"] == preset.messages_url
        assert item["discovery_url"] == preset.discovery_url
        assert item["default_model"] == preset.default_model
        assert item["suggested_models"] == list(preset.suggested_models)
        assert item["docs_url"] == preset.docs_url
        assert item["api_key_url"] == preset.api_key_url
        assert item["notes_i18n_key"] == preset.notes_i18n_key
        assert item["api_key_pattern"] == preset.api_key_pattern
        assert item["recommended"] is preset.is_recommended

    assert [field["key"] for field in schema["agent_fields"]] == [
        "display_name",
        "base_url",
        "api_key",
        "model",
        "haiku_model",
        "sonnet_model",
        "opus_model",
        "subagent_model",
    ]
    assert [field["key"] for field in schema["agent_fields"] if field["group"] == "advanced"] == [
        "haiku_model",
        "sonnet_model",
        "opus_model",
        "subagent_model",
    ]
