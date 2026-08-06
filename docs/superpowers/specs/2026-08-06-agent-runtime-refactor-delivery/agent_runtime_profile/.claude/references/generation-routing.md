# 生成路由

先读 `project.json` 的项目级路线，再调用工具。剧本骨架与路线不一致时，重做对应 step1 / 最终剧本；不按残留骨架改判路线。

## 路由矩阵

| content_mode | generation_mode | 正式剧本主结构 | 分镜 | 视频 | 旁白 |
|---|---|---|---|---|---|
| narration | storyboard | `segments[]` | 单图或宫格 | `video` | 逐段 TTS |
| drama | storyboard | `scenes[]` | 单图或宫格 | `video` | 不适用 |
| ad | storyboard | `shots[]` | 单图 | `video` | 剪映中完成 |
| narration / drama | reference_video | `video_units[]` | 跳过 | `reference_video` | 跳过 |
| ad | reference_video | `shots[]` + 派生 `reference_units` | 跳过 | `reference_video` | 剪映中完成 |

`grid_storyboard` 只在 narration / drama 的 storyboard 路线生效；ad 不开放宫格。

## 时长

执行或修改时长前调用 `mcp__arcreel__get_video_capabilities({})`：

- storyboard：片段、场景或镜头时长必须属于 `supported_durations`；
- narration / drama reference-video：unit 时长属于其引用状态对应的 `reference_unit_durations.with_references` 或 `.without_references`；
- ad reference-video：shot 时长为 1–15 秒整数，unit 由服务端派生。

内容装不下合法时长时拆分内容；不把超量台词压进较短档位。

## Prompt 与参考

- 图片和视频 prompt 使用中文叙事句，不写宽高比和时长；这些由 API 参数提供。
- 生成端统一追加 BGM、文字字幕和水印排除项，业务 prompt 不重复写。
- reference-video 正文用 `@[角色]`、`@[场景]`、`@[道具]` 引用已登记资产；外貌、服装和环境细节由参考图承担。
- 源文绑定字段保持原语言和原标点，不翻译。

## 依赖传播

修改资产定义、sheet、prompt、时长、模型设置或宫格开关后，provenance 自动将依赖旧输入的产物标为 stale。省略 ID 的生成工具默认处理 missing 与 stale；显式 ID 表示强制重生。
