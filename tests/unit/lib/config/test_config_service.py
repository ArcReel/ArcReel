import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from lib.config.service import ConfigService
from lib.db.repositories.credential_repository import CredentialRepository


@pytest.fixture
def config_service(db_session: AsyncSession) -> ConfigService:
    return ConfigService(db_session)


async def test_get_all_providers_status_empty(config_service: ConfigService):
    from lib.config.registry import PROVIDER_REGISTRY

    statuses = await config_service.get_all_providers_status()
    assert len(statuses) == len(PROVIDER_REGISTRY)
    assert {s.name for s in statuses} == set(PROVIDER_REGISTRY.keys())
    for s in statuses:
        assert s.status == "unconfigured"


async def test_provider_status_models_isolated_from_catalog(config_service: ConfigService):
    # models results must not alias process catalog indexes; mutating returned containers cannot contaminate catalog.
    from lib.catalog import query as catalog

    statuses = await config_service.get_all_providers_status()
    target = next(s for s in statuses if s.models)
    mid, model_dict = next(iter(target.models.items()))
    model = catalog.get_model(target.name, mid)
    assert model is not None
    before = list(model.capabilities)
    model_dict["capabilities"].append("__mutation_probe__")
    assert model.capabilities == before


async def test_provider_becomes_ready(config_service: ConfigService, db_session: AsyncSession):
    # status follows active credential-table records, not ProviderConfig.
    cred_repo = CredentialRepository(db_session)
    await cred_repo.create("gemini-aistudio", "default", api_key="AIza-test")
    await db_session.flush()
    statuses = await config_service.get_all_providers_status()
    aistudio = next(s for s in statuses if s.name == "gemini-aistudio")
    assert aistudio.status == "ready"


async def test_get_provider_config(config_service: ConfigService):
    await config_service.set_provider_config("grok", "api_key", "xai-test")
    config = await config_service.get_provider_config("grok")
    assert config == {"api_key": "xai-test"}


async def test_delete_provider_config(config_service: ConfigService):
    await config_service.set_provider_config("grok", "api_key", "xai-test")
    await config_service.delete_provider_config("grok", "api_key")
    config = await config_service.get_provider_config("grok")
    assert config == {}


async def test_system_settings(config_service: ConfigService):
    await config_service.set_setting("default_video_backend", "gemini-vertex/veo-3.1-fast-generate-001")
    val = await config_service.get_setting("default_video_backend")
    assert val == "gemini-vertex/veo-3.1-fast-generate-001"


async def test_video_poll_timeout_defaults_and_round_trips(config_service: ConfigService):
    assert await config_service.get_video_poll_timeout_seconds() == 3600

    await config_service.set_video_poll_timeout_seconds(7200)

    assert await config_service.get_video_poll_timeout_seconds() == 7200


@pytest.mark.parametrize("value", [True, 0, 59, 60.5])
async def test_video_poll_timeout_rejects_values_below_minimum(config_service: ConfigService, value: object):
    with pytest.raises(ValueError, match="at least 60"):
        await config_service.set_video_poll_timeout_seconds(value)


async def test_unknown_provider_raises(config_service: ConfigService):
    with pytest.raises(ValueError, match="Unknown provider"):
        await config_service.set_provider_config("unknown-provider", "key", "val")


@pytest.mark.parametrize("key", ["image_max_workers", "video_max_workers", "audio_max_workers"])
@pytest.mark.parametrize("value", ["", "3.7", "abc", "-1", "0"])
async def test_set_max_workers_rejects_invalid_values(config_service: ConfigService, key: str, value: str):
    from lib.config.service import ProviderConfigValueError

    with pytest.raises(ProviderConfigValueError) as exc_info:
        await config_service.set_provider_config("dashscope", key, value)
    assert exc_info.value.key == key
    assert exc_info.value.value == value
    # Router renders generic i18n from code/params; pin that contract here.
    assert exc_info.value.code == "max_workers_must_be_positive_integer"
    assert exc_info.value.params == {"field": key, "value": value}


@pytest.mark.parametrize("value", ["1", "5"])
async def test_set_max_workers_accepts_positive_integers(config_service: ConfigService, value: str):
    await config_service.set_provider_config("ark", "image_max_workers", value)
    config = await config_service.get_provider_config("ark")
    assert config["image_max_workers"] == value


@pytest.mark.parametrize(("raw", "canonical"), [(" 5 ", "5"), ("+5", "5"), ("1_0", "10")])
async def test_set_max_workers_canonicalizes_on_write(config_service: ConfigService, raw: str, canonical: str):
    # Normalize all int()-accepted forms on write; readers/number inputs receive canonical numeric strings.
    await config_service.set_provider_config("ark", "video_max_workers", raw)
    config = await config_service.get_provider_config("ark")
    assert config["video_max_workers"] == canonical


async def test_set_other_number_keys_not_restricted_to_integers(config_service: ConfigService):
    # request_gap / *_rpm permit fractions, outside positive-integer capacity validation.
    await config_service.set_provider_config("gemini-aistudio", "request_gap", "0.5")
    config = await config_service.get_provider_config("gemini-aistudio")
    assert config["request_gap"] == "0.5"
