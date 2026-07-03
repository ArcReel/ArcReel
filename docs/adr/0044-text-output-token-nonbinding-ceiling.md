---
status: accepted
---

# 文本输出 token 上限是非约束安全阀，结构化截断为可操作硬错误

分集规划、剧本生成、drama step1 规范化三处各自把 `max_output_tokens` 当**功能预算**下调至 16000 / 32000，用来「省钱/限时」；但这些任务的输出体量由 schema 与内容天然约束，压低只会把正常输出切断——大长篇 drama 整批规划（至多约 20 集含分集大纲）稳定触顶截断，instructor 的 Pydantic 结构化路径抛 `IncompleteOutputException` 冒泡为不可诊断的错误。决定把 `max_output_tokens` 重新定位为**非约束安全阀**（仅防模型退化性 runaway），三处收口至 `lib/text_backends/base.py` 的单一共享常量 64000——一个高到正常规划/剧本输出不会触发的值，只在病态超大批量或用户配置了输出能力偏低的模型时才触发。配套把「截断」升为一等错误：**结构化输出**（请求带 `response_schema`）被输出上限截断时抛 typed `TextOutputTruncatedError`（明确指出当前文本模型、提示改用输出能力更高的模型），instructor 的 `IncompleteOutputException` 也归一到该错误；**自由文本**（无 `response_schema`）维持 `warn_if_truncated` 的 log-only 行为不变。该错误从 `generate()` 抛出，天然短路规划器的校验重试循环——重发同一份必然再截断的请求没有意义。分集规划在此基础上追加自己的手动调节提示（调小 `planning_window_chars` / `planning_max_episodes`，见 `docs/adr/0032`）。

## 明确不采用

- **传 `None` 交给 provider 默认**：各家省略 `max_tokens` 时的默认不统一、不保证高（中转代理尤甚），且逻辑上反了——要让校验型 provider 因「够不到」而报错，必须主动要一个大数让它拒；`None` 是「什么都不要」，只会静默拿到可能很低的默认、照样截断且无换模型信号。存在 Anthropic 兼容面（`max_tokens` 必填）时 `None` 还会直接崩溃。显式高位值三种情况都更优。
- **截断即自适应缩窗口/集数重试**：靠静默缩批量隐式降级，与「显式失败、用户做主」相悖；缩窗口还可能把剧情弧切在更差的边界。改为把选择权交还用户（换更强模型或自行调小设置）。
- **建 per-model 输出上限能力表按模型动态设 cap**：最贴合，但要为所有内置与自定义供应商补外部数据、维护成本高，远超本决策范围；系统当前无此注册表，高位显式常量已足够。

## Consequences

- 输出上限收口为单一共享真相源；planning / script / normalize 三处引用同一常量，调整只改一处。
- 结构化输出截断是硬 typed 错误、自由文本截断维持软告警——两类行为分道，波及面限定在带 `response_schema` 的调用。
- 规划器的手动调节机制（`planning_window_chars` / `planning_max_episodes`）要真正可用，依赖 settings 写入接受这些值；二者构成用户触发截断错误后的两条自助路径（换模型 / 调小批量）。
- 64000 是非约束安全阀而非精确预算：用户配置了输出能力低于此的模型时，截断错误会更常出现，这是刻意的「显式失败换模型」而非缺陷。
