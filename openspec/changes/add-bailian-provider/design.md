## Context

ArcReel 已具备较成熟的多供应商抽象：文本、图像、视频分别通过 `text_backends/`、`image_backends/`、`video_backends/` 目录下的独立后端实现，并通过 `lib/config/registry.py` 统一暴露供应商元数据与模型能力。当前系统已支持 OpenAI、Gemini、Ark、Grok 等供应商，但缺少面向国内网络环境友好的国产供应商。

百炼接入同时覆盖三类媒体能力，属于跨模块变更：既要在配置层注册供应商，也要在三类 backend 中分别实现调用逻辑，还要补齐国际化文案与能力声明。文本生成可以复用现有 OpenAI 兼容调用模式；图像与视频生成则需要接入 DashScope 的异步任务接口，并与当前项目的轮询、下载、重试模式保持一致。

约束包括：
- 必须沿用现有 backend 接口，避免影响 `MediaGenerator`、任务队列和上层服务编排
- 文本能力需支持 structured output 降级策略，保持与现有 OpenAI 后端一致的行为预期
- 图像/视频结果 URL 为临时地址，后端必须及时下载并转换为本地可用结果
- 用户侧配置应保持与现有供应商配置体系一致，只新增必要字段，不引入额外配置流程

## Goals / Non-Goals

**Goals:**
- 以最小架构增量新增 `bailian` 供应商，并纳入现有 provider registry
- 为文本、图像、视频三类能力分别提供独立 backend，实现与现有工厂/注册机制兼容
- 文本后端复用 OpenAI 兼容模式，支持 `text_generation`、`structured_output`、`vision`
- 图像和视频后端统一采用“提交任务 → 轮询状态 → 下载结果”的异步任务模式
- 明确模型能力、时长约束、分辨率映射、并发控制等设计，以便后续 specs 和实现落地

**Non-Goals:**
- 不新增前端专属交互或独立配置页面，仅复用现有供应商配置 UI
- 不在本次设计中引入新的任务编排框架或统一抽象层重构
- 不扩展百炼之外的其他国产供应商接入方案
- 不处理计费展示、配额查询、区域路由自动选择等增强能力

## Decisions

### 1. 供应商以单一 `bailian` key 注册，并在 registry 中声明三类媒体模型

选择在 `lib/providers.py` 中新增 `PROVIDER_BAILIAN = "bailian"`，并在 `lib/config/registry.py` 中注册单一 provider meta，统一承载文本、图像、视频模型及其能力声明。

**原因：**
- 百炼的认证方式统一为 API Key，适合作为单一 provider 暴露给上层配置系统
- 现有 registry 已支持一个 provider 包含多种 media_type，符合当前项目模式
- 可减少前端和配置层的复杂度，用户只需维护一套凭证

**备选方案：**
- 拆成 `bailian-text` / `bailian-image` / `bailian-video` 三个 provider：实现上更分散，会增加配置与展示复杂度，不利于统一管理

### 2. 文本后端基于 OpenAI 兼容接口实现，而不是直接使用百炼原生 SDK

新增 `lib/text_backends/bailian.py`，结构参考 `lib/text_backends/openai.py`，使用 `AsyncOpenAI` 客户端指向 `https://dashscope.aliyuncs.com/compatible-mode/v1`。

**原因：**
- 可最大化复用现有消息构造、response_format、schema 降级、usage 解析等逻辑
- 降低接入成本，使行为与现有 OpenAI 文本后端保持一致
- 百炼兼容接口已覆盖本次所需核心能力

**备选方案：**
- 使用百炼原生文本 API：需要单独适配请求/响应格式，增加维护成本，且 structured output 降级逻辑需要重新实现

### 3. 图像与视频后端采用独立 HTTP 实现，不复用 OpenAI 兼容接口

新增 `lib/image_backends/bailian.py` 与 `lib/video_backends/bailian.py`，使用 `httpx` 直接调用 DashScope 原生 REST API，并复用项目现有重试与轮询工具。

**原因：**
- 图像与视频能力依赖百炼原生异步任务接口，OpenAI 兼容模式无法覆盖
- 现有 Gemini/Ark 视频与图像后端已存在“提交-轮询-下载”的实现范式，可直接对齐
- 独立实现便于封装百炼特有参数，如 `ratio`、`resolution`、`prompt_extend`、`watermark`

**备选方案：**
- 将异步任务轮询抽成新的共享基类：当前只新增一个供应商，抽象收益有限，容易过度设计；先保持按后端独立实现

### 4. 图像生成使用统一的 aspect ratio 到 size 映射，并在 backend 内完成转换

ArcReel 当前更偏向使用业务侧的 aspect ratio 表达，百炼图像接口要求 `size` 字符串。设计上在 `bailian.py` 内维护固定映射，如 `16:9 -> 1280*720`。

**原因：**
- 保持上层请求对象不变，避免影响调用方
- 映射关系是百炼特有约束，放在供应商后端最合适

**备选方案：**
- 在上层统一引入供应商无关的 size 抽象：会扩大本次变更范围，不符合最小改动原则

### 5. 视频模型能力差异通过 registry 声明约束，通过 backend 做运行时校验

