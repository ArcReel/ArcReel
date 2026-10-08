# Claude Agent SDK 的 usage 与费用字段

> 状态：调研完成，结论供地图 [#3079](https://github.com/ArcReel/ArcReel/issues/3079) 汇总。
> 关联：[#3084](https://github.com/ArcReel/ArcReel/issues/3084)（本票）。
> 版本基线：Python `claude-agent-sdk` 0.2.163（`uv.lock`），捆绑 Claude Code CLI 2.1.286（`claude_agent_sdk/_cli_version.py`）。CLI 源码引用来自捆绑二进制 `claude_agent_sdk/_bundled/claude` 中内嵌的 JS 文本。标识符已压缩，下文用 `fn:<压缩名>` 标注，仅用于复查，换版本后名字会变。
> 方法：只读源码与官方文档，没有用任何凭证发请求。标注「推断」的结论来自读代码，未经实测。

## 结论速览

| 问题 | 结论 |
|---|---|
| 每轮有哪些字段 | `AssistantMessage.usage`（单步、原样转发 Messages API 的 `usage`），`ResultMessage.usage`（本轮、仅主循环），`ResultMessage.total_cost_usd` 与 `model_usage`（整个 `query()` 调用的累计值，含子智能体）。详见 §1。 |
| 单轮还是累计 | 在 ArcReel 使用的 streaming input 模式下，`total_cost_usd` 与 `model_usage` 是**累计值**，resume 后还会带上会话此前的花费；只有 `ResultMessage.usage` 是本轮值。详见 §2。 |
| `costUSD` 怎么算 | CLI 在本地按构建时内置的 Claude 价格表估算，不是账单。未知模型名（例如 `deepseek-v4-pro`）不返回 0，而是按主循环模型的价格估算，主循环模型也未知时按内置默认价（每百万 token 输入 5 美元、输出 25 美元），并把 `costBasis` 标成 `unknown`。详见 §3。 |
| 网关缓存字段 | 不一致。文档明确与 Anthropic 口径一致的只有 Anthropic 官方、Kimi 开放平台和 MiniMax；DeepSeek、智谱、Kimi Code 的文档未说明；火山方舟的 `cache_creation_input_tokens` 恒为 0，且 `input_tokens` 可能包含命中部分。详见 §4。 |
| `model_usage` 的键 | 是发给 API 的模型 ID，即经过 `ANTHROPIC_DEFAULT_*_MODEL` / `CLAUDE_CODE_SUBAGENT_MODEL` 别名解析之后的真实模型名（推断）。详见 §5。 |
| ArcReel 能否自算 | 可以，而且应该自算。按轮对 `model_usage` 做差分，得到每个模型本轮的 input、output、cache_read、cache_creation，再用模型目录的价格计费。详见 §6。 |

另外发现一个现存缺陷：`_record_assistant_usage` 把累计的 `total_cost_usd` 当作本轮费用写入 `api_calls`，同一会话的费用会被重复计入。详见 §6.1。

## 1. 字段清单

### 1.1 `AssistantMessage.usage`（单步）

- Python 定义：`usage: dict[str, Any] | None`，同时有 `message_id`、`model`、`parent_tool_use_id`（`types.py` 中的 `AssistantMessage`）。
- 内容是该次 API 响应的 `usage` 原样转发，字段与 Messages API 一致：`input_tokens`、`output_tokens`、`cache_creation_input_tokens`、`cache_read_input_tokens`、`cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`、`server_tool_use.web_search_requests` 等（Messages API 字段见 §4.1）。
- 官方文档的两条约束：
  - 并行工具调用时，同一次响应会拆成多条 `AssistantMessage`，共享 `message_id` 和相同的 `usage`，需要按 `message_id` 去重。
  - 「Per-step `output_tokens` is a placeholder」：CLI 用 `message_start` 时的 usage 构造助手消息，`output_tokens` 只是开头的占位值，真实输出数要从 `ResultMessage` 读取。
  —— [agent-sdk/cost-tracking#track-per-step-usage](https://code.claude.com/docs/en/agent-sdk/cost-tracking#track-per-step-usage)、[#read-output-tokens-from-the-result-message](https://code.claude.com/docs/en/agent-sdk/cost-tracking#read-output-tokens-from-the-result-message)
- `parent_tool_use_id` 非空表示该消息来自子智能体。

### 1.2 `ResultMessage`

Python 定义（`types.py` 中的 `ResultMessage`）里与计费有关的字段：

| 字段 | 类型 | 语义 |
|---|---|---|
| `usage` | `dict \| None` | Messages API 形状的 usage。CLI schema 描述：「MAIN AGENT LOOP ONLY — excludes Task subagent, sidechain, and auxiliary model calls, and is per-turn in streaming-input sessions. Prefer modelUsage for token/cost accounting.」（CLI `zw` schema） |
| `total_cost_usd` | `float \| None` | 「Cumulative estimated cost in USD for this query() call, covering the same query-pipeline calls as modelUsage … cumulative across turns in streaming-input sessions … read the latest result rather than summing across results … An estimate, not a billing statement.」（CLI `Vw` 描述） |
| `model_usage` | `dict[str, ModelUsage] \| None` | 按模型的累计用量与费用。CLI 描述：「Per-model totals for every model call made through the query pipeline during this query() call — main loop, Task subagents, sidechains, and internal calls such as compaction and Workflow agents. Cumulative across turns in streaming-input sessions … Internal helper calls outside the query pipeline (e.g. the permission classifier, token-count probes) are excluded … a resumed or forked session continues from the totals its transcript saved … a mid-session /clear resets the running total.」（CLI `xw` 描述） |
| `num_turns` | `int` | 本轮内的模型往返次数，不是用户轮次。 |
| `terminal_reason` | `str \| None` | `aborted_streaming` / `aborted_tools` 表示被中断。 |
| `origin` | `MessageOrigin \| None` | 区分用户发起的轮次和后台任务通知触发的轮次（`{"kind": "task-notification"}`）。 |

### 1.3 `ModelUsage`（`model_usage` 的值）

Python `TypedDict` 声明的键：`inputTokens`、`outputTokens`、`cacheReadInputTokens`、`cacheCreationInputTokens`、`webSearchRequests`、`costUSD`、`contextWindow`、`maxOutputTokens`，可选 `canonicalModel`、`provider`。

CLI 2.1.286 实际还会输出两个 Python 类型没有声明的键，它们会随字典透传（CLI `qt` schema）：

- `thinkingTokens`：「already counted inside outputTokens」。
- `costBasis`：`list` / `managed` / `unknown`。`unknown` 的原文是「no pricing row and no built-in price matched the model ID, so costUSD is a guess at the default model's rate」。需要 Claude Code 2.1.246 及以上（[cost-tracking#break-down-usage-per-model](https://code.claude.com/docs/en/agent-sdk/cost-tracking#break-down-usage-per-model)）。

累加逻辑（`fn:FNn`）每次 API 响应执行一次：

```js
s.inputTokens += n.input_tokens
s.outputTokens += n.output_tokens
s.thinkingTokens = (s.thinkingTokens ?? 0) + (n.output_tokens_details?.thinking_tokens ?? 0)
s.cacheReadInputTokens += n.cache_read_input_tokens ?? 0
s.cacheCreationInputTokens += n.cache_creation_input_tokens ?? 0
s.webSearchRequests += n.server_tool_use?.web_search_requests ?? 0
s.costUSD += e            // e 为该次响应的估算费用
s.canonicalModel = ...; s.provider = ...; s.costBasis = ...
```

由此可得：

- `inputTokens` 与 API 的 `input_tokens` 同义，**不含**缓存读写部分。总输入 = `inputTokens + cacheReadInputTokens + cacheCreationInputTokens`。
- `model_usage` **没有** 5 分钟和 1 小时 TTL 的拆分，`cacheCreationInputTokens` 是两者之和。拆分只存在于单步 `AssistantMessage.usage.cache_creation`。CLI 计价时按单步拆分分别计价（`fn:Tx`），所以 `costUSD` 已区分 TTL，而 token 计数没有。
- 只有 `webSearchRequests`，没有 web fetch 计数。web fetch 在 Messages API 中不单独计费。

### 1.4 其他与用量相关的消息

- `TaskProgressMessage` / `TaskNotificationMessage` 的 `usage` 是 `TaskUsage`，只有 `total_tokens`、`tool_uses`、`duration_ms`，不分 input/output/cache，不能用于计价。
- `ConversationResetMessage`：`/clear` 等重置会话时发出，之后的 `total_cost_usd` 等累计值清零（`types.py` 中 `ConversationResetMessage` 的文档字符串）。
- `ClaudeSDKClient.get_context_usage()` 的 `apiUsage` 是会话累计的 API 用量，用于上下文展示，不适合计费。

## 2. 字段的作用域

### 2.1 单轮还是累计

官方文档「Track costs in streaming input mode」原文：

> * **`usage`**: covers only that turn, and within it only the main agent loop, not any subagents it ran.
> * **`total_cost_usd` and `modelUsage`, or `model_usage` in Python**: carry the running total for the whole call so far, plus any spend restored when the call resumed a session.
>
> In a call where your app never sends `/clear`, `/reset`, or `/new`, read the latest result for call totals rather than summing across results.

—— [agent-sdk/cost-tracking#track-costs-in-streaming-input-mode](https://code.claude.com/docs/en/agent-sdk/cost-tracking#track-costs-in-streaming-input-mode)

`ClaudeSDKClient` 始终工作在 streaming input 模式。ArcReel 每个会话由一个 `SessionActor` 持有一个 `ClaudeSDKClient`，多个用户轮次复用同一连接（`server/agent_runtime/session_actor.py`），所以每轮的 `ResultMessage.total_cost_usd` 都是从连接建立（或 resume）以来的累计值。

resume 的影响：「Claude Code saves the session's totals to its transcript when the process exits normally and restores them when a later call resumes or forks the session. Each result already includes the session's earlier spend. … Before v2.1.277, a session that you resumed through the SDK or `claude -p` started its totals at zero」。捆绑 CLI 是 2.1.286，已经是恢复累计值的行为。—— [cost-tracking#accumulate-costs-across-multiple-calls](https://code.claude.com/docs/en/agent-sdk/cost-tracking#accumulate-costs-across-multiple-calls)

### 2.2 子智能体与后台任务

| 字段 | 子智能体 |
|---|---|
| `ResultMessage.usage` | 不含 |
| `total_cost_usd` | 含 |
| `model_usage` | 含，并按模型拆分 |

—— [cost-tracking#get-the-total-cost-of-a-query](https://code.claude.com/docs/en/agent-sdk/cost-tracking#get-the-total-cost-of-a-query)

- 压缩（compaction）、Workflow agent、sidechain 都走 query pipeline，计入 `model_usage`。
- 权限分类器、token 计数探测等 query pipeline 之外的辅助调用不计入。
- 后台子智能体在主轮结束后继续运行时，它的用量会累加到进程级计数器，体现在之后某个 `ResultMessage` 的累计值里，而不是它启动时那一轮。官方文档只说明单消息输入模式下 CLI 会等后台子智能体结束再发出 result（[cost-tracking#get-the-total-cost-of-a-query](https://code.claude.com/docs/en/agent-sdk/cost-tracking#get-the-total-cost-of-a-query)），没有说明 streaming input 模式下的等待行为，所以后台花费差分到哪一轮取决于结果到达的时间（推断）。

### 2.3 压缩、重试、中断

- 压缩：计入 `model_usage`，与主循环同模型时合并在同一个键下。
- 重试：每次 API 响应只记账一次（`fn:RLe` 外有 `Fw === "credited"` 防重）。流式连接中途失败而重试前，CLI 会先把已收到的那部分 usage 记账（`fn:FA` 在重试分支被调用），所以失败的尝试只要已经开始返回，也会计入（推断）。在拿到任何响应之前就失败的请求没有 usage，不计入。
- 中断：`total_cost_usd` 与 `model_usage` 仍包含中断前已完成的调用。被中断的那次响应按已收到的 usage 记账，`output_tokens` 可能不完整（推断）。
- `error_max_budget_usd`：`usage` 不含越过预算的那次响应，`total_cost_usd` 与 `model_usage` 含。
- 进程崩溃：最后的 `error_during_execution` 结果可能把所有费用字段清零，官方建议改用崩溃前上一轮的结果。—— [cost-tracking#recover-totals-after-a-session-crash](https://code.claude.com/docs/en/agent-sdk/cost-tracking#recover-totals-after-a-session-crash)

## 3. `costUSD` 与 `total_cost_usd` 的计算

### 3.1 定性

官方文档：「The `total_cost_usd` and `costUSD` fields are client-side estimates, not authoritative billing data. The SDK computes them locally from a price table bundled at build time, unless a `modelPricing` table is in effect.」并明确「Do not bill end users or trigger financial decisions from these fields.」—— [cost-tracking#estimates-not-billing](https://code.claude.com/docs/en/agent-sdk/cost-tracking#estimates-not-billing)

`total_cost_usd` 是所有模型 `costUSD` 的总和，两者来自同一套计算。

### 3.2 单次响应的计价公式

`fn:fYe(price, usage)`：

```
cost = (input_tokens × 输入价
      + output_tokens × 输出价
      + cache_read_input_tokens × 缓存读价
      + 缓存写费用) × 地域系数
      + web_search_requests × 单次搜索价
```

- 缓存写费用（`fn:Tx`）：`cache_creation.ephemeral_1h_input_tokens` 部分按 1 小时写价，其余按 5 分钟写价。价格表没有 1 小时写价时，全部按 5 分钟写价。
- 地域系数（`fn:Ox`）：`usage.inference_geo === "us"` 时为 1.1，否则为 1。
- 单位都是美元每百万 token。

### 3.3 价格从哪里来

`fn:Hde(model, usage)` 决定用哪张价格表：

1. 有 `modelPricing`（见 §3.4）且命中该模型时，用 `modelPricing` 行。
2. 否则走 `fn:Lmt(model, usage)`：
   1. 内置价格表 `Nq`：由 CLI 构建时生成的模型目录（`G2().models`）展开而来，只含 Claude 模型，按 `Ue(model)` 规范化后的 ID 查找。
   2. `additionalModelCostsCache`：CLI 用 Anthropic 账号登录时从 bootstrap 接口拉取的附加价格。用 API key 指向第三方网关时不会有。
   3. 都未命中时调用 `fn:Dx` 上报 `tengu_unknown_model_cost`，并把会话标记为 `hasUnknownModelCost`，然后返回 `Nq[Ue(ll())] ?? Mmt`。`ll()` 是当前主循环模型；`Mmt` 是内置默认价。

`Mmt` 在 2.1.286 中的取值（`fn:kx`）：

| 项目 | 美元 / 百万 token |
|---|---|
| 输入 | 5 |
| 输出 | 25 |
| 5 分钟缓存写 | 6.25 |
| 1 小时缓存写 | 10 |
| 缓存读 | 0.5 |
| web search | 0.01 / 次 |

### 3.4 非 Claude 模型名的结果

以 ArcReel 通过网关使用 `deepseek-v4-pro` 为例（`ANTHROPIC_MODEL=deepseek-v4-pro`）：

- `Nq` 与 `additionalModelCostsCache` 都不会命中。
- 回落到 `Nq[Ue(ll())]`。主循环模型本身就是 `deepseek-v4-pro`，同样不命中，最终用 `Mmt`。
- 结果：`costUSD` **不是 0**，而是按 5 / 25 美元的 Claude 默认价估算，`costBasis` 为 `unknown`。
- 若主循环是 Claude 模型、只有子智能体映射到非 Claude 模型，子智能体的费用会按主循环 Claude 模型的价格估算。

因此对 ArcReel 支持的 DeepSeek、GLM、Kimi、MiniMax、火山方舟等网关，`total_cost_usd` 基本都是用 Claude 价格估出来的数，与真实价格量级都可能不同。

### 3.5 `modelPricing`：让 CLI 用指定价格

`modelPricing` 设置可以为任意模型 ID 指定 `input`、`output`、`cacheRead`、`cacheWrite` 四个价格，另有整体系数 `multiplier`。它影响 `total_cost_usd`。限制如下：

- 只在 managed settings 中生效（服务器下发、MDM、`managed-settings.json`、policy helper）。user、project、local 设置和 `--settings` 中都会被忽略。
- 例外：「A host application that embeds Claude Code and sets `CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST` can supply a table of its own through the SDK `managedSettings` option, which Claude Code uses only when no managed source sets the key and only in Claude Code v2.1.246 or later.」Python SDK 0.2.163 的 `ClaudeAgentOptions` 没有 `managed_settings` 参数；CLI 有 `--managed-settings <json>` 参数（`fn:$8o`），理论上可通过 `extra_args` 传入（推断，未验证）。
- `cacheWrite` 一个价格同时用于 5 分钟和 1 小时写入，不能分别定价。
- 设置 `CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST` 还会改变其他行为：忽略 settings 文件中的提供方、端点和认证变量，忽略 managed settings 中的模型选择键，并改变第三方提供方的遥测默认值。

—— [settings-reference#modelpricing](https://code.claude.com/docs/en/settings-reference)、[env-vars](https://code.claude.com/docs/en/env-vars)

## 4. 各 Anthropic 兼容端点的缓存 token 回报

CLI 对网关返回的 `usage` 不做任何换算，`model_usage` 的四个 token 数原样累加自网关回报（§1.3）。所以网关的口径直接决定 ArcReel 能否按 Anthropic 公式计价。

### 4.1 Anthropic 官方口径

- `usage` 字段：`input_tokens`、`output_tokens`、`cache_creation_input_tokens`、`cache_read_input_tokens`、`cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`、`server_tool_use.web_search_requests` / `web_fetch_requests`、`service_tier`、`inference_geo`、`output_tokens_details.thinking_tokens`。—— [Messages API](https://platform.claude.com/docs/en/api/messages/create)
- 「Total input tokens in a request is the summation of `input_tokens`, `cache_creation_input_tokens`, and `cache_read_input_tokens`.」`cache_creation_input_tokens` 等于 `cache_creation` 中各 TTL 之和。—— [prompt-caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- 价格倍率：5 分钟写 1.25 倍、1 小时写 2 倍、读 0.1 倍；但同页脚注列出部分新模型的读价为 0.05 倍或 0.025 倍，倍率不能写死，缓存价应作为模型目录中的独立价格项。
- 流式：`message_delta` 的 usage 是累计值，且不含 `cache_creation` 拆分、`service_tier`、`inference_geo`，这些只能从 `message_start` 取。—— [streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)

### 4.2 各网关对照

| 端点 | 两个缓存字段 | `input_tokens` 不含缓存 | 缓存触发方式 | 缓存写入收费 | TTL 拆分 | 依据 |
|---|---|---|---|---|---|---|
| Anthropic 官方 | 有 | 是 | 逐块 `cache_control` | 是 | 有 | 文档明确 |
| DeepSeek `api.deepseek.com/anthropic` | 未说明 | 未说明 | 自动硬盘缓存，`cache_control` 被忽略 | 定价页只有命中、未命中两档，未提写入费 | 无 | 字段口径文档未说明 |
| 智谱 GLM `open.bigmodel.cn/api/anthropic`、`api.z.ai/api/anthropic` | 未说明 | 未说明 | 自动隐式缓存 | 未提写入费，存储「限时免费」 | 无 | 字段口径文档未说明 |
| Kimi 开放平台 `api.moonshot.cn/anthropic` | 有 | 是 | 读自动；写需顶层 `cache_control`，消息体内的标记被忽略 | 是（kimi-k3：5 分钟写 1 倍、1 小时写 2 倍） | 有 | 文档明确 |
| Kimi Code `api.kimi.com/coding` | 未说明 | 未说明 | 未说明 | 订阅额度，未说明 | 未说明 | 文档未说明 |
| MiniMax `api.minimaxi.com/anthropic`、`api.minimax.io/anthropic` | 有 | 是 | 显式 `cache_control`（最多 4 个断点）与自动被动缓存并存 | 显式写入 1.25 倍；被动缓存不收写入费 | 无，只有 5 分钟 | 文档明确；被动缓存映射到 `cache_read` 是从示例推断 |
| 火山方舟 Messages API | 有，但 `cache_creation_input_tokens` 恒为 0 | 未说明 | 隐式缓存，自动启用且不可关闭 | 隐式缓存无写入费、无存储费 | 无 | 字段存在为文档明确，口径未说明 |

来源：

- DeepSeek：[Anthropic API 指南](https://api-docs.deepseek.com/guides/anthropic_api)、[KV 缓存](https://api-docs.deepseek.com/guides/kv_cache)、[定价](https://api-docs.deepseek.com/quick_start/pricing)。原生 OpenAI 形态用 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`，映射到 Anthropic 字段的方式文档没有写。
- 智谱：[Claude 兼容说明](https://docs.bigmodel.cn/cn/guide/develop/claude/introduction)、[上下文缓存](https://docs.bigmodel.cn/cn/guide/capabilities/cache)、[z.ai 缓存](https://docs.z.ai/guides/capabilities/cache)。OpenAI 形态命中数在 `prompt_tokens_details.cached_tokens`，从示例看 `prompt_tokens` 包含命中部分（推断）。
- Kimi：[Messages API](https://platform.kimi.com/docs/api/messages)（`input_tokens` 原文为「既未命中缓存、也未用于创建缓存条目的输入 Token 数」）、[上下文缓存](https://platform.kimi.com/docs/guide/context-caching.md)、[定价](https://platform.kimi.com/docs/pricing/chat.md)、[Kimi Code 接入 Claude Code](https://www.kimi.com/code/docs/third-party-tools/claude-code.html)。文档特别注明 Messages 端点的 `input_tokens` 口径与其 Chat Completions / Responses API 不同。
- MiniMax：[Anthropic 兼容缓存](https://platform.minimax.io/docs/api-reference/anthropic-api-compatible-cache)、[Prompt caching](https://platform.minimax.io/docs/api-reference/text-prompt-caching)。
- 火山方舟：[Messages API](https://www.volcengine.com/docs/82379/2655179)（`cache_creation_input_tokens` 原文为「当前暂不支持该计费方式，因此该字段返回为0」）、[上下文缓存](https://www.volcengine.com/docs/82379/1398933)、[Claude Code 配置](https://www.volcengine.com/docs/82379/1928262)。

### 4.3 结论

- 文档明确与 Anthropic 口径一致的只有 Anthropic 官方、Kimi 开放平台和 MiniMax。
- DeepSeek、智谱、Kimi Code 的 Anthropic 端点没有文档说明 `usage` 字段，需要实测。
- 火山方舟风险最高：它的 Responses API 文档写明输入量要用 `input_tokens - cached_tokens` 求得，即原生接口的 `input_tokens` 包含命中部分。Messages 端点若沿用这一口径，按 Anthropic 公式相加会重复计算命中部分（推断，需实测）。这一问题同样影响 CLI 的 `costUSD`，因为 CLI 也按 `input_tokens` 不含缓存来计价。
- 各家缓存读写的价格倍率不统一（Kimi 5 分钟写 1 倍、MiniMax 读 0.1 到 0.2 倍、部分家没有写入费），只能在模型目录中逐项给出价格，不能用固定倍率从输入价推导。
- 火山方舟 Messages 端点的 `message_delta.usage` 描述为「当前增量对应的用量信息」，与 Anthropic 的累计语义可能不同。CLI 按 Anthropic 语义处理 `message_delta`，若方舟实为增量，CLI 记录的 output 可能偏少（推断，需实测）。

## 5. `model_usage` 的键

- 记账入口 `fn:RLe(cost, usage, model, …)` 用 `D.model` 作为 `model_usage` 的键。`D.model` 是本次请求发给 API 的模型 ID，而不是响应体里的 `message.model`（同处 telemetry 另用 `Hf[0]?.message.model`）。
- 别名解析发生在请求之前：`sonnet` / `opus` / `haiku` 别名分别解析为 `ANTHROPIC_DEFAULT_SONNET_MODEL` / `ANTHROPIC_DEFAULT_OPUS_MODEL` / `ANTHROPIC_DEFAULT_HAIKU_MODEL` 的值（[env-vars](https://code.claude.com/docs/en/env-vars)）。子智能体模型由 `fn:wL` 解析，`CLAUDE_CODE_SUBAGENT_MODEL` 同样先解析再发请求。
- 因此 `model_usage` 的键是映射之后的真实模型名，例如 `deepseek-v4-pro`，而不是 `sonnet`（推断，依据是读代码，未用真实请求验证）。
- 另有 `canonicalModel` 字段：「Canonical model id used for the pricing lookup … May differ from the raw model string this entry is keyed by (provider-specific ids, aliases).」对非 Claude 模型，它就是规范化后的原名，没有价格意义。
- 服务端拒绝后回退到 fallback 模型时，记账键是 fallback 模型（`fn:Arn`）。
- `ANTHROPIC_DEFAULT_HAIKU_MODEL` 还用于后台功能（[env-vars](https://code.claude.com/docs/en/env-vars)）。在 query pipeline 内的后台调用会以 haiku 映射后的模型名出现在 `model_usage` 中，pipeline 外的不出现。

## 6. 对 ArcReel 的含义

### 6.1 现状缺陷：累计值被当成单轮值

`server/agent_runtime/session_manager.py` 的 `_record_assistant_usage` 每轮调用一次，把 `extract_assistant_cost(result_msg)` 的结果写进一行 `api_calls`。`extract_assistant_cost` 优先取 `total_cost_usd`，其次取 `model_usage` 中 `costUSD` 之和（`server/agent_runtime/usage_extraction.py`）。两者在 streaming input 模式下都是累计值（§2.1），所以：

- 同一会话第 n 轮写入的费用是前 n 轮的总和，按 `api_calls` 汇总会被重复计入，轮次越多偏差越大。
- 会话被回收后 resume，新连接的第一轮又会带上此前的累计值（CLI ≥ 2.1.277）。

token 侧的口径不一致：`extract_text_token_usage` 优先读 `ResultMessage.usage`，它是本轮、仅主循环，不含子智能体。于是同一行的 token 偏少、费用偏多。

`resolve_assistant_model` 优先读 `result_msg["model"]`，但 `ResultMessage` 没有这个字段，实际会回落到配置的 `ANTHROPIC_MODEL`。一轮内用到多个模型（子智能体、haiku 后台、压缩）时，全部记在一个模型名下。

### 6.2 能否按模型自行计费

可以。推荐做法：

1. 以 `model_usage` 为唯一数据源，每个会话（每个 SDK 连接）保存上一轮的 `model_usage` 快照。
2. 每轮结果到达时，按模型键对 `inputTokens`、`outputTokens`、`cacheReadInputTokens`、`cacheCreationInputTokens`、`webSearchRequests` 做差分，得到本轮各模型的增量。
3. 每个模型的增量写一行 `api_calls`，`model` 用 `model_usage` 的键，费用用模型目录中该模型的价格计算：输入、输出、缓存读、缓存写分别计价。不再使用 `costUSD` / `total_cost_usd`，最多作为对照。
4. 基线处理：
   - 新建连接（含 resume）时，第一轮结果里的累计值可能已包含此前的花费。基线需要持久化在会话元数据里，或在连接建立时以 resume 前最后一次快照为基线。
   - 收到 `ConversationResetMessage` 时把基线清零。
   - 差分出现负数时（计数器被重置），按当前值整体计入并记录告警。
   - 崩溃结果全为 0 时跳过，不更新基线。

这样做的好处：

- 与本图已定的「Agent 模型与生成模型共用同一个模型目录查价，缓存读写单独记账」一致。
- 子智能体、压缩、后台调用都按真实模型名、真实价格计入。
- 不依赖 CLI 内置的 Claude 价格表，非 Claude 模型不再被按 Claude 默认价估算。

取舍与限制：

- 自算的正确性取决于网关的 `usage` 口径（§4）。模型目录或供应商元信息需要能表达「`input_tokens` 是否已包含缓存命中」，对口径未说明的网关先用录制的真实响应确认，再决定默认值。口径未确认前，按 Anthropic 公式计算并在文档中注明。
- `model_usage` 没有 5 分钟与 1 小时缓存写的拆分。ArcReel 当前没有设置 `ENABLE_PROMPT_CACHING_1H` 或 `CLAUDE_CODE_PROMPT_CACHE_TTL`，用 API key 认证时主会话默认是 5 分钟 TTL，按 5 分钟写价计算即可。将来启用 1 小时 TTL 时，需要额外累加 `AssistantMessage.usage.cache_creation` 的拆分（按 `message_id` 去重），或在价格模型中接受单一缓存写价。
- 一轮内的多条增量拆成多行 `api_calls` 后，使用记录页需要能按会话轮次聚合展示。
- 差分把后台子智能体的花费记到它结束时所在的那一轮，而不是它启动的那一轮。
- 若改用 `modelPricing` 让 CLI 直接算对（§3.5），仍然需要差分，且依赖未在 Python SDK 中公开的 `--managed-settings` 与 `CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST` 及其副作用，缓存写也只能单一定价。不推荐作为主方案。

## 7. 风险与未查明项

- `model_usage` 的键是映射后真实模型名，以及中断、流式重试时的记账行为，都是读压缩代码得出的推断，建议在实现票中用录制的会话或单元测试固定。
- 后台调用中哪些在 query pipeline 之外（不计入 `model_usage`），文档只举了权限分类器和 token 计数探测两例，没有完整清单。
- CLI 的价格表和回落规则随版本变化，例如 `Mmt` 在不同版本可能指向不同的 Claude 模型价格。只把 `costUSD` 当作对照值时，这一风险可以接受。
- 网关侧的缓存字段语义（§4）都来自官方文档描述，未用真实请求核对。DeepSeek、智谱、Kimi Code、火山方舟的 `input_tokens` 是否包含命中部分需要实测；火山方舟 Coding Plan、Agent Plan 与 Kimi Code 这类订阅端点按额度结算，参考费用本身的意义有限。
- `ResultMessage` 的 Python 类型声明比 CLI 实际输出少 `thinkingTokens`、`costBasis`，读取时要按普通字典处理。

## 来源

- Python SDK 源码：`.venv/lib/python3.12/site-packages/claude_agent_sdk/types.py`（`AssistantMessage`、`ResultMessage`、`ModelUsage`、`TaskUsage`、`ConversationResetMessage`），版本 0.2.163。
- 捆绑 CLI 2.1.286：`claude_agent_sdk/_bundled/claude`，函数 `fn:FNn`、`fn:RLe`、`fn:Hde`、`fn:Lmt`、`fn:fYe`、`fn:Tx`、`fn:Ox`、`fn:Dx`、`fn:YUo`、`fn:No`、`fn:$8o`，常量 `kx`/`Mmt`，schema 描述 `zw`、`Vw`、`xw`、`qt`。
- [Track cost and usage — Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/cost-tracking)
- [Settings reference — modelPricing](https://code.claude.com/docs/en/settings-reference)
- [Environment variables](https://code.claude.com/docs/en/env-vars)
- Anthropic [Messages API](https://platform.claude.com/docs/en/api/messages/create)、[Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)、[Streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)
- 各网关文档见 §4.2。
- 仓库代码：`server/agent_runtime/session_manager.py`、`server/agent_runtime/usage_extraction.py`、`server/agent_runtime/session_actor.py`、`lib/billing/pricing/lookup.py`、`lib/agent/agent_provider_catalog.py`、`lib/config/service.py`。
