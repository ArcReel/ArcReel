---
status: proposed
---

# Agent 改项目 JSON 数据收归 in-process MCP 工具，裸 Write/Edit/Bash 一律 deny

Agent 今天能用裸 `Write`/`Edit`（甚至 Bash 的 `echo>`/`sed`/`python -c`）直改 `scripts/*.json` 与 `project.json`，只过一个 PreToolUse 的 **JSON 语法** hook——结构错误（`duration_seconds` 越界、缺 `image_prompt`、`ReferenceVideoUnit` 的 shots↔duration 不一致）照样落盘，绕开 `_write_script_unlocked` 咽喉（ADR-0002）。这条旁路让「单一守卫点」是假的。我们决定把 Agent 对项目 JSON 数据的一切写入收归一组 in-process MCP 工具，并在工具外**禁止**裸字节写入这两类文件，使 ADR-0002 的结构校验真正只有一个强制点。

工具集（均为 in-process MCP `arcreel`，跑在 server 进程、不在 agent sandbox 内）：

- `patch_episode_script` — 通用字段编辑，**按 `segment_id`/`scene_id`/`unit_id` 定位**（与 `update_scene_asset` 一致；序号仅生成时约定，运行时排序靠数组位，compose/`resolve_episode_from_script` 都不解析序号），三种内容/生成模式通用。纯 setter。
- `insert_segment` / `remove_segment` / `split_segment` — 结构性增删拆，三模式全覆盖（reference 模式作用于 `video_units`/`shots`）。**id 稳定不重排**，插入/拆分**按模式**发新 id 并加 `_{子序号}` 后缀：narration/drama 的 segments/scenes 用 `E{集}S{序号}`、reference 的 units 用 `E{集}U{序号}`（见 `script_models.py` 的 `segment_id`/`scene_id`/`unit_id` 定义；前缀不能统一成 `S`，否则 reference 走 Pydantic 校验会失败）。
- `patch_project` — `project.json` 加+改（按 table+name），**取代** `add_assets.py`（删除该脚本，`analyze-assets` subagent 改调本工具，顺带消灭其脆弱的单行 CLI-JSON 调用）。
- `generate_episode_script` — 整集生成，改为**经 `_write_script_unlocked` 写盘**（替代 `ScriptGenerator` 原先的裸 `json.dump`）。

强制（双层）：

- 声明式 **Edit-deny 规则**（`scripts/*.json` + `project.json`）。SDK 文档：sandbox 的文件**写**限制由 Edit allow/deny 规则派生为内核级 FS profile，对 sandbox 内**所有子进程（含 Bash）生效**——一个机制同时堵住裸 Write/Edit 与 Bash 写入。
- 剧本写入全 funnel 进 `_write_script_unlocked`：继承 ADR-0002 的「不更坏」语义 + metadata 重算（`total_scenes`/`estimated_duration_seconds`）+ 加锁 + filename↔episode 一致性。`project.json` 走 `update_project(_mutate)` + `validate_project`。

## Consequences

- in-process MCP 工具跑在 server 进程、**不在 agent sandbox 内**，故 FS write-deny profile 不约束它们，工具照常写盘；删掉 `add_assets.py` 后，sandbox 内已**无任何合法的 Bash 写 `scripts/*.json`/`project.json`**（`split_episode` 写 `source/`、compose 写视频输出，均不碰），内核级 write-deny 不会误伤。
- **Windows 无 sandbox**：内核级堵法不可用，回退到 `_check_write_access` deny（Write/Edit）+ 现有 `_WINDOWS_BASH_PREFIX_WHITELIST`（只放行 `python .claude/skills/`、ffmpeg、ffprobe，任意 `echo>`/`sed` 本就不在白名单）。Bash 旁路在 Windows 天然关闭。
- **实现首要验证项**：ArcReel 现在的 sandbox `denyRead` 走的是未文档化 passthrough；write-deny 须按文档用声明式 Edit-deny 规则，**需实测确认 SDK 把该规则下推到 sandbox 的 Bash FS profile**。若实测不下推，Bash 旁路在 Linux/macOS 仍开，需另寻 Bash 层封堵或显式记为残留风险。
- **`patch` 不作废 `generated_assets`**（纯字段 setter）。系统无新鲜度/陈旧检测（`status` 仅由路径有无算出），故改了 `image_prompt` 又不重生时，会出现「新 prompt + 旧图 + status=completed」的静默陈旧。这是刻意取舍：场景本就是「改 prompt **并**重新生成」，regen 会覆盖资产；自动作废需在 patch 里硬编码字段→资产依赖链，且可能误删用户想留的图。代价由 agent profile 的「改 prompt 必重生」纪律 + 本 ADR 承接。一个更轻的备选是改关键字段时把 `generated_assets.status` 重置为 `pending`（不删路径）——**不采纳**：剧本 JSON 编辑与资产生命周期**解耦**，patch 不对资产状态作任何声明，资产的生成/重生是独立的显式动作。
- **结构工具（split/remove）清受影响分镜的 `generated_assets`**：与字段编辑相反，结构改动改变了分镜身份（`E1S3` 拆成两个，旧资产无合理归属），故必须清空使其退回 pending。
- 工具**返回文本**是 agent-facing（免 i18n）；工具**显示名**是 user-facing，须在 `ARCREEL_MCP_TOOL_IDS` 注册并补 `tool_name_<id>` 三语（zh/en/vi）。
- 与 ADR-0002 同源：本 ADR 是其「Agent 裸写入面收归」承诺的兑现。reference_video 切分的精确语义（切 unit 还是切 shots）留作实现细节，约束是结果必须满足 `ReferenceVideoUnit` 的 `duration==sum(shots)` 校验。**注意**：`_write_script_unlocked` 今天只对 `segments`/`scenes` 做校验 dispatch 与统计（`video_units` 会落入 segments 兜底分支、`total_scenes` 错算），所以「由 funnel 兜住」reference **不是现状自动成立的**——需先把该函数扩展到识别 `video_units`（校验 dispatch + 统计），属 #604 的实现任务。