在 registry 中为不同视频模型配置 `supported_durations`，必要时补充能力标签；在 runtime 根据模型验证时长、输入媒体类型与分辨率组合。

**原因：**
- registry 是前端展示和后端能力判断的统一来源
- runtime 校验可以在请求发送前快速失败，避免无效调用浪费配额

**备选方案：**
- 仅依赖百炼 API 返回错误：用户体验差，错误定位不清晰

### 6. 结果下载与失败状态处理遵循现有后端模式

图像和视频任务完成后，后端应立即下载产出文件并返回本地结果；若任务状态为 `FAILED` 或终态无有效资源，应抛出明确错误。轮询过程复用现有 backoff / retry 思路，避免无界等待。

**原因：**
- 百炼返回的 OSS/临时 URL 具有有效期限制，不能直接长期透传
- 项目上层依赖稳定的本地结果路径或字节内容，而不是第三方临时链接

**备选方案：**
- 直接透传远端 URL：实现简单，但会引入过期风险和上层处理复杂度

### 7. 配置字段保持最小集合：`api_key` 必填，`base_url`/并发参数可选

provider meta 中仅声明 `api_key` 为必填；`base_url`、`image_max_workers`、`video_max_workers` 作为可选项暴露。

**原因：**
- 与文档和百炼接入要求一致
- 不把区域策略、上传策略等尚未稳定的扩展项提前暴露给用户

**备选方案：**
- 增加更多高级参数（区域、超时、上传策略）：灵活性更高，但会显著提高配置复杂度，不适合首版

## Risks / Trade-offs

- [百炼 OpenAI 兼容接口对部分 JSON Schema 支持不完整] → 沿用现有 schema error 降级到非原生 structured output 的策略，保证主要场景可用
- [图像/视频异步任务状态字段或结果结构与文档存在偏差] → 实现时增加状态解析兼容分支，并通过集成测试覆盖常见终态
- [临时 URL 过期或下载失败导致结果不可用] → 任务完成后立即下载，下载过程使用现有重试策略
- [不同视频模型的输入约束复杂，易在运行时出错] → 先覆盖文档中已确认的模型与参数范围，在 backend 做显式前置校验
- [单一 provider 同时承载三类媒体，未来若认证方式分裂会增加演进成本] → 当前统一认证收益更高，若未来分化再按 provider 维度拆分

## Migration Plan

1. 在 `lib/providers.py` 和 `lib/config/registry.py` 中注册 `bailian` 供应商与模型元数据
2. 实现 `lib/text_backends/bailian.py`，接入 OpenAI 兼容文本生成
3. 实现 `lib/image_backends/bailian.py`，接入图像异步任务提交、轮询和下载
4. 实现 `lib/video_backends/bailian.py`，接入视频异步任务提交、轮询和下载
5. 补充 `lib/i18n/{zh,en}/providers.py` 中的供应商名称与描述
6. 对修改文件执行 `ruff check`、`ruff format`，并补充必要测试验证
7. 发布后通过现有供应商配置页面添加 API Key 做联调验证；若发现兼容性问题，可通过移除 registry 注册项快速回退入口

## Open Questions

### 1. 图像高级参数支持范围

**决策**: 首版支持核心参数，高级参数按需补充

- ✅ **首版包含**: `prompt`、`size`/`aspect_ratio`、`n`（生成数量）、`watermark`
- ✅ **首版包含**: 参考图输入（最多 3 张，用于 image_to_image）
- ⏳ **后续补充**: `negative_prompt`、`seed`、`thinking_mode`、风格控制

**原因**: 核心流程优先，高级参数可在用户反馈后按需迭代

### 2. 视频结果元数据保留

**决策**: 保留 `task_id` 和 `request_id` 用于排障

在 `VideoGenerationResult` 中添加可选字段：
```python
task_id: str | None = None  # 百炼任务 ID
request_id: str | None = None  # 百炼请求 ID
```

**原因**: 便于用户反馈问题时快速定位，对现有结构影响最小

### 3. R2V 和 VideoEdit 实施优先级

**决策**: 首版实现 T2V + I2V，R2V 和 VideoEdit 作为 Phase 2

- ✅ **Phase 1**: `wan2.7-t2v`（文生视频）、`wan2.7-i2v`（图生视频）
- ⏳ **Phase 2**: `wan2.7-r2v`（参考视频生成）、`wan2.7-videoedit`（视频编辑）

**原因**: 
- T2V/I2V 覆盖主流场景，优先验证异步任务流程
- R2V 需要多媒体输入组合，VideoEdit 需要视频编辑语义，复杂度更高

### 4. 本地文件上传到临时 OSS URL

**决策**: 首版提供共享工具函数，放在 `lib/bailian_shared.py`

实现 `upload_file_to_oss()` 函数：
1. 调用 `/api/v1/uploads?action=getPolicy` 获取上传凭证
2. 使用 `httpx` 上传文件到 OSS
3. 返回 `oss://` 格式的临时 URL

**原因**:
- 文档中已提供完整的上传流程示例
- 图像和视频后端都可能需要上传本地文件，共享工具避免重复实现
- 首版限制为外部 URL 会显著降低易用性

**实现位置**: `lib/bailian_shared.py`（类似 `lib/gemini_shared.py` 的模式）
