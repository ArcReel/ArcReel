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
- 剧本写入全 funnel 进 `_write_script_unlocked`：继承 ADR-0002 的「不更坏」语义 + metadata 重算（`total_scenes`/`estimated_duration_seconds`）+ 加锁 + filename↔episode 一致性。`project.json` 走 `update_project(_mutate)`，并在 mutation 内对结果 payload 做**同款「不更坏」校验**（改前已非法的历史脏数据放行，仅当本次 upsert 把合法 project 改非法时拒写）——与剧本咽喉的 `_guard_no_worse` 同源；若改成「结果必须绝对合法」会让带历史问题（如空 `style`）的项目整条 `patch_project` 路径不可用（旧 `add_assets.py` 报告校验错误也不阻断写入）。

## Consequences

- in-process MCP 工具跑在 server 进程、**不在 agent sandbox 内**，故 FS write-deny profile 不约束它们，工具照常写盘；删掉 `add_assets.py` 后，sandbox 内已**无任何合法的 Bash 写 `scripts/*.json`/`project.json`**（`split_episode` 写 `source/`、compose 写视频输出，均不碰），内核级 write-deny 不会误伤。
- **无 sandbox 回退**（Windows，或 Linux bwrap 探测失败）：内核级堵法不可用，回退到 `_check_write_access` deny（Write/Edit，全平台生效）+ 现有 `_WINDOWS_BASH_PREFIX_WHITELIST`（只放行 `python .claude/skills/`、ffmpeg、ffprobe，任意 `echo>`/`sed`/`python -c` 本就不在白名单）。已复核：删除 `add_assets.py` 后，白名单放行的 `python .claude/skills/` 脚本中无一写 `scripts/*.json`/`project.json`（split 写 `source/`、compose 写视频输出、peek 只读），故无沙箱回退无需额外特殊防御。
- **denyWrite 内核级生效的实测**：`denyWrite` 走与 `denyRead` 相同的 `filesystem` passthrough（后者已在生产用于保护 `.env` 等，机制可信）。其对 Bash 子进程的内核级写拒绝是 SDK 文档承诺的同字段行为；落地后建议做一次 live smoke test（sandbox 启用时在 Bash 工具内 `echo > scripts/x.json` 应被内核拒、而 MCP 工具写盘正常）以翻 `accepted`。
- **`patch` 不作废 `generated_assets`**（纯字段 setter）。系统无新鲜度/陈旧检测（`status` 仅由路径有无算出），故改了 `image_prompt` 又不重生时，会出现「新 prompt + 旧图 + status=completed」的静默陈旧。这是刻意取舍：场景本就是「改 prompt **并**重新生成」，regen 会覆盖资产；自动作废需在 patch 里硬编码字段→资产依赖链，且可能误删用户想留的图。代价由 agent profile 的「改 prompt 必重生」纪律 + 本 ADR 承接。一个更轻的备选是改关键字段时把 `generated_assets.status` 重置为 `pending`（不删路径）——**不采纳**：剧本 JSON 编辑与资产生命周期**解耦**，patch 不对资产状态作任何声明，资产的生成/重生是独立的显式动作。
- **结构工具（split/remove）清受影响分镜的 `generated_assets`**：与字段编辑相反，结构改动改变了分镜身份（`E1S3` 拆成两个，旧资产无合理归属），故必须清空使其退回 pending。
- 工具**返回文本**是 agent-facing（免 i18n）；工具**显示名**是 user-facing，须在 `ARCREEL_MCP_TOOL_IDS` 注册并补 `tool_name_<id>` 三语（zh/en/vi）。
- 与 ADR-0002 同源：本 ADR 是其「Agent 裸写入面收归」承诺的兑现。reference_video 切分的精确语义（切 unit 还是切 shots）留作实现细节，约束是结果必须满足 `ReferenceVideoUnit` 的 `duration==sum(shots)` 校验（结构校验 `_select_model` 已将 `video_units` 路由到 `ReferenceVideoScript`、由其 model_validator 兜住）。`_write_script_unlocked` 的 metadata 重算（`total_scenes`/`estimated_duration_seconds`）原先只识别 `segments`/`scenes`（`video_units` 落入 segments 兜底、错算为 0），#604 已把该判别收敛到与 `_select_model` 同款的 `script_editor.resolve_items`，三处（结构校验 / 编辑核心 / metadata 重算）共用一处判别。
