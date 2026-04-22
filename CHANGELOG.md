# Changelog

## [0.10.0](https://github.com/ArcReel/ArcReel/compare/v0.9.0...v0.10.0) (2026-04-22)


### ✨ 新功能

* **agent:** 参考生视频模式 Agent 工作流 ([#337](https://github.com/ArcReel/ArcReel/issues/337)) ([a521eac](https://github.com/ArcReel/ArcReel/commit/a521eac1f1c8456aeab855af29b1e64034697d81))
* **backend:** reference-to-video mode API + executor (PR3/7) ([#332](https://github.com/ArcReel/ArcReel/issues/332)) ([0846691](https://github.com/ArcReel/ArcReel/commit/08466910d417214a9be885a0811c0c32c801bece))
* **frontend:** 参考生视频前端编辑器（PR5/7） ([#342](https://github.com/ArcReel/ArcReel/issues/342)) ([847c151](https://github.com/ArcReel/ArcReel/commit/847c151de378f54f97a3f402d228bd460d74787e))
* **frontend:** 参考生视频模式选择器 + Canvas 外壳（PR4/7） ([#338](https://github.com/ArcReel/ArcReel/issues/338)) ([64c3f5f](https://github.com/ArcReel/ArcReel/commit/64c3f5f652e6d0f391b3a79a68b91ecee7a21cb5))
* integrate release-please for automated versioning ([#312](https://github.com/ArcReel/ArcReel/issues/312)) ([dda244c](https://github.com/ArcReel/ArcReel/commit/dda244cff89472d4dc61d9f7a7a2fde3747751c0))
* **reference-video:** 优化 UX——模式排序、统一工具条、剧本手动保存 ([#393](https://github.com/ArcReel/ArcReel/issues/393)) ([abaad54](https://github.com/ArcReel/ArcReel/commit/abaad5484ca0a8bd04ebae73c360a083bb7e8081))
* **reference-video:** 参考生视频 @ mention 交互 + ui 优化 ([#374](https://github.com/ArcReel/ArcReel/issues/374)) ([0b23aa9](https://github.com/ArcReel/ArcReel/commit/0b23aa9974e843d896a44811fcc9bc0f1f678f3a))
* **reference-video:** 参考生视频 E2E + 发版（PR7/7 · 6 issue 清扫） ([#349](https://github.com/ArcReel/ArcReel/issues/349)) ([292fb79](https://github.com/ArcReel/ArcReel/commit/292fb79d188272ba013614100f7bdbbdd2d84ce6))
* **script-models:** 参考生视频数据模型 + shot parser (PR2/7) ([#330](https://github.com/ArcReel/ArcReel/issues/330)) ([ba0dd6b](https://github.com/ArcReel/ArcReel/commit/ba0dd6b138101aa9a28ad84480c7431519265c6e))
* **sdk-verify:** 参考生视频四家供应商 SDK 验证脚本与能力矩阵 ([#328](https://github.com/ArcReel/ArcReel/issues/328)) ([0aefaab](https://github.com/ArcReel/ArcReel/commit/0aefaab4ee011db3e58086910b7afc623b7344e0))
* **source:** 源文件格式扩展（.txt/.md/.docx/.epub/.pdf 统一规范化） ([#350](https://github.com/ArcReel/ArcReel/issues/350)) ([13a3bb6](https://github.com/ArcReel/ArcReel/commit/13a3bb6a15d52d67f2a1338ac4d78276b982d62b))
* 全局资产库 + 线索重构拆分为场景和道具（scenes/props 拆分） ([#307](https://github.com/ArcReel/ArcReel/issues/307)) ([51dde36](https://github.com/ArcReel/ArcReel/commit/51dde363d3c8492e0b0ac45bc0932d48cf8e362c))
* 自定义供应商支持 NewAPI 格式（统一视频端点） ([#305](https://github.com/ArcReel/ArcReel/issues/305)) ([433124d](https://github.com/ArcReel/ArcReel/commit/433124d87b299c9a99799adc65a35c2ff00df0c0))


### 🐛 Bug 修复

* **ark-video:** content image_url 项必须带 role 字段 ([abe370c](https://github.com/ArcReel/ArcReel/commit/abe370c9e618a5f1a59d67be51889cd18828573e))
* **assets:** 资产库返回按钮跟随来源页面 ([#389](https://github.com/ArcReel/ArcReel/issues/389)) ([b7e57be](https://github.com/ArcReel/ArcReel/commit/b7e57be923fb110b03c9323a070258e7fb6c3658))
* **ci:** pin setup-uv to v7 in release-please workflow ([#315](https://github.com/ArcReel/ArcReel/issues/315)) ([b602779](https://github.com/ArcReel/ArcReel/commit/b602779aa5476061bc73cb118f52f15c332ad646))
* **cost-calculator:** 修正预设供应商文本模型定价 ([#388](https://github.com/ArcReel/ArcReel/issues/388)) ([559e748](https://github.com/ArcReel/ArcReel/commit/559e748646a0ea5513f71bf78573ea69881c451f))
* **docs,ci:** address review feedback from PR [#310](https://github.com/ArcReel/ArcReel/issues/310)-314 ([#316](https://github.com/ArcReel/ArcReel/issues/316)) ([81ff8ce](https://github.com/ArcReel/ArcReel/commit/81ff8ce6b9ff8a3ff5c6f136d62e8a4cc66fc58f))
* **frontend:** regenerate pnpm-lock.yaml to fix duplicate keys ([#331](https://github.com/ArcReel/ArcReel/issues/331)) ([a91fd8b](https://github.com/ArcReel/ArcReel/commit/a91fd8be1167a2f6e55eb3ad7210e810242b5312))
* **frontend:** 配置检测支持自定义供应商 ([1665b69](https://github.com/ArcReel/ArcReel/commit/1665b697b6ca4269de4ba7e44a2fc5625c38b4ec))
* **popover:** 修复 ref 挂父节点时弹框定位到视窗左上角 ([#386](https://github.com/ArcReel/ArcReel/issues/386)) ([4247047](https://github.com/ArcReel/ArcReel/commit/42470478a702b9ff1d210420d2818e743a8219e5))
* **project-cover:** 合并 segments 与 video_units 遍历，修复封面误退到 scene_sheet ([#390](https://github.com/ArcReel/ArcReel/issues/390)) ([64d65c4](https://github.com/ArcReel/ArcReel/commit/64d65c4b0a68d4c2c5e9a43e029365d43dc07382))
* **reference-video:** Grok 生成默认 1080p 被 xai_sdk 拒绝 ([#387](https://github.com/ArcReel/ArcReel/issues/387)) ([79521da](https://github.com/ArcReel/ArcReel/commit/79521da748ac1b5611354a6da065d35c785bfecc))
* **reference-video:** 修复 @ 提及选单被裁切、生成按钮无反馈与项目封面缺失 ([#368](https://github.com/ArcReel/ArcReel/issues/368)/[#370](https://github.com/ArcReel/ArcReel/issues/370)) ([#378](https://github.com/ArcReel/ArcReel/issues/378)) ([65e33d7](https://github.com/ArcReel/ArcReel/commit/65e33d718c0f56d7c5502d26501b45011f52ffb1))
* **reference-video:** 补 OUTPUT_PATTERNS 白名单修复生成视频 P0 失败 ([#373](https://github.com/ArcReel/ArcReel/issues/373)) ([8eec638](https://github.com/ArcReel/ArcReel/commit/8eec638cfbc0e78f508bd2739b65d09ac579f7ce)), closes [#364](https://github.com/ArcReel/ArcReel/issues/364)
* **script:** 修复 AI 生成剧本集号幻觉污染 project.json ([#363](https://github.com/ArcReel/ArcReel/issues/363)) ([5320e2d](https://github.com/ArcReel/ArcReel/commit/5320e2d2d16c619f398eb30dda1d2fa17382f5e9))
* **script:** 剧本场景时长按视频模型能力匹配，修复被卡在 8 秒问题 ([#365](https://github.com/ArcReel/ArcReel/issues/365)) ([#379](https://github.com/ArcReel/ArcReel/issues/379)) ([4d9c97b](https://github.com/ArcReel/ArcReel/commit/4d9c97b1c56693199c4b4b8b127e64483c939930))
* **video:** seedance-2.0 模型不传 service_tier 参数 ([#325](https://github.com/ArcReel/ArcReel/issues/325)) ([66aa423](https://github.com/ArcReel/ArcReel/commit/66aa42394bc303473a4903fdbd815a5ac007a238))


### ⚡ 性能优化

* **backend:** 消除 _serialize_value 对 Pydantic 的双遍历 ([#298](https://github.com/ArcReel/ArcReel/issues/298)) ([#335](https://github.com/ArcReel/ArcReel/issues/335)) ([f945fad](https://github.com/ArcReel/ArcReel/commit/f945fad5c780dbd1531c55e0e87da0fdedcc3baa))


### ♻️ 重构

* **backend:** 后端 AssetType 统一抽象（关闭 [#326](https://github.com/ArcReel/ArcReel/issues/326)） ([#336](https://github.com/ArcReel/ArcReel/issues/336)) ([9dcd221](https://github.com/ArcReel/ArcReel/commit/9dcd221d57bd1b3bf182ff3bc254813503b9acf6))
* PR [#307](https://github.com/ArcReel/ArcReel/issues/307) tech-debt follow-up（P1 + P2 低风险） ([#327](https://github.com/ArcReel/ArcReel/issues/327)) ([c23972a](https://github.com/ArcReel/ArcReel/commit/c23972a2f017b825aa09ffff86bcfccfaec7f23d))


### 📚 文档

* add PR template, CODEOWNERS, extend CONTRIBUTING ([#308](https://github.com/ArcReel/ArcReel/issues/308)) ([4c0da4c](https://github.com/ArcReel/ArcReel/commit/4c0da4c9cbd2986589bf6cb14a4b2261705225aa))
