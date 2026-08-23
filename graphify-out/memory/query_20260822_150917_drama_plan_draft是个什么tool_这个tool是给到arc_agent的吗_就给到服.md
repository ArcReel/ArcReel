---
type: "query"
date: "2026-08-22T15:09:17.833491+00:00"
question: "Drama plan draft是个什么tool？这个tool是给到Arc Agent的吗？就给到服务商那边的吗？还是说这个是我们自己的一些tool？然后Structured output是Capability吗？Capability它在整个的体系里面自带的什么？是Text generation、Image generation还是Video generation这种Capability吗？有这种相应的Capability的List吗？你是在单独去添加一些自定义的Capability吗？然后这个是问题是基于这个arc agent plan去产生的吗？还是说in general针对于这个火山方舟的都有这种问题。这个是渠道带来的问题？还是模型带来的问题？还是什，还是这个模式带来的问题？"
contributor: "graphify"
outcome: "useful"
source_nodes: ["DramaPlanDraft", "TextCapability", "ModelInfo", "ArkTextBackend"]
---

# Q: Drama plan draft是个什么tool？这个tool是给到Arc Agent的吗？就给到服务商那边的吗？还是说这个是我们自己的一些tool？然后Structured output是Capability吗？Capability它在整个的体系里面自带的什么？是Text generation、Image generation还是Video generation这种Capability吗？有这种相应的Capability的List吗？你是在单独去添加一些自定义的Capability吗？然后这个是问题是基于这个arc agent plan去产生的吗？还是说in general针对于这个火山方舟的都有这种问题。这个是渠道带来的问题？还是模型带来的问题？还是什，还是这个模式带来的问题？

## Answer

Expanded from graph vocab: [drama, plan, draft, tool, agent, schema, structured, capability, text, image, video, generation, provider, model, registry]. DramaPlanDraft 是 lib/episode_planner.py 的本地 Pydantic 输出 schema，不是 Agent MCP Tool；Agent 真正调用的是 plan_episodes。规划器把 DramaPlanDraft 作为 TextGenerationRequest.response_schema 交给文本后端。原生 structured_output 路径把它作为 response_format.json_schema 发给供应商；Instructor TOOLS fallback 才会临时包装成名为 DramaPlanDraft 的 function tool 发给供应商，供应商回 tool_call.arguments 后本地解析校验，并不执行一个业务函数。structured_output 是现有 TextCapability，不是新造 token。封闭 ModelCapability 词表含 text_generation、structured_output、vision、text_to_image、image_to_image、generate_audio、text_to_speech；文本/图片/音频各有枚举，视频输入能力另由 VideoCapabilities 和 registry 时长分辨率字段表达。已证实故障范围是 ark-agent-plan /api/plan/v3 + deepseek-v4-pro + forced tool_choice：该组合接受 tools、完整 schema、tool_choice=auto、native json_schema，拒绝 required/forced function。标准 ark 使用不同 base_url 与模型 ID，不能据此断言全火山方舟都有问题。根因是两项组合：deepseek-v4-pro registry 漏标 structured_output（本地模型元数据问题），导致进入 Instructor forced tool_choice；Agent Plan 对该模型/端点的强制工具选择不兼容（远端模式兼容问题）。

## Outcome

- Signal: useful

## Source Nodes

- DramaPlanDraft
- TextCapability
- ModelInfo
- ArkTextBackend