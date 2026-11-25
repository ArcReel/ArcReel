---
status: accepted
---

# 文本调用统一经流式运行时执行，按送达歧义重试并设等待上限

文本生成按「统一文本执行运行时 + 按真实 Wire 协议划分的极薄异步协议驱动」分层，保留 `openai_chat`（统一承接 OpenAI Chat 兼容、DashScope、MiniMax、Ark 与 Agnes，按构造参数表达 `strict`、token 参数名与围栏清洗等差异）、`openai_responses`、`anthropic_messages`、`gemini_generate` 与 `grok` 五类协议驱动，避免各通道重复维护重试、降级与清洗骨架而行为漂移。

全部文本协议默认采用异步流式接收。统一运行时单点接管计时、重试裁决、响应清洗、运行态回调与无原文、无提示词、无密钥的结构化脱敏诊断日志；各协议驱动负责请求编码、终帧判定、截断识别与供应商用量解释（含 OpenAI Chat 尾部用量帧、Anthropic 5 分钟与 1 小时缓存写拆分、Gemini 自定义路径覆盖对 `:streamGenerateContent` 的兼容及外部 `httpx` 客户端关闭责任，以及 Grok 流式 `(Response, Chunk)` 元组拆解）。

## 重试边界

所有底层 SDK 必须禁用隐式重试（含 `google-genai` 显式使用 `httpx_async_client` 并设 `HttpRetryOptions(attempts=1)`，以及 Grok 文本客户端关闭 gRPC 通道默认的 `UNAVAILABLE` 自动重试），只保留统一运行时一层重试。重试包裹单次物理网络调用，不包裹外层复验与降级；判定依据取结构化异常类型与 HTTP/gRPC 状态码（同时兜住流式传输抛出的底层 `httpx.ReadTimeout`），不做异常消息字符串子串匹配。

重试遵循与 [ADR 0123](0123-provider-submission-and-retrieval-retry-boundaries.md) 对齐的送达歧义边界：仅对确认尚未送达的传输错误（建连、连接池或代理握手失败）及上游明确返回的可重试状态（408、425、429、5xx 与 gRPC 瞬态码）在单次调用内最多尝试 3 次；请求发出后的读超时、空闲停滞与流式中途断连均视为受理结果未知，不自动重发，避免代理缓冲响应或中途断流引发重复计费。

## 等待上限

每次文本调用同时受三段边界约束：建连超时（15 秒）、应用层有效增量空闲停滞看门狗（180 秒，由 `asyncio.timeout` 挂在有效正文或思考增量 chunk 迭代上重置，忽略 SSE `ping` 等纯保活帧），以及整次生成单调时钟总等待上限（900 秒，覆盖退避、原生调用与降级）。触达等待上限时以独立失败码终止本地等待；遵守 [ADR 0006](0006-cancel-only-queued-tasks.md)，界面明确告知「本地已停止等待，但上游请求仍可能继续完成并计费」，不把本地超时写成用户取消。结构化降级见 [ADR 0014](0014-instructor-selective-structured-output-fallback.md)，输出上限与截断见 [ADR 0044](0044-text-output-token-nonbinding-ceiling.md)。
