# 火山方舟 Agent Plan 使用限制对 `ark-agent-plan` 的影响评估

日期：2026-11-21（决策记录更新：2026-11-22）。背景：[#3094](https://github.com/ArcReel/ArcReel/issues/3094)。仓库基线：[`bf715a2a`](https://github.com/ArcReel/ArcReel/tree/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0)。本次调研仅基于火山引擎官方文档原文与仓库源码的静态核验，未进行真实付费调用实验，未绕过工具识别机制，也未向火山引擎提交工单。本报告为产品决策提供依据，自身不包含代码或界面改动；已采纳的后续落地见第七节。

## 结论总览

区分三件不同的事：**协议兼容**（请求格式符合 OpenAI / Anthropic / 方舟接口规范）、**技术可调用**（当前能拿到 HTTP 200 响应）与**官方授权使用**（符合火山引擎服务专用条款与产品文档的许可范围）。前两者均不能推出第三者。

| ArcReel 行为 | 证据判断 | 核心依据与适用前提 |
| --- | --- | --- |
| 1. 内置供应商 `ark-agent-plan` 文本生成（剧本生成、分集规划、项目概述、风格图分析） | **官方已明确禁止**（若主张 ArcReel 属于获准「AI 智能体工具」，则**现有公开资料不足以判断**，未获书面确认前应按高违规风险处理） | 套餐概览「额度使用限制」明文规定「文本生成模型及向量化模型不可用于 API 调用」，且服务专用条款 §2.8（二）、§2.11 禁止非认可工具及应用程序后端/自动化任务调用；ArcReel 不在官方列出的 15 款支持工具清单中。 |
| 2. 内嵌 Agent 经独立 `ark-agent-plan` 凭证使用 Claude Agent SDK | **现有公开资料不足以判断**（多人共享部署情形下为**官方已明确禁止**） | 官方文档明确支持在终端与 IDE 插件中配置 Claude Code 接入 `/api/plan`，但未说明第三方应用通过 Claude Agent SDK 嵌入式托管 Claude Code 子进程是否视同获准的 Claude Code 形态。若用于多人共享部署共用同一 API Key，则直接违反条款 §2.8（一）与 §2.9。 |
| 3. 内置供应商 `ark-agent-plan` 媒体生成（图片、视频，以及主线已新增的语音合成 TTS） | **存在官方产品文档明确支持的 API 接入依据，但与服务专用条款总则存在口径张力**（个人单席位交互触发倾向允许；多人共享服务端、批量转供或未经许可的商业对外提供为**官方已明确禁止**） | 视觉模型接入指南明确写明「无需在 AI 工具中使用」时可通过 `/api/plan/v3` 的 API 接入图片与视频生成（视频仅 Large / Max 套餐支持），语音模型接入指南亦直接提供 HTTP / WebSocket API 文档，且套餐概览未将视觉/语音模型列入「不可用于 API 调用」；但条款 §2.11 未按模态拆分，§2.8（一）（二）（四）与 §2.9 仍严格约束单席位与非共享边界。 |

## 一、官方原始资料核验与链接变更

以下官方页面均于 2026-11-21 直接拉取 Markdown 原文核验，未采用社区转述作为依据。

### 1.1 已核验的官方来源清单

| 文档 | 核验 URL | 关键章节 |
| --- | --- | --- |
| 火山方舟大模型订阅套餐服务专用条款 | [subscription-package-service-terms-conditions](https://docs.volcengine.com/docs/ark/subscription-package-service-terms-conditions?lang=zh) | §1.1、§1.3、§1.4、§2.2、§2.8、§2.9、§2.11、§3.1、§3.2 |
| Agent Plan（个人版）套餐概览 | [agent-plan-personal-plan-overview](https://docs.volcengine.com/docs/ark/agent-plan-personal-plan-overview?lang=zh) | 「支持模型及 Harness」「用量说明」「额度使用限制」「接入 AI 工具」 |
| Agent Plan（企业版）套餐概览 | [agent-plan-enterprise-plan-overview](https://docs.volcengine.com/docs/ark/agent-plan-enterprise-plan-overview?lang=zh) | 「支持模型及 Harness」「额度使用限制」「席位管理」「专属 API Key 规则」 |
| Agent Plan 接入 Claude Code（个人版 / 企业版） | [agent-plan-personal-claude-code](https://docs.volcengine.com/docs/ark/agent-plan-personal-claude-code?lang=zh)、[agent-plan-enterprise-claude-code](https://docs.volcengine.com/docs/ark/agent-plan-enterprise-claude-code?lang=zh) | 「配置 Claude Code」「添加图片生成/视频生成能力」 |
| Agent Plan 接入 Hermes Agent（个人版） | [agent-plan-personal-hermes-agent](https://docs.volcengine.com/docs/ark/agent-plan-personal-hermes-agent?lang=zh) | 「方式 2：手动配置」（Responses API、Chat API） |
| Agent Plan 接入视觉模型（个人版 / 企业版） | [agent-plan-personal-visual-models](https://docs.volcengine.com/docs/ark/agent-plan-personal-visual-models?lang=zh)、[agent-plan-enterprise-visual-models](https://docs.volcengine.com/docs/ark/agent-plan-enterprise-visual-models?lang=zh) | 「通过 Skill 接入」「通过 ArkCLI 接入」「通过 API 接入」 |
| Agent Plan 语音模型（个人版 / 企业版） | [agent-plan-personal-voice-model](https://docs.volcengine.com/docs/ark/agent-plan-personal-voice-model?lang=zh)、[agent-plan-enterprise-voice-model](https://docs.volcengine.com/docs/ark/agent-plan-enterprise-voice-model?lang=zh) | 「核心配置」「流式语音合成（Streaming TTS）」 |
| 常见问题（个人版 / 企业版） | [agent-plan-personal-faq](https://docs.volcengine.com/docs/ark/agent-plan-personal-faq?lang=zh)、[agent-plan-enterprise-faq](https://docs.volcengine.com/docs/ark/agent-plan-enterprise-faq?lang=zh) | 「Agent Plan 和 Coding Plan 有什么区别？」 |

### 1.2 旧链接重定向记录

1. `#3094` 原文引用的 `https://www.volcengine.com/docs/82379/2366394`（及 `https://docs.volcengine.com/docs/82379/2366394`）均已重定向至 `https://docs.volcengine.com/docs/ark/agent-plan-personal-plan-overview?lang=zh`。此前「支持模型页」的限制条款现合并在套餐概览的「额度使用限制」小节中。
2. 仓库既有注释（`lib/agent/agent_provider_catalog.py`、`docs/api-docs/providers/ark-agent-plan.md`）引用的 `https://www.volcengine.com/docs/82379/2373740?lang=zh` 已重定向至 `https://docs.volcengine.com/docs/ark/agent-plan-personal-claude-code?lang=zh`。

### 1.3 官方资料之间的张力与冲突

- **总则条款与多模态产品文档的口径张力**：服务专用条款 §2.8 开头与 §2.11 未区分模型模态，统称「本服务仅限于您在本服务支持和兼容的 AI 编程工具、AI 智能体工具或其他经火山引擎明确认可的工具中，以最终用户交互式调用的方式使用」，但在 §2.8（二）留有例外条款「但火山引擎明确支持或另行授权的场景除外」。与此同时，个人版与企业版套餐概览的「额度使用限制」仅点名「文本生成模型及向量化模型不可用于 API 调用」，而视觉模型文档明文写明「如果使用的 AI 工具不支持接入 Skill，或者无需在 AI 工具中使用，可通过 API 接入的方式使用图片生成、视频生成能力」，语音模型文档也直接提供 `openspeech.bytedance.com` 的 HTTP / WebSocket API 接入代码。两处文件的法律优先顺序在公开文档中未再进一步展开说明。
- **FAQ 未补充边界细则**：个人版与企业版 FAQ 页面目前仅对比 Agent Plan 与 Coding Plan 的定位和计费差异，未对「AI 工具判定标准」「嵌入式 SDK」「多用户部署」给出补充问答。

## 二、官方关键概念与适用维度拆解

### 2.1 「AI 工具」与「API 调用」：已证实含义与缺失定义

- **已证实事实（「AI 工具」）**：条款 §2.8 与 §2.11 要求在「本服务支持和兼容的 AI 编程工具、AI 智能体工具或其他经火山引擎明确认可的工具」内使用。个人版与企业版套餐概览的「接入 AI 工具」均列出 15 款具名工具：Claude Code、TraeCode、Roo Code、Codex CLI、Pi、OpenCode、Cline、Kilo Code、WorkBuddy、ZCode、OpenClaw、Cursor、Hermes Agent、DeepSeek Harness、OpenAI4S。
- **缺失定义（「AI 工具」）**：官方未公开「未列在 15 款清单中的第三方 AI 工具是否属于支持和兼容的 AI 智能体工具」的判定标准或白名单申请流程，也未说明「通过 Claude Agent SDK 内嵌 Claude Code 的第三方创作软件」是否视同 Claude Code。
- **已证实事实（「API 调用」）**：官方并不以传输协议是否走 HTTP API 作为禁止依据。`agent-plan-personal-hermes-agent` 明确说明「Agent Plan 兼容 OpenAI 接口协议，支持 Responses API、Chat API」，并给出 `https://ark.cn-beijing.volces.com/api/plan/v3` 配置示例；`agent-plan-personal-claude-code` 通过 `https://ark.cn-beijing.volces.com/api/plan` 走 Anthropic Messages API。结合条款 §2.8（二），官方禁止的「API 调用」针对的是「将本服务用于应用程序后端、服务器端服务、批量处理、自动化任务、定时任务、爬虫、数据处理等非最终用户直接交互的程序化调用场景，或者以其他方式将本服务变相用作 API 接口服务、后台计算服务、基础模型服务或算力资源」。
- **缺失定义（「API 调用」与风控机制）**：对于「用户在本机打开桌面/本地 Web 创作软件、点击按钮触发单次剧本生成请求」这类图形化软件直接调 `/api/plan/v3` Chat Completions 的行为，公开文档未说明其是否属于可豁免的交互式调用；条款 §2.11 仅写明「为了防止本服务被滥用，您的客户端的系统和设备信息将会被记录」，未公开具体风控识别规则（如是否校验 User-Agent、客户端指纹、请求特征或调用模式）。

### 2.2 个人版 vs. 企业版

- **已证实事实（使用限制无差异）**：服务专用条款 §1.1 明确本专用条款同时适用 Agent Plan 个人版和企业版。企业版套餐概览「额度使用限制」与个人版实质相同：「文本生成模型及向量化模型不可用于 API 调用，在非 AI 工具中使用 Agent Plan 企业版权益对应的 Base URL 和 API Key 有可能被识别为滥用/违规，会导致订阅停用或账号封禁。」企业版视觉模型与语音模型文档的 API 接入说明也与个人版一致。
- **已证实事实（数据与席位存在差异）**：
  - **数据授权**：个人版须同时适用《火山引擎数据授权使用协议》（§1.3），用户授权火山引擎及关联公司存储并使用模型输入输出用于机器学习和模型优化；企业版明确不留存交互数据，不用于服务优化（§1.4）。
  - **席位绑定**：企业版 2 席位起售，每席位对应 1 个子账号与 1 个专属 API Key，单月最多换绑 3 次；条款 §2.9 明确每个 API Key 仅限该席位使用人本人使用，禁止组织内未分配席位的其他人员共享。
- **研究者推断**：从个人版切换到企业版，**不能**解除文本模型 API 调用限制，也**不能**合法化多人共用同一把 API Key；其价值在于避免商业剧本/创作素材被纳入个人版训练数据授权，以及为团队按人头分配独立席位。

### 2.3 用户本人使用 vs. 多人共享部署

- **已证实事实**：条款 §2.8（一）明确禁止将 Base URL、API Key 或访问权限出借、共享、转让给未经授权第三方，也禁止通过中转、代理、池化、聚合、路由分发等方式搭建供多个未经授权用户共同使用的统一访问或调用服务；§2.8（四）禁止转售或商业化对外提供调用能力；§2.9 明确每项 API Key 仅限对应席位使用人本人使用；§2.2 同时约定服务仅限中国大陆地区使用，直接或间接获取商业收益需事先书面许可。
- **结论影响**：无论个人版还是企业版、无论文本、内嵌 Agent 还是媒体生成，**多人共享部署共用同一个 `ark-agent-plan` API Key 均属于官方已明确禁止的行为**。只有「用户在个人本地/独享实例中配置本人席位的专属 API Key 并自行交互使用」才具备讨论合规空间的前提。

## 三、仓库现状与固定版本代码证据

以下路径均固定于提交 [`bf715a2a`](https://github.com/ArcReel/ArcReel/tree/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0)。

### 3.1 内置供应商 `ark-agent-plan` 与模型目录

- **供应商注册**：[`lib/config/registry.py`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/lib/config/registry.py#L117-L123) 注册内置供应商 `ark-agent-plan`，`default_base_url` 为 `https://ark.cn-beijing.volces.com/api/plan/v3`，必填键仅 `api_key`。
- **出厂目录现状**：[`lib/catalog/snapshot/catalog.json`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/lib/catalog/snapshot/catalog.json#L18152-L18243) 在 `ark-agent-plan` 下收录了 10 个文本模型（`endpoint: "ark-chat"`）、2 个图片模型（`endpoint: "ark-seedream-4"`）与 4 个视频模型（`ark-seedance-2` / `ark-seedance-2-5`），此外 [`packages/arcreel-catalog-core/src/arcreel_catalog_core/endpoints.py`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/packages/arcreel-catalog-core/src/arcreel_catalog_core/endpoints.py#L115-L139) 与 [`lib/backends/backend_assembly/specs.py`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/lib/backends/backend_assembly/specs.py#L695-L704) 登记了语音合成端点 `ark-agent-plan-tts`。
- **默认推荐现状**：当前出厂快照 `ark-agent-plan.defaults` 仅保留 `"video": "doubao-seedance-2.0-mini"`，**已不再包含文本或图片默认推荐**（从 v0.36.0 起取消，见 [`lib/config/pre_upgrade_recommendations.py`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/lib/config/pre_upgrade_recommendations.py#L89-L145) 与 [ADR 0099](../adr/0099-model-catalog-shared-entries-per-model.md)）。但 v0.34.0 / v0.35.0 升级固化的老实例，以及手动选择过 `ark-agent-plan` 文本模型的用户，数据库中仍可能保存 `ark-agent-plan` 文本模型配置。
- **连通性测试**：[`server/routers/providers.py`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/server/routers/providers.py#L907-L918) 的 `_check_ark` 对 `ark-agent-plan` 调用 `client.content_generation.tasks.list(page_size=1)`，不调用文本模型接口。
- **现有提示文案**：[`lib/i18n/zh/providers.py`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/lib/i18n/zh/providers.py#L24-L35) 目前仅写「火山方舟面向个人用户推出的订阅式大模型服务套餐，按积分计费」以及 Agent 预设无模型列表接口提示，**未包含任何使用条款限制、单席位要求或数据授权提示**。

### 3.2 三类行为的实际调用路径

1. **内置供应商文本生成**：
   - 调用点（遵循 [ADR 0051](../adr/0051-text-backend-capability-tiers.md) 的简单/复杂两档）：`TextTaskType.SCRIPT`（剧本生成 [`lib/script/script_generator.py`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/lib/script/script_generator.py#L407)、分集规划 [`lib/episode/episode_planner.py`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/lib/episode/episode_planner.py#L524)、文稿修复与文本生成 [`server/draft_repair.py`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/server/draft_repair.py#L233) / [`server/text_generation.py`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/server/text_generation.py#L1016)）、`TextTaskType.OVERVIEW`（项目概述 [`lib/project/project_manager.py`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/lib/project/project_manager.py#L4180)）、`TextTaskType.STYLE_ANALYSIS`（风格图分析 [`server/routers/files.py`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/server/routers/files.py#L1442)）。
   - 装配与传输：经 [`TextGenerator.create()`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/lib/backends/text_generator.py#L76-L99) → [`create_text_backend_for_task()`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/lib/backends/text_backends/factory.py#L17-L36) → [`assemble_backend()`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/lib/backends/backend_assembly/assembler.py#L41-L88) 命中 [`PROVIDER_ENDPOINT_SPECS[("ark-agent-plan", "ark-chat")]`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/lib/backends/backend_assembly/specs.py#L761-L764)，构造 [`ArkTextBackend`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/lib/backends/text_backends/ark.py#L26-L92)，使用 `volcenginesdkarkruntime.Ark`（降级时用原生 `openai.OpenAI`）向 `https://ark.cn-beijing.volces.com/api/plan/v3` 发起标准 `chat.completions.create` 请求。
2. **内嵌 Agent（独立 Agent 凭证命名空间，见 [ADR 0027](../adr/0027-provider-secrets-banned-from-process-env.md)）**：
   - 预设定义：[`lib/agent/agent_provider_catalog.py`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/lib/agent/agent_provider_catalog.py#L188-L213) 的 `PRESET_PROVIDERS["ark-agent-plan"]` 配置 `messages_url = "https://ark.cn-beijing.volces.com/api/plan"`。
   - 凭证注入：[`build_anthropic_env()`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/lib/config/service.py#L85-L113) 读取生效 Agent 凭证，经 [`load_provider_env_overrides()`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/server/agent_runtime/options_assembler.py#L73-L92) 注入 `ClaudeAgentOptions.env`（含 `ANTHROPIC_API_KEY`、`ANTHROPIC_BASE_URL` 与各档位模型环境变量），供 `claude_agent_sdk` 拉起 Claude Code CLI 子进程进行交互式对话。它与内置供应商 `ark-agent-plan` 凭证及文本任务档位完全独立。
3. **内置供应商媒体生成**：
   - 图片：[`ArkImageBackend`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/lib/backends/image_backends/ark.py#L57-L120) 经 `https://ark.cn-beijing.volces.com/api/plan/v3` 调用 `images.generate`（对应官方视觉模型 API 文档 `/api/plan/v3/images/generations`）。
   - 视频：[`ArkVideoBackend`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/lib/backends/video_backends/ark.py#L94-L120) 经 `https://ark.cn-beijing.volces.com/api/plan/v3` 调用内容生成任务接口（对应官方 `/api/plan/v3/contents/generations/tasks`）。
   - 语音合成（主线新增，[#3813](https://github.com/ArcReel/ArcReel/pull/3813)）：[`ArkAgentPlanAudioBackend`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/lib/backends/audio_backends/ark_agent_plan.py#L1-L52) 向 `https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional` 发起 HTTP POST 请求，使用 `X-Api-Key` 与固定 `X-Api-Resource-Id: seed-tts-2.0`，与官方 `agent-plan-personal-voice-model` 文档一致。

## 四、三类行为的详细评估

### 4.1 行为 1：内置供应商 `ark-agent-plan` 文本生成

- **证据判断**：**官方已明确禁止**（对普通应用后端以 SDK / HTTP 直接调用 `/api/plan/v3` 文本生成模型而言）；若主张 ArcReel 整体属于「AI 智能体工具」，因官方未公布认定标准且 ArcReel 不在支持列表内，**现有公开资料不足以判断**，未获书面许可前应视为高违规风险。
- **已证实事实**：
  1. 个人版与企业版套餐概览「额度使用限制」均明确写明：「文本生成模型及向量化模型不可用于 API 调用，在非 AI 工具中使用 Agent Plan 权益对应的 Base URL 和 API Key 有可能被识别为滥用/违规，会导致订阅停用或账号封禁。」
  2. 服务专用条款 §2.8（二）禁止将本服务用于「应用程序后端、服务器端服务、批量处理、自动化任务……等非最终用户直接交互的程序化调用场景」。
  3. 服务专用条款 §2.11 规定在未经同意的非 AI 编程及智能体工具中使用专属 Base URL / API Key 可能被识别为滥用/违规，导致订阅停用或账号封禁且火山引擎免责；§3.2 允许平台不经事先通知直接封禁。
- **研究者推断**：`ArkTextBackend` 使用标准 Python `volcenginesdkarkruntime.Ark` / `openai.OpenAI` 客户端从 ArcReel 后端服务直接发往 `/api/plan/v3`，在请求形态、客户端指纹及场景定位上均接近「应用程序后端的通用文本 API 调用」，而非官方列出的 15 款 AI 编程/智能体工具之一。即便由用户本人在前端点按触发，也面临显著的条款违约与风控封禁风险。

### 4.2 行为 2：内嵌 Agent 使用 `ark-agent-plan` 独立凭证

- **证据判断**：**现有公开资料不足以判断**（若多人共享同一 API Key，则**官方已明确禁止**）。
- **已证实事实**：
  1. 官方 `agent-plan-personal-claude-code` 与企业版同页明确支持用户通过 `ANTHROPIC_BASE_URL=https://ark.cn-beijing.volces.com/api/plan` 与专属 API Key 配置 Claude Code（含 CLI 与 VSCode / JetBrains 插件）。
  2. ArcReel 遵守 [ADR 0027](../adr/0027-provider-secrets-banned-from-process-env.md) 与 [ADR 0051](../adr/0051-text-backend-capability-tiers.md)，将 Agent 凭证与内置供应商文本任务完全隔离；内嵌 Agent 经 `claude_agent_sdk` 拉起 Claude Code CLI 进行交互式多轮对话。
  3. 官网所有接入文档仅覆盖终端与 IDE 插件直连 Claude Code，未提及「第三方应用通过 Claude Agent SDK 嵌入式托管 Claude Code」。
  4. 条款 §2.8（一）与 §2.9 禁止将 API Key 提供给第三方或多人共享；§2.11 写明会记录客户端系统与设备信息用于反滥用检测。
- **未知项与风险**：不能因为底层复用 Claude Code CLI 或 Anthropic 协议、或单次请求能正常返回，就断言官方已授权第三方产品嵌入式使用。特别是 ArcReel 以服务端进程托管 SDK 子进程时，若在多用户服务器上使用同一凭证，会直接触发 §2.8（一）与 §2.9 违约。

### 4.3 行为 3：内置供应商 `ark-agent-plan` 媒体生成（图片、视频、语音）

- **检验命题**：「只保留媒体模型」是否确有条款依据？
- **证据判断**：**确有官方产品文档依据，但并非无条件许可**。
- **已证实事实**：
  1. 视觉模型文档（个人版与企业版同文）明确写明：「方舟 Agent Plan 提供图片生成、视频生成模型，支持通过 Skill、ArkCLI、API 接口调用方式接入并使用生图、生视频能力。」「如果使用的 AI 工具不支持接入 Skill，或者无需在 AI 工具中使用，可通过 API 接入的方式使用图片生成、视频生成能力。」并给出 `https://ark.cn-beijing.volces.com/api/plan/v3` 的 Curl / Python Ark SDK / Java / Go / OpenAI SDK 示例。
  2. 语音模型文档（个人版与企业版同文）直接提供 `https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional` 的 HTTP POST 与 WebSocket 接入示例。
  3. 套餐概览「额度使用限制」只点名「文本生成模型及向量化模型不可用于 API 调用」，未将图片、视频、语音模型列入该限制；服务专用条款 §2.8（二）包含「但火山引擎明确支持或另行授权的场景除外」的豁免字样。
  4. 套餐档位前提：Small 与 Medium 套餐**不支持视频生成**，仅 Large 与 Max 支持；图片与语音模型全档位支持。
- **仍需注意的保留风险**：
  1. 服务专用条款 §2.11 未按模态区分，仍笼统要求在支持和兼容的 AI 工具内使用，与视觉/语音产品文档之间存在未明文写清优先级的文本张力。
  2. 即便视觉/语音 API 接入落入 §2.8（二）的「明确支持」例外，§2.8（一）（三）（四）、§2.9、§2.2 依然有效：**仅限单席位用户本人使用，不可用于多用户共享服务、代理池化或未经书面许可的商业转供**。

## 五、产品调整方案比较与最小建议

以下方案仅供后续产品决策参考，本调研不实施任何改动。

| 方案 | 适用条件 | 用户影响与兼容性 | 风险与代价 |
| --- | --- | --- | --- |
| **A. 保持现状** | 维护者接受由用户自行承担文本模型条款与风控风险 | 无破坏性变更；新用户不会自动选中文本默认（v0.36.0+ 已无文本默认推荐） | 用户在设置页完全看不到限制提示，容易误把 `ark-agent-plan` 文本模型配进剧本生成或多人部署，触发封号且不知情。 |
| **B. 保留入口，补充明确提示** | 维护者希望在未获火山引擎书面答复前，不破坏任何存量配置，优先消除信息不对称 | 零兼容性破坏；新老用户在配置 `ark-agent-plan` 供应商与 Agent 预设时即可看到条款边界、单人席位、个人版数据授权与视频档位要求 | 不阻止用户手动选用 `ark-agent-plan` 文本模型；若未来火山引擎收紧风控，用户仍可能遇到拒服或封禁。 |
| **C. 隐藏/移除 `ark-agent-plan` 文本模型** | 维护者认定文本模型 API 调用风险不可接受，但希望继续观察 Agent 预设 | **存在存量兼容性问题**（见下文 5.1）：直接从目录删除会导致已选用户抛 `ModelNotInCatalogError` | 若只移除文本模型而未同步在媒体与 Agent 预设补充单人席位/数据授权提示，仍有合规盲区。 |
| **D. 内置供应商仅保留媒体模型（图/视频/语音）+ 保留独立 Agent 预设并附提示** | 维护者希望与官方「文本不可用于 API 调用、视觉/语音支持 API 接入」的产品文档口径严格对齐 | 与方案 C 相同，需要处理已配置 `ark-agent-plan` 文本模型的老用户；连通性测试 `_check_ark` 调的是视频任务列表接口，不受影响 | 合规姿态最清晰；但若火山引擎后续书面确认 ArcReel 获准，可再恢复文本模型。仍需在 Agent 预设说明 Claude Agent SDK 嵌入形态尚未获官方明文确认。 |

### 5.1 隐藏或移除文本模型时的兼容性注意点

1. **新用户与默认模型**：自 v0.36.0 起，`lib/catalog/snapshot/catalog.json` 中 `ark-agent-plan.defaults` 仅保留 `video`（`doubao-seedance-2.0-mini`），不再有 `text` 默认推荐（[`lib/config/pre_upgrade_recommendations.py`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/lib/config/pre_upgrade_recommendations.py#L137-L145)）。移除文本模型不会影响新用户的默认推荐解析。
2. **存量已配置用户**：v0.34.0 / v0.35.0 升级固化的实例，或用户在全局默认、简单/复杂文本档位、项目级模型设置中手动选过 `ark-agent-plan` 文本模型的，配置仍指向这些模型 ID。[`assemble_backend()`](https://github.com/ArcReel/ArcReel/blob/bf715a2a3fe5096475577efe4e29eb4f31b9b5f0/lib/backends/backend_assembly/assembler.py#L144-L160) 会调用 `catalog.require_callable()`，直接删除目录条目会导致老用户在下次文本生成时直接收到 `ModelNotInCatalogError`。
3. **平稳过渡做法**：仅在设置页做候选过滤或标记停用只能阻止新选，无法拦住已保存的 `ark-agent-plan` 文本配置经 `create_text_backend_for_task()` → `assemble_backend()` 继续发起调用。若采用方案 C 或 D 并要求真正停止存量文本调用，需在文本后端装配/派发入口显式拦截 `ark-agent-plan` 文本调用，并给出清晰的迁移提示引导切换到按量付费的 `ark` 或其他文本供应商，同时保持媒体模型与其他供应商可用，避免直接静默删除目录条目导致裸露的 `ModelNotInCatalogError`。
4. **不影响的功能**：供应商连通性校验 `_check_ark` 走视频任务列表 API，不依赖文本模型；独立的 `ark-agent-plan` Agent 预设走 `lib/agent/agent_provider_catalog.py`，不受内置供应商目录删改影响。

### 5.2 最小建议

- **最小无破坏建议（推荐立即考虑）**：先实施**方案 B**，在内置供应商 `ark-agent-plan` 与 Agent 预设 `ark-agent-plan` 的说明文案中给出克制提示并提供官方条款外链，重点涵盖：（1）官方声明文本模型仅用于认可的 AI 工具且不可用于非交互式 API 调用；（2）API Key 仅限本人单席位使用，不得多人共享；（3）个人版含数据授权（输入输出可用于模型优化），对数据隐私敏感应选企业版或按量付费；（4）视频生成仅 Large / Max 套餐支持；完整适用范围（包括 §2.2 约定的中国大陆地域限制与商业使用书面许可条件）由官方服务条款外链承接，并保留第六节第 5 项待确认问题。同时保持当前不提供文本默认推荐的状态。
- **若追求更严格合规基线**：在向火山引擎确认前采用**方案 D**，将内置供应商 `ark-agent-plan` 的候选模型收口至图片、视频与语音，并配套对存量已选文本模型的迁移提示；独立 Agent 预设保留并标注「嵌入 Claude Agent SDK 的授权边界尚待官方确认，仅建议个人本地使用」。

## 六、待向火山引擎确认的问题与人工材料清单

现有公开资料不足以对 ArcReel 给出确定许可。若维护者决定向火山引擎发起正式确认（工单或商务通道，不由 Agent 自动发送），建议直接询问以下问题：

1. **内置供应商文本生成**：开源视频创作应用 ArcReel 在用户本地单机部署、由订阅人本人操作触发单次剧本生成/风格分析时，通过 `/api/plan/v3` 调用 Agent Plan 文本生成模型，是否属于「支持和兼容的 AI 智能体工具」？如不属于，ArcReel 是否有官方申请纳入支持工具清单的渠道？
2. **内嵌 Claude Agent SDK 形态**：ArcReel 内嵌创作助手通过官方 `claude-agent-sdk` 运行 Claude Code CLI 子进程并连接 `/api/plan`，在用户本人单机交互使用的前提下，是否等同于官方已支持的 Claude Code 接入方式？
3. **视觉与语音模型 API 与条款 §2.11 的关系**：视觉模型接入指南明确写明「无需在 AI 工具中使用，可通过 API 接入」，语音模型文档直接提供 HTTP / WebSocket API 示例；请确认个人/企业单席位用户通过 ArcReel 调用生图、生视频及语音合成 API 是否落入服务专用条款 §2.8（二）「火山引擎明确支持」的例外范围，不受 §2.11「非 AI 工具」违规判定影响。
4. **风控与反滥用误判**：服务专用条款 §2.11 提及会记录客户端系统和设备信息用于防滥用；通过 `claude-agent-sdk` 或 ArcReel 本地客户端发起上述获准调用时，是否存在触发自动风控限流或封禁的风险？
5. **个人版与企业版及商业创作边界**：用户使用 Agent Plan（个人版或企业版）生成剧本、图片、视频、配音用于商业短视频发布，是否触及服务专用条款 §2.2「就本服务进行商业使用或以其他方式直接或间接获得收益，需事先获得书面许可」的限制？

**待人工收集并归档的确认材料**：

- 火山引擎官方工单回复截图或书面邮件原文（含回复日期、工单编号或对接方身份）。
- 回复所对应的《火山方舟大模型订阅套餐服务专用条款》及套餐概览生效版本或日期。
- 若获准纳入支持工具列表或取得特定场景书面豁免，留存授权范围（适用模态、个人版/企业版、有效期与限制条件）并据此更新本报告与产品配置。

## 七、已采纳决策（2026-11-22）

维护者采纳**方案 B**，并已在 [#3992](https://github.com/ArcReel/ArcReel/pull/3992) 落地：

- 保留内置供应商 `ark-agent-plan` 文本模型可选（继续不设文本默认推荐），不破坏存量配置；
- 在「设置 → 供应商」将多渠道厂商收纳为单条目并增加顶部渠道切换标签（`Google Gemini`：`AI Studio` / `Vertex AI`；`火山方舟`：`按量付费` / `Agent Plan`）；
- 在内置供应商 `ark-agent-plan` 详情头与 Agent 预设 `ark-agent-plan` 弹窗中补充克制的条款提示及官方[服务条款](https://docs.volcengine.com/docs/ark/subscription-package-service-terms-conditions?lang=zh)外链（覆盖 `zh`、`en`、`ru`、`vi` 四语），并同步更新中英文[供应商配置指南](../../website/docs/guide/providers.md)（含 §2.8 交互式调用限制）。
