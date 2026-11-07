"""All structured-output models sent as ``response_schema``, for parametrized schema-content tests.

Add models from new call sites to :func:`response_schema_models` so schema constraints cover them.
"""

import inspect
import json
from typing import get_args

import pytest
from pydantic import BaseModel


def response_schema_models() -> list[type[BaseModel]]:
    """Actual call-site response schemas, including dynamic multi-/single-value duration enums."""
    from lib.episode.episode_planner import DramaPlanDraft, NarrationPlanDraft
    from lib.project.project_manager import ProjectOverview
    from lib.script.script_models import (
        AdReferenceFlatScript,
        AdVisualScript,
        DramaVisualScript,
        NarrationScriptPlanDraft,
        NarrationVisualEpisodeScript,
        ReferencePromptAuthoringFlatScript,
        build_drama_normalized_script_model,
        build_episode_script_model,
        build_reference_units_script_plan_model,
        with_new_assets_field,
    )

    models: list[type[BaseModel]] = [
        # Episode planning
        NarrationPlanDraft,
        DramaPlanDraft,
        # Narration storyboard splitting / reference-to-video prompt authoring
        NarrationScriptPlanDraft,
        ReferencePromptAuthoringFlatScript,
        # Visual prompt authoring
        NarrationVisualEpisodeScript,
        DramaVisualScript,
        AdVisualScript,
        # Reference-to-video ad scripts
        AdReferenceFlatScript,
        # Project overview
        ProjectOverview,
    ]
    for durations in ([4, 6, 8], [8]):
        models += [
            build_drama_normalized_script_model(durations),
            build_reference_units_script_plan_model(durations),
            with_new_assets_field(build_episode_script_model("ad", durations)),
        ]
    return models


def response_schema_params() -> list:
    """pytest parameters for :func:`response_schema_models`; distinguish same-named dynamic models with numbered IDs."""
    return [pytest.param(model, id=f"{i}-{model.__name__}") for i, model in enumerate(response_schema_models())]


def _model_field_descriptions(model: type[BaseModel]) -> set[str]:
    """All field ``description`` values across models/nested models."""
    seen: set[type[BaseModel]] = set()
    found: set[str] = set()

    def visit(tp: object) -> None:
        if isinstance(tp, type) and issubclass(tp, BaseModel) and tp not in seen:
            seen.add(tp)
            for field in tp.model_fields.values():
                if field.description:
                    found.add(field.description)
                visit(field.annotation)
        for arg in get_args(tp):
            visit(arg)

    visit(model)
    return found


def _class_docstring_texts(model: type[BaseModel]) -> set[str]:
    """Class-docstring descriptions in default ``model`` schemas, text that must not reach models.

    Read root/nested ``$defs`` ``description`` values. Exclude text equal to field descriptions
    (e.g. ``Composition`` 构图信息): default schemas retain only definition descriptions then,
    making them indistinguishable, so collect field descriptions from model classes for exclusion.
    """
    raw = model.model_json_schema()
    nodes = [raw, *raw.get("$defs", {}).values()]
    return {node["description"] for node in nodes if "description" in node} - _model_field_descriptions(model)


def leaked_class_docstrings(model: type[BaseModel], wire_json: str) -> list[str]:
    """``model`` class docstrings found in endpoint ``wire_json``, matched by first lines.

    Instructor tool descriptions retain only opening paragraphs; first-line matching covers
    both complete explanations and extracted opening paragraphs.
    """
    first_lines = (inspect.cleandoc(text).splitlines()[0] for text in _class_docstring_texts(model))
    return [line for line in first_lines if json.dumps(line, ensure_ascii=False)[1:-1] in wire_json]
