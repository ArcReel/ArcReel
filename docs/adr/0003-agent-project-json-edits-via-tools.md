---
status: proposed
---

# Agent 改项目 JSON 数据收归 in-process MCP 工具，裸 Write/Edit/Bash 一律 deny

Agent 今天能用裸 `Write`/`Edit`（甚至 Bash 的 `echo>`/`sed`/`python -c`）直改 `scripts/*.json` 与 `project.json`，只过一个 PreToolUse 的 **JSON 语法** hook——结构错误（`duration_seconds` 越界、缺 `image_prompt`、`ReferenceVideoUnit` 的 shots↔duration 不一致）照样落盘，绕开 `_write_script_unlocked` 统一入口（ADR-0002）。这条旁路让「单一守卫点」是假的。我们决定把 Agent 对项目 JSON 数据的一切写入收归一组 in-process MCP 工具，并在工具外**禁止**裸字节写入这两类文件，使 ADR-0002 的结构校验真正只有一个强制点。

工具集（均为 in-process MCP `arcreel`，跑在 server 进程、不在 agent sandbox 内）：

- `patch_episode_script` — 通用字段编辑，**按 `segment_id`/`scene_id`/`unit_id` 定位**（与 `update_scene_asset` 一致；序号仅生成时约定，运行时排序靠数组位，compose/`resolve_episode_from_script` 都不解析序号），三种内容/生成模式通用。纯 setter。
- `insert_segment` / `remove_segment` / `split_segment` — 结构性增删拆，三模式全覆盖（reference 模式作用于 `video_units`/`shots`）。**id 稳定不重排**，插入/拆分**按模式**发新 id 并加 `_{子序号}` 后缀：narration/drama 的 segments/scenes 用 `E{集}S{序号}`、reference 的 units 用 `E{集}U{序号}`（见 `script_models.py` 的 `segment_id`/`scene_id`/`unit_id` 定义；前缀不能统一成 `S`，否则 reference 走 Pydantic 校验会失败）。
- `patch_project` — `project.json` 加+改（按 table+name），**取代** `add_assets.py`（删除该脚本，`analyze-assets` subagent 改调本工具，顺带消灭其脆弱的单行 CLI-JSON 调用）。
- `generate_episode_script` — 整集生成，改为**经 `_write_script_unlocked` 写盘**（替代 `ScriptGenerator` 原先的裸 `json.dump`）。

强制（双层）：

- **Bash 子进程**（Linux/macOS，内核级）：`sandbox.filesystem.denyWrite` 覆盖 `scripts/` 目录与 `project.json`。SDK 文档（sandboxing.md）明确 `denyWrite` 是 OS 级（Seatbelt / bwrap profile），对 sandbox 内**所有子进程（含 Bash 及其 child）生效**——堵住 `echo>`/`sed`/`python -c` 旁路。选 `denyWrite` 而非「Edit-deny 规则下推」：前者是文档化的 write-deny 字段，与现有 `denyRead` 同一 `filesystem` passthrough，不依赖 Edit allow/deny 规则被 SDK 派生进 Bash FS profile 这一未明文保证的行为。
- **内置 Write/Edit**（全平台）：内置文件工具不走 sandbox（走权限系统），由 `_check_write_access` hook 拒绝 `scripts/*.json` + `project.json`。与上面的 denyWrite 同源（同两类路径），构成双层。
- 剧本写入全 funnel 进 `_write_script_unlocked`：继承 ADR-0002 的「不更坏」语义 + metadata 重算（`total_scenes`/`estimated_duration_seconds`）+ 加锁 + filename↔episode 一致性。`project.json` 走 `update_project(_mutate)`，并在 mutation 内对结果 payload 做**同款「不更坏」校验**（改前已非法的历史脏数据放行，仅当本次 upsert 把合法 project 改非法时拒写）——与剧本统一入口的 `_guard_no_worse` 同源；若改成「结果必须绝对合法」会让带历史问题（如空 `style`）的项目整条 `patch_project` 路径不可用（旧 `add_assets.py` 报告校验错误也不阻断写入）。

## Consequences

