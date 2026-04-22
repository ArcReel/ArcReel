# Changelog

## [0.10.0](https://github.com/ArcReel/ArcReel/compare/v0.9.0...v0.10.0) (2026-04-22)


### 🌟 重点功能

* **参考生视频模式** — 全新工作流，支持以参考素材直接生成视频。本版本完成了从数据模型、后端 API/executor、前端模式选择器与 Canvas 编辑器、Agent 工作流、@ mention 交互到 UX 优化的完整链路，并覆盖四家供应商 SDK 验证与 E2E 测试 ([#328](https://github.com/ArcReel/ArcReel/issues/328), [#330](https://github.com/ArcReel/ArcReel/issues/330), [#332](https://github.com/ArcReel/ArcReel/issues/332), [#337](https://github.com/ArcReel/ArcReel/issues/337), [#338](https://github.com/ArcReel/ArcReel/issues/338), [#342](https://github.com/ArcReel/ArcReel/issues/342), [#349](https://github.com/ArcReel/ArcReel/issues/349), [#374](https://github.com/ArcReel/ArcReel/issues/374), [#393](https://github.com/ArcReel/ArcReel/issues/393))
* **全局资产库 + 线索重构** — 线索拆分为场景（scenes）与道具（props），新增跨项目的全局资产库 ([#307](https://github.com/ArcReel/ArcReel/issues/307))
* **源文件格式扩展** — 支持 `.txt` / `.md` / `.docx` / `.epub` / `.pdf` 统一规范化导入 ([#350](https://github.com/ArcReel/ArcReel/issues/350))
* **自定义供应商支持 NewAPI 格式**（统一视频端点） ([#305](https://github.com/ArcReel/ArcReel/issues/305))


### ✨ 其他新功能

* 引入 release-please 自动化版本管理 ([#312](https://github.com/ArcReel/ArcReel/issues/312)) ([dda244c](https://github.com/ArcReel/ArcReel/commit/dda244cff89472d4dc61d9f7a7a2fde3747751c0))


### 🐛 Bug 修复

* **reference-video:** 修复 @ 提及选单被裁切、生成按钮无反馈与项目封面缺失 ([#378](https://github.com/ArcReel/ArcReel/issues/378)) ([65e33d7](https://github.com/ArcReel/ArcReel/commit/65e33d718c0f56d7c5502d26501b45011f52ffb1))
* **reference-video:** 补 OUTPUT_PATTERNS 白名单修复生成视频 P0 失败 ([#373](https://github.com/ArcReel/ArcReel/issues/373)) ([8eec638](https://github.com/ArcReel/ArcReel/commit/8eec638cfbc0e78f508bd2739b65d09ac579f7ce))
* **reference-video:** Grok 生成默认 1080p 被 xai_sdk 拒绝 ([#387](https://github.com/ArcReel/ArcReel/issues/387)) ([79521da](https://github.com/ArcReel/ArcReel/commit/79521da748ac1b5611354a6da065d35c785bfecc))
* **script:** 剧本场景时长按视频模型能力匹配，修复被卡在 8 秒问题 ([#379](https://github.com/ArcReel/ArcReel/issues/379)) ([4d9c97b](https://github.com/ArcReel/ArcReel/commit/4d9c97b1c56693199c4b4b8b127e64483c939930))
* **script:** 修复 AI 生成剧本集号幻觉污染 `project.json` ([#363](https://github.com/ArcReel/ArcReel/issues/363)) ([5320e2d](https://github.com/ArcReel/ArcReel/commit/5320e2d2d16c619f398eb30dda1d2fa17382f5e9))
* **project-cover:** 合并 segments 与 video_units 遍历，修复封面误退到 scene_sheet ([#390](https://github.com/ArcReel/ArcReel/issues/390)) ([64d65c4](https://github.com/ArcReel/ArcReel/commit/64d65c4b0a68d4c2c5e9a43e029365d43dc07382))
* **assets:** 资产库返回按钮跟随来源页面 ([#389](https://github.com/ArcReel/ArcReel/issues/389)) ([b7e57be](https://github.com/ArcReel/ArcReel/commit/b7e57be923fb110b03c9323a070258e7fb6c3658))
* **cost-calculator:** 修正预设供应商文本模型定价 ([#388](https://github.com/ArcReel/ArcReel/issues/388)) ([559e748](https://github.com/ArcReel/ArcReel/commit/559e748646a0ea5513f71bf78573ea69881c451f))
* **popover:** 修复 ref 挂父节点时弹框定位到视窗左上角 ([#386](https://github.com/ArcReel/ArcReel/issues/386)) ([4247047](https://github.com/ArcReel/ArcReel/commit/42470478a702b9ff1d210420d2818e743a8219e5))
* **ark-video:** `content.image_url` 项必须带 `role` 字段 ([abe370c](https://github.com/ArcReel/ArcReel/commit/abe370c9e618a5f1a59d67be51889cd18828573e))
* **frontend:** 配置检测支持自定义供应商 ([1665b69](https://github.com/ArcReel/ArcReel/commit/1665b697b6ca4269de4ba7e44a2fc5625c38b4ec))
* **video:** seedance-2.0 模型不传 `service_tier` 参数 ([#325](https://github.com/ArcReel/ArcReel/issues/325)) ([66aa423](https://github.com/ArcReel/ArcReel/commit/66aa42394bc303473a4903fdbd815a5ac007a238))
* **frontend:** 重新生成 `pnpm-lock.yaml` 修复重复 key ([#331](https://github.com/ArcReel/ArcReel/issues/331)) ([a91fd8b](https://github.com/ArcReel/ArcReel/commit/a91fd8be1167a2f6e55eb3ad7210e810242b5312))
* **ci:** pin setup-uv to v7 in release-please workflow ([#315](https://github.com/ArcReel/ArcReel/issues/315)) ([b602779](https://github.com/ArcReel/ArcReel/commit/b602779aa5476061bc73cb118f52f15c332ad646))
* **docs,ci:** 回应 PR #310-314 review 反馈 ([#316](https://github.com/ArcReel/ArcReel/issues/316)) ([81ff8ce](https://github.com/ArcReel/ArcReel/commit/81ff8ce6b9ff8a3ff5c6f136d62e8a4cc66fc58f))


### ⚡ 性能与重构

* **backend:** 后端 AssetType 统一抽象（关闭 [#326](https://github.com/ArcReel/ArcReel/issues/326)） ([#336](https://github.com/ArcReel/ArcReel/issues/336)) ([9dcd221](https://github.com/ArcReel/ArcReel/commit/9dcd221d57bd1b3bf182ff3bc254813503b9acf6))
* **backend:** 消除 `_serialize_value` 对 Pydantic 的双遍历 ([#335](https://github.com/ArcReel/ArcReel/issues/335)) ([f945fad](https://github.com/ArcReel/ArcReel/commit/f945fad5c780dbd1531c55e0e87da0fdedcc3baa))
* PR [#307](https://github.com/ArcReel/ArcReel/issues/307) tech-debt follow-up（P1 + P2 低风险） ([#327](https://github.com/ArcReel/ArcReel/issues/327)) ([c23972a](https://github.com/ArcReel/ArcReel/commit/c23972a2f017b825aa09ffff86bcfccfaec7f23d))


### 📚 文档

* 新增 PR 模板、CODEOWNERS，扩展 CONTRIBUTING ([#308](https://github.com/ArcReel/ArcReel/issues/308)) ([4c0da4c](https://github.com/ArcReel/ArcReel/commit/4c0da4c9cbd2986589bf6cb14a4b2261705225aa))
