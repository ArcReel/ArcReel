---
status: proposed
---

# 内置 provider 的 backend 构造收口为声明式表，与自定义 endpoint 表对称但不合并

内置 provider 的 backend 构造（从 provider config + 解析出的 model 组装构造 kwargs）当前在 `server/services/generation_tasks.py` 的 `_get_or_create_video_backend` / `_get_or_create_image_backend` / `_get_or_create_audio_backend` 三处 + `lib/text_backends/factory.py` 各写一遍命令式 `if gemini / elif kling / else`，per-provider 知识全泄漏到调用方——gemini 的 `aistudio|vertex` 双模式、kling 的 JWT 双 secret + `api_model_name` 解耦、各家 base_url 优先级与 dashscope/minimax 文本走 OpenAI-compat 等差异，散落在这四处分支。`PROVIDER_ID_TO_BACKEND` 映射还存在两份（媒体侧与文本侧），已经漂移：文本侧把 dashscope/minimax 认作 `openai`，媒体侧不认。而自定义 provider 侧早已用 `lib/custom_provider/endpoints.py` 的 `ENDPOINT_REGISTRY`（每条 `EndpointSpec` 挂一个 `build_backend` 闭包）+ `factory.py` 两行转发，把同一件事做成一张声明式深表。两套平行写法，是本决策要消除的浅层重复（shallow duplication）。

我们决定把内置侧抬到与自定义侧同一水位，收口为一条「provider config + model → backend」的缝（seam）：① 新建 `lib/backend_assembly/`（落 lib——缝跨内置+自定义两族，且 lib 不能 import server），暴露一个统一入口 `assemble_backend(provider_id, media_type, model_id, ...)`，内部按 `is_custom_provider` 分流到两族适配器；② 内置侧新立一张 `(provider_id, media_type) → ProviderSpec` 表，每条挂一个 build 闭包，与自定义侧 `EndpointSpec` 同构；③ 构造拆成 **async 装载**（查 DB/config，产出 `LoadedConfig` 信封：凭证 overlay + `PROVIDER_REGISTRY` meta + 共享 rate_limiter）/ **sync 构造**（纯闭包读信封拼 backend）两段，sync 段是可脱离 DB 直接单测的深模块核心；④ 自定义侧 `ENDPOINT_REGISTRY` 一行不改，`_create_custom_backend` 的 DB 装载逻辑从 server 下移到 lib，与 text factory 内联的那份重复合一。

**明确不采用**：① **把两族合并成一张表**——两侧凭证模型本质不同（自定义固定 `api_key`+`base_url`，见 `docs/adr/0008`；内置多 secret 定型列，见 `docs/adr/0037`），且 `EndpointSpec` 还承载前端目录字段（video caps、request path、display name）内置侧并不需要；合并要么丢类型要么塞 `Any`。两表共享一个入口（`assemble_backend`），不共享表结构——一道门、门后两个对称房间。② **引入统一 `BackendRequest` 入参值对象**（为让单入口只收一个参数而把两种输入形状抹平）——它没有第二个消费者，只是让两个 build 签名长一样的胶水；删除测试戳穿：删掉它复杂度不会在调用方重现（调用方本就在 `is_custom_provider` 处分流），它还会把 0008/0037 刻意分开的两套凭证模型硬捏进一个类型。内置 build 吃 `LoadedConfig`、自定义 build 吃 `CustomProvider`，各吃各的。③ **内置 spec 用纯声明式数据规格**（凭证映射表 + base_url 策略枚举替代闭包）——对当前约 11 个内置 provider 是过度配置，且 base_url 派生与 `api_model_name` 解耦最终仍需闭包逃生口，纯数据没兑现；闭包派与自定义侧 `EndpointSpec` 范本、`docs/adr/0009` 定价按 `kind` 派发同手法。④ **简单族 provider「缺席即默认」**（查不到 spec 即套用通用简单闭包）——fail-silent，`provider_id` 打错会静默造一个简单 backend 而非报错；改为每个简单 provider 显式登记一行（共享同一个 build 闭包），fail-loud 且表即全貌，代价仅一行声明。

## Consequences

- **新增内置 provider 从「改 4 文件 5 处」降到「加一行」**：简单族加一条 `ProviderSpec`，特例族加一条 + 一个 build 闭包，与自定义侧加一条 `EndpointSpec` 对称；不再触动 `generation_tasks.py` 的三个 `_get_or_create_*` 与 text factory。
- **两份 `PROVIDER_ID_TO_BACKEND` 合并**进 `ProviderSpec` 的 registry 字段（每个 `(provider, media)` 行各自声明映射到哪个 registry backend），漂移在数据结构层面不再可能。
- **构造核心可脱离 DB 单测**：手搓一个 `LoadedConfig` 信封 + model_id 直接断言造出的 backend 构造参数（kling 双 secret 透传、gemini `backend_type` 分叉、dashscope 文本 base_url 派生、kling `api_model_name` 解耦），无需起 DB 或 mock resolver；async 装载段用内存 DB（local-substitutable）测。内置表的校验分两档：`build` 可调用、`(provider, media)` 唯一这类内表自洽检查放 import 期 fail-fast（同 `endpoints.py::_validate_video_caps_declarations`、等量轻）；而「`registry` 名都在媒体后端 registry 里」需 import 全部 `lib.{image,video,text}_backends` 才能断言，为免轻量场景（CLI / 迁移）因 import 本缝而被动拉起全部后端，归入单测（测内 import 全集无碍），不进 import 期。
- **缓存留在调用方**：`_backend_cache` 是 server 执行层的性能关切，不下沉进缝；缝无状态、纯构造，便于并发与测试。
- **范围边界**：缝只管「造 backend 实例」，不干涉任务执行、队列调度、计费等生命周期——`provider_job_id` 持久化见 `docs/adr/0007`、队列调度见 `docs/adr/0010`、pricing 见 `docs/adr/0009`，均不在本缝。
- **与未来插件市场正交**：用户分享/安装第三方供应商适配若落地，是自定义侧 `ENDPOINT_REGISTRY` 的后续独立 epic（`docs/adr/0008` 已为 plugin 指路：plugin 只能接 `api_key`+`base_url`，且需自带「内置 provider 风格」声明才能多字段）。本 ADR 收口的是**内置**侧；「两族不合并」的隔离正为该 epic 保留干净的插槽侧，不在本次范围。
- **本 ADR status=proposed，实现未落地**：`LoadedConfig` / 内置 `ProviderSpec` / `backend_assembly` 等新名字待实现真正落盘后再收入 `CONTEXT.md` 术语表（项目惯例：术语表只记录概念此刻是什么）。任何后续 PR 想合并两表、引入统一 `BackendRequest`、或把内置构造退回命令式散落分支，须先 deprecate 本 ADR。
