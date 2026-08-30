---
status: accepted
---

# Agent 改项目 JSON 数据收归 in-process MCP 工具，裸 Write/Edit/Bash 一律 deny

Agent 在本决策前能用裸 `Write`/`Edit`（甚至 Bash 的 `echo>`/`sed`/`python -c`）直改 `scripts/*.json` 与 `project.json`，只过一个 PreToolUse 的 **JSON 语法** hook——结构错误（`duration_seconds` 越界、缺 `image_prompt`、`ReferenceVideoUnit` 的 shots↔duration 不一致）照样落盘，绕开 `_write_script_unlocked` 统一入口（ADR-0002）。这条旁路让「单一守卫点」是假的。我们决定把 Agent 对项目 JSON 数据的一切写入收归一组 in-process MCP 工具，并在工具外**禁止**裸字节写入这两类文件，使 ADR-0002 的结构校验真正只有一个强制点。

工具集（均为 in-process MCP `arcreel`，跑在 server 进程、不在 agent sandbox 内）：

- `get_episode_script` + `patch_episode_script` — 先读取正文与 canonical JSON `sha256-v1` revision，再提交 `{script, base_revision, operations[]}`。operations 是有序的 `update` / `insert` / `remove` / `split` 判别联合，按 `segment_id` / `scene_id` / `unit_id` / `shot_id` 定位，各内容/生成模式通用。服务在项目锁内复核 revision，对内存 candidate 顺序应用全批，再统一预检结构、项目引用、适用的 Artifact Manifest basis 与 SpeechComposition；任一失败返回稳定 `code`、`operation_index`、unit/field location、`next_action`，整批零写入。
- `insert` / `split` 由 `patch_episode_script` 在同一批事务内完成结构投影。**id 稳定不重排**，插入/拆分按模式发新 id 并加 `_{子序号}` 后缀：narration/drama 的 segments/scenes 用 `E{集}S{序号}`、reference 的 units 用 `E{集}U{序号}`。split 首份保留原 id 及 `generated_assets` / `end_frame_image`，其余新身份清空资产。
- `patch_project` — `project.json` 加+改（按 table+name），**取代** `add_assets.py`（删除该脚本，`analyze-assets` subagent 改调本工具，顺带消灭其脆弱的单行 CLI-JSON 调用）。
- `generate_episode_script` — 整集生成，改为**经 `_write_script_unlocked` 写盘**（替代 `ScriptGenerator` 原先的裸 `json.dump`）。

强制（双层）：