- in-process MCP 工具跑在 server 进程、**不在 agent sandbox 内**，故 FS write-deny profile 不约束它们，工具照常写盘；删掉 `add_assets.py` 后，sandbox 内已**无任何合法的 Bash 写 `scripts/*.json`/`project.json`**（`split_episode` 写 `source/`、compose 写视频输出，均不碰），内核级 write-deny 不会误伤。
- **无 sandbox 回退**（Windows，或 Linux bwrap 探测失败）：内核级堵法不可用，回退到 `_check_write_access` deny（Write/Edit，全平台生效）+ 现有 `_WINDOWS_BASH_PREFIX_WHITELIST`（只放行 `python .claude/skills/`、ffmpeg、ffprobe，任意 `echo>`/`sed`/`python -c` 本就不在白名单）。已复核：删除 `add_assets.py` 后，白名单放行的 `python .claude/skills/` 脚本中无一写 `scripts/*.json`/`project.json`（split 写 `source/`、compose 写视频输出、peek 只读），故无沙箱回退无需额外特殊防御。
- **denyWrite 内核级生效的实测**：`denyWrite` 走与 `denyRead` 相同的 `filesystem` passthrough（后者已在生产用于保护 `.env` 等，机制可信）。其对 Bash 子进程的内核级写拒绝是 SDK 文档承诺的同字段行为；落地后建议做一次 live smoke test（sandbox 启用时在 Bash 工具内 `echo > scripts/x.json` 应被内核拒、而 MCP 工具写盘正常）以翻 `accepted`。
- **`patch` 不作废 `generated_assets`**（纯字段 setter）。系统无新鲜度/陈旧检测（`status` 仅由路径有无算出），故改了 `image_prompt` 又不重生时，会出现「新 prompt + 旧图 + status=completed」的静默陈旧。这是刻意取舍：场景本就是「改 prompt **并**重新生成」，regen 会覆盖资产；自动作废需在 patch 里硬编码字段→资产依赖链，且可能误删用户想留的图。代价由 agent profile 的「改 prompt 必重生」纪律 + 本 ADR 承接。一个更轻的备选是改关键字段时把 `generated_assets.status` 重置为 `pending`（不删路径）——**不采纳**：剧本 JSON 编辑与资产生命周期**解耦**，patch 不对资产状态作任何声明，资产的生成/重生是独立的显式动作。
- **结构工具（split/remove）清受影响分镜的 `generated_assets`**：与字段编辑相反，结构改动改变了分镜身份（`E1S3` 拆成两个，旧资产无合理归属），故必须清空使其退回 pending。
- 工具**返回文本**是 agent-facing（免 i18n）；工具**显示名**是 user-facing，须在 `ARCREEL_MCP_TOOL_IDS` 注册并补 `tool_name_<id>` 三语（zh/en/vi）。
- 与 ADR-0002 同源：本 ADR 是其「Agent 裸写入面收归」承诺的兑现。reference_video 切分的精确语义（切 unit 还是切 shots）留作实现细节，约束是结果必须满足 `ReferenceVideoUnit` 的 `duration==sum(shots)` 校验（结构校验 `_select_model` 已将 `video_units` 路由到 `ReferenceVideoScript`、由其 model_validator 兜住）。`_write_script_unlocked` 的 metadata 重算（`total_scenes`/`estimated_duration_seconds`）原先只识别 `segments`/`scenes`（`video_units` 落入 segments 兜底、错算为 0），#604 已把该判别收敛到与 `_select_model` 同款的 `script_editor.resolve_items`，三处（结构校验 / 编辑核心 / metadata 重算）共用一处判别。

## 「不更坏」语义的边界限定（post-#604 根因迭代）

PR #608 在 ADR-0002「不更坏」基础上落地了本 ADR 的工具收归,但多轮 code-review 反复审出同一类问题:`「不更坏」从一个具体策略悄悄泛化成了「宽容氛围」`,在写盘咽喉之外的 helper、读路径、跨集同步、agent 白名单都被复用了「遇到脏数据就降级」的态度,叠加产生 silent-noop / silent-overwrite 漏格。本次根因迭代把边界画死:

- **「不更坏」只存在两个咽喉点**:剧本写盘 `_write_script_unlocked` 的 `_guard_no_worse`(对剧本结构,基于 `_select_model` + Pydantic ValidationError);`upsert_assets` 的 `_mutate` 内 error-set diff(对 project.json,基于 `DataValidator.validate_project_payload` 的 errors 集合差)。这两处之外的所有 helper / caller **不允许**自带「脏数据怎么办」的局部策略。
- **咽喉外一律 fail-loud**:`resolve_items` 在分镜数组键存在但非 list 时抛 `ScriptEditError`(已经如此);`batch_update_scene_assets` 在 id 未命中时 fail-loud 抛 `KeyError`(本 PR);`_write_script_unlocked` metadata 重算的 `duration_seconds=None` 视为缺失而非 crash;`get_storyboard_items` 走 `resolve_items` 让脏数据异常类型对齐(不再 `list(None)` 抛 generic TypeError)。
- **降级是 caller 的显式决策**:`versions.py::_sync_storyboard_metadata` 从 `except Exception` 收紧为 `except ScriptEditError` + warning 包含集名 + continue(脏脚本跨集同步降级,有可观测信号);未预期异常让其冒到 router 5xx。读路径 `_resolve_items_or_warn` 在脏数据时 warning(已经如此),missing key 返回 `[]` 不 warning(空草稿合法初始态)。**禁止零信号成功**——任何降级路径必须有 warning。
- **agent 白名单 silent drop 改为显式反馈**:`upsert_assets` 返回诊断 dict(added / merged / dropped_fields / dropped_legacy),`patch_project` 工具据此构造文本告知 agent「以下字段不在 agent 可编辑范围(reference_image / sheet_field),已忽略」「以下历史字段已废弃(type / importance)」,让 LLM 不再重复尝试同样会被丢的字段;`analyze-assets` subagent prompt 改为严格 skip 已存在(调用 patch_project 前过滤),消除「不覆盖」与「可修订」自相矛盾的措辞。
- **路由按数据形状优先**:`resolve_kind` 取消 `generation_mode` 作为优先判别项,改为按 `video_units` / `segments` / `scenes` 顶层键存在性 + `content_mode` 辅助路由。理由:partial migration 中间态(配置改了 reference 但数据还在 segments)下,旧逻辑让 `generation_mode` 单向赢会导致整集脚本对所有 MCP 编辑工具不可触达,agent 看到「未找到 id」无线索定位。
- **工具职责边界**:`patch_episode_script` 的 `_set_nested` 在叶子(最后一段)不存在时**允许写入**(LLM 漏写的 optional 字段如 `video_prompt.note` agent 应能补,而非被迫走 remove+insert 重生整集),父节点(中间路径段)不存在仍 fail-loud(挡 typo);`split_segment` 保留 `parts[0]`(锚点)的 `generated_assets` 不动,与 `insert_segment` 锚点资产保留语义对齐,误用 split 当 insert 不再丢失已生成资产。

横切原则:**fail-loud 改造时需先枚举二维矩阵**(读/写 × 键缺失/键脏 × validate/no-validate),逐格做决策;不能把「在结构校验层不引入新错误」的「不更坏」策略下沉到没有 before/after 概念的 helper(元数据重算、key lookup、异常处理)——那些场景的脏数据降级是 caller 的显式职责,不是 helper 的默认行为。
