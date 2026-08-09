# HappyHorse 1.1（阿里云百炼 DashScope）视频生成系列调研

调研日期:2026-08-08。来源限定为阿里云一手渠道:`help.aliyun.com` 官方文档(最高权威)与 `developer.aliyun.com` 阿里云开发者社区官方文章(用于定价通稿与版本对比,权威性次之)。查不到的项标注「未能确认」。

## 0. 「happyhorse」是不是别名?

**结论:不是别名,是阿里云自研的真实公开系列,置信度高。**

- `help.aliyun.com` 官方 API 参考直接以 `happyhorse-1.1-t2v` / `happyhorse-1.1-i2v` / `happyhorse-1.1-r2v` / `happyhorse-1.0-*` 为 model id 建文档页(见下文各节来源)。
- 阿里云开发者社区介绍:HappyHorse(快乐小马)为阿里自研原生多模态视频生成模型(ATH 创新事业部),2026-04-27 开启灰测,15B 参数、单流 Transformer、音画联合生成;水印文案固定 "Happy Horse"。来源:<https://developer.aliyun.com/article/1731571>、<https://developer.aliyun.com/article/1731726>
- 本仓库 `lib/config/registry.py` 登记的 `happyhorse-1.0-{i2v,t2v,r2v}`(720P ¥0.9/s、1080P ¥1.6/s、3–15s、r2v 9 张参考图)与官方文档逐项吻合,即 registry 内的 id 就是官方 id,无需「真实系列」映射。
- 该系列发布晚于常见模型知识截止时间,公开检索需以 2026 年 4 月后的资料为准。

## 1. 结论摘要表

| 模态 | model id | 分辨率档位 | 时长 | 能力位 | 定价(元/秒,刊例) |
|------|----------|-----------|------|--------|------------------|
| 文生视频 | `happyhorse-1.1-t2v` | 480P / 720P / 1080P(默认) | 3–15s 整数,默认 5 | 无首帧;音画同步出声;ratio 9 档 | 480P 0.45 / 720P 0.9 / 1080P 1.2 |
| 图生视频(首帧) | `happyhorse-1.1-i2v` | 480P / 720P / 1080P(默认) | 3–15s 整数,默认 5 | 首帧 ✓、尾帧 ✗;宽高比随首帧,无 ratio 参数;音画同步出声 | 同上 |
| 参考生视频 | `happyhorse-1.1-r2v` | 480P / 720P / 1080P(默认) | 3–15s 整数,默认 5 | 参考图 1–9 张;无首帧;音画同步出声 | 同上 |
| 首尾帧 | **无 1.1 变体** | — | — | 官方首尾帧场景推荐 `wan2.7-i2v-2026-04-25` | — |
| 视频编辑 | `happyhorse-1.0-video-edit`(仅 1.0,无 1.1) | 720P / 1080P(默认) | 输出 3–15s(输入视频 3–60s) | 视频 + 0–5 张参考图 + 文本指令,风格转换/局部替换 | 未能确认 |

促销:1.1 全系曾有限时 6 折(2026-06-22 至 2026-07-06,720P 0.54 / 1080P 0.72 元/秒),同期 1.0 享 8 折;当前是否仍在折扣期未能确认。新用户免费额度:10 秒视频生成。

## 2. 各项详述与引用

### 2.1 文生视频 `happyhorse-1.1-t2v`

来源:<https://help.aliyun.com/zh/model-studio/happyhorse-text-to-video-api-reference>

- 同页并列 `happyhorse-1.1-t2v` 与 `happyhorse-1.0-t2v` 两个 model id。
- `resolution`:480P / 720P / 1080P(默认 1080P)。注意:参数表为 1.1/1.0 合并呈现,480P 是否两版通用未能确认(1.1 上线通稿只宣传 720P/1080P)。
- `duration`:「[3, 15] 之间的整数,默认值为 5」。
- `ratio`:16:9(默认)、9:16、1:1、4:3、3:4、4:5、5:4、9:21、21:9。
- prompt:「长度不超过 5000 个非中文字符或 2500 个中文字符,超过部分将自动截断」——与 wan2.7 相同的静默截断行为。
- `watermark`:默认 `true`,水印在右下角,文案固定 "Happy Horse";传 `false` 关闭。
- 音频:API 参数表**无音频开关**;官方视频模型总览将文生视频描述为「通过文本生成有声视频」,1.1 功能通稿称「音画同步」原生联合生成 → 推断音频恒开、无开关(与 1.0 口径一致)。来源:<https://help.aliyun.com/zh/model-studio/video-generate-edit-model/>、<https://developer.aliyun.com/article/1749474>
- 可用地域:华北2(北京)、新加坡、日本(东京)、德国(法兰克福)、美国(弗吉尼亚)。

