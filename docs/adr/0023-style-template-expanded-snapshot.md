---
status: accepted
---

# 项目 style 存模版 prompt 的展开快照，与风格参考图互斥

只存 `style_template_id`、生成时查表展开虽能让模版升级自动传导，但会让已出片项目在 registry 改动后风格突变、破坏既有成片一致性。决定选定风格模版时把整段画风 prompt 展开写入 project.json 的 `style` 字段（同时保留 `style_template_id` 作来源标记——PATCH 传入新模版 id 时重新展开写入，读时迁移仅为缺 id 的 legacy 短标签做一次性解析，不按已存 id 重展开），registry 后续改动不主动回写老项目；并把项目风格定为「模版 / 自定义风格参考图 / 无」三选一互斥终态，由数据写入路径保证二者不同时生效，让 prompt 合成端只消费已展开的单一来源。

## Consequences

- 模版优化不主动传导已建项目；`style` 语义从短标签变长文本（对喂 LLM 透明，但破坏性）。
- 互斥约束贯穿创建 / PATCH / 迁移多处写路径：写入模版时清除 `style_image`，写入风格参考图时清除 `style_template_id`，显式清空模版（id 置 null）时连同已展开的 `style` 一并清空、不留孤儿文本；历史数据竞态时以 style_image 优先并主动清除 template_id。
