import json
import uuid
from pathlib import Path

import comfy.model_management as model_management
import numpy as np
from comfy_api.latest import ComfyExtension, InputImpl, io, ui
from PIL import Image

from . import runner

ROOT = Path(__file__).resolve().parents[2]


class MacQualityVideo(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="MacQualityVideo",
            display_name="Mac Video — LTX 2.3",
            category="video/Mac",
            description="Local Apple Silicon video with optional start and end anchors. Two anchors use dedicated dev keyframe interpolation and require 19 GiB available. Start-only Animated dialogue supports 2, 4, or 5 seconds at 24 fps and requires 14 GiB; other presets require 19 GiB. Stop requests a safe exit.",
            inputs=[
                io.String.Input("prompt", multiline=True),
                io.Combo.Input(
                    "quality", options=["Balanced", "High quality", "Animated dialogue"], default="Balanced"
                ),
                io.Combo.Input("duration", options=["2 seconds", "4 seconds", "5 seconds"], default="2 seconds"),
                io.Int.Input("seed", default=2026100442, min=0, max=4294967295, control_after_generate=True),
                io.Image.Input("reference_image", optional=True),
                io.Image.Input("end_image", optional=True),
                io.Int.Input("frames", default=121, min=49, max=121, step=24, optional=True),
            ],
            outputs=[io.Video.Output()],
            is_output_node=True,
        )

    @classmethod
    def execute(cls, prompt, quality, duration, seed, reference_image=None, end_image=None, frames=None):
        if not prompt.strip():
            raise ValueError("Describe the scene and movement first.")
        if frames is not None and frames not in (49, 97, 121):
            raise ValueError("Choose 49, 97, or 121 frames (2, 4, or 5 seconds at 24 fps).")
        model_management.unload_all_models()
        model_management.soft_empty_cache()
        preset = "high" if quality == "High quality" else "balanced"
        config = json.loads((ROOT / f"user/video-quality-{preset}.json").read_text())
        config.update(
            name="mac-video-" + uuid.uuid4().hex[:12],
            prompt=prompt,
            frames=frames if frames is not None else {"2 seconds": 49, "4 seconds": 97, "5 seconds": 121}[duration],
            seed=seed,
        )
        if quality == "Animated dialogue":
            config["recipe"] = "animated-dialogue"
        jobs = ROOT / "user/video-jobs"
        jobs.mkdir(parents=True, exist_ok=True)
        for key, tensor in (("image", reference_image), ("end_image", end_image)):
            if tensor is None:
                continue
            reference_path = jobs / (config["name"] + "-" + key + ".png")
            pixels = np.clip(tensor[0].cpu().numpy() * 255, 0, 255).astype(np.uint8)
            Image.fromarray(pixels).save(reference_path)
            config[key] = str(reference_path)
        config_path = jobs / (config["name"] + ".json")
        config_path.write_text(json.dumps(config, indent=2))
        report = runner.run(config_path, model_management.throw_exception_if_processing_interrupted)
        video_path = Path(report["output"])
        video = InputImpl.VideoFromFile(str(video_path))
        return io.NodeOutput(
            video, ui=ui.PreviewVideo([ui.SavedResult(video_path.name, "quality", io.FolderType.output)])
        )


class MacVideoExtension(ComfyExtension):
    async def get_node_list(self):
        return [MacQualityVideo]


async def comfy_entrypoint():
    return MacVideoExtension()
