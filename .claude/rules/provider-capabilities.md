---
paths:
  - "lib/video_backends/**"
  - "lib/image_backends/**"
  - "lib/text_backends/**"
  - "lib/audio_backends/**"
  - "lib/backend_assembly/**"
  - "lib/custom_provider/**"
  - "lib/config/**"
  - "lib/pricing/**"
  - "lib/providers.py"
  - "lib/capability_buckets.py"
  - "lib/cost_calculator.py"
  - "lib/kling_backend_base.py"
  - "lib/agent_provider_catalog.py"
  - "docs/api-docs/**"
---

# 供应商能力与契约

修改 provider、endpoint、供应商 API 契约、能力、参数约束或计费前，先读 `docs/api-docs/AGENTS.md`，并同步对应官方文档索引。

能力数据按字段划分真相源，改动前对照以下决策：`docs/adr/0013`（型号级能力真相源）、`docs/adr/0018`（`supported_durations` 未登记即 fail loud、无隐性 fallback）、`docs/adr/0054`（视频能力位与各类上限归 backend，与请求构造同源）、`docs/adr/0056`（执行期判定与请求构造同源）。自定义模型读 DB 声明；agent prompt 模板与 `agent_runtime_profile/` 不硬编码能力数值，占位符由编排层注入，配置界面此类字段不预填。

注意：个别 backend 持有独立于 registry 的执行期白名单（如 `lib/video_backends/vidu.py` 的分辨率白名单）。修改 registry 的分辨率或时长声明时须同步核对对应 backend，否则用户可选、backend 不支持的档位会被静默替换为默认档位。
