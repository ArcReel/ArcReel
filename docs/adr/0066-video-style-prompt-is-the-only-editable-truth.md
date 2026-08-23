# ADR 0066：统一视频风格以一段提示词为唯一可编辑真相

## 状态

Accepted（2026-08-24）

## 决策

- 项目级 `video_style` 只持久化 `prompt`、`source` 与 `updated_at`。设置页、Web API、Agent 工具、视频单元摘要和 H3 优化共同读写这一个对象。
- Agent 分析仍以动态画面、镜头语言、节奏、声音重点、背景音乐、声音设计及其他项目级约束为内部检查维度，但结构化输出只返回一段连贯提示词；这些维度不再成为用户表单或持久化字段。
- H3 将 `project.video_style.prompt` 作为项目级权威方向，并把其中的明确禁止项和要求视为硬约束。
- 项目 schema 从 v9 升至 v10。迁移把每个旧字段以源语言标签写入同一段提示词，保留原始字段内容、`source` 和 `updated_at`；runner 在迁移前保留 `project.json.bak.v9-*`，校验失败时不提交 schema 版本。

## 结果

视频风格与视觉 Style 使用相同的单段文本心智模型，Web 和 Agent 不会出现两套编辑契约。旧项目不会丢失任何既有维度，但 v10 之后不再依赖 `music_policy` 或 `sound_focus` 等结构化分支做生成后机械改写，约束由统一提示词直接传递给生成链。
