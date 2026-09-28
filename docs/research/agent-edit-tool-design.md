# 编码 Agent 原生读写工具的设计，及其对结构化文档编辑工具的启示

> 状态：调研完成（2026-09-28）。
> 问题：主流编码 Agent harness 如何设计 read / write / edit 工具？哪些经验可迁移到"LLM Agent 编辑结构化文档"的工具上——对象是一集的剪辑时间线（有序片段 + 入出点、转场、字幕/旁白、BGM 轨），每次写入追加一条不可变修订，每集约数十个片段。
> 相关：[`agent-editing-prior-art.md`](https://github.com/ArcReel/ArcReel/blob/research/agent-editing-prior-art/docs/research/agent-editing-prior-art.md)（`research/agent-editing-prior-art` 分支，调研的是剪辑 Agent 产品，本文调研的是工具接口）。
> 版本基线：Claude Code 2.1.283（CHANGELOG 与本机捆绑二进制，bun 打包、标识符已压缩；二进制内字符串均为模型可见的工具描述或 tool_result 文本）；`badlogic/pi-mono` @ `6f75515`；`openai/codex` @ `1cc7e23`；Anthropic 平台文档 2026-09 版本。

## 结论速览

| 维度 | Claude Code | Anthropic text editor / memory 工具（API） | pi | Codex CLI | Aider |
|---|---|---|---|---|---|
| 写粒度 | 单点 `str_replace` + `replace_all`；MultiEdit 已移除 | 单点 `str_replace` / `insert` / `create` | 一次调用内 `edits[]` 批量，彼此不重叠，全部相对原文件匹配 | 一个 patch 覆盖多文件多 hunk | 多个 SEARCH/REPLACE 块，或整文件重写 |
| 定位方式 | 精确字符串，必须唯一 | 精确字符串，必须唯一 | 精确字符串（失败后退到规范化模糊匹配），必须唯一 | 上下文行（默认前后 3 行）+ `@@` 锚点，无行号 | 字符串块；明确反对行号 |
| 写后返回 | 一句话确认，并告诉模型"你上下文里的文件状态是最新的，不必回读" | 参考实现：确认 + 改动处前后 4 行带行号片段 + "Review the changes" | 仅给模型一句 `Successfully replaced N block(s)`；diff 只给 UI | `Success. Updated the following files:` + A/M/D 清单 | — |
| 过期读检测 | 有：read-before-edit + 自上次读取后文件已变的检测；2.1.208 起放宽为"old_string 在当前内容仍唯一匹配则照常写入并附提示" | 无（留给实现方） | 无；只有按文件串行化的写队列 | 无显式检测，靠上下文行匹配隐式兜底 | 无 |

对剪辑时间线工具的建议（§6 展开）：**按稳定 ID 定位的批量操作（一次调用一个原子事务）为主写接口，整份替换作为兜底**；**读返回完整但紧凑的 JSON 是合适的**（几十个片段量级）；**写返回简短确认 + 新修订号 + 受影响片段的新状态，不回传整份文档**；**基于 base revision 的乐观并发是标准做法且在"追加不可变修订"模型下几乎零成本，建议采用，但失败信息要可操作，并可仿 Claude Code 对不冲突的写做宽松处理**。

## 1. Claude Code：Read / Write / Edit

### 1.1 工具集与 Edit 的语义

- 官方工具参考：Edit "Makes targeted edits to specific files using exact string replacement (no regex or fuzzy matching)"，写入前须通过三项检查：read-before-edit、`old_string` 精确匹配、`old_string` 唯一（否则补充上下文或设 `replace_all: true`）。—— [code.claude.com/docs/en/tools-reference](https://code.claude.com/docs/en/tools-reference)
- 输入 schema（Agent SDK `sdk-tools.d.ts`，`@anthropic-ai/claude-agent-sdk` 0.3.283）：`FileEditInput { file_path, old_string, new_string /* must be different from old_string */, replace_all?: boolean /* default false */ }`。
- 模型可见的工具描述（二进制内字符串）要点：
  - "Performs exact string replacements in files."
  - "The edit will FAIL if `old_string` is not unique in the file. Either provide a larger string with more surrounding context to make it unique or use `replace_all` to change every instance of `old_string`."
  - "Use `replace_all` for replacing and renaming strings across the file."
  - 明确告诉模型 Read 输出的行号前缀格式，要求 `old_string` 不得包含行号前缀——说明"读输出为模型可读而加的装饰"必须与"写入时的定位文本"严格区分。
- 2.1.91："Edit tool now uses shorter `old_string` anchors, reducing output tokens"（CHANGELOG）——输出 token 成本是持续优化目标。

### 1.2 read-before-edit 与过期读（乐观并发）

- 错误文本（二进制）：
  - "File has not been read yet. Read it first before writing to it."
  - "File has been modified since read, either by the user or by a linter. Read it again before attempting to write it."
  - "File content has changed since it was last read. This commonly happens when a linter or formatter run via Bash rewrites the file. Call Read on this file to refresh, then retry the edit."
  这是典型的"读时记录版本（mtime/内容），写时比对"的乐观并发；错误信息直接给出下一步动作（Read 后重试）。
- 放宽（v2.1.208 起，tools-reference）："Files changed on disk after Claude last read them can still be edited when `old_string` matches current content exactly and unambiguously … result notes the file carries other changes so Claude re-reads before edits depending on surrounding content"。对应 tool_result 附注："(note: the file had been modified on disk since you last read it — the edit applied cleanly, but the file contains other changes not in your context. Read it before edits that depend on surrounding content.)"。CHANGELOG 2.1.208："Fixed the Edit tool failing on files modified after reading when the target text still matches uniquely"。
  - 含义：**以"定位锚点仍唯一匹配当前内容"作为冲突判据，比"版本号严格相等"更宽松**；无关的并发改动不再强制一轮 Read-重试。
- 新模型可编辑未读文件（在读取无需权限提示时）；Opus 4.6、Haiku 4.5 及更早模型仍强制先读（tools-reference）。CHANGELOG 2.1.228 记录 Write 对齐此规则（"newer models can overwrite an existing file they haven't read this session, matching the Edit tool's rules"）。
- 读的去重：若文件自上次读取未变，重复 Read 返回存根 "File unchanged since last read. The content from the earlier Read tool_result in this conversation is still current — refer to that instead of re-reading."（二进制）——同样出于 token 经济。

### 1.3 Edit 返回什么

- 模型看到的 tool_result（二进制中 `mapToolResultToToolResultBlockParam`）：
  - 成功：`The file {path} has been updated successfully.`；`replace_all` 时 `… All occurrences were successfully replaced.`
  - 附加一句 " (file state is current in your context — no need to Read it back)"（用户未改动提议内容时）。
  - 用户修改了提议内容时："The user modified your proposed changes before accepting them."
  - **不返回片段也不返回 diff**。结构化的 `structuredPatch` / `originalFile` / `gitDiff` 只存在于 SDK 输出类型 `FileEditOutput`（供 hook、UI 使用），不进模型上下文。
- 与 API 参考实现（§2.1）返回 ±4 行片段不同：Claude Code 的设计假设模型能从"自己发出的 old/new + 先前读到的内容"推出新状态，直接断言"你的上下文就是真相"，以省 token 并抑制无意义回读。

### 1.4 MultiEdit 的移除

- MultiEdit（单文件多处替换的批量工具）在 2025-10 前后（约 v2.0.8）从工具列表消失；当前 tools-reference 已无此工具。用户报告见 [anthropics/claude-code#8994](https://github.com/anthropics/claude-code/issues/8994)、[#11125](https://github.com/anthropics/claude-code/issues/11125)、[HN 45473139](https://news.ycombinator.com/item?id=45473139)。
- **未找到 Anthropic 的官方说明**：CHANGELOG 2.0.x 无相关条目，issue 被机器人按不活跃关闭，HN 讨论中仅有推测（"Probably had too many bugs"）。二进制中 Edit 的渲染代码仍保留对 `edits` 字段的分支（`if(e.edits!=null)return"Update"`），可能是兼容旧会话。结论：移除事实可证，动机无一手来源，本文不做推断。

## 2. Anthropic API：text editor 工具、memory 工具与工程博客

### 2.1 text editor 工具（`str_replace_based_edit_tool`）

来源：[platform.claude.com/docs/en/agents-and-tools/tool-use/text-editor-tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/text-editor-tool)

- 命令：`view`（可带 `view_range`，1 起始，`-1` 表示到末尾）、`str_replace`、`create`、`insert`（在 `insert_line` 之后插入，0 为文件开头）。schema 内建于模型、不可修改（"schema-less tool"）。
- 版本史："`text_editor_20250429` … removes the `undo_edit` command"；`text_editor_20250728` 加 `max_characters` 控制 `view` 截断，"otherwise identical"。初版 `20241022` 含 `undo_edit`。
- `old_str` "must match exactly, including whitespace and indentation"；实现须知 "Unique matching: Make sure replacements match exactly one location to avoid unintended edits."
- 推荐错误文本（引导模型下一步）：多匹配 "Error: Found 3 matches for replacement text. Please provide more context to make a unique match."；无匹配 "Error: No match found for replacement. Please check your text and try again."
- 行号："Line numbers are not required, but they are essential for successfully using the `view_range` parameter … and the `insert_line` parameter"。
- 文档示例的成功返回仅为 "Successfully replaced text at exactly one location."
- 官方参考实现 [anthropic-quickstarts `computer_use_demo/tools/edit.py`](https://github.com/anthropics/anthropic-quickstarts/blob/main/computer-use-demo/computer_use_demo/tools/edit.py)：`SNIPPET_LINES = 4`，成功时返回 "The file {path} has been edited. " + 改动处前后 4 行 `cat -n` 片段 + "Review the changes and make sure they are as expected. Edit the file again if necessary."；多匹配时报出所在行号："Multiple occurrences of old_str `…` in lines [...]. Please ensure it is unique"。

### 2.2 memory 工具（`memory_20250818`）

来源：[platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)

- 命令：`view` / `create` / `str_replace` / `insert` / `delete` / `rename`，与 text editor 同构，存储由客户端实现（可映射到数据库键）。
- 返回：`str_replace` 成功 "The memory file has been edited." 后接带行号的改动片段（原文："followed by a snippet of the edited file with line numbers"）；多匹配 "No replacement was performed. Multiple occurrences of old_str `{old_str}` in lines: {line_numbers}. Please ensure it is unique"。错误设 `is_error: true`。
- "These specifications describe the recommended behaviors and return strings: Claude reads whatever text your tool result contains, so you can return different strings if your application needs to." —— 返回文本是可设计的接口，不是协议。
- 无任何并发/版本控制指引。

### 2.3 工程博客中的设计原则

- [Raising the bar on SWE-bench Verified](https://www.anthropic.com/engineering/swe-bench-sonnet)："We experimented with several different strategies for specifying edits to existing files and had the highest reliability with string replacement, where the model specifies `old_str` to replace with `new_str`."；工具强制绝对路径以"error-proof"。
- [Building effective agents, Appendix 2](https://www.anthropic.com/engineering/building-effective-agents)：编辑可以用 diff 或整文件重写；格式应"close to what the model has seen naturally occurring in text on the internet"，避免"formatting overhead"如维护准确行数或字符串转义；"poka-yoke your tools"；"we actually spent more time optimizing our tools than the overall prompt"。
- [Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)：
  - 返回"only high signal information"；把晦涩 UUID 解析为语义化标识"significantly improves Claude's precision"；
  - 可提供 `response_format`（concise / detailed）枚举；分页、范围选择、过滤、截断并设合理默认值，Claude Code 默认把工具响应限制在 25,000 tokens；
  - 错误应"clearly communicate specific and actionable improvements, rather than opaque error codes or tracebacks"；
  - 合并高频连用的工具（`schedule_event` 取代 `list_users` + `list_events` + `create_event`）；
  - 参数"unambiguously named"，用严格数据模型消歧。

## 3. pi（badlogic/pi-mono coding agent）

### 3.1 工具集与理念

- 作者博文 [What I learned building an opinionated and minimal coding agent](https://mariozechner.at/posts/2025-11-30-pi-coding-agent/)：四个工具 read / write / edit / bash，"these four tools are all you need for an effective coding agent"；系统提示加工具定义不足 1,000 tokens；批评 MCP 服务器每次会话灌入全部工具描述（占上下文 7–9%）。
- read：默认前 2000 行或 50KB，`offset`/`limit` 分页，描述中明确"When you need the full file, continue with offset until complete"（`packages/coding-agent/src/core/tools/read.ts`）。
- write：整文件创建/覆盖，返回 `Successfully wrote to {path}`（`write.ts`）。

### 3.2 edit：批量、原子、相对原文件匹配

`packages/coding-agent/src/core/tools/edit.ts`、`edit-diff.ts`：

- 参数 `{ path, edits: [{ oldText, newText }, …] }`。描述："Every edits[].oldText must match a unique, non-overlapping region of the original file. If two changes affect the same block or nearby lines, merge them into one edit … Do not include large unchanged regions just to connect distant changes."
- 系统提示指引："When changing multiple separate locations in one file, use one edit call with multiple entries in edits[] instead of multiple edit calls"；"Each edits[].oldText is matched against the original file, not after earlier edits are applied"。
- 演进（CHANGELOG）：先加入 multi-edit；随后"Built-in `edit` tool input now uses `edits[]` as the only replacement shape, reducing invalid tool calls caused by mixed single-edit and multi-edit schemas"（[#2639](https://github.com/badlogic/pi-mono/issues/2639)）。`prepareArguments` 还容错：某些模型把 `edits` 作为 JSON 字符串或单对象发送，会被静默归一。——**同一工具只保留一种输入形状，能显著减少无效调用**。
- 匹配：先精确，失败后退到规范化匹配（去行尾空白、NFKC、智能引号/破折号转 ASCII），仍须唯一。所有 edit 先全部定位、检查重叠，再一次性写入——全有或全无。
- 错误文本按批量场景定位到具体下标："Found {n} occurrences of edits[{i}] in {path}. Each oldText must be unique. Please provide more context to make it unique."；"edits[{a}] and edits[{b}] overlap in {path}. Merge them into one edit or target disjoint regions."；"No changes made … The replacement produced identical content."
- 返回：模型只看到 `Successfully replaced ${edits.length} block(s) in ${path}.`；diff、unified patch、首个改动行号放在 `details`，只供 TUI 渲染。
- 并发：无读前检查、无过期检测；只有 `withFileMutationQueue` 按文件串行化写入。

## 4. OpenAI Codex CLI：`apply_patch`

- 工具定义（`codex-rs/core/src/tools/handlers/apply_patch_spec.rs`）：GPT-5 系列上作为 **freeform 工具**，用 Lark 语法约束输出（`format: grammar/lark`），描述"This is a FREEFORM tool, so do not wrap the patch in JSON."——即刻意绕开 JSON 转义。
- 格式（`codex-rs/core/gpt_5_2_prompt.md`）："a stripped‑down, file‑oriented diff format designed to be easy to parse and safe to apply"：`*** Begin Patch` … `*** End Patch` 信封内若干 `*** Add File:` / `*** Delete File:` / `*** Update File:`（可带 `*** Move to:`），hunk 以 `@@ <锚点>` 开头，`-`/`+`/空格前缀行。一次调用即可改多个文件多处。
- 设计原理（[OpenAI GPT-4.1 Prompting Guide](https://developers.openai.com/cookbook/examples/gpt4-1_prompting_guide)，"Generating and Applying File Diffs"）：该 V4A 格式被"extensively trained"；有效的 diff 格式两个共性——不用行号、同时给出被替换原文与替换文本且分隔清晰；"We do not use line numbers in this diff format, as the context is enough to uniquely identify code"，默认上下各 3 行上下文；SEARCH/REPLACE 与"pseudo-XML format with no internal escaping"也表现良好。
- 应用：`codex-rs/apply-patch/src/seek_sequence.rs` 按"精确 → 忽略行尾空白 → 忽略首尾空白"逐级放宽定位上下文行。
- 返回：`Success. Updated the following files:` 后跟 `A/M/D <path>` 清单（`apply-patch/src/lib.rs`）——只有摘要，无内容回显。
- 过期读：没有显式机制；上下文行匹配失败即报错，隐式承担冲突检测。

## 5. Aider：编辑格式基准

- [Edit formats](https://aider.chat/docs/more/edit-formats.html)：`whole`（返回整文件，"slow and costly"）、`diff`（SEARCH/REPLACE 块，"efficient"）、`diff-fenced`（为 Gemini 调整围栏）、`udiff`、architect 模式下的 `editor-diff` / `editor-whole`。
- [GPT code editing benchmarks](https://aider.chat/docs/benchmarks.html)："Plain text edit formats worked best"；"Using the new functions API for edits performed worse than the above whole file method, for all the models"——原因："It makes GPT write worse code. Keeping the output format simple seems to allow GPT to devote more attention to the actual coding task" 且降低格式遵循率。
- [Unified diffs](https://aider.chat/docs/unified-diffs.html)：GPT-4 Turbo 懒惰基准从 20%（SEARCH/REPLACE）升至 61%（udiff）；四原则 FAMILIAR / SIMPLE / HIGH LEVEL / FLEXIBLE；"GPT is terrible at working with source code line numbers"；关闭宽松打补丁后编辑错误增加 9 倍。
- 注意：这些数据来自 2023–2024 年 GPT-3.5/4 与"把**代码**塞进 JSON 字符串"的场景；其结论针对的是长文本内容的转义负担，不直接否定"用 JSON 表达结构化操作"。

## 6. 跨 harness 的共性（批量 vs 单点 vs 整体、返回内容、过期检测）

1. **定位靠内容锚点，不靠位置号**。Anthropic、OpenAI、Aider 独立得出"不要行号"。结构化文档的等价物是**稳定 ID**（片段 ID），而不是数组下标——下标在插入/删除后漂移，正是"行号"问题的翻版。
2. **唯一性 + 可操作的错误**是防误改的核心；错误中带出候选位置（行号 / 下标）与下一步建议。
3. **批量**：三种立场并存——Claude Code 单点（MultiEdit 移除，原因无一手说明）；pi 单文件批量且全部相对原文件匹配、原子应用、禁止重叠；Codex 多文件批量 patch。共同点：**批量必须原子**，且批内各操作相对同一基线解释（pi 明确规定），避免"后一操作的定位依赖前一操作结果"。pi 的经验还表明：**只保留一种输入形状**（总是数组）比"单个或数组"更少出错。
4. **整体重写**：各家都保留 write/whole 作为兜底（新文件、大改），Aider 数据显示整体重写对弱模型最可靠但成本高。
5. **写后返回**：趋势是越来越少——Claude Code 与 pi 只回一句确认（diff 只给 UI），Codex 只回文件清单；Anthropic API 参考实现仍回 ±4 行片段并提示复核。Claude Code 甚至显式告诉模型"不必回读"。
6. **过期读检测**：只有 Claude Code 做了显式的读后版本校验，并在两年内从"严格拒绝"演进为"锚点仍唯一匹配则放行 + 附提示"。其他 harness 依赖内容锚点隐式发现冲突。

## 7. 对结构化文档编辑工具（剪辑时间线）的启示

前提差异：时间线是结构化 JSON、体量小（数十片段），且每次写入产生不可变修订——与"大文本文件 + 原地覆盖"的编码场景不同。字符串替换在编码场景胜出，是因为源码没有稳定结构 ID；时间线有，所以锚点应换成 ID，而非照搬 `str_replace`。

**(a) 写粒度：推荐"按 ID 的批量操作（单次调用一个原子事务）"为主，整份替换为兜底；不推荐 str_replace-like。**
- 操作集保持小而语义化（如 insert / update / move / remove 片段，设置转场、字幕、BGM），每个操作以片段 ID 定位，不用数组下标（§6.1）。
- 批内操作相对同一 base 解释、全有或全无，失败时指出第几个操作、哪个 ID、为何失败（pi 的 `edits[i]` 报错模式）。参数形状固定为数组，即使只有一个操作（pi #2639 的教训）。
- 保留整份替换（`replace_timeline`）用于初次生成或结构大改，对应各家的 write / whole。
- str_replace 作用于序列化 JSON 会引入转义与格式噪声，且放弃了 schema 校验——这正是 Aider 和 Anthropic 指出的 JSON/转义负担。
- 数值字段（入出点）用严格 schema 与单位（毫秒或帧，二选一），配合"poka-yoke"式校验（入点 < 出点、不越过素材时长），错误信息给出合法范围（仿 memory 工具 `insert_line` 越界报错格式）。

**(b) 读：数十个片段量级返回完整 JSON 是合适的。**
- 体量远低于 Claude Code 25k token 响应上限与 pi 50KB 读上限，不需要分页；但要"high signal"：去掉冗余字段、用可读 ID（如 `c07`）替代 UUID、附带派生量（每片段时长、总时长）省去模型心算。
- 读结果须带当前修订号（供 (d) 使用）。可选 `concise/detailed` 或按片段范围读取，为将来长剧集留余地。
- 读输出中的展示性装饰（如序号）不得与写入时的定位键混淆——Claude Code 专门警告不要把行号前缀抄进 `old_string`。

**(c) 写：返回简短确认 + 新修订号 + 受影响片段的新状态（或紧凑摘要），不回传整份文档。**
- 各家趋势是写后少回传（§6.5）；Claude Code 以"file state is current in your context — no need to Read it back"明确抑制回读。
- 但结构化写入有服务端派生（ID 分配、时长重算、校验后的规范化值），模型无法从自己的输入推出——因此至少回显**新建片段的 ID**和**被规范化或裁剪的字段**；这对应 Anthropic 参考实现"返回改动处片段"的做法，只是粒度换成受影响片段。
- 有告警（如总时长超出目标、字幕与片段错位）放在返回里，作为可操作提示。

**(d) 乐观并发（base revision）：标准做法，在本场景不算过度设计，建议采用并做宽松处理。**
- Claude Code 的 read-before-edit + "modified since read" 就是乐观并发；Web 端的等价标准是 HTTP `If-Match` / `412 Precondition Failed`（[RFC 9110 §13.1.1](https://www.rfc-editor.org/rfc/rfc9110#section-13.1.1)），JSON Patch 亦提供 `test` 操作并要求整份 patch 原子应用（[RFC 6902 §4.6、§5](https://www.rfc-editor.org/rfc/rfc6902)）。
- 追加不可变修订的模型下，修订号天然存在，比对成本近乎为零；同时存在用户在前端编辑与 Agent 并发写的真实风险。
- 建议：写调用携带 `base_revision`；若不等于当前修订：(1) 若批内所有操作引用的 ID 仍存在且目标字段自 base 以来未被改动，则照常应用并在返回中注明"期间有其他修订，可重新读取"（Claude Code 2.1.208 的放宽策略）；(2) 否则拒绝，错误中给出当前修订号与冲突的片段 ID，并指示"先读取再重试"。严格拒绝版本也可接受，但会让模型在无关并发改动下频繁空转一轮读取。
- `base_revision` 缺省时的行为需明确：要么强制必填（poka-yoke），要么视为"基于最新"并在返回中提示——前者更安全。
