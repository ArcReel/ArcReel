"""Style templates: slotless templates in category ``style`` in the builtin catalog.

Stored ``style_template_id`` omits the ``style/`` prefix and has form ``{category}_{slug}``,
category ∈ {live, anim}. Definition order is UI order, ensured by numeric filename prefixes
(the registry scans sorted paths). Each template frontmatter description explains wording choices.
"""

from __future__ import annotations

from lib.prompts.prompt_templates.builtin import builtin_templates

_TEMPLATE_PREFIX = "style/"
_CATEGORIES = ("live", "anim")


def list_template_ids() -> list[str]:
    """Return all style template IDs in definition order."""
    return [
        entry.id.removeprefix(_TEMPLATE_PREFIX)
        for entry in builtin_templates.list_templates()
        if entry.category == "style"
    ]


def resolve_template_prompt(template_id: str) -> str:
    """Render the full style prompt. Unknown IDs raise KeyError for callers to convert to HTTPException."""
    if not is_known_template(template_id):
        raise KeyError(template_id)
    return builtin_templates.render(f"{_TEMPLATE_PREFIX}{template_id}")


def is_known_template(template_id: str) -> bool:
    return template_id in list_template_ids()


def list_templates_by_category() -> dict[str, list[dict]]:
    """Group by category, retaining definition order within lists.
    Entries have form {'id': 'live_xxx', 'prompt': '...'}."""
    grouped: dict[str, list[dict]] = {category: [] for category in _CATEGORIES}
    for template_id in list_template_ids():
        category = template_id.split("_", 1)[0]
        grouped[category].append({"id": template_id, "prompt": resolve_template_prompt(template_id)})
    return grouped
