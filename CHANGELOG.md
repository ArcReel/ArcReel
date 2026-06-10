# Changelog

## [0.16.1](https://github.com/ArcReel/ArcReel/compare/v0.16.0...v0.16.1) (2026-06-08)


### 🐛 Bug 修复

* **project-manager:** 损坏剧本（数组含非对象元素）不再导致 500，按 script_editor 既有模式干净降级 ([#719](https://github.com/ArcReel/ArcReel/issues/719)) ([b11b3d4](https://github.com/ArcReel/ArcReel/commit/b11b3d470aa48f555e2e40f925fc3b351d224224))
* **script:** 剧本生成时将分镜时长约束到视频模型支持范围，避免不支持时长导致视频生成失败 ([#741](https://github.com/ArcReel/ArcReel/issues/741)) ([72d178f](https://github.com/ArcReel/ArcReel/commit/72d178f3e1eef3aa4d5667ad4b32243abe04b269))
* **video-backends:** V2 create 响应解析 generation_id ([#718](https://github.com/ArcReel/ArcReel/issues/718)) ([ebbeb35](https://github.com/ArcReel/ArcReel/commit/ebbeb359fc15395c1968ea4d7c6f87d2f501d60b)), closes [#716](https://github.com/ArcReel/ArcReel/issues/716)
* **video-backends:** 自定义供应商 BytePlus seedance-2 参考生视频不再因 service_tier 报 400 ([#723](https://github.com/ArcReel/ArcReel/issues/723)) ([ff067d6](https://github.com/ArcReel/ArcReel/commit/ff067d67af96c5077836c9b853a7550f797a66f3))


### ♻️ 重构

* **worker:** 重构生成并发管理,修改并发上限配置不再有中断正在运行任务的风险 ([#726](https://github.com/ArcReel/ArcReel/issues/726)) ([a02e687](https://github.com/ArcReel/ArcReel/commit/a02e6874bbd4181abd2547783f098a57c795eb31))


### 📚 文档

* **context:** 新增「参考图与压缩」术语条目 ([#742](https://github.com/ArcReel/ArcReel/issues/742)) ([a433c60](https://github.com/ArcReel/ArcReel/commit/a433c60c93c8f8d04aef962c99b18faacd979fd6))
* 更新飞书交流群二维码 ([#746](https://github.com/ArcReel/ArcReel/issues/746)) ([f7eb5ce](https://github.com/ArcReel/ArcReel/commit/f7eb5ce4369b030b8fe60a68770c3c36a967b6e1))

## [0.16.0](https://github.com/ArcReel/ArcReel/compare/v0.15.2...v0.16.0) (2026-06-03)


### ✨ 新功能

* **agent:** Agent 改项目 JSON 数据收归 MCP 工具，拒绝通过 Write/Edit/Bash 直接修改 ([#604](https://github.com/ArcReel/ArcReel/issues/604)) ([#608](https://github.com/ArcReel/ArcReel/issues/608)) ([0188e8a](https://github.com/ArcReel/ArcReel/commit/0188e8ad7bd62e43895c7ca3d8b9f56bb18c5e01))
* **custom-provider:** 扩充视频 endpoint 生态并重构 endpoint 自动推断 ([#683](https://github.com/ArcReel/ArcReel/issues/683)) ([35493a1](https://github.com/ArcReel/ArcReel/commit/35493a15666aa614a25ede39327a2a2a49f3faee))
* **provider:** 预设供应商接入阿里百炼 DashScope 全模态 ([#690](https://github.com/ArcReel/ArcReel/issues/690)) ([2c230d0](https://github.com/ArcReel/ArcReel/commit/2c230d0112354e623eb3a0caa6d09eced502cbbd))
* 项目配置新增「每集目标字数」字段；分集切分按源语言（中/英/越）度量 ([#668](https://github.com/ArcReel/ArcReel/issues/668)) ([d0fcf67](https://github.com/ArcReel/ArcReel/commit/d0fcf676fc4283a5a7a2ec1458d73e37a143964f))


### 🐛 Bug 修复

* **compose-video:** 守卫中段双侧 xfade 时间窗重叠 ([#680](https://github.com/ArcReel/ArcReel/issues/680)) ([d6446f0](https://github.com/ArcReel/ArcReel/commit/d6446f04912f6aa8c7f9a8febbb3d0f17b0eba2f)), closes [#667](https://github.com/ArcReel/ArcReel/issues/667)
* **frontend:** 未登录访问项目工作区 URL 重定向到正确的登录页 ([#675](https://github.com/ArcReel/ArcReel/issues/675)) ([567b197](https://github.com/ArcReel/ArcReel/commit/567b19797efa4a66926f20312d84702a6058e462))
* **frontend:** 源文件支持格式统一为共享常量，修正欢迎页格式范围展示 ([#672](https://github.com/ArcReel/ArcReel/issues/672)) ([81f306b](https://github.com/ArcReel/ArcReel/commit/81f306b5174c73e1bbb28382ea408b14a0351424))
* **frontend:** 自定义供应商 Base URL 占位符去掉 /v1 后缀,避免误导用户 ([#686](https://github.com/ArcReel/ArcReel/issues/686)) ([c40608c](https://github.com/ArcReel/ArcReel/commit/c40608c9fa3b8d3ced0979665d9455792132c38a))
* **generation:** 分镜图/视频统一遵循项目比例（比例优先、清晰度其次） ([#712](https://github.com/ArcReel/ArcReel/issues/712)) ([e9742c4](https://github.com/ArcReel/ArcReel/commit/e9742c4bf54ae8f0b8a22dd253f37b9863fe276a))
* **grid:** 分组超过 9 个分镜时宫格预览不显示 ([#662](https://github.com/ArcReel/ArcReel/issues/662)) ([eb0617b](https://github.com/ArcReel/ArcReel/commit/eb0617b73cfa651116884d3406da9682d6a01245))
* **reference-video:** 修正参考生视频的参考图数量上限，避免超出模型支持被供应商拒绝 ([#681](https://github.com/ArcReel/ArcReel/issues/681)) ([1c71c18](https://github.com/ArcReel/ArcReel/commit/1c71c18e9581544398645d080bf622b5c9ba4ca3))
* **settings:** 修复调用端点选择器无法滚动到底部,端点名称支持中文/越南语显示 ([#706](https://github.com/ArcReel/ArcReel/issues/706)) ([815b296](https://github.com/ArcReel/ArcReel/commit/815b29669d65c54b64d0747555ca37655beb583c))
* **settings:** 修复配置提醒没有即时显示的问题 ([#703](https://github.com/ArcReel/ArcReel/issues/703)) ([375e18f](https://github.com/ArcReel/ArcReel/commit/375e18f9051b388f1d37c5639f9cc499acbde476))
* **tasks:** worker 吸收 inflight 任务取消的 CancelledError，主循环不再退出 ([#679](https://github.com/ArcReel/ArcReel/issues/679)) ([f3a3b00](https://github.com/ArcReel/ArcReel/commit/f3a3b0085575eabbbba0642eec4c787948d644bd))
* **tasks:** 任务恢复防双扣费 + 调度器加固（[#647](https://github.com/ArcReel/ArcReel/issues/647) + 代码审查 15 项收敛） ([#663](https://github.com/ArcReel/ArcReel/issues/663)) ([ed9c359](https://github.com/ArcReel/ArcReel/commit/ed9c359ca417b3eca88a46062296c4f2a9515879))
* **video-backends:** 中转视频后端按 status_code 闸门重试,确定性 4xx 秒级失败 ([#688](https://github.com/ArcReel/ArcReel/issues/688)) ([a377d0d](https://github.com/ArcReel/ArcReel/commit/a377d0d26179f0e6314a858b30b5bd13858859f6))
* 编辑自定义供应商未改 apikey 时测试连接复用已存储凭证 ([#671](https://github.com/ArcReel/ArcReel/issues/671)) ([b592915](https://github.com/ArcReel/ArcReel/commit/b59291504248004a8016d8a484c7125598dfe83e))


### ⚡ 性能优化

* **reference-video:** 优化参考视频生成性能 ([#689](https://github.com/ArcReel/ArcReel/issues/689)) ([cbf7e8f](https://github.com/ArcReel/ArcReel/commit/cbf7e8fbf4af666bed24aba2661e4bfc9ad82f8d))


### ♻️ 重构

* **pricing:** 声明式定价重构——定价并进 ModelInfo、按 kind 派发 ([#682](https://github.com/ArcReel/ArcReel/issues/682)) ([b7efac2](https://github.com/ArcReel/ArcReel/commit/b7efac2a4be94c644ea904a3fa8d46762e7b1a43)), closes [#670](https://github.com/ArcReel/ArcReel/issues/670)


### 📚 文档

* **provider:** 视频 API 协议适配调研 + 凭证/定价 ADR + 术语表 ([#678](https://github.com/ArcReel/ArcReel/issues/678)) ([a5cbc7a](https://github.com/ArcReel/ArcReel/commit/a5cbc7aca1ca91bc43621a90d1b449c3a1af5e30))
* **tts:** 旁白配音设计记录与供应商调研(CONTEXT + ADR 0010) ([#705](https://github.com/ArcReel/ArcReel/issues/705)) ([0c40539](https://github.com/ArcReel/ArcReel/commit/0c40539c3637bfd90704bccc275108f8d0930239))

## [0.15.2](https://github.com/ArcReel/ArcReel/compare/v0.15.1...v0.15.2) (2026-05-26)


### 🐛 Bug 修复

* **assistant:** "/" 唤起 skills 列表识别 content_mode 变体文件 ([#625](https://github.com/ArcReel/ArcReel/issues/625)) ([4c541f0](https://github.com/ArcReel/ArcReel/commit/4c541f0ffa69cfa88edeb0e55f741ab07a6a5687))
* **project:** 中文标题不再塌成 slug 作为项目显示名 ([#641](https://github.com/ArcReel/ArcReel/issues/641)) ([5936c44](https://github.com/ArcReel/ArcReel/commit/5936c448b65fc64eb92c711c6c78162dab7a3888))
* **reference-video:** support wrapped asset mentions ([#596](https://github.com/ArcReel/ArcReel/issues/596)) ([48b2484](https://github.com/ArcReel/ArcReel/commit/48b24847ebda11d6f3d53eb65b910a8c4941aed5))
* **script:** 清理 schema 冗余字段,修复 novel 注入与 ShotList null 崩溃 ([#644](https://github.com/ArcReel/ArcReel/issues/644)) ([6662c75](https://github.com/ArcReel/ArcReel/commit/6662c75e59ebd5741fc1268e2c881db63e092509))
* **skill:** pr-ai-review-loop round_count 只在 HEAD 切换时计数 ([#627](https://github.com/ArcReel/ArcReel/issues/627)) ([2cf9173](https://github.com/ArcReel/ArcReel/commit/2cf9173ecbd95cd52ccb2f9f209c67d9bbc049c9))
* **tasks:** 任务队列死锁修复 ([#640](https://github.com/ArcReel/ArcReel/issues/640)) + 代码审查 8 处缺陷收敛 ([#646](https://github.com/ArcReel/ArcReel/issues/646)) ([387456a](https://github.com/ArcReel/ArcReel/commit/387456afbebb37503a0137067ea43a8e06ff667e))
* **video:** OpenAI 后端 resolution=None 时按 aspect_ratio 兜底 size ([#645](https://github.com/ArcReel/ArcReel/issues/645)) ([6926f59](https://github.com/ArcReel/ArcReel/commit/6926f59fc6d792530487095afc1b4e2d0c3d1b43))


### 📚 文档

* **adr:** 队列卡死与取消语义的设计决策（0006 + 0007） ([#628](https://github.com/ArcReel/ArcReel/issues/628)) ([f9455b1](https://github.com/ArcReel/ArcReel/commit/f9455b1227de3cc364cf7c52a44a821d69e04dc2))

## [0.15.1](https://github.com/ArcReel/ArcReel/compare/v0.15.0...v0.15.1) (2026-05-23)


### 🐛 Bug 修复

* **custom-providers:** classify vidu models by media type ([#597](https://github.com/ArcReel/ArcReel/issues/597)) ([4e4a5f0](https://github.com/ArcReel/ArcReel/commit/4e4a5f0e76e38295c202f719645545e0616b9a1d))
* **frontend:** 任务失败通知不再在切走再回项目时重弹 ([#619](https://github.com/ArcReel/ArcReel/issues/619)) ([4cfc3fa](https://github.com/ArcReel/ArcReel/commit/4cfc3fa3d6684f52df231a119c6702570303526c))
* **logging:** 日志目录搬出 projects 根 + 加固迁移与 agent 沙箱 ([#620](https://github.com/ArcReel/ArcReel/issues/620)) ([4b17958](https://github.com/ArcReel/ArcReel/commit/4b17958b6afb3b34002352a450629c69eab17d22))


### ♻️ 重构

* **project_manager:** 剧本保存校验单一守卫点（「不更坏」语义） ([#606](https://github.com/ArcReel/ArcReel/issues/606)) ([9a7486d](https://github.com/ArcReel/ArcReel/commit/9a7486d901b92c569abffa97fc3749dfb49ad1a7))
* **sse:** 把会话/项目事件流深化到 async 上下文管理器背后 ([#613](https://github.com/ArcReel/ArcReel/issues/613)) ([#617](https://github.com/ArcReel/ArcReel/issues/617)) ([46d8f22](https://github.com/ArcReel/ArcReel/commit/46d8f22dd55f0b927fa3d04f3619b4a44b62e8d8))
* 收敛 provider 解析为深模块 + legacy provider 名一次性迁移 ([#599](https://github.com/ArcReel/ArcReel/issues/599)) ([#600](https://github.com/ArcReel/ArcReel/issues/600)) ([dccf220](https://github.com/ArcReel/ArcReel/commit/dccf2207029f7cf77df412f15d4d3f2b134f523e))
* 收敛资源路径与剧本字段名形状常量到单一真相源 ([#611](https://github.com/ArcReel/ArcReel/issues/611)) ([#616](https://github.com/ArcReel/ArcReel/issues/616)) ([7a8be58](https://github.com/ArcReel/ArcReel/commit/7a8be58f4999c2351ca1bee61aeddc0239269443))


### 📚 文档

* **adr:** ADR-0002 不更坏语义 + ADR-0003 Agent JSON 工具 ([#605](https://github.com/ArcReel/ArcReel/issues/605)) ([65265d5](https://github.com/ArcReel/ArcReel/commit/65265d5d99e8f570377bcc0bdc928d9490f207d4))
* **adr:** ADR-0004 导入修复留在 archive + 统一入口术语替换 ([#610](https://github.com/ArcReel/ArcReel/issues/610)) ([f43fbf8](https://github.com/ArcReel/ArcReel/commit/f43fbf88eea9d1ef4367b5100f15c10a57033e43))
* **adr:** ADR-0005 SSE 流走 async 上下文管理器收清理 ([#614](https://github.com/ArcReel/ArcReel/issues/614)) ([148d539](https://github.com/ArcReel/ArcReel/commit/148d539c63a05b98f8cc236f6149a8b9c02d01b4))
* 新增 CONTEXT 术语表与 ADR-0001（provider 解析走查） ([b4c1286](https://github.com/ArcReel/ArcReel/commit/b4c12869389e68128ed2f65136ea728face726b4))
* 核实并清理过时设计文档 ([#595](https://github.com/ArcReel/ArcReel/issues/595)) ([7cae4cc](https://github.com/ArcReel/ArcReel/commit/7cae4ccd6fc963a891fa59497f035e4f65c34d54))

## [0.15.0](https://github.com/ArcReel/ArcReel/compare/v0.14.0...v0.15.0) (2026-05-20)


### ✨ 新功能

* **ark:** 火山方舟支持 Agent Plan 和 Coding Plan 端点 ([#566](https://github.com/ArcReel/ArcReel/issues/566)) ([db4617f](https://github.com/ArcReel/ArcReel/commit/db4617fe5399c0e0f9def3cf0756e324c537e29c))
* **logs:** 日志持久化（7d） + 日志下载 ([#576](https://github.com/ArcReel/ArcReel/issues/576)) ([bc9424f](https://github.com/ArcReel/ArcReel/commit/bc9424f8984a8b8813b959a9747d641411d92f1f))
* **notification:** 后台任务失败统一可点击回跳通知 ([#399](https://github.com/ArcReel/ArcReel/issues/399)) ([#587](https://github.com/ArcReel/ArcReel/issues/587)) ([b8b9b1d](https://github.com/ArcReel/ArcReel/commit/b8b9b1d67a0976548334d0211e321b6e62fdb385))
* **script:** 提升剧本 image_prompt / video_prompt 输出质量 ([#581](https://github.com/ArcReel/ArcReel/issues/581)) ([74d1356](https://github.com/ArcReel/ArcReel/commit/74d1356c904021b2960d020ee804e7891335202a))
* **usage:** track assistant usage costs ([#593](https://github.com/ArcReel/ArcReel/issues/593)) ([8828121](https://github.com/ArcReel/ArcReel/commit/8828121a1f9fe21896f6e5b2d6cda5e668dabe4d))


### 🐛 Bug 修复

* agent_credential_repo delete 不存在 ID 时返回 404 ([#577](https://github.com/ArcReel/ArcReel/issues/577)) ([688da37](https://github.com/ArcReel/ArcReel/commit/688da37e1b39f2b14f31b40be8f221b4232a2352))
* **archive:** reference_video 导入对齐 narration 的引用资产自愈 ([#586](https://github.com/ArcReel/ArcReel/issues/586)) ([2d795bd](https://github.com/ArcReel/ArcReel/commit/2d795bd1a3723781283bdefcbd774ad66b2b3255))
* **archive:** 归档导入遍历 reference_video 的 video_units ([#333](https://github.com/ArcReel/ArcReel/issues/333)) ([#584](https://github.com/ArcReel/ArcReel/issues/584)) ([924f26e](https://github.com/ArcReel/ArcReel/commit/924f26e6cfb251606ae2285c576170f5baf08448))
* **ci:** lowercase GHCR image name + Codecov ([#567](https://github.com/ArcReel/ArcReel/issues/567)) ([82e8d3a](https://github.com/ArcReel/ArcReel/commit/82e8d3ad7e443bc5afbb1d09b398134fb62edc20))
* **compose-video:** 修复 ffmpeg 滤镜图与 fps fallback 多处问题 ([#578](https://github.com/ArcReel/ArcReel/issues/578)) ([c4294a7](https://github.com/ArcReel/ArcReel/commit/c4294a723ac00d0bb7ee071ba6dba041fbf50f9c))
* **concurrency:** 统一 ProjectManager 读-改-写锁语义（跨 script / project） ([#585](https://github.com/ArcReel/ArcReel/issues/585)) ([973adf6](https://github.com/ArcReel/ArcReel/commit/973adf6d63bcf0c6775f1745859ced62a7bf127a))
* issue [#589](https://github.com/ArcReel/ArcReel/issues/589) follow-up（reference_videos i18n + 两处既有行为修正） ([#590](https://github.com/ArcReel/ArcReel/issues/590)) ([be9c136](https://github.com/ArcReel/ArcReel/commit/be9c1362710b2e275b027058f591f29fae2eed9d))
* propagate image usage tokens ([#570](https://github.com/ArcReel/ArcReel/issues/570)) ([7e2eb8f](https://github.com/ArcReel/ArcReel/commit/7e2eb8f209bde44ad241c82f3a1e116227ddc301))
* **reference-videos:** 消除 episode↔script_file 绑定的跨锁竞态 ([#589](https://github.com/ArcReel/ArcReel/issues/589)) ([#591](https://github.com/ArcReel/ArcReel/issues/591)) ([825bb06](https://github.com/ArcReel/ArcReel/commit/825bb060868ddf18db33eaeb1607cee486451142))
* **script:** 注入 episode 到 prompt 并兜底重写 ID 前缀，避免跨集分镜覆盖 ([#574](https://github.com/ArcReel/ArcReel/issues/574)) ([#579](https://github.com/ArcReel/ArcReel/issues/579)) ([4929636](https://github.com/ArcReel/ArcReel/commit/49296360f2726d0cb47012c9dc3932853474d899))
* **timezone:** 容器/后端/前端时间统一为 TZ-aware ([#582](https://github.com/ArcReel/ArcReel/issues/582)) ([e3080a8](https://github.com/ArcReel/ArcReel/commit/e3080a8cee285fed0eae66dfbb59cdcb9bf25327))
* **usage:** support multi-currency cost totals ([#588](https://github.com/ArcReel/ArcReel/issues/588)) ([24cbd41](https://github.com/ArcReel/ArcReel/commit/24cbd41410abcbb780fb7076f55922cac16ed59f))
* 透传 Claude SDK stderr，让 Windows agent 启动失败可诊断 ([#573](https://github.com/ArcReel/ArcReel/issues/573)) ([8d24788](https://github.com/ArcReel/ArcReel/commit/8d24788e41ee66a9fd683589f903b31f441aac4a))


### ♻️ 重构

* **agent-runtime:** 拆分 _is_path_allowed 为 dispatch + 读/写 sub-check ([#583](https://github.com/ArcReel/ArcReel/issues/583)) ([18326bf](https://github.com/ArcReel/ArcReel/commit/18326bfbf17b2dc9bf547a3438c56aec115c5222))
* **project_manager:** update_project 返回迁移后 project，消除写后二次读 ([#589](https://github.com/ArcReel/ArcReel/issues/589)) ([#592](https://github.com/ArcReel/ArcReel/issues/592)) ([19771fa](https://github.com/ArcReel/ArcReel/commit/19771fac3234d45414807c01cc828e283aac746d))


### 📚 文档

* **changelog:** 0.14.0 加上沙箱升级须知 ([22f364c](https://github.com/ArcReel/ArcReel/commit/22f364cb0eb57bb540b0b1d92ab805d31972bd2f))
* 同步 README/getting-started/CLAUDE/AGENTS 反映 Vidu 与沙箱现状 ([#565](https://github.com/ArcReel/ArcReel/issues/565)) ([5a0067b](https://github.com/ArcReel/ArcReel/commit/5a0067b0bda180bcfc27a0fe6e2458dcd8abab20))

## [0.14.0](https://github.com/ArcReel/ArcReel/compare/v0.13.0...v0.14.0) (2026-05-18)


### ⚠️ 升级须知（Breaking）

本版本默认启用 **Agent Bash 沙箱**（[#521](https://github.com/ArcReel/ArcReel/issues/521)），server 启动期会强制探测；缺依赖或宿主内核策略禁用 user namespace 时会以 `SANDBOX_UNAVAILABLE` / `SANDBOX_BWRAP_BROKEN` 启动失败，启动日志会直接打印对应修复命令。

**Docker 部署需要在 compose 放开沙箱所需的权限**：

```yaml
security_opt:
  - seccomp:unconfined
  - apparmor:unconfined
cap_add:
  - NET_ADMIN
```

Ubuntu 24.04+ 宿主还需在**宿主机**（不是容器内）关一次 AppArmor user namespace 限制：

```bash
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
echo "kernel.apparmor_restrict_unprivileged_userns=0" | sudo tee /etc/sysctl.d/60-arcreel-bwrap.conf
```

macOS 沿用系统 `sandbox-exec` 无需改动；Windows 原生自动降级到 Bash 命令白名单。


### ✨ 新功能

* **agent:** Agent 支持配置多供应商 + 预设默认供应商 ([#507](https://github.com/ArcReel/ArcReel/issues/507)) ([5e94cc2](https://github.com/ArcReel/ArcReel/commit/5e94cc2c121e9846765de1a10a1abd11a7f0ac73))
* **agent:** 启用 Agent Bash 沙箱隔离，安全加固并提高 bash 自由度 + provider secrets 下线 os.environ ([#521](https://github.com/ArcReel/ArcReel/issues/521)) ([3a9ed4f](https://github.com/ArcReel/ArcReel/commit/3a9ed4f47ff9983c52cfea204e8a1adc0ae9553a))
* **branding:** centralize product name via BRAND config + i18n placeholder ([#494](https://github.com/ArcReel/ArcReel/issues/494)) ([c93b0c9](https://github.com/ArcReel/ArcReel/commit/c93b0c9d33533096273c20c21bc8947949950a75))
* env-driven runtime configuration and graceful fallbacks ([#515](https://github.com/ArcReel/ArcReel/issues/515)) ([c042541](https://github.com/ArcReel/ArcReel/commit/c0425418c0df1a4d88c703994fea099c55d1f97b))
* **profile:** 按 content_mode 动态注入 agent 配置（narration/drama 变体） ([#546](https://github.com/ArcReel/ArcReel/issues/546)) ([1030a29](https://github.com/ArcReel/ArcReel/commit/1030a29b5ad0c6e1bffe0cf45d65552d5d2b28db))
* **thumbnail:** add extract_video_last_frame helper ([#539](https://github.com/ArcReel/ArcReel/issues/539)) ([06be4da](https://github.com/ArcReel/ArcReel/commit/06be4daba640c78d5d030efbbacc0c9ba5fde5de))


### 🐛 Bug 修复

* **agent-profile:** skill 脚本路径围栏 + 文档对齐 ([#548](https://github.com/ArcReel/ArcReel/issues/548)) ([b4f4dd2](https://github.com/ArcReel/ArcReel/commit/b4f4dd2aa6cd3b39a6c2ecf05316c0592da441e4))
* **agent:** bwrap sandbox 修复 + agent profile 同步机制（manifest+sha256） ([#535](https://github.com/ArcReel/ArcReel/issues/535)) ([3a17c12](https://github.com/ArcReel/ArcReel/commit/3a17c12fe772a39ff0f8f810d248e8e01dc51334))
* **agent:** normalize_drama_script 传入 project_name 让项目级文本后端生效 ([#529](https://github.com/ArcReel/ArcReel/issues/529)) ([f1aeddb](https://github.com/ArcReel/ArcReel/commit/f1aeddb37b9dfc12442ef8a68158501e7b4e6acb))
* **agent:** 配置 no-op WorktreeCreate hook 避免派发 subagent 报错 ([#533](https://github.com/ArcReel/ArcReel/issues/533)) ([0c9bff0](https://github.com/ArcReel/ArcReel/commit/0c9bff067a3b960e765645c2a05df0836aa0d50f))
* **ark:** 显式注入 Seedream size 参数，修复项目 aspect_ratio 失效 ([#514](https://github.com/ArcReel/ArcReel/issues/514)) ([a397a98](https://github.com/ArcReel/ArcReel/commit/a397a98d61a37c579bf595f030b00067ef28e3b6))
* **auth:** 前端根据 AUTH_ENABLED 状态判断是否跳过登录 ([#522](https://github.com/ArcReel/ArcReel/issues/522)) ([70c3394](https://github.com/ArcReel/ArcReel/commit/70c33942ad44de6a1d15bd1ff682e08eb0c6a34b))
* **compose-video:** zero-align concatenated episode output ([#537](https://github.com/ArcReel/ArcReel/issues/537)) ([efc79a3](https://github.com/ArcReel/ArcReel/commit/efc79a3233a85ae56777e3421387e85ade0b4de7))
* **copilot:** guard IME Enter in agent input ([#516](https://github.com/ArcReel/ArcReel/issues/516)) ([7c94a57](https://github.com/ArcReel/ArcReel/commit/7c94a57924e6d6109278f1f20cd2b0dd9f10f5ba))
* **deps:** 添加 socksio 以兼容系统 SOCKS 代理 ([#527](https://github.com/ArcReel/ArcReel/issues/527)) ([8183b40](https://github.com/ArcReel/ArcReel/commit/8183b40edef864a8dc3d13ac3cfbda6814830783))
* **docker:** skip corepack download prompt in non-TTY builds ([#513](https://github.com/ArcReel/ArcReel/issues/513)) ([06d234b](https://github.com/ArcReel/ArcReel/commit/06d234bae93bec9645e76039429c35be09e5bdd0))
* **env_init:** 沙箱内 .env 不可读时降级，不阻断 import lib ([#526](https://github.com/ArcReel/ArcReel/issues/526)) ([4f59796](https://github.com/ArcReel/ArcReel/commit/4f597969157dab6207a67ccfa55a2fe7bf561dca))
* **grid:** 修复宫格图重新生成后 UI 仍显示旧图 ([#524](https://github.com/ArcReel/ArcReel/issues/524)) ([7197fe1](https://github.com/ArcReel/ArcReel/commit/7197fe139838e2243302b3d94f50c48fc6f18ff8))
* **scenes:** drama PATCH 改用 script-scenes 路径，避开与项目场景资产 CRUD 撞车 ([#530](https://github.com/ArcReel/ArcReel/issues/530)) ([5e82fb2](https://github.com/ArcReel/ArcReel/commit/5e82fb2cf1c085fd7c7f7d8877ff85457a879cca))
* **skills:** clarify compose-video content mode ([#549](https://github.com/ArcReel/ArcReel/issues/549)) ([d141505](https://github.com/ArcReel/ArcReel/commit/d1415057b34fca72a46f05d1343d03b456902822))
* **status:** 按产物倒序判定阶段，overview 降级为软信号 ([#505](https://github.com/ArcReel/ArcReel/issues/505)) ([0bee4f7](https://github.com/ArcReel/ArcReel/commit/0bee4f7deb18f9dca6df4756bfb2975e121580e0))
* **storyboard:** 分镜详情面板恢复关联资产展示与编辑 ([#547](https://github.com/ArcReel/ArcReel/issues/547)) ([5f2d3e7](https://github.com/ArcReel/ArcReel/commit/5f2d3e747f33f7f85e2c9ae126a62d5f77198204))
* **ui:** 修复模型选择下拉被外部组件裁剪 ([#531](https://github.com/ArcReel/ArcReel/issues/531)) ([f95b4d3](https://github.com/ArcReel/ArcReel/commit/f95b4d3baac3437d696c4b0935d3ad9d5fc9ea8b))
* **windows:** 修复创建项目崩溃 + 清理 POSIX-only 假设 ([#560](https://github.com/ArcReel/ArcReel/issues/560)) ([e99d4d4](https://github.com/ArcReel/ArcReel/commit/e99d4d44d8b9a82ffb89ff33f632b87f80af49cb))


### ⚡ 性能优化

* **i18n:** 按需加载 i18n namespace，首屏 bundle -56KB gzip ([#489](https://github.com/ArcReel/ArcReel/issues/489)) ([#502](https://github.com/ArcReel/ArcReel/issues/502)) ([0fdbb5a](https://github.com/ArcReel/ArcReel/commit/0fdbb5a2040ef4fc87535532973df4d882efb789))


### ♻️ 重构

* **agent:** 技能脚本迁移到 SDK 进程内 MCP 工具，沙箱与路径收紧 ([#528](https://github.com/ArcReel/ArcReel/issues/528)) ([7629173](https://github.com/ArcReel/ArcReel/commit/7629173eeb1132d779f849432ab103c23340faa9))
* **content-mode:** 拆分 content_mode 与 generation_mode 两条独立维度 ([#542](https://github.com/ArcReel/ArcReel/issues/542)) ([#543](https://github.com/ArcReel/ArcReel/issues/543)) ([5059767](https://github.com/ArcReel/ArcReel/commit/505976714fe6cd5c72cd54e3a9176aff4e87c494))
* **env:** make vertex_keys + agent_profile paths env-configurable ([#523](https://github.com/ArcReel/ArcReel/issues/523)) ([046d0c0](https://github.com/ArcReel/ArcReel/commit/046d0c041031704cda3334f14790d4115894e381))
* **source_loader:** PDF 抽取由 PyMuPDF 迁移到 pdf_oxide ([#506](https://github.com/ArcReel/ArcReel/issues/506)) ([c0f77b7](https://github.com/ArcReel/ArcReel/commit/c0f77b7d989d2b88deecce14348f56bcb75c3c1d))
* **ui:** 抽 ModalShell + GlassModal/Popover 收拢 13 处弹窗 chrome ([#470](https://github.com/ArcReel/ArcReel/issues/470), [#487](https://github.com/ArcReel/ArcReel/issues/487)) ([#500](https://github.com/ArcReel/ArcReel/issues/500)) ([24f1816](https://github.com/ArcReel/ArcReel/commit/24f18169aa2cce5128bbed5cee159d09487238b1))


### 📚 文档

* **skills:** clarify MCP-only execution for migrated skills ([#540](https://github.com/ArcReel/ArcReel/issues/540)) ([fa97ca0](https://github.com/ArcReel/ArcReel/commit/fa97ca06a51b392b0f9fcb58263cbdba4faa34b6))

## [0.13.0](https://github.com/ArcReel/ArcReel/compare/v0.12.0...v0.13.0) (2026-05-10)


### ✨ 新功能

* **backends:** 调用 provider SDK 前打印生成参数日志 ([#461](https://github.com/ArcReel/ArcReel/issues/461)) ([ec86bb4](https://github.com/ArcReel/ArcReel/commit/ec86bb488132f3ae4280b29ace3f79aa1ac0d244))
* **i18n:** add Vietnamese (vi) language support ([#469](https://github.com/ArcReel/ArcReel/issues/469)) ([7337388](https://github.com/ArcReel/ArcReel/commit/7337388d512102ccda96bd39e196031a2ef863ac))
* **projects:** 项目大厅全新 ui 设计 ([#478](https://github.com/ArcReel/ArcReel/issues/478)) ([5942c68](https://github.com/ArcReel/ArcReel/commit/5942c6842f33321991721580d9e708d90b878130))
* **prompt:** agent / prompt 优化 — 拆分节奏 + 分镜视频提示词 + 资产提示词 ([#475](https://github.com/ArcReel/ArcReel/issues/475)) ([ee96c5e](https://github.com/ArcReel/ArcReel/commit/ee96c5ebe6fc644408016c75fc173007a2e276b3))
* SDK 0.1.73 eager session_store_flush + reconnect dedup 修复 ([#472](https://github.com/ArcReel/ArcReel/issues/472)) ([cd02afa](https://github.com/ArcReel/ArcReel/commit/cd02afa111840b2f3eefbc01020536003f410a3b))
* **sdk:** claude-agent-sdk 升级到 0.1.76 并适配部分新特性 ([#473](https://github.com/ArcReel/ArcReel/issues/473)) ([e8f529c](https://github.com/ArcReel/ArcReel/commit/e8f529cca0b5eb25ee46119bdbaf904949238fc7))
* **settings:** 全局设置页 / 项目设置页 / 新建项目向导 全新 Darkroom UI ([#483](https://github.com/ArcReel/ArcReel/issues/483)) ([ff19412](https://github.com/ArcReel/ArcReel/commit/ff1941218c491fe3d2f112ab709cb4cea29d57a9))
* **ui:** Agent 面板支持拖拽调宽 + 大厅 ui 优化 ([#492](https://github.com/ArcReel/ArcReel/issues/492)) ([f3a9ce9](https://github.com/ArcReel/ArcReel/commit/f3a9ce97a3ee47bfa6e34859e5d82464848e0973))
* **ui:** 资产库改版 + 前端 Darkroom UI 收尾 (v0.13.0 RC) ([#486](https://github.com/ArcReel/ArcReel/issues/486)) ([a84fdce](https://github.com/ArcReel/ArcReel/commit/a84fdcecd8795d0b418043257f34431640c3d136))
* **vidu:** 集成 Vidu 作为预置图片+视频供应商 ([#481](https://github.com/ArcReel/ArcReel/issues/481)) ([fc9deee](https://github.com/ArcReel/ArcReel/commit/fc9deee4b3bef3031cb707893ef735e72bcf004b))
* **workbench:** 项目工作台全新 UI ([#471](https://github.com/ArcReel/ArcReel/issues/471)) ([ff9ea3b](https://github.com/ArcReel/ArcReel/commit/ff9ea3b94a72bbc5f004be33ad63962e8f757c58))
* 模型选择器支持搜索 ([#458](https://github.com/ArcReel/ArcReel/issues/458)) ([713f8c4](https://github.com/ArcReel/ArcReel/commit/713f8c4fecc1f2f6705689ff6f69cd34c060176c))
* 视频可选时长 (supported_durations) 系统性重设计 ([#468](https://github.com/ArcReel/ArcReel/issues/468)) ([39c8feb](https://github.com/ArcReel/ArcReel/commit/39c8feb23aafc228c586b2399d922cdca7c27136))


### 🐛 Bug 修复

* **ci:** 用 packageManager 字段固定 pnpm 版本，修复 Docker 构建失败 ([#482](https://github.com/ArcReel/ArcReel/issues/482)) ([f7fbbae](https://github.com/ArcReel/ArcReel/commit/f7fbbae4e488ebfbcfdfe8f16634c63b82b530ec))
* **image-dual-select:** 渐进式渲染 + 按 capability 过滤选项 ([#459](https://github.com/ArcReel/ArcReel/issues/459)) ([911be8f](https://github.com/ArcReel/ArcReel/commit/911be8f2aea14a6c98c831a13f71c82aaca5e867))
* **openai-text:** 代理返回非 JSON 时降级到 Instructor ([#493](https://github.com/ArcReel/ArcReel/issues/493)) ([13a321c](https://github.com/ArcReel/ArcReel/commit/13a321c2646168f5902c06bc3448f2691e9addd5))
* **timeline:** 修正费用币种展示与视频全屏宽高比 ([#480](https://github.com/ArcReel/ArcReel/issues/480)) ([123a70f](https://github.com/ArcReel/ArcReel/commit/123a70f4f36a395a7163121a2bf8aed68de8088a))
* **timeline:** 分镜卡片状态独占首行 + ShotDetail 三栏修复溢出滚动 ([#491](https://github.com/ArcReel/ArcReel/issues/491)) ([e38905d](https://github.com/ArcReel/ArcReel/commit/e38905dba7c204a9d8f8248cfbecbfb1b89e3a24))
* **vidu:** 连接测试用数字 task id 避免 400 CODEC parse error ([#490](https://github.com/ArcReel/ArcReel/issues/490)) ([61486f4](https://github.com/ArcReel/ArcReel/commit/61486f48997ea9f9a5d6236eda8fc7815a5e5ccd))
* **workbench:** 修复新版工作台 SSE 项目事件后的自动定位 ([#477](https://github.com/ArcReel/ArcReel/issues/477)) ([ef83144](https://github.com/ArcReel/ArcReel/commit/ef83144f79b18c5260f692152f6161c461aa6480))

## [0.12.0](https://github.com/ArcReel/ArcReel/compare/v0.11.1...v0.12.0) (2026-05-02)


### ✨ 新功能

* **agent-config:** 智能体配置支持模型发现与复用自定义供应商 ([#455](https://github.com/ArcReel/ArcReel/issues/455)) ([ce14ea5](https://github.com/ArcReel/ArcReel/commit/ce14ea51307fd1b6ca47107cb744cf14c936dac3))
* **cost:** OpenAI 图片改为 token-based 计费 ([#448](https://github.com/ArcReel/ArcReel/issues/448)) ([5939dcf](https://github.com/ArcReel/ArcReel/commit/5939dcf80f9b7e7e889eac30e2a26218e2efac55))
* **providers:** OpenAI 新增 GPT-5.5 与 GPT Image 2 ([#446](https://github.com/ArcReel/ArcReel/issues/446)) ([86211fe](https://github.com/ArcReel/ArcReel/commit/86211fe2d4399042324c4c51571baff77f27335a))
* **session-store:** 会话记录改为 DB 存储 ([#451](https://github.com/ArcReel/ArcReel/issues/451)) ([f9407f0](https://github.com/ArcReel/ArcReel/commit/f9407f07978245ec80c09023c51ff966aa5744a9))


### 🐛 Bug 修复

* **image-backends:** 处理 OpenAI/Ark 空 response.data 避免 IndexError ([#452](https://github.com/ArcReel/ArcReel/issues/452)) ([05702e2](https://github.com/ArcReel/ArcReel/commit/05702e288d920bb89d5199964a9f0e44038aff07))


### ♻️ 重构

* **custom-provider:** 收敛 endpoint 元数据为运行时 catalog API ([#450](https://github.com/ArcReel/ArcReel/issues/450)) ([2858e52](https://github.com/ArcReel/ArcReel/commit/2858e52d5be5c58e5aee3a397a73bedf892c41e9)), closes [#414](https://github.com/ArcReel/ArcReel/issues/414)
* **custom-provider:** 视频模型默认 endpoint 改为 openai-video ([#453](https://github.com/ArcReel/ArcReel/issues/453)) ([225c0b1](https://github.com/ArcReel/ArcReel/commit/225c0b170f457e795079833e8ccc3cdd6430896a))
* **images:** OpenAI 图像生成端点支持按文生图（T2I） / 图生图（I2I）分别配置 ([#454](https://github.com/ArcReel/ArcReel/issues/454)) ([66be8c6](https://github.com/ArcReel/ArcReel/commit/66be8c61c4f4b405b5a286809a00745cacfa06ba))


### 📚 文档

* 限定 uvicorn --reload-dir 避免扫描 node_modules ([d4aa6a2](https://github.com/ArcReel/ArcReel/commit/d4aa6a2554a185a074a55cc7e6971d14c9d8c964))

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
