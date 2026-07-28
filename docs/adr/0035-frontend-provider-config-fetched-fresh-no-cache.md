---
status: accepted
---

# 前端供应商配置按消费时点拉取，不持久缓存可变配置

`provider-models.ts` 的模块级缓存（`_cache` 内置 / `_customCache` 自定义）从不失效——`invalidateProviderModelsCache` 零调用，是"供应商设置里给模型加了 10s、项目设置时长选择器仍只显示旧值"的根因：前端缓存成了会漂移的第二真相源，违背 ADR 0018（`supported_durations` per-model 单一真相源）与 ADR 0013（能力模型级真相源）。决定把两个 fetcher 改为完全无状态——每次调用直拉 `GET /custom-providers`、`GET /providers`，删除全部模块缓存、in-flight promise 与 invalidate 函数——以"消费即重拉"从结构上消灭陈旧这一类，而非依赖调用方记得失效。

## Consequences

- 进设置 / 创建向导 / 画布时各多拉一次供应商列表（小 JSON）。惊群不发生：消费者都在页面顶层各调一次再 props 下传，无兄弟组件并发拉同一 fetcher，故不引入 in-flight 去重。
- 否决 zustand store 方案：其"响应式同屏一致"收益在本应用路由结构下用不上（设置页与项目页不同屏），属过度设计；将来真出现 N 个并发消费的热路径，才是引入 store 的时机。
- `config-status-store` 走独立 fetch + 自带 `refresh()`（变更后已被调用），只产出配置红点与 `availableMediaTypes`、不向时长/能力选择器暴露模型列表，不在本约束范围内。
- 已存 `default_duration` 因模型时长缩小而越界，由后端 resolver fail-loud（ADR 0018）兜底；前端 picker 的"存值失效"提示是单独议题，不在本次范围。
