# ad 模式不接入 step1→step2 审核 gate

ad（广告/短片）内容模式不接入 step1→step2 两段式剧本流水线与阻塞式 web 审核 gate。gate 的适用范围维持 content_mode ∈ {drama, narration} 且 effective_mode ≠ reference_video。

## Why this is out of scope

step1→step2 审核 gate（docs/adr/0041）解决的两个核心问题在 ad 结构性不成立：

- **二次转写失真**：drama / narration 的逐字口播内容要穿过 step1→step2 两段生成，存在跨段改写风险；ad 剧本单次生成直接产出最终结构（平铺 `shots[]` + 一等口播文案 `voiceover_text`），不存在两段边界，也就没有跨段转写。
- **中间态不可审**：drama / narration 在 step1 完成、step2 未跑之间用户在 web 端无感知；ad 剧本直接写入 `scripts/episode_1.json`，web 时间线全程可见可改。

唯一部分成立的「昂贵视觉生成前缺硬检查点」由两点覆盖：ad 恒单集、镜头数少（成片 15–90 秒），费用爆炸半径远小于 drama 一集；agent 工作流已有逐阶段确认约定——剧本生成后向用户呈现镜头列表与口播文案、`product_sheet` 软门禁、分镜生成后产品保真审核（在产生视频费用前拦截）。

「ad 剧本一键生成不走 step1 中间文件」是 ADR 0033 的有意设计，CONTEXT.md 词条「广告/短片模式（ad）」记录了该契约。为 gate 对称性给 ad 单独构建一条两段式流水线，收益不成比例。

## 重访条件

- ad 出现真实保真痛点：用户在 brief 中提供的逐字广告文案被 LLM 改写且无法定位源头；
- ad 剧本生成因其他原因演进为两段式——届时 gate 同步接入（step1 文件名注册表收敛后仅需登记一处，见 #985）。

## Prior requests

- #987 — ad 内容模式未接入 step1→step2 审核 gate（契约空白）
