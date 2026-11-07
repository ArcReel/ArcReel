"""Provider/model/entry display names in failure copy.

Failure ``params`` stores machine values (provider IDs, upstream model IDs, entry references) used
by Agents/frontend for identification. Replace agreed parameter values with creator-recognizable
names only at rendering. :meth:`DisplayNames.apply` alone defines replacements; every failure
renderer passes the catalog here as a mandatory argument.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: Parameter names whose values are provider IDs.
PROVIDER_PARAMS = ("provider", "provider_id", "claimed_provider_id", "actual_provider_id")
#: Parameter names whose values are model IDs.
MODEL_PARAM = "model"
#: Parameter names holding entry references (“模型厂商/官方模型名”): official successors of discontinued models.
SUCCESSOR_PARAM = "successor"
DISPLAY_NAME_PARAMS = frozenset((*PROVIDER_PARAMS, MODEL_PARAM))


#: Agent-provider ID prefix: accounting records ``provider`` as ``agent-<凭证 id>``.
AGENT_PROVIDER_PREFIX = "agent-"


def is_agent_provider(provider_id: str) -> bool:
    """Whether ``provider_id`` uses Agent-provider accounting form (``agent-<凭证 id>``)."""
    return provider_id.startswith(AGENT_PROVIDER_PREFIX) and provider_id[len(AGENT_PROVIDER_PREFIX) :].isdecimal()


def agent_provider_id(credential_id: int) -> str:
    return f"{AGENT_PROVIDER_PREFIX}{credential_id}"


@dataclass(frozen=True)
class DisplayNames:
    """Provider/model/entry names localized to one request’s language.

    ``providers`` keys are provider IDs; ``models`` keys are ``(供应商 ID, 模型 ID)``; ``entries`` keys
    are entry references, including only references with unique display names (omit multimodal
    entries when media types have different names).
    ``deleted_provider`` generically names providers absent from the catalog;
    ``deleted_agent_provider`` names absent Agent providers (``agent-<凭证 id>``, deleted credentials),
    defaulting to ``deleted_provider`` when omitted.
    """

    providers: Mapping[str, str]
    models: Mapping[tuple[str, str], str]
    deleted_provider: str
    deleted_agent_provider: str | None = None
    entries: Mapping[str, str] = field(default_factory=dict)
    _models_by_id: Mapping[str, str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Without a provider parameter, look up names by model ID; if its names differ across providers,
        # ownership is ambiguous, so retain the model ID.
        candidates: dict[str, set[str]] = {}
        for (_, model_id), name in self.models.items():
            candidates.setdefault(model_id, set()).add(name)
        unique = {model_id: next(iter(names)) for model_id, names in candidates.items() if len(names) == 1}
        object.__setattr__(self, "_models_by_id", unique)

    def provider(self, provider_id: str) -> str:
        """Provider name; missing catalog entries render localized “deleted provider” or “deleted Agent provider” labels."""
        name = self.providers.get(provider_id)
        if name is not None:
            return name
        if self.deleted_agent_provider is not None and is_agent_provider(provider_id):
            return self.deleted_agent_provider
        return self.deleted_provider

    def model(self, model_id: str, *, provider_id: str | None = None) -> str:
        """Model name, falling back to model ID when absent from the catalog."""
        if provider_id is not None:
            return self.models.get((provider_id, model_id), model_id)
        return self._models_by_id.get(model_id, model_id)

    def entry(self, ref: str) -> str:
        """Entry display name, falling back to its reference when absent or when one reference has multiple display names."""
        return self.entries.get(ref, ref)

    def apply(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Copy parameters, replacing IDs under agreed names with display names and retaining all others unchanged."""
        applied = dict(params)
        owner = next((value for key in ("provider", "provider_id") if isinstance(value := params.get(key), str)), None)
        for key in PROVIDER_PARAMS:
            value = params.get(key)
            if isinstance(value, str):
                applied[key] = self.provider(value)
        model = params.get(MODEL_PARAM)
        if isinstance(model, str):
            applied[MODEL_PARAM] = self.model(model, provider_id=owner)
        successor = params.get(SUCCESSOR_PARAM)
        if isinstance(successor, str):
            applied[SUCCESSOR_PARAM] = self.entry(successor)
        return applied
