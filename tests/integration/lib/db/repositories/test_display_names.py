"""显示名目录：内置名称按请求语言翻译，自定义名称取用户填写的值。"""

from lib.db.models.agent_credential import AgentAnthropicCredential
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.db.repositories.display_names import load_display_names
from lib.i18n import _


async def test_catalog_names_builtin_and_custom_providers_in_the_request_locale(async_session):
    provider = await CustomProviderRepository(async_session).create_provider(
        display_name="我的中转站",
        discovery_format="openai",
        base_url="https://relay.test/v1",
        api_key="sk-relay",
        models=[{"model_id": "i2v-h3", "display_name": "图生视频 H3", "endpoint": "openai-video", "is_enabled": False}],
    )
    await async_session.flush()

    for locale in ("zh", "en", "vi"):
        names = await load_display_names(async_session, locale)

        assert names.provider("ark") == _("provider_name_ark", locale=locale)
        assert names.provider(provider.provider_id) == "我的中转站"
        assert names.model("i2v-h3", provider_id=provider.provider_id) == "图生视频 H3"
        assert names.provider(f"custom-{provider.id + 1}") == _("deleted_provider_display_name", locale=locale)


async def test_catalog_names_agent_providers_by_credential_and_marks_deleted_ones(async_session):
    credential = AgentAnthropicCredential(
        preset_id="deepseek", display_name="我的 DeepSeek", base_url="https://x.test", api_key="sk-x"
    )
    async_session.add(credential)
    await async_session.flush()

    for locale in ("zh", "en", "vi"):
        names = await load_display_names(async_session, locale)

        assert names.provider(f"agent-{credential.id}") == "我的 DeepSeek"
        assert names.provider(f"agent-{credential.id + 1}") == _("deleted_agent_provider_display_name", locale=locale)
        # 不是 agent-<数字> 的未知 id 仍是泛称「已删除的供应商」
        assert names.provider("agent-plan") == _("deleted_provider_display_name", locale=locale)