### 2.2 图生视频 `happyhorse-1.1-i2v`

来源:<https://help.aliyun.com/zh/model-studio/happyhorse-image-to-video-api-reference>

- 首帧 ✓、尾帧 ✗(1.1 与 1.0 均不支持尾帧;文档页标题即「图生视频-基于首帧」)。
- 「图生视频的宽高比自动跟随输入首帧图像」,「不支持 ratio 参数」。
- 输入图约束:JPEG/JPG/PNG/WEBP;宽高均 ≥300px;宽高比 1:2.5–2.5:1;≤20MB。
- 分辨率/时长/prompt/watermark 同 t2v。

### 2.3 参考生视频 `happyhorse-1.1-r2v`

来源:<https://help.aliyun.com/zh/model-studio/happyhorse-reference-to-video-api-reference>

- 参考图「1~9 张」(1.1 与 1.0 上限相同)。
- `input.media` 元素 `{type: "reference_image", url}`;prompt 用 `[Image N]` 指代参考图。
- 分辨率 480P/720P/1080P(默认 1080P)、时长 3–15s。
- 无首帧输入(与 wan2.7-r2v 的「首帧 + 参考图」形态不同)。

### 2.4 视频编辑 `happyhorse-1.0-video-edit`

来源:<https://help.aliyun.com/zh/model-studio/happyhorse-video-edit-api-reference>

- **仅 1.0**,无 1.1 变体。输入视频 MP4/MOV(建议 H.264)、3–60s、长边 ≤4096px、短边 ≥360px、≤100MB、>8fps;参考图 0–5 张;输出 3–15s,720P / 1080P(默认)。定价未能确认。

### 2.5 定价与免费额度

- **1.1 刊例价:480P ¥0.45/秒、720P ¥0.9/秒、1080P ¥1.2/秒**,t2v/i2v/r2v 同价(按输出秒数计费)。720P/1080P 来源:<https://developer.aliyun.com/article/1750051>(「720P at 0.9 yuan/second and 1080P at 1.2 yuan/second」),与 6 折促销价 0.54/0.72 反推一致;480P 为维护者自百炼控制台价格表核实(help.aliyun 模型大全页为动态渲染,公开页未抓到)。
- 限时 6 折(2026-06-22 至 2026-07-06):720P ¥0.54/秒、1080P ¥0.72/秒。来源:<https://developer.aliyun.com/article/1743014>、<https://developer.aliyun.com/article/1742909>
- **1.0 刊例价:720P ¥0.9/秒、1080P ¥1.6/秒**(与本仓库 registry 现值一致)。来源:<https://developer.aliyun.com/article/1731470>
- 免费额度:「免费领取 HappyHorse 免费 10 秒视频生成」。来源:<https://developer.aliyun.com/article/1743014>

### 2.6 与 1.0 的差异

来源:<https://developer.aliyun.com/article/1742909>(阿里云开发者社区功能详解)

- 1080P 单价下调:¥1.6/s → ¥1.2/s(720P 持平 ¥0.9/s)。
- 指令遵循更严格、参考图还原度更高(会如实渲染文字/边框)、口型同步精度提升、内容安全阈值放宽、出片更快(1080P 15s 约 12 分钟 vs 1.0 约 14 分钟)。
- 档位:两版同为 3–15s;API 参考现列 480P 档(1.0 早期资料仅 720P/1080P,480P 归属版本未能确认)。
- **弃用计划:未能确认**。截至调研日,1.0 全系仍在官方 API 参考与 Token Plan 文档中在售(且曾与 1.1 同期享 8 折促销),未检索到官方停售/下线公告。

