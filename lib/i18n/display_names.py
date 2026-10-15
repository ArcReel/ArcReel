"""失败文案里的供应商与模型显示名。

失败的 ``params`` 存机器值（供应商 ID、上游模型 ID），Agent 与前端都按它们定位；渲染成文时才把
约定参数名的值换成创作者认得的名称。替换规则只有 :meth:`DisplayNames.apply` 这一份，所有失败
渲染函数都把目录作为必填参数传到这里。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: 值是供应商 ID 的参数名。
PROVIDER_PARAMS = ("provider", "provider_id", "claimed_provider_id", "actual_provider_id")
#: 值是模型 ID 的参数名。
MODEL_PARAM = "model"
DISPLAY_NAME_PARAMS = frozenset((*PROVIDER_PARAMS, MODEL_PARAM))


#: Agent 供应商的供应商 ID 前缀：记账时 ``provider`` 记 ``agent-<凭证 id>``。
AGENT_PROVIDER_PREFIX = "agent-"


def is_agent_provider(provider_id: str) -> bool:
    """``provider_id`` 是否为 Agent 供应商记账的形态（``agent-<凭证 id>``）。"""
    return provider_id.startswith(AGENT_PROVIDER_PREFIX) and provider_id[len(AGENT_PROVIDER_PREFIX) :].isdecimal()


def agent_provider_id(credential_id: int) -> str:
    return f"{AGENT_PROVIDER_PREFIX}{credential_id}"


@dataclass(frozen=True)
class DisplayNames:
    """一次请求内按请求语言成文的供应商名与模型名。

    ``providers`` 以供应商 ID 为键；``models`` 以 ``(供应商 ID, 模型 ID)`` 为键。
    ``deleted_provider`` 是目录里查不到的供应商的泛称；``deleted_agent_provider`` 是查不到的
    Agent 供应商（``agent-<凭证 id>``，凭证已删除）的泛称，未提供时与 ``deleted_provider`` 相同。
    """

    providers: Mapping[str, str]
    models: Mapping[tuple[str, str], str]
    deleted_provider: str
    deleted_agent_provider: str | None = None
    _models_by_id: Mapping[str, str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # 参数里只有模型 ID、没有供应商时按模型 ID 查名；同一个模型 ID 在不同供应商下名称不一时
        # 无从判断指哪一个，保留模型 ID。
        candidates: dict[str, set[str]] = {}
        for (_, model_id), name in self.models.items():
            candidates.setdefault(model_id, set()).add(name)
        unique = {model_id: next(iter(names)) for model_id, names in candidates.items() if len(names) == 1}
        object.__setattr__(self, "_models_by_id", unique)

    def provider(self, provider_id: str) -> str:
        """供应商名；目录里查不到时返回「已删除的供应商」，Agent 供应商返回「已删除的 Agent 供应商」。"""
        name = self.providers.get(provider_id)
        if name is not None:
            return name
        if self.deleted_agent_provider is not None and is_agent_provider(provider_id):
            return self.deleted_agent_provider
        return self.deleted_provider

    def model(self, model_id: str, *, provider_id: str | None = None) -> str:
        """模型名；目录里查不到时返回模型 ID。"""
        if provider_id is not None:
            return self.models.get((provider_id, model_id), model_id)
        return self._models_by_id.get(model_id, model_id)

    def apply(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """返回把约定参数名的 ID 换成显示名后的参数副本，其余参数原样保留。"""
        applied = dict(params)
        owner = next((value for key in ("provider", "provider_id") if isinstance(value := params.get(key), str)), None)
        for key in PROVIDER_PARAMS:
            value = params.get(key)
            if isinstance(value, str):
                applied[key] = self.provider(value)
        model = params.get(MODEL_PARAM)
        if isinstance(model, str):
            applied[MODEL_PARAM] = self.model(model, provider_id=owner)
        return applied