- **Bash 子进程**（Linux/macOS，内核级）：`sandbox.filesystem.denyWrite` 覆盖 `scripts/` 目录与 `project.json`。[Claude Code sandbox 的 OS-level enforcement](https://code.claude.com/docs/en/sandboxing#os-level-enforcement) 明确约束由 Seatbelt / bwrap 在 OS 级执行，对 sandbox 内**所有子进程（含 Bash 及其 child）生效**——堵住 `echo>`/`sed`/`python -c` 旁路。选 `denyWrite` 而非「Edit-deny 规则下推」：前者是文档化的 write-deny 字段，与现有 `denyRead` 同一 `filesystem` passthrough，不依赖 Edit allow/deny 规则被 SDK 派生进 Bash FS profile 这一未明文保证的行为。
- **内置 Write/Edit**（全平台）：内置文件工具不走 sandbox（走权限系统），由 `_check_write_access` hook 拒绝 `scripts/*.json` + `project.json`。与上面的 denyWrite 同源（同两类路径），构成双层。
- 剧本写入全 funnel 进 `_write_script_unlocked`。批量人工编辑在其外增加 `ScriptBatchEditor` 深模块：同一项目/剧本临界区内做 OCC、candidate 预检，并把 script、project episode 索引与适用的 episode-script Manifest entry 作为可补偿提交；后一步失败时在锁释放前逐字恢复 script/project，Manifest hook 自行恢复旧 entry。底层写入口仍保留 ADR-0002 的「不更坏」兼容策略，批量命令则要求本次 candidate 通过完整结构与引用预检；唯一兼容例外是未被本批改变的 legacy speech blocker，不阻塞无关编辑。

## Consequences

- in-process MCP 工具跑在 server 进程、**不在 agent sandbox 内**，故 FS write-deny profile 不约束它们，工具照常写盘；删掉 `add_assets.py` 后，sandbox 内已**无任何合法的 Bash 写 `scripts/*.json`/`project.json`**（`split_episode` 写 `source/`、compose 写视频输出，均不碰），内核级 write-deny 不会误伤。
- **无 sandbox 回退**（Windows，或 Linux bwrap 探测失败）：内核级堵法不可用，回退到 `_check_write_access` deny（Write/Edit，全平台生效）+ 现有 `_WINDOWS_BASH_PREFIX_WHITELIST`（只放行 `python .claude/skills/`、ffmpeg、ffprobe，任意 `echo>`/`sed`/`python -c` 本就不在白名单）。已复核：删除 `add_assets.py` 后，白名单放行的 `python .claude/skills/` 脚本中无一写 `scripts/*.json`/`project.json`（split 写 `source/`、compose 写视频输出、peek 只读），故无沙箱回退无需额外特殊防御。
- **denyWrite 内核级生效的实测**：`denyWrite` 走与 `denyRead` 相同的 `filesystem` passthrough（后者已在生产用于保护 `.env` 等，机制可信）。其对 Bash 子进程的内核级写拒绝是 SDK 文档承诺的同字段行为；双层拒绝的配置编译与谓词行为由 `server/agent_runtime/agent_access_policy.py` 的单元测试覆盖，内核级实际拒写依赖 SDK 承诺，未做过 live smoke test——在 sandbox 启用环境验证时，预期为 Bash 工具内 `echo > scripts/x.json` 被内核拒、而 MCP 工具写盘正常。
- **编辑不删除已有媒体，也不改写 `generated_assets`**。改 prompt 后旧媒体由显式重新生成替换；结构 remove 只移除剧本引用，项目内已有文件继续保留。split 的同 id 锚点延续旧资产，新派生 id 清空资产。Manifest currency 在读时由 basis 比较推导，不把 stale 状态写进剧本。
- **structured basis 只登记正式直接输入**：narration / drama（包括 reference_video 路线）存在 canonical script_plan 时，用该 script_plan 构造 episode-script basis；无 script_plan 时不登记。ad 当前没有 canonical script_plan，编辑服务不以修改后的 script 自身制造 basis，避免产物自引用。
- 工具**返回文本**是 agent-facing（免 i18n）；工具**显示名**是 user-facing，须在 `ARCREEL_MCP_TOOL_IDS` 注册并补 `tool_name_<id>` 三语（zh/en/vi）。
- 与 ADR-0002 同源：本 ADR 是其「Agent 裸写入面收归」承诺的兑现。reference_video 的结构工具作用于顶层 `video_units`；unit 的 `duration_seconds` 是独立编排字段，不从成员 shots 求和。结构校验 / 编辑核心 / metadata 重算共用 `script_editor.resolve_items` 判别。

## 「不更坏」语义的边界限定（post-#604 根因迭代）

PR #608 在 ADR-0002「不更坏」基础上落地了本 ADR 的工具收归,但多轮 code-review 反复审出同一类问题:`「不更坏」从一个具体策略悄悄泛化成了「宽容氛围」`,在写盘咽喉之外的 helper、读路径、跨集同步、agent 白名单都被复用了「遇到脏数据就降级」的态度,叠加产生 silent-noop / silent-overwrite 漏格。本次根因迭代把边界画死:

- **「不更坏」只存在两个咽喉点**:剧本写盘 `_write_script_unlocked` 的 `_guard_no_worse`(对剧本结构,基于 `_select_model` + Pydantic ValidationError);`upsert_assets` 的 `_mutate` 内 error-set diff(对 project.json,基于 `DataValidator.validate_project_payload` 的 errors 集合差)。这两处之外的所有 helper / caller **不允许**自带「脏数据怎么办」的局部策略。
- **咽喉外一律 fail-loud**:`resolve_items` 在分镜数组键存在但非 list 时抛 `ScriptEditError`(已经如此);`batch_update_scene_assets` 在 id 未命中时 fail-loud 抛 `KeyError`(本 PR);`_write_script_unlocked` metadata 重算的 `duration_seconds=None` 视为缺失而非 crash;`get_storyboard_items` 走 `resolve_items` 让脏数据异常类型对齐(不再 `list(None)` 抛 generic TypeError)。
- **降级是 caller 的显式决策**:`versions.py::_sync_storyboard_metadata` 从 `except Exception` 收紧为 `except ScriptEditError` + warning 包含集名 + continue(脏脚本跨集同步降级,有可观测信号);未预期异常让其冒到 router 5xx。读路径 `_resolve_items_or_warn` 在脏数据时 warning(已经如此),missing key 返回 `[]` 不 warning(空草稿合法初始态)。**禁止零信号成功**——任何降级路径必须有 warning。
- **agent 白名单 silent drop 改为显式反馈**:`upsert_assets` 返回诊断 dict(added / merged / dropped_fields / dropped_legacy),`patch_project` 工具据此构造文本告知 agent「以下字段不在 agent 可编辑范围(reference_image / sheet_field),已忽略」「以下历史字段已废弃(type / importance)」,让 LLM 不再重复尝试同样会被丢的字段;`analyze-assets` subagent prompt 改为严格 skip 已存在(调用 patch_project 前过滤),消除「不覆盖」与「可修订」自相矛盾的措辞。
- **编辑路由按数据形状优先**:编辑工具经取证解析(`lib/script_skeleton.resolve_script_kind`)按 `video_units` / `segments` / `scenes` / `shots` 顶层键存在性 + `content_mode` 辅助路由,不看项目声明的生成路线。理由:编辑面对的问题是「这份剧本现在长什么样」,骨架与项目路线不符的存量剧本若按路线路由会整集对所有 MCP 编辑工具不可触达,agent 看到「未找到 id」无线索定位。生成侧相反,按项目路线分派,Agent 的生成入队工具与数据校验另经路线闸门拒绝失配剧本(见 `docs/adr/0045`、`docs/adr/0055`)——失配集因此可读、可改、可归档导出(剪映草稿导出按剧本 content_mode 的规范骨架取片段,失配集取不到已完成片段),只是不能经 Agent 按它生成。
- **工具职责边界**:`patch_episode_script` 的 `_set_nested` 在叶子(最后一段)不存在时**允许写入**(LLM 漏写的 optional 字段如 `video_prompt.note` agent 应能补,而非被迫走 remove+insert 重生整集),父节点(中间路径段)不存在仍 fail-loud(挡 typo);`split` 保留 `parts[0]`(锚点)的 `generated_assets` 不动,与 `insert` 的锚点资产保留语义对齐,误用 split 当 insert 不再丢失已生成资产。

横切原则:**fail-loud 改造时需先枚举二维矩阵**(读/写 × 键缺失/键脏 × validate/no-validate),逐格做决策;不能把「在结构校验层不引入新错误」的「不更坏」策略下沉到没有 before/after 概念的 helper(元数据重算、key lookup、异常处理)——那些场景的脏数据降级是 caller 的显式职责,不是 helper 的默认行为。
