---
type: "query"
date: "2026-08-22T15:06:10.085588+00:00"
question: "火山没有返回具体参数名，所以无法断言究竟是 tools、强制 tool_choice，还是 DramaPlanDraft 的工具 schema 中某个细节；但已经可以确定，问题就在这一组 TOOLS 结构化调用参数，不是 Agent 的 {}。如果你无法断言具体是什么，那你需要通过测试来确定到底是哪里的问题啊。确定完问题之后，你再出解决方案。"
contributor: "graphify"
outcome: "corrected"
correction: "实测确认具体失败字段是强制 tool_choice；更上游根因是 deepseek-v4-pro 漏标 structured_output，导致本应走原生 json_schema 的模型被错误送入 Instructor 强制工具调用路径。"
source_nodes: ["EpisodePlanner", "ArkTextBackend", "instructor_support.py"]
---

# Q: 火山没有返回具体参数名，所以无法断言究竟是 tools、强制 tool_choice，还是 DramaPlanDraft 的工具 schema 中某个细节；但已经可以确定，问题就在这一组 TOOLS 结构化调用参数，不是 Agent 的 {}。如果你无法断言具体是什么，那你需要通过测试来确定到底是哪里的问题啊。确定完问题之后，你再出解决方案。

## Answer

Expanded from original query via graph vocab: [episode, planner, ark, instructor, structured, tools, tool, choice, schema, parameter, request, fallback]. 对 ark-agent-plan/deepseek-v4-pro 做最小差分实测：plain=200；最小 tools 无 tool_choice=200；tools+tool_choice=auto=200；tools+tool_choice=required=400 InvalidParameter；tools+强制 function object=400；完整 DramaPlanDraft schema 无 tool_choice=200；完整 schema+强制 function=400。故具体无效参数是强制工具选择 tool_choice，不是 tools 或 DramaPlanDraft schema。进一步实测 native response_format=json_schema 返回 200 且通过 DramaPlanDraft 校验，max_tokens=64000 同样 200；TOOLS auto 也能正常返回 tool_calls；MD_JSON 也通过 schema。根因是 registry 中 deepseek-v4-pro 仅登记 text_generation、漏登 structured_output，ArkTextBackend 因此错误进入 Instructor TOOLS 路径，而 Instructor 强制指定 tool_choice，触发 Agent Plan 400。推荐最小修复：为 ark-agent-plan/deepseek-v4-pro 增加 structured_output capability，使其走已经实测通过的原生 json_schema 路径；补 registry/Ark backend 回归测试与请求形态测试。不要把所有通用 400 全部降级，也无需改 plan_episodes、源文件或 DramaPlanDraft。

## Outcome

- Signal: corrected
- Correction: 实测确认具体失败字段是强制 tool_choice；更上游根因是 deepseek-v4-pro 漏标 structured_output，导致本应走原生 json_schema 的模型被错误送入 Instructor 强制工具调用路径。

## Source Nodes

- EpisodePlanner
- ArkTextBackend
- instructor_support.py