## 3. Token Plan(Token 套餐)覆盖与调用路径

来源:<https://help.aliyun.com/zh/model-studio/token-plan-overview>、<https://help.aliyun.com/zh/model-studio/token-plan-multimodal-gen>、<https://help.aliyun.com/zh/model-studio/token-plan-personal-overview>、<https://help.aliyun.com/zh/model-studio/token-plan-faq>

### 覆盖型号

- 官方个人版文档明确列出套餐内视频模型:`happyhorse-1.1-i2v` / `happyhorse-1.1-t2v` / `happyhorse-1.1-r2v`(个人版与团队版对视频模型「双版本通用」)。
- 多模态接入文档以 `happyhorse-1.1-t2v` 为默认模型,并给出 `happyhorse-1.0-t2v`、`happyhorse-1.1-r2v` 的调用示例 → 1.0 系列亦可在套餐内调用;完整清单以控制台模型列表页为准。
- 万相(wan)视频系列:未见于套餐官方文档的视频示例,是否在套餐内**未能确认**(FAQ 提及 `wan2.7-image` 图像模型属套餐范围)。

### 调用路径:DashScope 原生异步路径,非 compatible-mode

官方多模态接入文档给出的视频调用为:

```http
POST https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis
GET  https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1/tasks/{task_id}
```

即**与标准 DashScope 完全同构的原生异步两步式**(含 `X-DashScope-Async: enable` header),只是换域名 + 换套餐 Key。文本对话才走 `https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`(OpenAI 协议,另兼容 Anthropic 协议)。

### 套餐域名 vs 标准 dashscope.aliyuncs.com 的差异

| 维度 | 标准 DashScope | Token Plan |
|------|----------------|-----------|
| 域名 | `dashscope.aliyuncs.com` | `token-plan.cn-beijing.maas.aliyuncs.com`(仅华北2-北京) |
| API Key | 普通百炼 Key | 专属 Key,`sk-sp-` 前缀;个人版/团队版 Key 独立、不可混用 |
| 路径 | 原生 `/api/v1/...` + `/compatible-mode/v1` | 同样两类路径均支持;视频走原生异步路径 |
| 计费 | 按量计费 | Credits 统一抵扣;**额度用尽直接阻断(429 Allocated quota exceeded),不回落按量计费** |
| 混用后果 | — | 套餐 Key 打到 `dashscope.aliyuncs.com` 或反向混用 → `401 InvalidApiKey`(FAQ 明确「误用了百炼通用 Base URL」是 401 常见原因);不会静默转按量 |
| 额度周期 | — | 团队版坐席月度额度(25k/100k/250k Credits)到期重置不结转;个人版滚动窗口(7 天限额,窗口内未用完不结转) |

视频模型的 Credits 换算率(每秒/每档位扣多少 Credits):**未能确认**,官方文档未披露,以控制台为准。

## 4. 对 ArcReel registry/backend 的增补建议

对照 ADR 0018(fail-loud:`supported_durations` 未登记即拒绝,无隐性 fallback):

### `lib/config/registry.py`(DashScope 供应商 video 段)

新增三条,字段与 1.0 条目同构:

- `happyhorse-1.1-t2v` / `happyhorse-1.1-i2v` / `happyhorse-1.1-r2v`
  - `supported_durations = list(range(3, 16))`(官方 3–15s 整数,须显式登记)
  - `resolutions = ["480p", "720p", "1080p"]`
  - `pricing = _dashscope_video_pricing(..., {"480p": 0.45, "720p": 0.9, "1080p": 1.2})`(刊例价,480P 为控制台核实价;促销价不入 registry)
  - 建议将 `default=True` 从 `happyhorse-1.0-i2v` 移到 `happyhorse-1.1-i2v`(1080P 更便宜且官方总览以 1.1 为推荐系列),同步改 `lib/video_backends/dashscope.py::DEFAULT_MODEL`
- 保留 1.0 三条(仍在售、无弃用公告);`happyhorse-1.0-video-edit` 与 ArcReel 现有流水线无对应能力形态,暂不登记

