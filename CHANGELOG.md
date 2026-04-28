# Changelog

## [0.11.1](https://github.com/ArcReel/ArcReel/compare/v0.11.0...v0.11.1) (2026-04-28)


### 🐛 Bug 修复

* **generate:** 补充 prompt str 分支的空字符串校验 ([#443](https://github.com/ArcReel/ArcReel/issues/443)) ([5c9a40a](https://github.com/ArcReel/ArcReel/commit/5c9a40af5643dc88c46ab4fbe33064d8f22761cd))
* replace fcntl with portalocker for Windows compatibility ([#442](https://github.com/ArcReel/ArcReel/issues/442)) ([e5657b0](https://github.com/ArcReel/ArcReel/commit/e5657b0356846bb0b64b97f87e6b51e3d403ae52))
* **settings:** 自定义供应商编辑时 base_url 变更需重输 API Key 才能发现模型 ([#440](https://github.com/ArcReel/ArcReel/issues/440)) ([972298e](https://github.com/ArcReel/ArcReel/commit/972298e4ff896afc110bab1620d12e040bbfce3f)), closes [#439](https://github.com/ArcReel/ArcReel/issues/439)

## [0.11.0](https://github.com/ArcReel/ArcReel/compare/v0.10.0...v0.11.0) (2026-04-26)


### ✨ 新功能

* **custom-provider:** 自定义供应商支持按照模型设置 API 端点 ([#415](https://github.com/ArcReel/ArcReel/issues/415)) ([8c7fa75](https://github.com/ArcReel/ArcReel/commit/8c7fa756ef4b370b44b33503c234509f5ddbcc94))
* **settings:** 重设计自定义供应商端点选择器并打磨 UI ([#417](https://github.com/ArcReel/ArcReel/issues/417)) ([8244396](https://github.com/ArcReel/ArcReel/commit/82443964efe65e53e1d140572616ecdc4e648b1f))
* 分镜卡片支持编辑角色/场景/道具引用 ([#416](https://github.com/ArcReel/ArcReel/issues/416)) ([7a3e62c](https://github.com/ArcReel/ArcReel/commit/7a3e62c0b8def13b1164f6f7c3b01d92f875edac))
* 视频/图片 resolution 参数重构 (closes [#359](https://github.com/ArcReel/ArcReel/issues/359)) ([#402](https://github.com/ArcReel/ArcReel/issues/402)) ([9357973](https://github.com/ArcReel/ArcReel/commit/935797313fb13e0010b03c48f28f4986d24803f0))
* 设置-关于页面，支持查看当前版本和检查更新 ([#403](https://github.com/ArcReel/ArcReel/issues/403)) ([c6809fb](https://github.com/ArcReel/ArcReel/commit/c6809fb29da4b2c520bf77c9222c7f6773d583a9))


### 🐛 Bug 修复

* **frontend:** 分镜枚举接入 i18n（镜头类型 / 运镜） ([#396](https://github.com/ArcReel/ArcReel/issues/396)) ([9c244db](https://github.com/ArcReel/ArcReel/commit/9c244dbb4f3268754c17b12f16b5b89335eda02f)), closes [#352](https://github.com/ArcReel/ArcReel/issues/352)
* **frontend:** 项目设置页 header 与内容左对齐 ([#411](https://github.com/ArcReel/ArcReel/issues/411)) ([88b717b](https://github.com/ArcReel/ArcReel/commit/88b717b7b0efca456e4467a7c71949d5603259e6))
* **grid-mode:** 修复宫格生视频报错并清理首尾帧命名遗留 ([#412](https://github.com/ArcReel/ArcReel/issues/412)) ([e0ea46c](https://github.com/ArcReel/ArcReel/commit/e0ea46c768aef844180e3526833d709df8f6e014))
* **image-backends:** OpenAI/Ark 图片响应按 b64_json/url 降级解析 ([#404](https://github.com/ArcReel/ArcReel/issues/404)) ([2523736](https://github.com/ArcReel/ArcReel/commit/252373695511d7ff982f0c19307031fe4f89df00))
* **video:** 修复自定义供应商生成视频立即报 400 "Task is not completed yet" 的问题 ([#410](https://github.com/ArcReel/ArcReel/issues/410)) ([fe10c81](https://github.com/ArcReel/ArcReel/commit/fe10c814660dc7912bff7f337a8326ddb601e896))


### ♻️ 重构

* **notifications:** toast 与持久通知解耦 ([#351](https://github.com/ArcReel/ArcReel/issues/351)) ([#398](https://github.com/ArcReel/ArcReel/issues/398)) ([cdcb1d3](https://github.com/ArcReel/ArcReel/commit/cdcb1d315e1c5c9617a70008726a29a7edb3b325))

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
