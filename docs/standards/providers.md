---
paths:
  - "lib/backends/**"
  - "lib/custom_provider/**"
  - "lib/config/**"
  - "lib/billing/**"
  - "lib/agent/agent_provider_catalog.py"
  - "lib/prompts/prompt_builders*.py"
  - "agent_runtime_profile/**"
  - "docs/api-docs/**"
---

# 供应商能力与契约

## 能力数据

### 每个能力字段只有一个真相源

能力数据按字段划分真相源，改动前对照对应决策：

- `docs/adr/0013`：型号级能力的真相源。
- `docs/adr/0018`：`supported_durations` 未登记时直接报错，不做隐式回退。
- `docs/adr/0054`：视频能力位、各类上限与成片音轨形态归 backend 的 `VideoCapabilities`，与请求构造同源；音轨按 i2v / r2v 两条执行路径各声明一份。
- `docs/adr/0056`：执行期判定与请求构造同源。

自定义模型的能力读 DB 声明，配置界面不预填这类字段。在真相源之外再写一份能力数值（另起常量、在调用方补默认值、在 UI 预填）是违规：两份数据迟早不一致，用户看到的可选项会与实际请求不符。

### 提示词模板不持有能力数值

`agent_runtime_profile/` 与 `lib/prompts/prompt_builders*.py` 中的模板不硬编码时长、分辨率等档位，占位符由编排层从「每个能力字段只有一个真相源」列出的真相源注入。硬编码的档位在供应商调整能力后不会跟着变，Agent 会按旧档位规划。

### 改 registry 的分辨率或时长声明，同步核对 backend 的执行期白名单

个别 backend 持有独立于 registry 的执行期白名单（如 `lib/backends/video_backends/vidu.py` 的分辨率白名单）。registry 放开某个档位而 backend 白名单没有同步时，用户能选中该档位，backend 却会把它静默替换为默认档位：请求成功，结果却不是用户选的档位。
