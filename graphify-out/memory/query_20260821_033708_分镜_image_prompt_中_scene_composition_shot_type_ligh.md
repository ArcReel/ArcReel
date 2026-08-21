---
type: "explain"
date: "2026-08-21T03:37:08.131022+00:00"
question: "分镜 image_prompt 中 scene、composition.shot_type、lighting、ambiance 在哪个阶段产生，style 如何嵌入？"
contributor: "graphify"
outcome: "useful"
source_nodes: ["Composition", "ImagePrompt", "NarrationVisualSegment", "DramaSceneVisual"]
---

# Q: 分镜 image_prompt 中 scene、composition.shot_type、lighting、ambiance 在哪个阶段产生，style 如何嵌入？

## Answer

两阶段脚本链路：step1 生成并审核内容层；step2 视觉 LLM 接收 step1、资产和项目 style，按 scene_id/segment_id 生成 image_prompt.scene 与 composition 的 shot_type/lighting/ambiance，随后合并写入最终剧本。点击生成分镜图时 generation_tasks 读取已保存 image_prompt，并由 build_storyboard_prompt 将项目 style/style_description 与结构化字段序列化为最终 provider prompt，不再生成 composition。缺失/非法 shot_type 仅有 Medium Shot 容错。

## Outcome

- Signal: useful

## Source Nodes

- Composition
- ImagePrompt
- NarrationVisualSegment
- DramaSceneVisual