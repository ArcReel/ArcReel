"""Agent 凭证的模型档位：每个档位实际调用的模型名，以及它按模型目录对应到的条目（docs/adr/0102）。

档位是主模型与 Claude Code 的四个模型路由：``haiku`` / ``sonnet`` / ``opus`` 三个能力等级与子智能体。环境变量组装、
记账时取档位的条目绑定、设置页的档位定价状态共用这里的「档位实际调用的模型名」，三处对同一条凭证得到同样的名字。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from arcreel_catalog_core import normalize_model_name

if TYPE_CHECKING:
    from lib.db.models.agent_credential import AgentAnthropicCredential

Tier = Literal["model", "haiku", "sonnet", "opus", "subagent"]

#: 档位的固定顺序：主模型在前。同一模型名出现在几个档位时，取第一个带绑定的档位。
TIERS: tuple[Tier, ...] = ("model", "haiku", "sonnet", "opus", "subagent")

#: Anthropic 官方预设：留空的档位由 CLI 自行解析，不替用户填成主模型。
OFFICIAL_PRESET_ID = "anthropic-official"

#: 档位在凭证上的字段名。
_CREDENTIAL_FIELDS: Mapping[Tier, str] = {
    "model": "model",
    "haiku": "haiku_model",
    "sonnet": "sonnet_model",
    "opus": "opus_model",
    "subagent": "subagent_model",
}

#: 档位在系统设置里的旧键：凭证上的档位留空时以它兜底（凭证之前的配置方式）。
TIER_SETTING_KEYS: Mapping[Tier, str] = {
    "model": "anthropic_model",
    "haiku": "anthropic_default_haiku_model",
    "sonnet": "anthropic_default_sonnet_model",
    "opus": "anthropic_default_opus_model",
    "subagent": "claude_code_subagent_model",
}


def tier_models(credential: AgentAnthropicCredential, settings: Mapping[str, str]) -> dict[Tier, str]:
    """各档位实际调用的模型名；空串表示交给 CLI 决定。

    凭证上留空的档位先取系统设置里的旧值。凭证不是 Anthropic 官方预设时，仍为空的四个路由档位填成主模型名，
    避免 CLI 退回默认的 Claude 模型名。
    """
    names: dict[Tier, str] = {
        tier: (getattr(credential, field) or settings.get(TIER_SETTING_KEYS[tier], "").strip())
        for tier, field in _CREDENTIAL_FIELDS.items()
    }
    main_model = names["model"]
    if main_model and credential.preset_id != OFFICIAL_PRESET_ID:
        for tier in TIERS[1:]:
            if not names[tier]:
                names[tier] = main_model
    return names


def tier_binding_for(credential: AgentAnthropicCredential, settings: Mapping[str, str], model: str) -> str | None:
    """API 模型名 ``model`` 所在档位的条目绑定；模型名比较忽略大小写与命名空间。没有带绑定的档位时为 ``None``。"""
    bindings = credential.tier_bindings or {}
    if not bindings:
        return None
    target = normalize_model_name(model)
    names = tier_models(credential, settings)
    for tier in TIERS:
        ref = bindings.get(tier)
        if ref and names[tier] and normalize_model_name(names[tier]) == target:
            return ref
    return None


@dataclass(frozen=True)
class TierStatus:
    """一个档位的定价状态，按当前目录现查，不存储。

    ``model_name`` 为 ``None`` 表示档位留空、由 CLI 决定调用哪个模型，此时其余字段没有意义。``manually_bound`` 表示
    档位的条目绑定指向目录里的条目、对应关系取自绑定；绑定的条目已不在目录里时回到按模型名对应，记为假。
    ``priced`` 表示对应到的条目有当前代码认得的价目表。
    """

    tier: Tier
    model_name: str | None
    entry_display_name: str | None
    manually_bound: bool
    priced: bool


def tier_statuses(credential: AgentAnthropicCredential, settings: Mapping[str, str]) -> list[TierStatus]:
    """凭证全部档位的定价状态，按 :data:`TIERS` 的顺序。"""
    # 目录查询导入放函数内：它经注册表拉起 dashscope_shared→usage_repo→agent_credential_repo→本模块，模块级导入会成环。
    from lib.catalog import query as catalog

    bindings = credential.tier_bindings or {}
    statuses: list[TierStatus] = []
    for tier, name in tier_models(credential, settings).items():
        if not name:
            statuses.append(TierStatus(tier, None, None, manually_bound=False, priced=False))
            continue
        ref = bindings.get(tier)
        bound = catalog.find_bound_entry(ref, "text") if ref else None
        entry = bound or catalog.find_entry(name, media_type="text")
        statuses.append(
            TierStatus(
                tier,
                name,
                entry.display_name if entry is not None else None,
                manually_bound=bound is not None,
                priced=entry is not None and entry.price is not None and entry.price.is_priceable,
            )
        )
    return statuses
