# Local Inference Protocol Proxy

ArcReel 主仓库不收录为本地推理服务（如 llama.cpp 的 llama-server）做协议转换的代理脚本或服务。

## Why this is out of scope

请求示例（PR #2445）：在主仓库 `scripts/` 下新增一个代理，把内嵌 Agent 发出的 Anthropic 协议请求转换后转发给本地 llama-server，并改写 system / developer 消息的排列。

这类代理面向的是某个具体推理服务的协议差异，随上游版本变化而变化，放进主仓库就要由 ArcReel 跟进维护和安全审查（该 PR 自带的 CodeQL 告警即属此类）。智能体凭证支持自定义 `base_url`，完整兼容 Anthropic Messages API（`/v1/messages`）的服务可以直接接入；不兼容时，转换层更适合作为独立项目维护，由使用者自行部署。

重开条件：某类本地推理服务成为大量用户的主要接入方式，且无法通过自定义 `base_url` 接入。此时应开 issue 讨论在产品内提供正式支持，而不是收录转换脚本。

## Prior requests

- PR #2445 — Add Anthropic-compatible proxy for local llama.cpp agent provider
