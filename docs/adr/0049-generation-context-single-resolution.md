---
status: proposed
---

# 生成任务的 provider 解析产物收口为 GenerationContext：一次解析、按实际 backend 身份取值

生成任务执行时需要的配置产物——各 media lane 的 ProviderModel、resolution（清晰度档位）、supported_durations 等能力上限——由同一次 provider 解析派生，但执行层长期缺一个承载它们的接口：`get_media_generator` 在内部解析后只返回 MediaGenerator，产物被丢弃。下游于是各自取回：图片/视频任务在拿到 generator 后重开 ConfigResolver session 再解析一遍（两次解析之间配置变更会「按 A 构造 backend、按 B 查分辨率」）；视频任务在二次解析失败时静默落到硬编码的 provider/model 元组继续跑；参考视频任务掏 `generator._video_backend` 私有属性、手工 split `project["video_backend"]` 字符串重建 registry provider_id，还要防御「caps 查到的 model 与 backend 实际 model 不一致」（自定义供应商目标 model 被禁用时 `load_custom_backend` 静默回退，按解析意图查能力就会错配）。接口侧则以 `require_image_backend` / `needs_i2i` / `needs_audio` 三个布尔旗标枚举「本次任务要哪些 lane」，且无论声明与否都连带解析另一条 lane——只配了图片供应商的项目跑图片任务，会因视频解析失败而整体报错。

我们决定收口为一个深模块：`server/services/generation_context.py` 暴露唯一入口 `resolve_generation_context(project_name, payload, *, project, user_id, image=, video=, audio=)`，返回不可变的 `GenerationContext`。① **一次解析**：单个 ConfigResolver session 内完成全部声明 lane 的解析与 backend 构造，产物（`generator` + 各 lane 结果值对象）随返回值交付，消费方不再重解析。② **lane 具名声明**：`ImageLaneRequest(capability=...)` / `VideoLaneRequest()` / `AudioLaneRequest()` 传即声明，任务只为自己用到的 lane 付出配置要求与构造成本；未声明 lane 经 property 访问直接抛错（fail-loud，非 Optional 类型）。③ **按实际身份取值**：每条 lane 固定求解顺序——解析 ProviderModel → 经 `assemble_backend`（`docs/adr/0039`）构造 backend → 以 backend 实际的 `.name` / `.model` 查 resolution 与能力。解析意图与实际构造物的错配路径在结构上不存在，消费方无需再做一致性防御。lane 结果同时暴露 `provider_model`（规范 registry 身份）与 `backend_name` / `backend_model`（实际身份）两组字符串字段，不暴露 backend 实例。④ **错误语义**：lane 解析或构造失败原样上抛、整次调用失败，不产出部分结果，也没有跨 provider 的静默兜底；仅能力查询失败降级为空值放行（`docs/adr/0002` 口径）。resolution 保留 None＝不传 SDK 参数的语义（`docs/adr/0019`），video lane 另供 `resolution_or_fallback` 给需要非空值的调用方。⑤ **audio lane 携带 narration voice/speed**：解析逻辑仍在 ConfigResolver，仅在同一 session 内委托取值，语音任务不再为此单开第二个 session。⑥ `resolution_resolver.py` 作为公开模块消失：需 DB 的优先级链（project 覆盖 > legacy > 自定义供应商默认）收编为 `ConfigResolver.resolve_resolution()`，纯查表的 `get_provider_fallback` 移入 `lib/config/resolver.py` 模块级公开函数。

**明确不采用**：① **深化 MediaGenerator 本身承载解析产物**——MediaGenerator 只关心「怎么生成」，混入「怎么选的 provider」是两个关注点的杂物袋；用薄的外层值对象组合两者。② **为 ConfigResolver / ProjectManager / backend 构造开三个注入 Port**——前两者是 local-substitutable 依赖（测试用内存 DB 与 tmp_path 真实例，不 mock），只有 backend 构造是真外部缝且 `assemble_backend` 已是现成 adapter 对；为只有一个生产实现的依赖开 Port 是单 adapter 假想缝。③ **部分成功**——声明的 lane 解析失败却返回残缺 context，会把配置错误伪装成部分正常状态。④ **能力查询失败也原子失败**——能力是已选定 provider/model 的元数据，缺失不代表不可调用，原子失败会倒退 `docs/adr/0002` 的「不更坏」语义。⑤ **保留解析失败时的硬编码 provider/model 兜底**——它只因「第二次解析可能失败而 backend 已构造好」存在；单次解析下前提消失，留着反而在用户明确配置某供应商时静默切到无关供应商跑出计费与内容。

## Consequences

- **消费方一次调用拿全**：generation_tasks 内 5 个站点（storyboard/character/design/grid/video）、reference_video_tasks、resume_executor 的「构造 generator + 另行重解析 / 掏私有属性 / 手工 split」全部消失；reference_video 的 caps 一致性防御分支成为死代码删除。
- **行为变更**：图片任务不再要求视频供应商配置（反之亦然）；视频解析失败从「静默换成硬编码模型跑」变为「任务失败、留痕可查」；grid 落盘的 provider/model 元数据改记 backend 实际身份。
- **边界不动**：解析仍只在执行层发生（`docs/adr/0001`）；resolver 优先级 payload > project > 全局默认不变；`_backend_cache` 仍是 server 执行层关切（`docs/adr/0039`「缓存留在调用方”），随本模块迁移并保留失效入口供供应商配置变更路由调用。
- **测试面收口**：消费方测试改为替换 `resolve_generation_context` 单点、以 frozen dataclass 直接拼装假 context；模块自身测试用真实测试 DB + tmp_path + fake backend 走接口断言，不断言私有属性。原先跨多个测试文件拼装 resolve/backend 组合 monkeypatch 的模式作废。
- **cost_estimation 不是消费方**：费用预估只解析不构造 backend，改为直接调用 `ConfigResolver.resolve_image_backend / resolve_video_backend` 消除其手工重演优先级的解析副本，保留解析失败降级 unknown 的展示语义。
- **本 ADR status=proposed，实现未落地**：`GenerationContext` / lane 请求与结果类型等新名字待实现真正落盘后再收入 `CONTEXT.md` 术语表（项目惯例：术语表只记录概念此刻是什么）。后续 PR 若要回退到消费方自行重解析、引入部分成功语义、或恢复跨 provider 兜底，须先 deprecate 本 ADR。
