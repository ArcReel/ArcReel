# 字幕精确对齐在剪映中完成

ArcReel 按阅读单位比例生成字幕草稿，不提供逐帧设定单条字幕起止时刻的功能。剪辑时间线只保存剪辑决策，导出的剪映草稿则会将字幕放入独立文本轨，创作者可在剪映中逐条调整时间。

- 字幕生成规则见 [ADR 0089](../docs/adr/0089-single-speech-owner-per-video-unit-narration-delivery-per-project.md)。
- 剪辑时间线的边界见 [ADR 0090](../docs/adr/0090-edit-timeline-named-follows-current-not-artifact.md)。如果时间线以后承载字幕，可以重评此项。

## Prior requests

- #3334 — 将烧入字幕放到指定帧
