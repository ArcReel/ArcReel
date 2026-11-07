"""Capture local tool/model structure for comparison with the fixed source archive.

Run from the repository environment with an output JSON path. This imports model
and tool declarations but never starts the application or invokes generation.
"""

import argparse
import inspect
import json
from pathlib import Path

from pydantic import BaseModel

import lib.script.script_models as models
from lib.project.project_manager import ProjectOverview
from lib.project.source_revision import SourceScope
from server.agent_toolset.toolset import AGENT_TOOLSET


def structural(value):
    """Remove description metadata only at schema nodes, preserving instance/property data."""
    if not isinstance(value, dict):
        return value
    out = {}
    for k, v in value.items():
        if k == "description":
            continue
        if k in ("properties", "$defs", "definitions", "patternProperties") and isinstance(v, dict):
            out[k] = {name: structural(schema) for name, schema in v.items()}
        elif k in (
            "items",
            "additionalProperties",
            "not",
            "if",
            "then",
            "else",
            "contains",
            "propertyNames",
        ) and isinstance(v, dict):
            out[k] = structural(v)
        elif k in ("allOf", "anyOf", "oneOf", "prefixItems") and isinstance(v, list):
            out[k] = [structural(x) for x in v]
        else:
            out[k] = v
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    out = {"tools": {}, "models": {}}
    for d in AGENT_TOOLSET:
        out["tools"][d.name] = {
            "schema": structural(d.input_schema),
            "domain_key": d.domain_key,
            "long_task": getattr(d, "long_task", False),
            "migration": type(getattr(d, "migration", None)).__name__,
            "handler": d.handler.__module__ + "." + d.handler.__name__,
        }
    for name, cls in vars(models).items():
        if inspect.isclass(cls) and issubclass(cls, BaseModel) and cls.__module__ == models.__name__:
            out["models"][name] = structural(cls.model_json_schema())
    out["additional_models"] = {
        "ProjectOverview": structural(ProjectOverview.model_json_schema()),
        "SourceScope": structural(SourceScope.model_json_schema()),
    }
    out["dynamic_models"] = {}
    for durations in ([], [6], [4, 6, 8]):
        factories = {
            **{
                f"episode/{mode}": (
                    lambda mode=mode, durations=durations: models.build_episode_script_model(mode, durations)
                )
                for mode in ("narration", "drama", "ad")
            },
            "drama_plan": lambda durations=durations: models.build_drama_normalized_script_model(durations),
            "reference_plan": lambda durations=durations: models.build_reference_units_script_plan_model(durations),
        }
        for name, factory in factories.items():
            key = f"{name}/{durations}"
            try:
                model = factory()
                out["dynamic_models"][key] = structural(model.model_json_schema())
                out["dynamic_models"][key + "/new_assets"] = structural(
                    models.with_new_assets_field(model).model_json_schema()
                )
            except ValueError as error:
                out["dynamic_models"][key] = {"error_type": type(error).__name__, "error": str(error)}
    args.output.write_text(json.dumps(out, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(len(out["tools"]), "tools;", len(out["models"]), "script models")


if __name__ == "__main__":
    main()
