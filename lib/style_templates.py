"""风格模版：内置提示词模版目录里类别为 ``style`` 的无槽位模版。

项目里存的 ``style_template_id`` 是去掉 ``style/`` 前缀的模版 id，形如 ``{category}_{slug}``，
category ∈ {live, anim}。定义顺序即 UI 展示顺序，由模版文件名的序号前缀保证（注册表按路径排序
扫描目录）。正文措辞的取舍写在各模版 frontmatter 的 description 里。
"""

from __future__ import annotations

from lib.prompt_templates.builtin import builtin_templates

_TEMPLATE_PREFIX = "style/"
_CATEGORIES = ("live", "anim")


def list_template_ids() -> list[str]:
    """按定义顺序返回全部风格模版 id。"""
    return [
        entry.id.removeprefix(_TEMPLATE_PREFIX)
        for entry in builtin_templates.list_templates()
        if entry.category == "style"
    ]


def resolve_template_prompt(template_id: str) -> str:
    """渲染出整段画风 prompt。未知 id 抛 KeyError（交给调用方转成 HTTPException）。"""
    if not is_known_template(template_id):
        raise KeyError(template_id)
    return builtin_templates.render(f"{_TEMPLATE_PREFIX}{template_id}")


def is_known_template(template_id: str) -> bool:
    return template_id in list_template_ids()


def list_templates_by_category() -> dict[str, list[dict]]:
    """按 category 分组，返回列表保持定义顺序。
    每项形如 {'id': 'live_xxx', 'prompt': '...'}。"""
    grouped: dict[str, list[dict]] = {category: [] for category in _CATEGORIES}
    for template_id in list_template_ids():
        category = template_id.split("_", 1)[0]
        grouped[category].append({"id": template_id, "prompt": resolve_template_prompt(template_id)})
    return grouped
