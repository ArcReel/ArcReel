---
status: accepted
---

# 图像端点按能力拆分为 T2I/I2I 双槽，运行时严格 gating 不做隐式 fallback

NewAPI/OneAPI 中转生态里很多模型只暴露 generations（不支持 edits），给「只有 generations 的模型」传参考图会被路由到 edit 调用、远端 404 且归因模糊。决定把 OpenAI 图像端点从单条通配拆为「通配 / 仅 T2I / 仅 I2I」三条，默认配置按能力分两个独立槽（`default_image_backend_t2i` / `default_image_backend_i2i`）；运行时严格按是否携带参考图选择路径，能力不匹配直接抛出带稳定 code 的 `ImageCapabilityError`，**不再做**「参考图无法读取就回退 T2I」之类的隐式 fallback——失败前置且错误清晰，胜过自动派发后远端模糊报错。

## Consequences

- 配置粒度变细，用户要分别配 T2I/I2I 模型；前端双下拉、数据迁移随之。
- 与 `docs/adr/0001`（capability 执行时解析）正交：那条讲「请求形态决定 t2i/i2i」，这条讲「按能力拆默认配置槽 + 不 fallback」。同一三 mode + 错误码已被 dashscope / vidu 等图像后端复用。
