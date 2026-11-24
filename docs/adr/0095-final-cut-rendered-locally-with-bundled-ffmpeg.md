---
status: accepted
---

# 成片使用随包 FFmpeg 渲染，成片和剪映草稿都是项目产物

成片渲染与剪映草稿导出共用生成队列的本地 `render` 车道。并发固定为 1，限制渲染峰值内存。两者无需调用供应商，因此不生成供应商用量记录，见 [ADR 0076](0076-usage-record-is-projection-of-provider-calls.md)。

成片固定调用 `imageio-ffmpeg` 随包分发的 FFmpeg，字幕使用随仓库分发的字体。系统可执行文件和字体在不同部署中的能力不同，按系统环境挑选或缺能力时退化为硬切会使成片与剪映草稿分叉。随包 FFmpeg 无法运行时，成片、剪映草稿的定格延长和 BGM 响度测量不可用，普通剪映草稿仍可导出。媒体探测另有入口，FFmpeg 包未提供 ffprobe。

产物按集、剪辑时间线和旁白版本区分，成片再区分是否烧入字幕。依据从启动渲染时的剪辑修订取快照，渲染中修订变化会让结果如实判为过期。剪映的本机路径和版本在下载时处理。每个产物身份仅保留最新文件，大体积的本地结果可重新渲染，项目归档不包含两类交付产物。时间线与旁白的有效规则见 [ADR 0090](0090-edit-timeline-named-follows-current-not-artifact.md) 和 [ADR 0089](0089-single-speech-owner-per-video-unit-narration-delivery-per-project.md)。

随包二进制与字体随发布附许可证声明及源码获取方式。成片字幕的中文换行由 ASS 生成侧处理，剪映平台自行排版，交付物无需逐像素一致。
