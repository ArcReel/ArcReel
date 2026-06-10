---
status: accepted
---

# 项目 style 存模版 prompt 的展开快照，与风格参考图互斥

只存 `style_template_id`、生成时查表展开虽能让模版升级自动传导，但会让已出片项目在 registry 改动后风格突变、破坏既有成片一致性。决定选定风格模版时把整段画风 prompt 展开写入 project.json 的 `style` 字段（同时保留 `style_template_id` 作来源标记、可在 PATCH / 读时迁移被重新解析），registry 后续改动不主动回灌老项目；并把项目风格定为「模版 / 自定义风格参考图 / 无」三选一互斥终态，由数据写入路径保证二者不同时生效，让 prompt 合成端只消费已展开的单一来源。

## Consequences

- 模版优化不主动传导已建项目；`style` 语义从短标签变长文本（对喂 LLM 透明，但破坏性）。
- 互斥约束贯穿创建 / PATCH / 迁移多处写路径：写模版清 `style_image`，传风格参考图清 `style_template_id`；历史数据竞态时以 style_image 优先并主动清 template_id。
