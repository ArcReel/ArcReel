# 批量看素材时的上下文管理与分工：主对话还是子 Agent

> 本文服务于 #2675「决策：L2 视频审阅闭环」，隶属地图 #2667。要回答的问题是：首轮自动剪辑时，Agent 要看一集 30～60 个视频单元的联系表，这些审阅放在主对话里做，还是交给子 Agent 做、只收回每个单元的文字报告；如果交给子 Agent，报告里应该写什么。
>
> 本文只补充**上下文管理与分工**这个角度，以下内容不再重复：
> - 各项目的工具面，即工具名、联系表尺寸、ffmpeg 信号：见 [agent-editing-tool-surface.md](https://github.com/ArcReel/ArcReel/blob/research/agent-editing-tool-surface/docs/research/agent-editing-tool-surface.md)。
> - 剪辑数据模型：见 [agent-editing-prior-art.md](https://github.com/ArcReel/ArcReel/blob/research/agent-editing-prior-art/docs/research/agent-editing-prior-art.md)。
> - 各家多模态模型审视频的能力：见 [video-review-multimodal.md](https://github.com/ArcReel/ArcReel/blob/research/video-review-multimodal/docs/research/video-review-multimodal.md)。
>
> 本文只陈述一手事实，来源包括官方文档、源码和论文原文。调研日期为 2026-09-28。源码链接固定到调研当日读取的 commit。"估算"一律标明，指作者按文档公式自行计算的结果，不是引文。

## 一、核心结论

1. **一次看几十张图，主流做法都不放在长期运行的主对话里。** 常见做法有三种：
   - **子 Agent 看图，只把文字交回主 Agent。** 代表：Resolve MCP 的 `cut-reviewer`、video-use 的 editor/critic 子 Agent、video-os-v2 的 `footage-triager`、video-digest 的分块子 Agent。
   - **固定流水线里的一次性 VLM 调用，结果转成文字或 JSON 落盘。** 代表：NarratoAI、FireRed-OpenStoryline、VideoDB Director，以及几乎所有学术系统（LAVE、ExpressEdit、两个 VideoAgent、EditDuet 等）。
   - **主 Agent 自己看，但每次只看少量图，按需看。** 代表：Diffusion Studio editor，它明确禁用了子 Agent；OpenMontage 的自审阅也属于这一类。这种做法对应的是"改一处、看一处"的交互式剪辑，不是批量审阅。
2. **没有任何项目在主对话中途清理或压缩历史图片。** 各项目隔离图片的办法都是事先安排好的：
   - 交给子 Agent 或一次性调用；
   - 只返回路径；
   - 限制内联图片的数量和大小，例如 Diffusion 每次最多 4 张、每张不超过 1 MB；
   - 先看文字、后看像素。
3. **Anthropic 官方文档的指引与上述做法一致：**
   - 子 Agent 的中间工具结果（包括图片）不回到父对话，父对话只收到最终消息。
   - 多轮对话每轮都会重发全部历史，图片也算在内。
   - Claude Code 在触及上限时会**自动丢弃最早的图片**。
   - 同一份文档也提醒：需要频繁来回、共享大量上下文的任务应该留在主对话里做。
4. **审阅者交回的内容分两类：**
   - **要落盘复用的结果，用结构化格式**：Resolve 逐镜头 JSON、video-os-v2 的 selects YAML（带 in/out、`why_it_matches`、`risks`、`confidence`）、video-use 的 EDL JSON、OpenStoryline 的 `{caption, aes_score}`。
   - **一次性的批评，用带时间码的排序文字**：Resolve `cut-reviewer`、video-use critic、EditDuet Critic。

   审阅**生成视频**的学术系统（Anim-Director、AniMaker、VISTA）都先用便宜的指标或粗评筛一遍，再交给昂贵的视觉判断，最后返回分数或排序。只有 VISTA 在分数之外还逐维度给出批评意见。
5. **审阅结果持久化、按签名复用，只有少数项目真正做到了：**
   - Resolve MCP：签名包括源文件 stat、prompt 哈希、深度和帧预算；人工修改的行永远优先。
   - video-os-v2：签名是内容 SHA-256。
   - VideoDB：由服务端索引。
   - 学术系统的预处理产物按视频落盘，已经存在就跳过。
   - NarratoAI 写了结果缓存的 key 函数，但目前是死代码；Diffusion 的缓存只在内存里。
6. **对 ArcReel 的建议（详见第七节）：**
   - 首轮自动剪辑的批量审阅**交给专用的只读审阅子 Agent**。按场景或相邻单元分片，并行派发。
   - 每个视频单元返回一段紧凑的结构化报告：结论、建议保留区间、问题列表、与提示词的符合度、可以直接写进 `reason` 的一句话。
   - 主 Agent 只根据文字报告写时间线。
   - `inspect_video_units` 在主对话里仍然保留，但只用于对话中的少量抽查。

## 二、Anthropic 侧的约束与指南

### 2.1 子 Agent 的上下文隔离

- **父对话只收到最终消息。** Agent SDK 文档原文："intermediate tool calls and results stay inside the subagent; only its final message returns to the parent."，还有 "The parent receives the subagent's final message as the Agent tool result"。来源：[Agent SDK subagents](https://code.claude.com/docs/en/agent-sdk/subagents)。agent loop 文档也写道："The main agent's context grows by that summary, not by the full subtask transcript."（[agent-loop](https://code.claude.com/docs/en/agent-sdk/agent-loop)）
- **什么时候用子 Agent，什么时候留在主对话。** Claude Code 文档写得很直接（[sub-agents](https://code.claude.com/docs/en/sub-agents)）：
  - 用子 Agent："Use one when a side task would flood your main conversation with search results, logs, or file contents you won't reference again"，以及 "The work is self-contained and can return a summary"。
  - 留在主对话："The task needs frequent back-and-forth or iterative refinement"，以及 "Multiple phases share significant context"。
  - 同一页还提醒："Running many subagents that each return detailed results can consume significant context"。
- **只能通过 prompt 传入信息。** "The only content you pass from parent to subagent is the Agent tool's prompt string"。子 Agent 看不到父对话的历史，也看不到父对话的工具结果。来源：[Agent SDK subagents](https://code.claude.com/docs/en/agent-sdk/subagents)。
- **并行、深度与模型：**
  - 子 Agent 默认在后台运行，可以并发。默认最多 20 个同时运行，超出会报 `Concurrent subagent limit reached`。默认允许嵌套 3 层。
  - 每个子 Agent 可以单独指定 `model` 和 `effort`。
  - 子 Agent 继承主对话可用的 MCP 工具，并可以用 `tools` 或 `disallowedTools` 收窄。
  - 来源：[sub-agents](https://code.claude.com/docs/en/sub-agents)、[Agent SDK Python](https://code.claude.com/docs/en/agent-sdk/python)。
  - 注意：ArcReel 自己的约定写在 `agent_runtime_profile/CLAUDE.*.md`，规定"子智能体不能 spawn 子智能体"，多步工作流由主 Agent 链式派发。
- **子 Agent 输出没有独立的 schema 约束。** Python SDK 的 `output_format`（json_schema）只作用于顶层 query。文档里找不到针对单个子 Agent 的输出 schema，只能写在子 Agent 的 prompt 里约束格式。另一个办法是让子 Agent 调用工具把结果写进外部存储，再交回一个轻量引用。Anthropic 多 Agent 研究系统的博客描述过后一种做法："Subagents call tools to store their work in external systems, then pass lightweight references back to the coordinator."（[multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)）
- **摘要应该多长。** "Each subagent might explore extensively, using tens of thousands of tokens or more, but returns only a condensed, distilled summary of its work (often 1,000-2,000 tokens)."（[Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)）
- **代价与不适用的场景**（[multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)）：
  - 代价："multi-agent systems use about 15× more tokens than chats"。
  - 不适用："domains that require all agents to share the same context or involve many dependencies between agents are not a good fit"。
  - 派发要求："Each subagent needs an objective, an output format, guidance on the tools and sources to use, and clear task boundaries."
- **Opus 5 更倾向于派发子 Agent。** 提示词指南写道："Delegation pays off on genuinely independent, sizeable tracks of work, but it multiplies cost and time when applied to small tasks."（[prompting Claude Opus 5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5)）

### 2.2 多轮对话里的图片

- **每轮都会重发。** 原文："each request resends the full conversation history. If images are base64-encoded, the full image bytes are included in the payload on every turn"（[vision](https://platform.claude.com/docs/en/build-with-claude/vision)）。
- **数量上限：**
  - 200k 上下文窗口的模型：每个请求最多 100 张图；其他模型：600 张。
  - 请求里超过 20 张图时，每张图的边长不能超过 2000 px。这个计数包括历史轮次重发的图，也包括嵌在 `tool_result` 里的图。
  - ArcReel 的联系表长边不超过 2000 px，满足这条限制。
  - 来源：同上。
- **成本：**
  - 每张图约 `⌈w/28⌉ × ⌈h/28⌉` 个视觉 token。
  - 估算：一张 2000×1125 的联系表约 72×41 ≈ 2950 token。
- **缓存：**
  - 图片可以进入提示词缓存。但"Adding/removing images anywhere in the prompt affects message blocks"，也就是说，增删图片会让消息段的缓存失效。来源：[prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)。
  - Claude Code 在请求触及图片上限时，"removes a batch of the oldest images and PDFs from what it sends … Claude can no longer see the removed images"。来源：[Claude Code prompt caching](https://code.claude.com/docs/en/prompt-caching)。
  - Agent SDK 与 Claude Code 共用运行时，所以内嵌 Agent 很可能也会这样丢弃旧图。这一点是推断，未实测。
- **图片只能内联传递。** Agent SDK 的自定义工具返回图片时，"carries the image bytes inline, encoded as base64. There is no URL field"（[custom-tools](https://code.claude.com/docs/en/agent-sdk/custom-tools)）。因此 Files API 的 `file_id` 这条路在 MCP 工具结果里走不通，未核实有无例外。

### 2.3 事后清理的手段

- **工具结果清理**（API 的 context editing，`clear_tool_uses_20250919`）：
  - 超过阈值后，按时间顺序把最早的工具结果替换成占位文本。
  - 参数有 `trigger`（默认 100k input tokens）、`keep`（默认保留 3 个）、`clear_at_least`、`exclude_tools`。
  - 清理会让缓存失效。
  - 文档没有专门提到图片。"会连同 tool_result 里的图片一起清掉"是从"clears tool results"推出来的，未核实。
  - 来源：[context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing)。
- **压缩（compaction）：**
  - API 文档写明，被摘要的消息里的图片 "are gone once the block replaces them"（[compaction](https://platform.claude.com/docs/en/build-with-claude/compaction-on-demand)）。
  - Agent SDK 会在上下文接近上限时自动压缩，并提供 `PreCompact` hook（[agent-loop](https://code.claude.com/docs/en/agent-sdk/agent-loop)）。
- **两者的共同点：都是事后补救。** 图片被清掉之后，模型就再也看不到它；如果当时的结论没有写成文字，就只能重新看一遍。博客把工具结果清理称为 "One of the safest lightest touch forms of compaction"（[Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)），但它不能替代事先的隔离。

### 2.4 评审者模式

- **evaluator-optimizer 模式。** "particularly effective when we have clear evaluation criteria, and when iterative refinement provides measurable value"（[Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)）。
- **评分方式。** 多 Agent 研究系统的评审发现，"a single LLM call with a single prompt outputting scores from 0.0-1.0 and a pass-fail grade was the most consistent and aligned with human judgements"。

## 三、开源剪辑 Agent：谁来看，交回什么

| 项目 | 谁看图 | 交回给编辑者的内容 | 图片怎么隔离 | 批量与上限 | 结果持久化 |
|---|---|---|---|---|---|
| **DaVinci Resolve MCP** | 素材入库：主对话（`host_chat_paths`，每个 clip 两次工具调用）。成片审阅：仓库自带的 Claude Code 子 Agent | 入库：逐镜头 JSON。审阅：带 clip 序号和时间码的文字批评、配对差值加 pass/fail | 只返回路径；平铺联系表（约省 15 倍）；审阅子 Agent 专门用来"keep them out of the main context" | 每 clip、每 job、每天三级 token 上限，调用前预估、超额拒绝；逐 clip 串行 | SQLite 为真相源，按签名复用，人工修改优先，签名不匹配时拒绝复用 |
| **browser-use/video-use** | 主 Agent 先读文字（转写），在决策点看 `timeline_view`；挑 take 交给 editor 子 Agent；成片交给 critic 子 Agent | editor：EDL JSON `[{source,start,end,beat,quote,reason}]`。critic：结论加按严重度排序的问题（带时间码和证据） | 文字优先，"Not a scan tool" | 自评最多 3 轮 | 转写按文件缓存；`project.md` 作为跨会话记忆 |
| **mocchalera/video-os-v2** | `footage-triager` 子 Agent（haiku，后台）；`roughcut-critic` 子 Agent（sonnet） | triager：`selects_candidates.yaml`（segment_id、in/out、`why_it_matches`、`risks`、`confidence`）。critic：审阅报告加经过校验的补丁 | 先铺全部联系表，再只看入围片段 | `maxTurns: 10` | VLM/STT 结果按内容 SHA-256 缓存 |
| **FireRed-OpenStoryline** | 流水线节点，每个 clip 发一次 MCP sampling VLM 调用 | `{caption, aes_score}`，再做一次纯文本的整体摘要 | Agent 的 ToolMessage 只拿到 `node_summary`，完整结果存进 ArtifactStore | 每 clip 2～6 帧，长边 600 px；全局最多 48 个图片块 | 按会话存 ArtifactStore |
| **catalan-adobe/skills `video-digest`** | 短视频直接在主对话读；长视频切块，"one Agent per chunk, ALL IN PARALLEL" | 每块写一份 `chunk_N_summary.txt`（关键时刻、值得注意的帧） | 5×4 联系表，每张 20 帧 | 所有块并行 | 文件落盘 |
| **Diffusion Studio editor** | 全部在主 Agent 看；子 Agent 被显式禁用 | —— | 内联图片最多 4 张、每张不超过 1 MB，超出只给路径；聊天记录重放时单独限额 | 联系表最多 12 格；`FRAME_CAP=100`；skill 要求"读到够回答为止" | 转写只在内存里，其余不缓存 |
| **OpenMontage** | 主 Agent 自审阅（skill 协议）；生成素材由人在 Backlot 审批 | markdown 分级意见，结论为 PASS/REVISE/PASS_WITH_WARNINGS | 只返回路径 | 参考片 20 帧，成片抽 4 帧 | 审阅文字写进检查点 |
| **NarratoAI** | 固定流水线，每批帧做一次 VLM 调用 | 每批一份 `frame_observations[]` JSON，转成 markdown 后交给文本 LLM | 图片不进入写稿 LLM 的上下文 | 每批 10 帧，并发 2 | 关键帧有缓存；VLM 结果不复用（key 函数是死代码） |
| **VideoDB Director** | VideoDB 服务端索引 | 推理引擎只收到 `scene_index_id`；选片时对场景描述做分块并行的一次性文本调用 | 图片从不进入 LLM | 按块并行 | 索引存在服务端并复用 |

逐项目要点与出处：

### 3.1 DaVinci Resolve MCP（[`89da04b1`][resolve]）

- **成片审阅交给子 Agent，理由写在描述里。**
  - `.claude/agents/cut-reviewer.md` 的描述是："Reads frames as images, so run it as a subagent to keep them out of the main context."
  - 职责边界："Never modify the timeline. You review; the main session edits."
  - CHANGELOG v2.80.2："Two review subagents in `.claude/agents/`, run in their own context so frame images stay out of the main session"。
  - 来源：[cut-reviewer.md][resolve-cr]、[CHANGELOG.md][resolve-cl]。
- **素材入库分析反而放在主对话里。**
  - `host_chat_paths` 协议是 "two tool calls per clip"：服务端返回帧路径和 JSON-schema 提示，宿主读完图后调用 `commit_vision` 回写逐镜头 JSON，字段包括 `clip_summary`、`shot_descriptions[]{description, editing_value, qc_flags}`。
  - 主对话里没有任何清理机制，只靠平铺联系表省 token。skill 原文："Reading those individually … costs ~1,100 tokens each … Tile them instead … Roughly a 15x saving with no loss of coverage."
  - 来源：[media-analysis-guide.md][resolve-mag]、[resolve-rough-cut/SKILL.md][resolve-rc]。
- **预算是真正的 token 账本。**
  - [`analysis_caps.py`][resolve-caps] 有三档预设，每 clip / 每 job / 每天的上限分别为：minimal 16k / 60k / 150k，standard 100k / 1M / 2M，generous 250k / 3M / 6M。
  - 按每帧 1000 token 在**调用前**估算，超额就拒绝。
  - 批处理默认每次只处理 1 个 clip，最多 25 个，不做并行视觉分析。
- **持久化做得最完整。**
  - [`analysis_store.py`][resolve-store]："The per-project SQLite DB … is the source of truth for clip analysis … human rows always win and survive re-analysis"。
  - 缓存签名包含分析版本、深度、帧预算、源文件大小与 mtime、视觉供应商、`prompt_hash`。
  - `commit_vision` 会拒绝过期的提交。
  - 已有分析但无法校验时，返回 `reuse_blocked`，而不是悄悄重跑。

### 3.2 browser-use/video-use（[`b8770638`][vu]）

- **先读文字，再看画面。** README 给出的理由："30,000 frames × 1,500 tokens = 45M tokens of noise … 12KB text + a handful of PNGs"。`timeline_view` 被明确定位为 "Not a scan tool — use it at decision points"。来源：[SKILL.md][vu-skill]。
- **多 take 挑选交给 editor 子 Agent**，返回 EDL JSON。skill 里写着："The structure is load-bearing"。
- **要发布的成片交给 critic 子 Agent。** 原文："a verdict, ranked problems with timecodes and evidence (frames, levels), and the 5 fixes to do first. Fresh eyes catch what the author stopped seeing"。

### 3.3 mocchalera/video-os-v2（[`04c39350`][vos]）

- 这是一个小项目，约 5 star。收录它是因为它把"triage 子 Agent → selects 文件 → 主流程规划"这条链做得完整。
- [`footage-triager.md`][vos-tri] 的 frontmatter 为 `model: haiku`、`background: true`、`maxTurns: 10`。
- 分类有 hero/support/transition/texture/dialogue/reject；每个候选带 segment_id、in/out、`why_it_matches`、`risks`、`confidence`。
- [`analysis-cache.ts`][vos-cache]："Cache key: SHA-256(full source-content SHA-256 + file size + duration_us)"。

### 3.4 FireRed-OpenStoryline（[`c9e94521`][fr]）

- `understand_clips` 节点对每个 clip 串行调用一次 VLM（`max_tokens=2048`），返回 `{caption, aes_score}`。
- `GLOBAL_MAX_IMAGE_BLOCKS = 48`，理由是 "to prevent payload overflow"。
- 节点拦截器只把 `node_summary` 放进 Agent 的 ToolMessage。完整结果要通过 `read_node_history` 按需读取。
- 来源：[understand_clips.py][fr-uc]、[node_interceptors.py][fr-ni]。

### 3.5 Diffusion Studio editor（[`666cdced`][ds]）

- **内嵌的 Claude Agent SDK 聊天禁用了子 Agent**：`disallowedTools: ["Agent", "Task"]`（[claude.ts#L218][ds-claude]）。引入这一行的提交没有说明理由。
- **内联图片有上限。** `INLINE_MAX_IMAGES = 4`、`INLINE_MAX_BYTES = 1 MB`，超出只返回路径（[present.ts][ds-present]）。
- **聊天记录另有限额。** 原文："Images ride inline in the transcript, which is replayed whenever a chat opens, so a capture is fine but a wall of full-size renders is not"（[harness.ts][ds-harness]）。
- **skill 要求按需看。** 原文："Read only as much of the footage as the answer requires"、"Only questions about what is *seen* need frames"（[watch.md][ds-watch]）。
- 它面向的是逐处修改、逐处验证的交互式剪辑，不涉及一次审几十个 clip。

### 3.6 OpenMontage（[`08e2151f`][om]）

- **审阅就是主 Agent 的自审阅协议。** 原文："This skill replaces the Python reviewer class with an instruction-driven self-review protocol"（[reviewer.md][om-rev]）。
- **子 Agent 只在一处作为可选提示出现。** 原文："Use a sub-agent to read thumbnail images for visual confirmation if needed"（[asset-director.md#L154][om-ad]），没有配套的子 Agent 定义。
- **生成素材由人审。** assets 阶段是人工门控，在 Backlot 的 filmstrip 上逐场景审批。`quality_score` 是按供应商写死的常量，不是审阅结果。

### 3.7 NarratoAI（[`9fa69e02`][narrato]）与 VideoDB Director（[`70e0b3df`][director]）

- **NarratoAI：**
  - 每批 10 帧、并发 2，逐批得到 `frame_observations` JSON。
  - 结果先转成 markdown（`## 片段 N / 时间范围 / 片段描述`），再交给写稿的 LLM（[generate_narration_script.py][n-gen]）。
  - 结果缓存的 key 函数 `_build_cache_key` 只有测试在调用（[frame_analysis_service.py][n-fa]），所以每次运行都会重新调用 VLM。
- **Director：**
  - `IndexAgent` 只把 `scene_index_id` 交回推理引擎（[index.py][d-idx]）。
  - 剪辑 Agent 有独立的 `agent_context["editing"]`，按会话存进数据库（[editing/agent.py][d-edit]）。

## 四、学术系统

| 系统 | 谁看像素 | 规划者看到什么 | 预算与上限 | 持久化 | 消融或理由 |
|---|---|---|---|---|---|
| **LAVE**（IUI 2024，[2402.10294](https://arxiv.org/abs/2402.10294)） | 离线：LLaVA 每秒一帧生成描述，GPT-4 汇总成每个 clip 的标题和摘要 | 只有文字"visual narrations"；只有裁剪用逐帧描述 | 历史最多保留 6000 token，一次调用约容纳 40 个视频的描述 | 预处理一次，所有功能复用 | "8192 token window … restrict the amount of video information" |
| **ExpressEdit**（IUI 2024，[2403.17693](https://arxiv.org/abs/2403.17693)） | 离线 CV：SAM、InternVideo、BLIP-2 | 每 10 s 片段的文字摘要；先检索 top-10 再交给 GPT-4 | 1 fps，10 s 为一个单元 | 离线预处理 | 实时交互、元数据量太大 |
| **HKUDS VideoAgent**（[2606.23327](https://arxiv.org/abs/2606.23327)，[`f207987e`][hku]） | 预处理由 Gemini 描述片段；裁剪 Agent 只在检索到的片段内看帧，返回一个起始帧号 | 压缩后的视觉摘要加每个镜头的检索查询 | 每段 3 帧，检索 top-k 为 20～30 | KV 与向量库落盘，已存在就跳过；LLM 缓存 | 帧太少召回下降，帧太多引入噪声 |
| **VideoAgent（Stanford）**（[2403.10517](https://arxiv.org/abs/2403.10517)） | VLM 为 CLIP 检索到的帧生成描述 | 按帧序拼接的描述；自评置信度 1～3 | 初始 5 帧最优，第 3 轮后饱和，平均 8.4 帧 | CLIP 帧特征缓存复用 | "LLMs suffer from long contexts and can be easily distracted" |
| **VideoAgent（Fan，ECCV 2024）**（[2403.11481](https://arxiv.org/abs/2403.11481)） | 离线建时间记忆（描述）和对象记忆（SQL）；像素只通过 VQA 工具按需看 | 工具返回的文字 | "Due to the context limit, the longest time window allowed is 15 segments" | `preprocess/<video>` 已存在就跳过 | 只用描述 40.7 分；加上 VQA 和定位 +22，加上对象记忆 +15.3 |
| **EditDuet**（SIGGRAPH 2025，[2509.10761](https://arxiv.org/abs/2509.10761)） | 预计算描述；Critic 是纯文本 | Critic 返回自由文字反馈或 RENDER | 检索每次最多 5 条 | 预计算元数据 | 不用 VLM Critic，因为 token "greatly exceeds current open source VLM context sizes"；加 Critic 后失败率 23.8% → 19.5% |
| **Prompt-Driven Agentic Video Editing**（[2509.16811](https://arxiv.org/abs/2509.16811)） | 建索引时一次性看（480p、1 fps）；辅助 Agent 判断"要不要再看画面" | 压缩摘要（"guided context compression"） | "low cognitive load per LLM invocation" | 持久索引，可复用 | 推理与结构化输出分开做，因为同时做时 "performance often degrades" |
| **Anim-Director**（[2408.09787](https://arxiv.org/abs/2408.09787)） | 10 个生成候选先用指标筛到 3 个，再交给 GPT-4V 选 1 个 | 选中结果 | 每个视频拼成 5 帧的合成图 | —— | 便宜的过滤器在前 |
| **AniMaker**（[2506.10540](https://arxiv.org/abs/2506.10540)） | Reviewer 是 14 项指标，不是 LLM | 每个 clip 结合前后镜头打分 | MCTS 每节点约 4.37 次生成 | —— | 每个 clip 只生成 1 个候选时 −7.1% |
| **VISTA**（[2510.15831](https://arxiv.org/abs/2510.15831)） | 先逐视频生成 probing critique，再两两比较；三方法官按维度出批评和 1～10 分 | 批评加分数，用于改写提示词 | 每轮约 0.7M token、28 个视频 | —— | 单个法官在多场景任务上只有 17.2% |
| **Unified Agentic Video Editing**（[2609.12769](https://arxiv.org/abs/2609.12769)） | Agent 不看原视频，只读共享的结构化文字表示；视觉检查是单独的 `contact_sheets` 渲染步骤 | 集、场、镜头元数据 | 预告片流水线：24 步、52 次调用、约 497K 输入 token | 共享文字表示 | —— |

各系统的共同模式：

1. **"描述器 → 文字 → 规划者"几乎是通用做法。** 规划者或批评者很少直接看像素。
2. **文字分两种粒度。** 按 clip 的摘要用于规划；逐秒描述只在裁剪时使用。
3. **按需再看有闸门。** 由规划者判断信息够不够，再决定是否调用看图工具。
4. **消融一再显示帧多了、轮数多了会变差。** 这是在上下文里加噪声的代价。

## 五、商业产品

- **Descript Underlord：**
  - 抽帧后交给多模态模型生成描述，"stitch them together into like a big file, which you can literally just read"。
  - 按内容类型调整帧数和分辨率。
  - 索引是否持久化没有公开信息。
  - 来源：[Underlord can watch videos](https://www.descript.com/blog/article/underlord-ai-can-watch-videos)。
- **Adobe Premiere：**
  - Media Intelligence 的分析在本机完成（[Adobe blog](https://blog.adobe.com/en/publish/2025/04/02/introducing-new-ai-powered-features-workflow-enhancements-premiere-pro-after-effects)）。
  - "结果存进 media cache"这一说法来自帮助页摘要，原页面返回 403，未核实。
  - 2026 年的 AI Assistant 怎么看素材，没有公开信息。
- **Runway Agent、CapCut/剪映 AI Agent：** 官方页面没有说明怎么审阅生成镜头，也没有说明怎么管理上下文。

## 六、放在 ArcReel 场景下的估算

以下都是作者按 2.2 节的公式自行计算，不是引文。前提：一张联系表约 3k token，一集 60 个视频单元，每次工具调用看 1 个单元，不计提示词缓存。

| 方案 | 累计输入的图片 token | 首轮之后每轮对话的额外负担 | 风险 |
|---|---|---|---|
| 全部在主对话里看 | 3k × (1+2+…+60) ≈ **5.5M** | 每轮都重发约 180k 图片 token | 200k 窗口的模型只允许 100 张图；接近上限时触发压缩或丢弃旧图，看过的图就没了；用户后续每改一处，都要背着这 180k |
| 6 个子 Agent，每个 10 个单元 | 6 × 3k × (1+…+10) ≈ **1.0M** | 主对话只增加约 60 × 150 ≈ 9k token 的文字报告 | 子 Agent 自身也有开销，约为额外的系统提示加报告 |
| 10 个子 Agent，每个 6 个单元 | 10 × 3k × (1+…+6) ≈ **0.63M** | 同上 | 分片太细时，子 Agent 看不到跨片段的连贯性 |

- 如果一次调用可以看多个单元，累计 token 还会继续下降。
- 提示词缓存能降低重发的单价，但是主对话里每新增一张图，消息段的缓存就会失效。

## 七、对 ArcReel 的建议

背景：
- 已经确定的工具形态（#2674）：只读工具 `inspect_video_units`，返回服务端生成的联系表（最多 12 帧、长边不超过 2000 px、带时间码），附带 ffmpeg 确定性信号，按视频版本缓存。
- 结论写进剪辑片段的 `reason`；挑版本就是改 current；重生成必须经用户同意。
- 内嵌 Agent 已经在用"主 Agent 编排、子 Agent 干重活"的结构，见 `agent_runtime_profile/CLAUDE.drama.md`：子 Agent 用于"需要大量上下文 → 保护主 Agent context"，而且子 Agent 不能再派发子 Agent。

### 1. 首轮批量审阅交给子 Agent，对话中的抽查留在主对话

- **新增只读的审阅子 Agent**，暂定名 `review-video-units`。
  - 工具只给 `inspect_video_units` 和读取剧本、项目状态的只读工具，**不给写时间线的工具**。这一点照搬 Resolve 的 "You review; the main session edits"。
  - 主 Agent 在首轮自动剪辑时并行派发多个这样的子 Agent，收齐报告后再写剪辑时间线。
- **分片原则：**
  - 按场景或相邻的视频单元分片，每片约 6～10 个单元，让子 Agent 能判断相邻镜头之间是否连贯。
  - 同一个视频单元的多个候选版本放在同一片里比较。
  - 每个子 Agent 的图片总数控制在 20 张以内，不触发更严的尺寸规则，也不会触发 Claude Code 丢弃旧图。
  - 一集 60 个单元大约拆成 6～10 个子 Agent，低于默认 20 个的并发上限。
- **派发 prompt 要自带全部上下文**，因为子 Agent 看不到父对话：
  - 单元 ID 与版本；
  - 每个单元的剧本段落和视频提示词；
  - 相邻单元的摘要；
  - 本集的节奏或时长目标；
  - 报告格式。

  这四项（目标、输出格式、工具、边界）对应 Anthropic 对子 Agent 派发的要求。
- **主对话仍然保留 `inspect_video_units`**，用在用户说"第 12 镜换个版本看看"这类对话里的少量抽查：一次看 1～3 个单元。这类场景需要频繁来回、依赖共享上下文，官方指引就是放在主对话里；Diffusion Studio 的做法也是这样。
- **工具描述里写明使用边界**，例如"超过 N 个单元的批量审阅应交给审阅子 Agent"。服务端对单次调用的单元数设硬上限，参照 Diffusion 每次最多 4 张图。外部 MCP 客户端没有 ArcReel 的子 Agent 定义，只能靠这条上限兜底，防止一次调用把宿主的上下文撑爆。

### 2. 每个单元的报告要紧凑、结构化、可以直接落到时间线上

子 Agent 的最终消息按单元逐条给出，每条大约 100～200 token。字段建议如下：

| 字段 | 内容 | 依据 |
|---|---|---|
| `unit_id`、`version` | 审阅的对象；有多个候选版本时列出比较了哪些 | Resolve 按 clip 输出；#2674 规定挑版本就是改 current |
| `verdict` | `use` / `trim` / `prefer_version(vX)` / `propose_regenerate` / `unusable` | video-os-v2 的 hero…reject 分类；Resolve `rank_takes` 只排序、不宣称"最好" |
| `keep_range` | 建议的入出点（秒，基于源素材），并注明依据是 ffmpeg 信号还是看图判断 | LAVE、video-use 的 trim segment；ffmpeg 信号给出精确边界，看图给出粗定位 |
| `issues[]` | `{kind, start, end, severity, confidence, evidence_tc}`，其中 `evidence_tc` 是联系表上的时间码 | video-use critic 的"timecodes and evidence"；Resolve 的 `qc_flags` |
| `prompt_fit` | 1～5 分加一句理由 | 多模态审阅调研里模型相对最可靠的就是"动作与提示词不符"；VISTA 同时给分数和批评 |
| `continuity` | 可选：与相邻单元在角色、场景、动作上是否衔接 | AniMaker 结合前后镜头打分 |
| `reason` | 一句话，可以直接写进剪辑片段的 `reason` 字段 | OpenMontage 的 `cut.reason`；#2674 |

报告末尾再单独列两项：
- **需要用户决定的事项**：重生成建议、不可用的单元。主 Agent 据此汇总后向用户征求确认。
- **本片的整体观察**：几句话即可。

注意事项：
- 格式靠子 Agent 的 prompt 约束，因为 SDK 不支持单独给子 Agent 设输出 schema。主 Agent 按单元 ID 核对是否每个单元都有报告，缺了就补派。
- 模型给出的缺陷判断只用来排序和提建议（见多模态审阅调研第 6 节），不能作为自动弃用素材的唯一依据。

### 3. 持久化：先随时间线落地，再考虑按版本缓存审阅记录

- **第一步：结论落到剪辑时间线上，不另建存储。** 结论写进 `reason`，挑版本改 current，入出点写进 cut。这些内容本身会随时间线版本一起持久化，后续会话读时间线就能看到。
- **第二步（可以晚做）：把审阅报告按视频版本存成记录。** 参照 Resolve 的签名设计，签名包括：
  - 视频单元与版本；
  - 联系表参数；
  - 审阅提示词或评分标准的版本；
  - 审阅所用的模型。

  这样同一集建第二条时间线、或换会话之后，就不必重新看 60 张图；人工修改过的结论永远优先。落地方式可以是子 Agent 调用一个写审阅记录的工具，只交回引用；也可以由主 Agent 转写。触发条件：观察到重复审阅确实成为成本热点。
- **不建议依赖 compaction 或工具结果清理来"事后腾空间"。** 清掉的图片不可恢复；也没有任何项目用这种方式管理批量看图。

### 4. 模型与成本

- **子 Agent 的模型保持可配置，默认继承主 Agent。** 调研对象的选择很分散：Resolve 的审阅子 Agent 用 opus，video-os-v2 的 triage 用 haiku。而生成视频的缺陷识别本身就不可靠（见多模态审阅调研第 3 节），换成更便宜的模型之前应该先用 ArcReel 自己的片段做小样本对比。
- **先跑确定性信号。** 在派发子 Agent 之前，ffmpeg 信号以文字形式交给主 Agent。信号已经给出明确结论的情况，例如整段黑帧、冻结，可以在报告里直接引用，但仍然要看图确认。这对应 Anim-Director 和 VISTA 的"便宜过滤在前"。
- 子 Agent 本身有固定开销，包括系统提示和工具定义。但在这个场景里图片 token 占大头，分片后总输入量反而比放在主对话里低一个数量级（见第六节）。

## 八、未核实与局限

- Agent SDK 内嵌会话在触及图片上限时是否也像 Claude Code 那样丢弃最早的图片、压缩后图片如何处理，都是按文档推断，未实测。
- `clear_tool_uses` 是否会清除 tool_result 里的图片，文档没有写明。
- MCP 工具结果能否用 Files API 的 `file_id` 代替内联 base64，未找到依据。现有文档只描述了内联方式。
- Diffusion Studio 禁用子 Agent的理由没有公开。
- NarratoAI 的结果缓存函数"未被调用"是 grep 得出的结论。
- video-os-v2 是低 star 的个人项目，只作为模式示例。
- Adobe、Runway、剪映 AI Agent 的内部做法没有公开信息。
- 第六节的 token 估算没有计入提示词缓存、系统提示和文字输出，只用于比较量级。
- 分片大小（6～10 个单元）是按图片数规则和连贯性需要推出来的起点，需要实测后再调整。

[resolve]: https://github.com/samuelgursky/davinci-resolve-mcp/tree/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23
[resolve-cr]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/.claude/agents/cut-reviewer.md
[resolve-cl]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/CHANGELOG.md
[resolve-mag]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/docs/guides/media-analysis-guide.md
[resolve-rc]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/.agents/skills/resolve-rough-cut/SKILL.md
[resolve-caps]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/utils/analysis_caps.py
[resolve-store]: https://github.com/samuelgursky/davinci-resolve-mcp/blob/89da04b19b0dbcbf6e5a53a54fe80542a8d2ef23/src/utils/analysis_store.py
[vu]: https://github.com/browser-use/video-use/tree/b877063835e6ea6e457124da7e28a0ae26691dc3
[vu-skill]: https://github.com/browser-use/video-use/blob/b877063835e6ea6e457124da7e28a0ae26691dc3/SKILL.md
[vos]: https://github.com/mocchalera/video-os-v2/tree/04c39350f915f9ee96edc25b4dd9a8c1dee305e0
[vos-tri]: https://github.com/mocchalera/video-os-v2/blob/04c39350f915f9ee96edc25b4dd9a8c1dee305e0/.claude/agents/footage-triager.md
[vos-cache]: https://github.com/mocchalera/video-os-v2/blob/04c39350f915f9ee96edc25b4dd9a8c1dee305e0/runtime/pipeline/analysis-cache.ts
[fr]: https://github.com/FireRedTeam/FireRed-OpenStoryline/tree/c9e945215586f45c12a61c1951ee9a8e9c43a027
[fr-uc]: https://github.com/FireRedTeam/FireRed-OpenStoryline/blob/c9e945215586f45c12a61c1951ee9a8e9c43a027/src/open_storyline/nodes/core_nodes/understand_clips.py
[fr-ni]: https://github.com/FireRedTeam/FireRed-OpenStoryline/blob/c9e945215586f45c12a61c1951ee9a8e9c43a027/src/open_storyline/mcp/hooks/node_interceptors.py
[ds]: https://github.com/diffusionstudio/editor/tree/666cdced1f6b97a792b63e551f45797649efb27a
[ds-claude]: https://github.com/diffusionstudio/editor/blob/666cdced1f6b97a792b63e551f45797649efb27a/packages/agent-chat/src/host/claude.ts#L218
[ds-present]: https://github.com/diffusionstudio/editor/blob/666cdced1f6b97a792b63e551f45797649efb27a/apps/desktop/src/dapi/present.ts#L19-L21
[ds-harness]: https://github.com/diffusionstudio/editor/blob/666cdced1f6b97a792b63e551f45797649efb27a/packages/agent-chat/src/host/harness.ts#L70-L76
[ds-watch]: https://github.com/diffusionstudio/editor/blob/666cdced1f6b97a792b63e551f45797649efb27a/docs/skills/watch.md
[om]: https://github.com/calesthio/OpenMontage/tree/08e2151fa02de28a5d6a312b3d575692bf147ad7
[om-rev]: https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/skills/meta/reviewer.md
[om-ad]: https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/skills/pipelines/documentary-montage/asset-director.md#L154
[narrato]: https://github.com/linyqh/NarratoAI/tree/9fa69e022d4add41205ee385207561df8796b3f1
[n-gen]: https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/app/services/generate_narration_script.py#L66-L94
[n-fa]: https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/app/services/documentary/frame_analysis_service.py
[director]: https://github.com/video-db/Director/tree/70e0b3dfdf59c679a25f4bea511e3cc4c5f2457f
[d-idx]: https://github.com/video-db/Director/blob/70e0b3dfdf59c679a25f4bea511e3cc4c5f2457f/backend/director/agents/index.py
[d-edit]: https://github.com/video-db/Director/blob/70e0b3dfdf59c679a25f4bea511e3cc4c5f2457f/backend/director/agents/editing/agent.py
[hku]: https://github.com/HKUDS/VideoAgent/tree/f207987e3cffb554aaa6ffdbe733efb30f4b51ed
