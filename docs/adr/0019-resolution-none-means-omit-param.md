---
status: accepted
---

# 分辨率等纯清晰度参数未配置时「不传」而非填我方默认

保留我方按模型硬编码的默认分辨率表（`DEFAULT_VIDEO_RESOLUTION` 等）等于替每家供应商猜默认值、散落难维护，且「未配置」与「显式选某档」无法区分。决定删除这些我方默认表；分辨率作为**纯清晰度**参数按 project → legacy → 自定义模型默认 → None 解析，None 表示「调用 SDK 时不携带该参数」、走 SDK 自身默认，而非我方兜底——把默认权交还 SDK。

## Consequences

- 不增设系统级全局默认分辨率（自定义供应商的模型级默认已覆盖系统层语义）。
- **边界**：本决策只覆盖「分辨率与比例正交、可省略」的后端。当尺寸须**承载比例**时（如 OpenAI Sora size、图片 size），改由 `docs/adr/0011` 的 `aspect_size` 永远计算并下传、绝不 None 省略——那条路径与本决策相反，互不冲突。各家原生命名（OpenAI size / Grok resolution / Gemini image_size）的归一也已并入 `docs/adr/0011` 的集中计算，不再由各 backend 维护静态翻译表。
