来源票：https://github.com/ArcReel/ArcReel/issues/1379

# 火山方舟 Seedance 2.0 音频参考能力调研

调研时间：2026-07-27。以下结论均来自官方文档站（`docs.volcengine.com`，域名重定向自 `www.volcengine.com/docs/`）在调研当时的公开内容，官方文档可能随版本迭代变更，引用处均标注文档页最后更新时间。

## 1. 结论摘要

- **支持**：Seedance 2.0 系列（`doubao-seedance-2-0-260128` / `doubao-seedance-2-0-fast-260128` / `doubao-seedance-2-0-mini-260615`）官方明确支持"参考音频"输入，用于多模态参考生视频（继承参考音频的音色、旋律、对话内容）。
- **不支持**：`doubao-seedance-1-5-pro-251215`（Seedance 1.5 Pro）以及 1.0 系列均不支持音频参考输入；官方 API 文档原文明确写"仅 Seedance 2.0 系列支持输入音频"。1.5 Pro 的"声音能力"仅限于通过文本 prompt 描述音色/情绪/BGM/SFX，不接受音频文件作为参考素材。
- **支持**：音频作为 content 数组条目，字段为 `{"type": "audio_url", "audio_url": {"url": "..."}, "role": "reference_audio"}`；`role` 当前仅有 `reference_audio` 一个取值。`audio_url.url` 支持公网 URL / Base64 data URI（`data:audio/<格式>;base64,<内容>`）/ `asset://<ASSET_ID>` 三种形态。
- **支持（隐式绑定，非独立占位符语法）**：prompt 中通过"素材类型+序号"文本方式引用音频，即"音频1"/"音频2"/"音频3"，序号按 content 数组中同类型（`audio_url`）条目的出现顺序从 1 计数，不是结构化字段绑定；未发现类似图片参考旧写法 `[图1]` 的方括号占位符语法，当前官方文档统一使用不带方括号的"音频N"文本，示例中另有 `@音频1`、`<音频N>` 两种非强制的书写变体。
- **无公开资料**：官方计价文档未列出"音频参考输入"作为独立计价项；Seedance 2.0 系列的 token 估算公式仅含"(输入视频时长 + 输出视频时长)"，未把参考音频时长纳入计算变量。是否存在未公开的隐性算力开销，官方文档未说明。

## 2. 逐条调研发现

### 2.1 支持的模型 / 档位

