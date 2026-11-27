"""本次生成的模型与参数覆盖；只派生项目快照，不写项目配置。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GenerationOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: str | None = Field(default=None, min_length=3, max_length=512, pattern=r"^[^/\s]+/\S+$")
    resolution: str | None = Field(default=None, max_length=64)
    aspect_ratio: Literal["16:9", "9:16", "1:1", "4:3", "3:4"] | None = None
    generate_audio: bool | None = None
    duration_seconds: int | None = Field(default=None, gt=0, le=300)


def apply_generation_options(project: dict, payload: dict | None, media_type: str) -> dict:
    raw = (payload or {}).get("generation_options")
    if raw is None:
        return project
    options = GenerationOptions.model_validate(raw)
    result = {**project}
    if options.backend:
        keys = (
            ("default_image_backend", "image_provider_t2i", "image_provider_i2i")
            if media_type == "image"
            else ("video_backend", "video_provider_i2v", "video_provider_r2v")
        )
        result.update(dict.fromkeys(keys, options.backend))
        if "resolution" in options.model_fields_set:
            settings = {**project.get("model_settings", {})}
            settings[options.backend] = {"resolution": options.resolution}
            result["model_settings"] = settings
            result["video_model_settings"] = {}
    if options.aspect_ratio is not None:
        result["aspect_ratio"] = options.aspect_ratio
    if options.generate_audio is not None and media_type == "video":
        result["video_generate_audio"] = options.generate_audio
    return result
