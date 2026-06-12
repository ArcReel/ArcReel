import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from lib.config.service import ConfigService
from lib.db.base import Base
from lib.db.repositories.credential_repository import CredentialRepository


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def config_service(session: AsyncSession) -> ConfigService:
    return ConfigService(session)


async def test_get_all_providers_status_empty(config_service: ConfigService):
    from lib.config.registry import PROVIDER_REGISTRY

    statuses = await config_service.get_all_providers_status()
    assert len(statuses) == len(PROVIDER_REGISTRY)
    assert {s.name for s in statuses} == set(PROVIDER_REGISTRY.keys())
    for s in statuses:
        assert s.status == "unconfigured"


async def test_provider_status_models_isolated_from_registry(config_service: ConfigService):
    # models 返回值须与全局 PROVIDER_REGISTRY 隔离：改写其可变容器不应污染注册表。
    from lib.config.registry import PROVIDER_REGISTRY

    statuses = await config_service.get_all_providers_status()
    target = next(s for s in statuses if s.models)
    mid, model_dict = next(iter(target.models.items()))
    before = list(PROVIDER_REGISTRY[target.name].models[mid].capabilities)
    model_dict["capabilities"].append("__mutation_probe__")
    assert PROVIDER_REGISTRY[target.name].models[mid].capabilities == before


async def test_provider_becomes_ready(config_service: ConfigService, session: AsyncSession):
    # 新逻辑：status 由凭证表中的活跃凭证决定，而不是 ProviderConfig 表
    cred_repo = CredentialRepository(session)
    await cred_repo.create("gemini-aistudio", "default", api_key="AIza-test")
    await session.flush()
    statuses = await config_service.get_all_providers_status()
    aistudio = next(s for s in statuses if s.name == "gemini-aistudio")
    assert aistudio.status == "ready"
    assert aistudio.missing_keys == []


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


async def test_get_default_video_backend(config_service: ConfigService):
    await config_service.set_setting("default_video_backend", "ark/doubao-seedance-1-5-pro-251215")
    provider_id, model_id = await config_service.get_default_video_backend()
    assert provider_id == "ark"
    assert model_id == "doubao-seedance-1-5-pro-251215"


async def test_get_default_backend_fallback(config_service: ConfigService):
    provider_id, model_id = await config_service.get_default_video_backend()
    assert provider_id == "gemini-aistudio"


async def test_unknown_provider_raises(config_service: ConfigService):
    with pytest.raises(ValueError, match="Unknown provider"):
        await config_service.set_provider_config("unknown-provider", "key", "val")


@pytest.mark.parametrize("key", ["image_max_workers", "video_max_workers", "audio_max_workers"])
@pytest.mark.parametrize("value", ["", "3.7", "abc", "-1"])
async def test_set_max_workers_rejects_invalid_values(config_service: ConfigService, key: str, value: str):
    from lib.config.service import ProviderConfigValueError

    with pytest.raises(ProviderConfigValueError) as exc_info:
        await config_service.set_provider_config("dashscope", key, value)
    assert exc_info.value.key == key
    assert exc_info.value.value == value
    # router 依赖 code/params 泛化渲染 i18n 文案，契约在此 pin 住
    assert exc_info.value.code == "max_workers_must_be_nonnegative_integer"
    assert exc_info.value.params == {"field": key, "value": value}


@pytest.mark.parametrize("value", ["0", "5"])
async def test_set_max_workers_accepts_nonnegative_integers(config_service: ConfigService, value: str):
    await config_service.set_provider_config("ark", "image_max_workers", value)
    config = await config_service.get_provider_config("ark")
    assert config["image_max_workers"] == value


@pytest.mark.parametrize(("raw", "canonical"), [(" 5 ", "5"), ("+5", "5"), ("1_0", "10")])
async def test_set_max_workers_canonicalizes_on_write(config_service: ConfigService, raw: str, canonical: str):
    # int() 接受的非规范形态统一规范化入库，读取方与 number 输入框拿到的都是纯数字串
    await config_service.set_provider_config("ark", "video_max_workers", raw)
    config = await config_service.get_provider_config("ark")
    assert config["video_max_workers"] == canonical


async def test_set_other_number_keys_not_restricted_to_integers(config_service: ConfigService):
    # request_gap / *_rpm 语义允许小数，不受容量键的非负整数校验约束
    await config_service.set_provider_config("gemini-aistudio", "request_gap", "0.5")
    config = await config_service.get_provider_config("gemini-aistudio")
    assert config["request_gap"] == "0.5"
