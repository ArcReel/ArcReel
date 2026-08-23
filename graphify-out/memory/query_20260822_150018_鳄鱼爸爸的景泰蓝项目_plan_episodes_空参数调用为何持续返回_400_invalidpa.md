---
type: "query"
date: "2026-08-22T15:00:18.265793+00:00"
question: "鳄鱼爸爸的景泰蓝项目 plan_episodes 空参数调用为何持续返回 400 InvalidParameter，请结合 Request ID 021787410493856f209293b0689fc4da4ccbf3e82cce55dea0e03 定位后端"
contributor: "graphify"
outcome: "useful"
source_nodes: ["EpisodePlanner", "ArkTextBackend"]
---

# Q: 鳄鱼爸爸的景泰蓝项目 plan_episodes 空参数调用为何持续返回 400 InvalidParameter，请结合 Request ID 021787410493856f209293b0689fc4da4ccbf3e82cce55dea0e03 定位后端

## Answer

结论：plan_episodes 的空对象输入合法，instructions 是可选参数；源文件为 screenplay 也不是失败原因。EpisodePlanner 要求 DramaPlanDraft 结构化输出，而 ark-agent-plan/deepseek-v4-pro 未声明 structured_output 能力，因此 ArkTextBackend 进入 Instructor 降级链并首先以 TOOLS 模式发送 tools 与强制 tool_choice=DramaPlanDraft。相同端点、模型的最小普通文本调用成功；把规划请求 max_tokens 从 64000 降至 4096 后仍同样 400，说明 API key、base URL、模型名、普通 max_tokens 以及源文本格式均不是主因。Agent Plan 端点对该结构化 TOOLS 请求组合返回通用 InvalidParameter 且 param 为空；_classify_mode_failure 只有错误文本明确出现 tools/tool_choice/functions 等关键字才会降到 MD_JSON，因通用错误未命中而直接 PROPAGATE，所以反复重置也不会改善。属于 provider 结构化工具调用兼容性与本地降级识别之间的后端问题。查询扩展词：episode planner parameter request backend provider error validation text generation ark response。

## Outcome

- Signal: useful

## Source Nodes

- EpisodePlanner
- ArkTextBackend