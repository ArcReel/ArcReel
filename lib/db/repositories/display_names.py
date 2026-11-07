"""Provider/model display names: registry names for built-ins and user-entered names for custom entries.

Usage reports and failure copy share these lookups. Resolve names at read time, without
snapshotting on failure writes or accounting: renaming a provider updates old records'
display names, and language follows the request reading them.
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from lib.backends.providers import PROVIDER_ANTHROPIC, PROVIDER_GEMINI
from lib.db.repositories.agent_credential_repo import AgentCredentialRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.i18n import DEFAULT_LOCALE, _, translate_or
from lib.i18n.display_names import DisplayNames, agent_provider_id, is_agent_provider

# Display fallback for bare provider values outside the registry. Do not migrate rows; supply friendly names only for display:
# - "gemini": before identity inversion, text Gemini calls recorded bare backend.name "gemini" (image/video
#   already used "gemini-aistudio"). The registry has only new-format keys (gemini-aistudio / gemini-vertex),
#   so bare values have no metadata.
# - "anthropic": all Agent records used this before credential-based accounting; still used without an
#   active credential when environment variables come from system settings.
_LEGACY_PROVIDER_DISPLAY_NAMES = {PROVIDER_GEMINI: "Gemini", PROVIDER_ANTHROPIC: "Anthropic"}


def _builtin_provider_name(provider_id: str) -> str | None:
    """Default-language registry name for built-in providers, including legacy bare values; return ``None`` for others."""
    # Lazy import: registry → dashscope_shared → usage_repo would cycle at module scope
    from lib.config.registry import PROVIDER_REGISTRY

    meta = PROVIDER_REGISTRY.get(provider_id)
    return meta.display_name if meta else _LEGACY_PROVIDER_DISPLAY_NAMES.get(provider_id)


async def _agent_provider_names(session: AsyncSession) -> dict[str, str]:
    """``agent-<凭证 id>`` → current credential display name."""
    return {
        agent_provider_id(cred.id): cred.display_name
        for cred in await AgentCredentialRepository(session).list_for_user()
    }


async def provider_display_names(
    session: AsyncSession, provider_ids: set[str], *, locale: str = DEFAULT_LOCALE
) -> dict[str, str]:
    """Provider ID → display name; unknown IDs (including malformed ones such as ``"custom-abc"``) fall back to the ID itself.

    Built-in providers use default-language registry names. ``agent-<凭证 id>`` uses the
    Agent credential's current display name; deleted credentials use the deleted-Agent-provider
    label in ``locale``.
    """
    agent_ids = {provider_id for provider_id in provider_ids if is_agent_provider(provider_id)}
    agents: dict[str, str] = {}
    if agent_ids:
        known = await _agent_provider_names(session)
        deleted = _("deleted_agent_provider_display_name", locale=locale)
        agents = {provider_id: known.get(provider_id, deleted) for provider_id in agent_ids}
    builtin = {
        provider_id: _builtin_provider_name(provider_id) for provider_id in provider_ids if provider_id not in agent_ids
    }
    custom: dict[str, str] = {}
    if None in builtin.values():
        custom = {cp.provider_id: cp.display_name for cp in await CustomProviderRepository(session).list_providers()}
    return {
        **agents,
        **{provider_id: name or custom.get(provider_id, provider_id) for provider_id, name in builtin.items()},
    }


def build_display_names(
    locale: str,
    *,
    custom_providers: Mapping[str, str],
    custom_models: Mapping[tuple[str, str], str],
    agent_providers: Mapping[str, str] | None = None,
) -> DisplayNames:
    """Merge custom provider/model names over built-in registry/catalog names and render in ``locale``.

    Translate built-in provider names where a translation table exists; otherwise use
    original registry names. Model and entry names use catalog-entry display names,
    independent of language. Include hidden models because old failures may reference
    them. Key entry names by entry reference; omit cross-media entries whose display names
    differ by media type. ``agent_providers`` maps ``agent-<凭证 id>`` to Agent credential display names.
    """
    from lib.catalog import query as catalog  # Same import-cycle reason as _builtin_provider_name
    from lib.config.registry import PROVIDER_REGISTRY  # Same import-cycle reason as _builtin_provider_name

    providers: dict[str, str] = dict(_LEGACY_PROVIDER_DISPLAY_NAMES)
    models: dict[tuple[str, str], str] = {}
    for provider_id, meta in PROVIDER_REGISTRY.items():
        providers[provider_id] = translate_or(f"provider_name_{provider_id}", meta.display_name, locale)
        for model_id, info in catalog.list_models(provider_id).items():
            models[(provider_id, model_id)] = info.display_name
    entry_names: dict[str, set[str]] = {}
    for entry in catalog.catalog_data().entries:
        entry_names.setdefault(catalog.entry_ref(entry), set()).add(entry.display_name)
    return DisplayNames(
        providers={**providers, **custom_providers, **(agent_providers or {})},
        models={**models, **custom_models},
        deleted_provider=_("deleted_provider_display_name", locale=locale),
        deleted_agent_provider=_("deleted_agent_provider_display_name", locale=locale),
        entries={ref: next(iter(names)) for ref, names in entry_names.items() if len(names) == 1},
    )


async def load_display_names(session: AsyncSession, locale: str) -> DisplayNames:
    """Complete display-name directory rendered in ``locale``; custom providers/models use user-entered names, including disabled models."""
    custom_providers: dict[str, str] = {}
    custom_models: dict[tuple[str, str], str] = {}
    for provider, provider_models in await CustomProviderRepository(session).list_providers_with_models():
        provider_id = provider.provider_id
        custom_providers[provider_id] = provider.display_name
        for model in provider_models:
            custom_models[(provider_id, model.model_id)] = model.display_name
    return build_display_names(
        locale,
        custom_providers=custom_providers,
        custom_models=custom_models,
        agent_providers=await _agent_provider_names(session),
    )
