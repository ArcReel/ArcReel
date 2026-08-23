"""Request-scoped image provider/model selection shared by Web and Agent entry points."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ImageModelSelection(BaseModel):
    """An optional exact provider/model pair; both omitted means project default."""

    model_config = ConfigDict(extra="forbid")

    image_provider: str | None = Field(default=None, min_length=1)
    image_model: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _require_complete_pair(self) -> "ImageModelSelection":
        if (self.image_provider is None) != (self.image_model is None):
            raise ValueError("image_provider and image_model must be provided together")
        return self

    def image_override_payload(self) -> dict[str, str]:
        if self.image_provider is None or self.image_model is None:
            return {}
        return {"image_provider": self.image_provider, "image_model": self.image_model}


def image_override_from_args(args: dict[str, Any]) -> dict[str, str]:
    """Validate optional Agent-tool fields through the same Pydantic contract."""

    return ImageModelSelection.model_validate(
        {
            "image_provider": args.get("image_provider"),
            "image_model": args.get("image_model"),
        }
    ).image_override_payload()


IMAGE_MODEL_TOOL_PROPERTIES: dict[str, dict[str, Any]] = {
    "image_provider": {
        "type": "string",
        "description": "可选，本次请求使用的图片供应商 ID；与 image_model 同时提供，省略则跟随项目默认",
    },
    "image_model": {
        "type": "string",
        "description": "可选，本次请求使用的图片模型 ID；与 image_provider 同时提供，省略则跟随项目默认",
    },
}

