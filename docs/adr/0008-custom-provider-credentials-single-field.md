---
status: proposed
---

# 自定义 provider 凭证模型固定为 `api_key` + `base_url`，多字段凭证协议只能走内置 provider

ArcReel 的 `CustomProvider` ORM（`lib/db/models/custom_provider.py`）当前对每个用户运行时创建的供应商只暴露两个凭证字段：`api_key`（单字符串，受 `mask_secret` 掩蔽）+ `base_url`（HTTP 入口）。同期视频 API 协议适配调研发现多种"原生多字段凭证"协议：可灵 Kling 官方走 JWT HS256（`access_key` + `secret_key` 双密钥，30 分钟 token 过期），Vertex AI Veo 走 service account JSON 文件（`credentials_path`，再加上 project_id / OAuth2 scopes），火山 visual.volcengineapi.com 视觉 CV 走 AKSK + canonical request + HMAC 签名。这些协议如果原样作为 endpoint 暴露给自定义 provider，必须把 CustomProvider 凭证模型扩展为多字段（候选方案：新增 `extra_credentials: JSON` 字段、或新增 `api_secret` / `service_account_json` 等专字段）。

我们决定**不**扩展自定义 provider 的凭证模型。`CustomProvider` 长期固定 `api_key` 单字段 + `base_url`；任何"原生多字段凭证"协议**只能**通过内置 provider（`PROVIDER_REGISTRY.required_keys: list[str]`，已经支持多字段，例：`gemini-vertex.required_keys=["credentials_path"]`、Kling 计划 `required_keys=["access_key","secret_key"]`）接入。用户从中转站接多字段协议的兼容路径是**中转站做 protocol translation**——中转站在自己服务端解决多字段鉴权，对外暴露 Bearer `api_key` + 中转站 `base_url`（事实佐证：可灵中转站普遍用 `/kling/v1/videos/{text2video|image2video|multi-image2video}` 路径透传 Kling 原生 schema，但鉴权折叠成 Bearer 单密钥）。endpoint 闭包看到的永远是单 api_key。这是凭证模型与协议形态解耦的边界。

## Consequences

- **凭证模型不被协议形态污染**：CustomProvider schema、Repository、`mask_secret` 路径、加密存储、UI 凭证表单（设置页"添加自定义供应商"）、`/api/v1/custom-providers/discover` 都不引入"多字段凭证"特例。每加一个原生多字段协议（Kling、Vertex、AKSK、未来某个新供应商）也无须再触动这一整条链——所有协议适配的扩展点收敛在 `lib/video_backends/`、`lib/image_backends/`、`lib/text_backends/` 的 backend 实现与 `ENDPOINT_REGISTRY` 注册，凭证层稳定。
- **同一 backend 实现可以承载两种凭证模式**：典型例是 `KlingVideoBackend`（待实现）将同时支持 JWT 模式（用于内置 `kling-official` provider，构造时接 `access_key + secret_key`）和 Bearer 模式（用于 `kling-proxy-video` endpoint 闭包，构造时只接 `api_key + base_url`），两种模式共享同一份请求 body schema 与状态机映射。这跟 `GeminiVideoBackend` 现有 `backend_type=aistudio|vertex` 双模式（`lib/video_backends/gemini.py`）是同一种模式：backend 是协议形态载体，鉴权方式是构造参数。
- **不可中转代理的协议永远不会出现在 endpoint 下拉**：Vertex AI Veo（service account JSON 文件无法 base_url 重定向到中转站）、火山可视化 CV（SigV4-like 签名通常不被中转站原样转发，会被中转站重包装成 NewAPI 协议透传）等在自定义供应商 UI 上一直缺席。这是 conscious trade-off——若用户确有此需求，走内置 provider 即可。
- **未来 plugin 机制必须延续这条约束**：当运行时 plugin 机制（参考 `docs/research/arcreel-video-api-protocol-research.md` §7.5）落地时，plugin endpoint 同样只能接 `api_key` + `base_url`；plugin 想暴露多字段协议须自带"内置 provider 风格"声明（且该路径等同于 plugin 自己提供 PROVIDER_REGISTRY 扩展，复杂度显著上升，本 ADR 范围不预设）。这条约束让 plugin 接口面保持稳定。
- **CONTEXT.md 已经把这条原则收入 glossary**（"自定义 provider"条目第二句），本 ADR 给出的是"为什么不扩展凭证模型"的决策理由，与 glossary 描述"是什么"形成互补。任何后续 PR 想破例（加 JSON 凭证字段、加 secondary key 字段）须先 deprecate 本 ADR。
- **不应通过"在 api_key 字段内拼接分隔符字符串"绕过约束**（如 `api_key="access_key:secret_key"` 自己拆解）。这破坏了 `mask_secret` 单一密钥的展示语义，让 UI 端无法判断哪段是真正的 secret；同时让 backend 实现混入字符串解析逻辑，模糊"协议结构"与"凭证表达"的边界。如果有用户场景真的需要破例，正确路径是写新的内置 provider，不是 hack endpoint 闭包。
- **本 ADR 与 `docs/adr/0001-image-capability-resolved-at-execution.md` 风格一致**：把"运行时确定 vs 启动时声明"的边界划清楚——凭证形态在 provider 注册时声明（`PROVIDER_REGISTRY.required_keys`），不在请求时动态扩展。endpoint 走运行时构造但凭证形态由 ENDPOINT_REGISTRY + CustomProvider schema 静态约束。
