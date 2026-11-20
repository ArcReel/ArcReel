---
status: accepted
---

# 视频单元以正文为真相，参考图与发声从文本派生

参考生视频路线每个视频单元对应一次生成请求。单元持久化自由文本正文与编排时长，正文中的 `@[名称]`、`@[角色]{台词}` 和 `{画外音}` 分别表达资产提及、角色台词和画外音。参考图与发声在读取时由同一[解析器](../../lib/script/reference_video/text_parser.py)派生。如果将派生列表另行持久化，编辑器、Agent、迁移和归档就必须各自维护同步，只要漏改一处，界面正文与实际下发请求就会出现不一致。

参考图按正文首次提及顺序编号和去重，商品也依此顺序。只用作说话人的名称绑定声音，不产生参考图。编辑可以保留未登记提及并提示创作者，生成要求引用和图像齐备，详见 [ADR 0073](0073-generation-entry-requires-registered-assets-with-sheets.md)。分镜图生视频路线的商品参考规则见 [ADR 0034](0034-product-fidelity-dual-layer-anchor.md)。

旧 `shots[*].text` 随项目迁移按顺序拼接为 `text`，保留作者写下的「镜头 N」等文本和已有重规划标记，移除独立引用列表与镜头结构。损坏的脚本规划草稿原样保留，迁移阻断范围见 [ADR 0098](0098-backfill-keeps-existing-content-gaps-only-gate-generation.md)。画外音不随视觉提示词发送，发声归属与产物时效的边界见 [ADR 0089](0089-single-speech-owner-per-video-unit-narration-delivery-per-project.md)。
