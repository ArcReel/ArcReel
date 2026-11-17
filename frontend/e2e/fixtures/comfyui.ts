import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/** API 格式样本与后端上传/构造测试共用，录制只调用本地定义管理接口。 */
export const COMFYUI_AUDIO_ASPECT = {
  kind: "comfyui",
  schema_version: "1.0.0",
  meta: { name: "MiniMax H3 参考音频与画幅", author: "ArcReel", version: "1.0.0" },
  media_type: "video",
  workflow: JSON.parse(readFileSync(resolve(import.meta.dirname, "../../../tests/unit/lib/custom_provider/comfyui/data/minimax_h3_ref2va.json"), "utf8")) as Record<string, unknown>,
  bindings: {
    prompt: [{ node: "138", input: "value", class_type: "PrimitiveStringMultiline" }],
    output: [{ node: "92", class_type: "SaveVideo" }],
    reference_audio: ["501", "502", "503"].map((node) => ({ node, input: "audio", class_type: "LoadAudio" })),
    aspect_ratio: [{ node: "115", input: "aspect_ratio", class_type: "ResolutionSelector", values: {
      "16:9": "16:9 (Widescreen)", "9:16": "9:16 (Portrait)",
    } }],
    width: [], height: [], frames: [], fps: [], seed: [], start_image: [], end_image: [], reference_images: [], negative_prompt: [],
  },
};