官方文档明确的模型能力矩阵（来源：[Doubao Seedance 2.0 快速入门](https://docs.volcengine.com/docs/82379/2291680?lang=zh)，最近更新 2026.07.07；[创建视频生成任务 API](https://docs.volcengine.com/docs/82379/1520757?lang=zh)，最近更新 2026.07.10）：

| 模型 | Model ID | 音频参考输入 |
|---|---|---|
| Seedance 2.0 | `doubao-seedance-2-0-260128` | 支持 |
| Seedance 2.0 Fast | `doubao-seedance-2-0-fast-260128` | 支持 |
| Seedance 2.0 Mini | `doubao-seedance-2-0-mini-260615` | 支持 |
| Seedance 1.5 Pro | `doubao-seedance-1-5-pro-251215` | **不支持** |
| Seedance 1.0 Pro / Pro Fast / lite | 各带日期戳 Model ID | **不支持** |

《创建视频生成任务 API》原文分模型列出"支持的模型能力"，Seedance 1.5 Pro / 1.0 Pro / 1.0 Pro Fast 条目下均只有"图生视频-首尾帧""图生视频-首帧""文生视频"，不含音频参考；音频条目的字段说明段落原文明确写"仅 Seedance 2.0 系列支持输入音频"。

《Seedance-1.5-pro 提示词指南》（[docs.volcengine.com/docs/82379/2168087](https://docs.volcengine.com/docs/82379/2168087?lang=zh)）逐字通读全文，未出现任何"输入参考音频""audio_url"相关内容；该页描述的"声音生成"能力（对话音色、多语言、音效、BGM）全部通过文本 prompt 描述实现，不接受音频文件输入。

本地缓存文档 `docs/ark-docs/seedance2.0.md` 能力表中"多模态参考(图/视频)"列未单列音频，且未展示音频作为 content 条目的字段写法——该文档信息不完整，已由官方文档补全和更新（如 Seedance 2.0 Mini 这一档在本地缓存中缺失，仅在最新官方文档出现）。

注：官方计价文档（见 2.4 节）的价格表表头使用不带日期戳的简写 `doubao-seedance-2.0` / `doubao-seedance-2.0-fast` / `doubao-seedance-2.0-mini`，与 API 文档中的带日期戳 Model ID（如 `doubao-seedance-2-0-260128`）指代同一模型族，前者是计价文档的展示简写，`model` 字段实际取值以带日期戳的 Model ID 为准。

### 2.2 输入形态

来源：[创建视频生成任务 API](https://docs.volcengine.com/docs/82379/1520757?lang=zh) "音频信息"章节（原文逐字摘录）。

**字段结构**：

```json
{
    "type": "audio_url",
    "audio_url": {
        "url": "https://ark-project.tos-cn-beijing.volces.com/doc_audio/r2v_tea_audio1.mp3"
    },
    "role": "reference_audio"
}
```

- `content.type`（string，必选）：固定值 `"audio_url"`
- `content.audio_url`（object，必选）：音频对象
- `content.role`（string，条件必填）：当前仅支持 `reference_audio`
- `content.audio_url.url` 支持三种形态：
  - 公网可访问 URL
  - Base64 编码：`data:audio/<格式>;base64,<编码内容>`（格式需小写）
  - 素材库 Asset ID：`asset://<ASSET_ID>`

**格式与限制**（原文）：

- 单个音频格式：wav、mp3
- 单个音频时长：`[2, 15]` 秒
- **每请求最多 3 段参考音频，总时长不超过 15 秒**
- 单个音频大小：不超过 15MB；请求体总大小不超过 64MB，大文件不建议用 Base64 编码

与本地缓存 `docs/ark-docs/seedance2.0.md` 6.1 节"音频单个 <15MB，支持 wav/mp3，时长 2~15 秒"基本一致，官方文档额外补充了"最多 3 段、总时长不超过 15 秒"这一数量与总时长上限，本地缓存未记录该限制。

**输入组合限制**（原文，来源同上及 [快速入门](https://docs.volcengine.com/docs/82379/2291680?lang=zh)）：

- 支持的组合：文本(可选)+图片、文本(可选)+视频、文本(可选)+图片+音频、文本(可选)+图片+视频、文本(可选)+视频+音频、文本(可选)+图片+视频+音频
- **不可单独输入音频，应至少包含 1 个参考视频或图片**（即不支持"纯音频"输入，也不支持"文本+音频"这种不带图片/视频的组合）

**多角色各绑一段参考音频**：官方文档未提供结构化的"角色-音频"绑定字段（`role` 只有 `reference_audio` 一个取值，不区分角色/说话人）。绑定完全依赖 prompt 文本关联，例如提示词指南 FAQ 示例"使用@音频1低厚温润带细碎颗粒感中年男声的音色说……"，即在台词前用文本描述把某段音频与某句台词/某个角色关联，不是 API 层面的结构化绑定。

### 2.3 与 prompt 中台词的配合方式

来源：[Doubao Seedance 2.0 系列提示词指南](https://docs.volcengine.com/docs/82379/2222480?lang=zh)，最近更新 2026.07.20；[快速入门](https://docs.volcengine.com/docs/82379/2291680?lang=zh) "提示词技巧 > 提示词规则"。

**核心机制：按 content 数组顺序隐式绑定序号，非独立占位符语法。**

《快速入门》"提示词规则"原文：

> 提示词中必须使用"素材类型+序号"格式引用素材，序号为请求体中该素材在同类素材中的排序。例如「图片 n」指代 content 数组中第 n 个 `type="image_url"` 的参考图片（按数组顺序从 1 开始计数）。注意不支持使用 Asset ID 指代素材

该规则同等适用于音频：音频序号按 content 数组中 `type="audio_url"` 条目出现的顺序从 1 计数，写作"音频1""音频2""音频3"。

《提示词指南》"基础公式 > 多模态参考"给出的推荐句式：

> 音频参考：参考\<音频N\>中的音色，生成...

实战案例中另出现内联写法 `@音频1`（如"环境音效与@音频1自然融合"、"使用@音频1低厚温润带细碎颗粒感中年男声的音色说"）。

**结论**：官方文档中"音频N"的引用文本本身有两种书写变体（`<音频N>` 模板句式 / `@音频N` 内联标注），核心机制是按数组顺序的序号引用，不是独立的方括号占位符语法。这与本地缓存 `docs/ark-docs/seedance2.0.md` 3.4 节展示的图片参考示例 `[图1]戴着眼镜...` 方括号写法不同——当前官方文档（快速入门 + 提示词指南）均未出现方括号写法，本地缓存的方括号语法可能是历史版本遗留，未在最新官方文档中找到对应说明。

`role`/`type` 字段与 prompt 序号引用是两条独立机制：`role: reference_audio` 只标记该条目是参考音频（供后端识别用途），实际"哪个角色说哪段音频"完全靠 prompt 文本自然语言表达，无结构化字段。

《提示词指南》"注意事项 > 特殊字符规范"表格额外定义了音乐/音效/台词/字幕的书写符号（音乐用 `（）`、音效用 `<>`、台词用 `{}`、字幕用 `【】`），这套符号用于在 prompt 正文中描述"生成什么声音效果"，与"引用第几段输入音频素材"是两回事，二者不要混淆。

### 2.4 计价影响

来源：[火山方舟模型价格](https://docs.volcengine.com/docs/82379/1544106?lang=zh)，最近更新 2026年7月27日。

**Seedance 2.0 系列按 token 计费，公式为**：

```
token 用量 = (输入视频时长 + 输出视频时长) × 输出视频的宽 × 输出视频的高 × 输出视频的帧率 / 1024
```

该公式仅包含"输入视频时长"，**未出现"输入音频时长"变量**。据此，官方公式层面没有把参考音频时长作为独立的计费维度纳入计算；输入音频是否会隐性影响底层模型算力/实际 token 消耗，官方文档未说明，仅提示"准确用量以调用 API 返回的 `usage` 字段为准"。

**单价**（在线推理，元/百万 token）：

- `doubao-seedance-2.0`：输入不含视频 46.00；输入包含视频 28.00
- `doubao-seedance-2.0-fast`：输入不含视频 37.00；输入包含视频 22.00

价格表按"是否包含输入视频"两档区分，**没有"是否包含输入音频"这一档**——即传入参考音频不改变计价档位，只有传入参考视频才会切到"输入包含视频"这一较低单价档。

**未找到 Seedance 2.0 Mini 的官方单价**：官方计价文档的价格示例章节出现了 Mini 档在"输入包含视频"场景下的价格点（480p 16:9、5 秒输出、2~4 秒输入视频时为 1.27 元/个），但未见到 Mini 的独立"按 token 单价表"条目（该表仅列 2.0 与 2.0-fast 两档）。本地缓存 `docs/ark-docs/火山方舟费用参考.md` 完全没有 Mini 档，是过期信息。

**Seedance 1.5 Pro 有声/无声定价**（与音频参考无关，是 `generate_audio` 输出音轨开关的计价，二者是两件事）：

- 有声视频：16.00 元/百万 token（在线推理）/ 8.00 元/百万 token（离线推理）
- 无声视频：8.00 元/百万 token（在线推理）/ 4.00 元/百万 token（离线推理）

该数字与本地缓存 `docs/ark-docs/火山方舟费用参考.md` 一致，官方文档确认单位为"元/百万 token"。**1.5 Pro 不支持音频参考输入**（见 2.1 节），故该有声/无声价格差异只对应"是否生成视频自带音轨"（`generate_audio` 参数），与"音频参考输入决定角色配音"无关——这正是任务描述中要求区分的两件事，本次调研确认二者在 1.5 Pro 上根本不存在交集（因为 1.5 Pro 压根不支持音频参考）。

**结论**：**无公开资料**表明"音频参考"这个输入本身有独立计价项。计价只按分辨率档位 + 是否含输入视频两个维度区分，音频参考不改变计价公式或单价档位。

## 3. 与 `lib/video_backends/ark.py` / `lib/video_backends/base.py` 的接线点评估

以下仅列评估要点，不写代码：

- **`VideoGenerationRequest`（`lib/video_backends/base.py` 409-432 行）**：需新增类似 `reference_images` 的 `reference_audios: list[Path] | None = None` 字段，语义上与现有 `reference_images` 平行（参考素材列表，非首尾帧）。
- **`VideoCapabilities`（`lib/video_backends/base.py` 378-393 行）**：需新增 `reference_audios: bool = False` / `max_reference_audios: int = 0` 两个字段，与现有 `reference_images` / `max_reference_images` 对称。docstring 需要补充音频参考与首尾帧/图片参考路径之间是否互斥的说明——官方文档显示音频参考属于"多模态参考生视频"这一互斥场景（与首帧/首尾帧图生视频三选一，参见 `1520757` 文档"图生视频-首帧/首尾帧/参考图三种场景互斥"的既有实现逻辑，`ArkVideoBackend.video_capabilities_for_model` 141-150 行注释已描述这条互斥规则对 `reference_images` 成立，音频参考共享同一互斥场景，需要复用而非另起一套判定）。
- **`ArkVideoBackend._create_task`（`lib/video_backends/ark.py` 189-261 行）**：`content` 数组构造需仿照 220-233 行 `reference_images` 的 for 循环，新增音频分支：`{"type": "audio_url", "audio_url": {"url": data_uri_or_url}, "role": "reference_audio"}`。与图片参考不同的是，音频参考的 `url` 官方支持公网 URL / Base64 data URI / `asset://` 三种形态（图片参考当前实现固定走 `image_to_base64_data_uri` 转 Base64），若音频文件较大（临近 15MB 上限）需要评估 Base64 膨胀后是否触达"请求体不超过 64MB"的整体限制，可能需要引入公网 URL 直传路径而非无脑复用图片参考的纯 Base64 模式。
- **`_is_seedance_2` / `video_capabilities_for_model`（`lib/video_backends/ark.py` 72-163 行）**：现有 `_is_seedance_2` 是宽松子串判定（供 FLEX_TIER 剔除复用），`_SEEDANCE_2_LAST_FRAME_ALLOW_SUBSTRINGS`（115-122 行）是尾帧能力的已验证型号白名单。音频参考能力应比照这一模式新增独立白名单（例如 `_SEEDANCE_2_REFERENCE_AUDIO_ALLOW_SUBSTRINGS`），只覆盖已验证支持音频参考的三个具体型号（`seedance-2-0` / `seedance-2-0-fast` / `seedance-2-0-mini` 及其点号写法），不应让 `_is_seedance_2` 的宽松族群判定直接授予音频参考能力——避免未来 seedance-2.5 等未验证型号被误判为支持。经本次调研，Seedance 2.0 全系三档（含 mini）均官方支持音频参考，与尾帧能力的三档全覆盖情况一致，因此若要收窄，可直接复用现有 `_SEEDANCE_2_LAST_FRAME_ALLOW_SUBSTRINGS` 同一份白名单常量，或新增一份内容相同但语义独立命名的常量（两者当前覆盖模型集合相同，是否合并取决于后续实现是否希望保持"能力各自独立声明"的可读性）。
- **计价/费用预估层（`server/services/cost_estimation.py`、`lib/cost_calculator.py`、`lib/pricing/strategies.py`、`lib/pricing/types.py`）**：`lib/pricing/strategies.py` 的 `PricingParams`（dataclass，约 41-59 行）当前已有 `generate_audio: bool` 维度（对应输出音轨开关），但没有"是否传入参考音频"维度。根据 2.4 节结论，官方计价公式和单价表都不区分音频参考输入，**当前不需要在 `PricingParams` 新增维度**；后续若官方文档更新出现独立音频参考计价项，再评估是否需要在 `PricingParams` 增加 `has_reference_audio: bool` 之类的字段并接入 `lib/pricing/strategies.py` 对应 Seedance 2.0 的计费策略函数。`server/services/cost_estimation.py` 头部已引入 `PricingParams`，无需改动即可继续复用现有 token 估算路径（分辨率 + 是否含输入视频两维度）。

## 4. 信源清单

### 官方文档

- [创建视频生成任务 API](https://docs.volcengine.com/docs/82379/1520757?lang=zh) — 音频/图片/视频 content 字段定义、role 取值、格式与数量/时长/大小限制、模型能力矩阵、输入组合限制（最近更新 2026.07.10）
- [Doubao Seedance 2.0 快速入门](https://docs.volcengine.com/docs/82379/2291680?lang=zh) — 多模态参考含音频的完整代码示例、模型能力表（三档 Model ID）、提示词序号引用规则（最近更新 2026.07.07）
- [Doubao Seedance 2.0 系列提示词指南](https://docs.volcengine.com/docs/82379/2222480?lang=zh) — 音频参考推荐句式、`<音频N>`/`@音频N` 写法、素材配置策略、特殊字符规范、"音色参考不准"FAQ（最近更新 2026.07.20）
- [Seedance-1.5-pro 提示词指南](https://docs.volcengine.com/docs/82379/2168087?lang=zh) — 确认 1.5 Pro 无音频参考能力，声音能力仅限文本描述
- [火山方舟模型价格](https://docs.volcengine.com/docs/82379/1544106?lang=zh) — Seedance 2.0 系列计价公式与单价表、1.5 Pro 有声/无声单价、无独立音频参考计价条目（最近更新 2026年7月27日）
- [火山方舟套餐概览（Agent Plan）](https://www.volcengine.com/docs/82379/2366394?lang=zh) — Seedance 2.0 全系三档在套餐制下仅 Large/Max 可用，附带信息，非本次核心结论依据

### 第三方交叉参考（未作为结论唯一出处）

- [How to Use Seedance 2.0 API 2026 — apidog.com](https://apidog.com/blog/seedance-2-0-api/) — 提及"最多 3 段音频、总时长 15 秒"，与官方文档一致，可交叉验证
- [Does Seedance 2.0 Accept Voice Reference? — seedance2pro.io](https://seedance2pro.io/blog/does-seedance-2-accept-voice-reference) — 声称音频参考"不是声音克隆"，仅作结构/节奏/口型对齐参考；**官方文档未使用"声音克隆"或与之对立的措辞，该定性未经官方文档证实**，仅供参考
- [Seedance 2.0音频输入指南 — volcengine.com/article/40909](https://www.volcengine.com/article/40909) — 托管在 volcengine.com 域名但署名"阿华AIGC实验室"（第三方作者），内容为营销性质，未见有效技术细节，不作为结论依据
- 本地缓存 `docs/ark-docs/seedance2.0.md`、`docs/ark-docs/火山方舟费用参考.md` — 任务描述中的调研起点，经核实存在信息缺失（未展示音频 content 字段写法）与过期（缺 Seedance 2.0 Mini 定价），已被本报告的官方文档信息替代/补全