### `lib/video_backends/dashscope.py::_MODEL_PROFILES`

- `"happyhorse-1.1-t2v": VideoCapabilities(first_frame=False)`
- `"happyhorse-1.1-i2v": VideoCapabilities(first_frame=True)`
- `"happyhorse-1.1-r2v": VideoCapabilities(first_frame=False, max_reference_images=9)`
- key 之间互不为子串的不变式仍成立(`happyhorse-1.0-*` 与 `happyhorse-1.1-*` 无包含关系),`_profile_for_model` 的子串容忍逻辑不需要改
- 可考虑给 happyhorse 全系补 `max_prompt_chars`(官方:非中文 5000 / 中文 2500,超限静默截断照常计费——与 wan2.7 同类陷阱,现只有 wan2.7 有付费前 gate)。注意 happyhorse 的上限是按中英文区分的双阈值,现有单一 `max_prompt_chars` 字段表达不了中文 2500 的档,需要先扩字段或按保守值 2500 登记
- watermark:1.1 默认仍为 `true`(文案 "Happy Horse"),backend 构造 payload 时须继续显式传 `watermark: false`(与 1.0 同口径)

### 周边

- `lib/custom_provider/duration_presets.py` 的 `happyhorse` 正则(3–15)与 `lib/custom_provider/endpoints.py` 的 `"happyhorse"` 子串路由(dashscope-async-video)天然覆盖 1.1,无需改动
- 首尾帧需求不要落在 happyhorse 上(全系无尾帧);官方首尾帧推荐 `wan2.7-i2v-2026-04-25`
- Token Plan 接入:视频走原生异步路径且 schema 同构,理论上现有 `DashScopeVideoBackend` 换 `base_url`(`https://token-plan.cn-beijing.maas.aliyuncs.com/api/v1`)+ `sk-sp-` Key 即可复用;但额度用尽是 429 硬阻断而非转按量,若接入需把该错误映射成用户可读的配额提示,且仅北京地域可用
- `docs/dashscope-docs/参考生视频-HappyHorse.md` 现为 1.0 快照(且写「同系列 t2v/i2v 待官方扩充」),若实施 1.1 接入应按维护约定回写该目录

## 来源清单

官方文档(help.aliyun.com):

- 文生视频 API:<https://help.aliyun.com/zh/model-studio/happyhorse-text-to-video-api-reference>
- 图生视频(首帧)API:<https://help.aliyun.com/zh/model-studio/happyhorse-image-to-video-api-reference>
- 参考生视频 API:<https://help.aliyun.com/zh/model-studio/happyhorse-reference-to-video-api-reference>
- 视频编辑 API:<https://help.aliyun.com/zh/model-studio/happyhorse-video-edit-api-reference>
- 视频生成与编辑总览:<https://help.aliyun.com/zh/model-studio/video-generate-edit-model/>
- 模型大全:<https://help.aliyun.com/zh/model-studio/models>(动态页,价格未抓取到)
- Token Plan 概述 / 个人版 / 多模态接入 / FAQ:<https://help.aliyun.com/zh/model-studio/token-plan-overview>、<https://help.aliyun.com/zh/model-studio/token-plan-personal-overview>、<https://help.aliyun.com/zh/model-studio/token-plan-multimodal-gen>、<https://help.aliyun.com/zh/model-studio/token-plan-faq>

阿里云开发者社区(定价通稿与版本对比):

- 1.0 定价:<https://developer.aliyun.com/article/1731470>
- 系列介绍:<https://developer.aliyun.com/article/1731571>、<https://developer.aliyun.com/article/1731726>
- 1.1 价格拆解(6 折 0.54/0.72):<https://developer.aliyun.com/article/1743014>
- 1.1 功能详解/成本指南(促销窗口、1.0 对比):<https://developer.aliyun.com/article/1742909>
- 1.1 功能介绍(音画同步、档位):<https://developer.aliyun.com/article/1749474>
- Token Plan 解析(1.1 刊例价 0.9/1.2):<https://developer.aliyun.com/article/1750051>
