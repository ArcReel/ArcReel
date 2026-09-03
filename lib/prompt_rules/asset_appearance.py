"""资产候选块里「一个引用名此刻长什么样」的渲染规则。

script_plan 与 prompt_authoring 的资产块要让写剧本的模型知道每个可引用名字对应的外观。
角色的引用名除资产名外还有 ``本体名/衍生名``（见 ``docs/adr/0072``）：衍生只登记相对本体的
变化，故它的外观是**本体描述加上这一段变化**，两条 builder（drama / narration 的
``lib.prompt_builders_script`` 与参考生视频的 ``lib.prompt_builders_reference``）共用本模块
渲染，措辞只有一份。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from lib.asset_types import ASSET_SPECS, DERIVATIVES_FIELD
from lib.reference_catalog import derivative_reference

#: 衍生外观描述的引导词，接在本体描述之后单起一行。面向 LLM 的 prompt 文本，按仓库口径豁免 i18n。
CURRENT_FORM_PREFIX = "当前形态："


def _description(entry: object) -> str:
    value = entry.get("description") if isinstance(entry, Mapping) else None
    return value.strip() if isinstance(value, str) else ""


def iter_asset_appearances(asset_type: str, bucket: Mapping[str, Any] | None) -> Iterator[tuple[str, str]]:
    """逐个产出该类型可写进剧本的 ``(引用名, 外观描述)``，本体在前、其衍生紧随其后。

    未开启衍生能力的类型（``AssetSpec.supports_derivatives``）只产出资产名本身。外部编辑
    写坏的 project.json 里条目或衍生表可能不是 Mapping，按无描述处理，不抛。
    """
    supports_derivatives = ASSET_SPECS[asset_type].supports_derivatives
    for name, entry in (bucket or {}).items():
        base_description = _description(entry)
        yield str(name), base_description
        if not supports_derivatives or not isinstance(entry, Mapping):
            continue
        derivatives = entry.get(DERIVATIVES_FIELD)
        if not isinstance(derivatives, Mapping):
            continue
        for derivative_name, derivative in derivatives.items():
            change = _description(derivative)
            parts = [part for part in (base_description, f"{CURRENT_FORM_PREFIX}{change}" if change else "") if part]
            yield derivative_reference(str(name), str(derivative_name)), "\n".join(parts)


def asset_reference_names(asset_type: str, bucket: Mapping[str, Any] | None) -> list[str]:
    """该类型可写进剧本引用字段的全部名字，本体在前、其衍生紧随其后。"""
    return [name for name, _appearance in iter_asset_appearances(asset_type, bucket)]
