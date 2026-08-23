---
type: "query"
date: "2026-08-22T15:11:32.769260+00:00"
question: "可以的，就是改了并补上这个Structured Output就可以了是吧？还需要做其他的内容吗？"
contributor: "graphify"
outcome: "useful"
source_nodes: ["ModelInfo", "ArkTextBackend", "EpisodePlanner"]
---

# Q: 可以的，就是改了并补上这个Structured Output就可以了是吧？还需要做其他的内容吗？

## Answer

Expanded from graph vocab: [structured, capability, registry, ark, backend, model, resolver, factory, provider, cache, migration]. 对当前 400 的生产逻辑修复只需在 PROVIDER_REGISTRY[ark-agent-plan].models[deepseek-v4-pro].capabilities 中补现有 token structured_output。ArkTextBackend 每次实例化在 _resolve_capabilities 中直接扫描 registry，命中后自动走 response_format=json_schema；无第二份能力配置、持久化缓存、项目 schema 或数据库迁移。无需改 plan_episodes、DramaPlanDraft、Instructor、Agent provider catalog、前端或源文件。工程上必须补 registry 与 Ark native-path 回归测试，部署后重启后端进程并重试 plan_episodes。该修复消除已确认的强制 tool_choice 400；模型输出内容仍可能触发规划器正常 schema/锚点校验重试，这是另一类业务校验。

## Outcome

- Signal: useful

## Source Nodes

- ModelInfo
- ArkTextBackend
- EpisodePlanner