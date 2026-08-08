# 内嵌智能体工作流的裸文件读写依赖面盘点

> Research ticket: [#1703](https://github.com/ArcReel/ArcReel/issues/1703)（属地图 [#1702](https://github.com/ArcReel/ArcReel/issues/1702)）。
> 目的：为外部 agent 走远程 MCP 的工具面清单决策提供事实输入——逐项列出内嵌工作流哪些步骤依赖对项目目录的裸文件 Read/Write/Edit/Glob/Bash，今天是否已有结构化 MCP 工具覆盖，Spec [#1669](https://github.com/ArcReel/ArcReel/issues/1669) 落地后还剩什么缺口。

## 调查范围与方法

通读以下一手材料（均为 `origin/main` 当前内容）：

- 9 个 Skill：`agent_runtime_profile/.claude/skills/` 下 8 个 SKILL.md + `manga-workflow/` 的三份内容模式变体（`SKILL.narration.md` / `SKILL.drama.md` / `SKILL.ad.md`，物化时按 content_mode 三选一）
- 6 个 Subagent：`agent_runtime_profile/.claude/agents/*.md`
- 三份系统 prompt 变体：`agent_runtime_profile/CLAUDE.{narration,drama,ad}.md`
- SDK 进程内 MCP 工具集：`server/agent_runtime/sdk_tools/`（26 个工具，清单见 `__init__.py::ARCREEL_MCP_TOOL_IDS`）
- 写禁边界的运行时真相源：`server/agent_runtime/agent_access_policy.py`（`PROTECTED_WRITE_RULES` + sandbox denyWrite/denyRead 投影）
- Spec #1669 正文（目标态：workflow-state 服务、事务式 step1 编辑、批量完成契约、隔离草稿保留）

## 运行时写禁边界现状（真相源：`agent_access_policy.py`）

理解「哪些裸写是刻意允许的」需要先看强制层。`PROTECTED_WRITE_RULES` 声明两条规则，hook（内置 Write/Edit）与 sandbox denyWrite（Bash 子进程，内核级）同表投影：

| 规则 | hook 层（Write/Edit） | sandbox 层（Bash） | 收归到的 MCP 工具 |
|---|---|---|---|
| `project_json` | 拒 `project.json` + `scripts/` 整子树 | 同覆盖面 | `patch_project` / `patch_episode_script` / `insert_segment` / `remove_segment` / `split_segment` / `patch_episode_meta` |
| `reference_step1` | 只拒正式 `drafts/episode_N/step1_reference_units.json`；**同目录 `.invalid.json` 隔离草稿刻意放行**（注释原文：「隔离草稿正是留给内置 Edit 的编辑工位」） | 拒 `drafts/` 整目录 | `open_reference_step1_for_edit` → 裸 Edit 草稿 → `validate_and_promote_reference_draft` |

关键推论：**narration / drama 的 step1 文件（`step1_segments.json` / `step1_normalized_script.json`）不在任何写禁规则内**——Write/Edit 直改是当前设计内的合法路径（Bash 写被 sandbox 拒，但内置 Edit 放行）。另外 hook 拒项目内代码扩展名写入（`.py/.js/.ts/...`），读侧由 denyRead 拒敏感文件（`.env` / `vertex_keys/` / `.arcreel.db*` 等），项目根与仓库参考资料放行读取。

## 文件依赖 → 结构化工具覆盖状态清单

「覆盖」列口径：✅ 已有结构化 MCP 工具承担该读/写；⚠️ 部分覆盖；❌ 只能裸文件操作。「#1669 后」列按该 Spec 的 Implementation Decisions 判断。

### 1. `project.json`

| 依赖项 | 读/写 | 出处（工作流步骤） | 今日覆盖 | #1669 后 |
|---|---|---|---|---|
| 状态检测 / 阶段路由（modes、episodes 账本、`ledger_status`、`planning_cursor`、三类资产 sheet 缺失判定） | 读 | manga-workflow 三变体「状态检测」；CLAUDE.\*.md 路径规范；ad 工作流步骤 1 | ❌ 无读侧工具，靠 Read `project.json`（`list_pending_assets` 仅覆盖缺 sheet 判定一角） | ✅ 权威 workflow-state 服务（MCP/REST 同 schema）返回状态 + 稳定 `next_action`，明确取代文件检视（User Story 51） |
| 资产/概述/settings 内容读取（analyze-assets 记录已有名称；split 类 subagent 核对 `@[名称]` / `characters_in_segment` 引用；ad 起草卖点读 brief/products；generate-assets subagent 验证 sheet 字段回写） | 读 | analyze-assets Step 1；split-narration-segments / normalize-drama-script / split-reference-video-units 修改口径；SKILL.ad；generate-assets subagent Step 1/3 | ❌ 无 `get_project` / 资产读取工具 | ⚠️ 未覆盖——#1669 只提供工作流状态投影与批量完成契约（验证类读取被逐 ID 结果取代），资产 description 等**内容**读取仍是裸 Read |
| 任何写入（资产 upsert、settings、overview、账本、sheet 回写） | 写 | 全部 | ✅ `patch_project` / `plan_episodes` / `reset_episode_planning` + worker 回写；hook+sandbox 双层拒直改 | ✅ 不变 |

### 2. `source/*.txt`（含派生的 `source/episode_N.txt`）

| 依赖项 | 读/写 | 出处 | 今日覆盖 | #1669 后 |
|---|---|---|---|---|
| 整部小说原文批量读取 | 读 | analyze-assets Step 2：Glob `source/` + 按序 Read 全部 `.txt/.md/.text`（唯一在 agent 上下文里装载全文的步骤；主 agent 被约束「小说原文不进主 agent」） | ❌ 无源文读取工具（split/规划类工具在服务端自行读源文，不经 agent） | ❌ 未覆盖——#1669 的 asset-inventory completion 记录「分析完成与源文修订」，但分析本身仍由 agent 读原文完成 |
| 源文写入/派生 | 写 | 无 agent 写入路径：上传走 Web，`episode_N.txt` 由 `plan_episodes` 派生维护（CLAUDE.\*.md 明示「不要手工编辑」） | ✅（无需求） | ✅ |

### 3. `drafts/episode_N/step1_segments.json`（narration）与 `step1_normalized_script.json`（drama）

| 依赖项 | 读/写 | 出处 | 今日覆盖 | #1669 后 |
|---|---|---|---|---|
| 首次生成后验证结构 | 读 | split-narration-segments 情况 A Step 2；normalize-drama-script 情况 A Step 3 | ⚠️ 生成走 `split_narration_segments` / `normalize_drama_script`，验证读是裸 Read | ⚠️ 逐 ID 完成契约弱化「重读验证」需求，但读侧无专用工具 |
| **修改已有拆分（情况 B）＋「结构有问题直接用 Edit 修复」** | **写（裸 Edit）** | split-narration-segments 情况 B（「用 Edit 工具直接修改」）；normalize-drama-script 情况 B Step 2 与情况 A「直接用 Edit 工具修复」 | ❌ **今日最大的正式数据裸写面**：无事务工具、无修订冲突检测、与 Web 端保存无锁协同（写禁规则刻意未覆盖这两个文件） | ✅ #1669 新增原子、revision-checked 的 step1 patch 接口（update/insert/move/remove、整批校验 all-or-nothing、与 Web 保存同锁、编辑自然作废 step1 审核）——该裸写面被明令关闭（Out of Scope：「Allowing Agent file tools to directly edit formal project, script or step1 structures」） |
| 状态检测按存在性判阶段 3/4 | 读（Glob） | manga-workflow 状态检测第 3 条；create-episode-script Step 1 前置检查 | ❌ 文件存在性检视 | ✅ workflow-state 服务取代 |

### 4. `drafts/episode_N/step1_reference_units.json`（reference_video 正式 step1）

| 依赖项 | 读/写 | 出处 | 今日覆盖 | #1669 后 |
|---|---|---|---|---|
| 生成/晋升后验证 | 读 | split-reference-video-units 情况 A Step 2 | ⚠️ 同上，验证是裸 Read | ⚠️ 同上 |
| 修改 | 写 | **已收归**：hook 明拒直改（持锁写入路径冲突），走 `open_reference_step1_for_edit` → 草稿 → `validate_and_promote_reference_draft` | ✅（唯一已实现「事务式 step1 编辑」的变体） | ✅ 保留原机制（User Story 38） |

### 5. `drafts/episode_N/step1_reference_units.invalid.json` / `step2_reference_script.invalid.json`（隔离草稿）

| 依赖项 | 读/写 | 出处 | 今日覆盖 | #1669 后 |
|---|---|---|---|---|
| 隔离草稿修复循环：Read 草稿 → 按 `violations[]` 逐条 Edit `content.units[i]` → 晋升，可无限轮次 | 读 + 写（裸 Read/Edit，**设计内**） | split-reference-video-units 情况 B/C；create-episode-script Step 2「违约产物待处置」；generate-script SKILL 前置条件 4 | ⚠️ 半结构化：开草稿与晋升是工具，中间编辑显式依赖内置 Edit（`agent_access_policy.py` 注释称其为「编辑工位」） | ⚠️ **保留为唯一合法文件编辑面**（Decision：「only tool-issued invalid isolated drafts may use file editing」）——内嵌 agent 无缺口，但这是外部 agent 的硬缺口（见下文） |

### 6. `scripts/episode_N.json`

| 依赖项 | 读/写 | 出处 | 今日覆盖 | #1669 后 |
|---|---|---|---|---|
| 生成后验证 / 各阶段「验证方式：重新读取 scripts/episode_N.json 检查 storyboard_image / video_clip / narration_audio 字段」 | 读 | create-episode-script Step 3；manga-workflow 阶段 6/7/8 dispatch 模板；generate-narration-audio 状态检测；generate-assets subagent Step 3 | ❌ 裸 Read（且被批量任务契约缺失所迫：任务成功靠重读文件推断） | ✅ 批量逐 ID succeeded/failed/blocked 契约 + 「任务成功绝不从文件存在性推断」（Decision）取代验证性重读 |
| 编辑前置检视：「批量编辑前先 Read 该剧本确认现状」 | 读 | CLAUDE.\*.md「编辑项目 JSON」条目 | ❌ 裸 Read | ⚠️ 未覆盖——patch 是写侧工具，读侧仍裸 |
| 任何写入 | 写 | 全部 | ✅ `generate_episode_script` + `patch_episode_script` / `patch_episode_meta` / `insert_segment` / `remove_segment` / `split_segment`；hook+sandbox 双层拒直改（含 `scripts/` 整子树） | ✅ 不变 |

### 7. 媒体与产物目录（`characters/` `scenes/` `props/` `products/` `storyboards/` `grids/` `videos/` `reference_videos/` `audio/` `thumbnails/`）

| 依赖项 | 读/写 | 出处 | 今日覆盖 | #1669 后 |
|---|---|---|---|---|
| sheet/分镜图文件存在性检查（「`*_sheet` 字段为空或文件不存在」「sheet 文件存在」清单项） | 读 | generate-assets SKILL Pending 判定；generate-video 生成前检查（reference 模式） | ⚠️ `list_pending_assets` 覆盖资产 pending；剧本侧仍靠字段+文件检视 | ✅ artifact manifest + current/stale/missing/blocked 分类接管 |
| 写入 | 写 | 无 agent 写入路径（worker 回写；产品原图用户上传） | ✅ | ✅（manifest 明确「runtime-owned and outside Agent direct-write permissions」） |

### 8. `output/` 与 compose-video（唯一保留的 Bash+Python 脚本路径）

| 依赖项 | 读/写 | 出处 | 今日覆盖 | #1669 后 |
|---|---|---|---|---|
| `python .claude/skills/compose-video/scripts/compose_video.py scripts/episode_N.json`：读剧本 + `videos/*.mp4`，写 `output/*.mp4`；BGM 文件读取 | 读 + 写（Bash 子进程，仅 drama） | compose-video SKILL；CLAUDE.\*.md「Bash 用途」；Bash 白名单要求脚本落在 `.claude/skills/<skill>/scripts/` | ❌ 无 MCP 等价物（刻意：其余模式走 Web 端剪映草稿导出） | ❌ 明确不收归（Out of Scope：「Replacing or productizing the compose-video media algorithm and CLI」，仅更新其 skill 文案） |

### 9. 泛用 Bash 文件浏览

CLAUDE.\*.md 把 Bash 定位为「通用排查与文件浏览（ls / cat / jq / python / curl）」。这不绑定具体工作流步骤，但被多处隐含依赖（排查生成失败、核对产物）。今日无结构化替代；#1669 通过 workflow-state 与 blocked 诊断（机器可读字段定位 + 折叠技术细节）压缩其必要性，但不消除。

## 今日全景小结

**写侧已基本收归**：project.json、scripts/、正式 reference step1 三类正式数据的全部写入路径都有事务式/服务端 MCP 工具，并由 hook + sandbox 双层强制。今日仅剩两块裸写面：

1. **narration / drama 的 step1 直改**（split-narration-segments / normalize-drama-script 情况 B 及结构修复）——无锁、无 revision 冲突检测，是 #1669 事务式 step1 patch 的靶子；
2. **隔离草稿 `.invalid.json` 的 Edit 修复循环**——刻意设计、#1669 保留。

**读侧几乎全裸**：26 个 MCP 工具中只有 `list_pending_assets` 和 `get_video_capabilities` 是读侧工具。状态检测、生成结果验证、编辑前置检视、资产内容读取、源文读取全部依赖 Read/Glob（+Bash 浏览）。

## #1669 落地后剩余缺口（内嵌视角）

1. **资产/项目内容读取**（description、brief、products、overview）——workflow-state 只给状态投影，不给内容；
2. **源文正文读取**（analyze-assets 的全文分析）——inventory completion 记录完成性，不替代读取；
3. **step1 / 剧本正文读取**（向用户呈现、编辑前检视）——patch 系工具是写侧；
4. **隔离草稿 Read/Edit**——保留为文件编辑工位（内嵌可用内置工具，无缺口，但见下）；
5. **compose-video CLI**——明确不收归。

## 外部 agent 走远程 MCP 时缺什么

外部 agent 只有 MCP 工具面，没有内嵌 harness 的 Read/Write/Edit/Glob/Bash（沙箱内文件工具）。对照上表，缺口按严重度排序：

| 缺口 | 阻断的工作流 | 备注 |
|---|---|---|
| **隔离草稿不可达**：`.invalid.json` 修复循环完全依赖内置 Read+Edit，远程侧既读不到 `violations[]` 也改不了 `content.units[i]` | reference_video 的 step1/step2 违约修复（该路线的核心纠错循环）；#1669 后依旧（Spec 保留文件编辑工位） | 需要草稿读取/patch 工具对（如 `get_reference_draft` / `patch_reference_draft`），或在工具报错里内联草稿全文并提供结构化改稿入口 |
| **narration / drama step1 编辑不可达**（今日）：情况 B 靠裸 Edit | 说书/剧集的 step1 修改 | #1669 的事务式 step1 patch 工具落地即消解——外部工具面应直接对齐该接口，不做过渡方案 |
| **无项目/剧本/step1 读取工具**：状态检测、编辑前检视、向用户呈现内容都无从做起 | 全部工作流的入口与确认环节 | #1669 workflow-state 工具解决「下一步做什么」，但内容读取（资产 description、剧本正文、step1 正文）仍需读侧工具或 REST 兜底；与地图既定决策「结构化专用工具为主、只读文件工具兜底」一致 |
| **无源文读取**：analyze-assets 等价物无法在外部执行 | 阶段 1 资产提取 | 两条路：提供受限只读文件工具（源文/草稿白名单），或把资产分析像 `plan_episodes` 一样整体服务端化 |
| **验证性重读不可达**：「重新读取 …json 检查字段」模式失效 | 各生成阶段的完成判定 | #1669 逐 ID 完成契约本就要取代该模式——外部工具面按目标态设计即可，无需补读侧 |
| **媒体查看**：sheet/分镜图审阅无文件访问 | 各审核检查点 | 审阅本就发生在 WebUI；外部 agent 需要的最多是产物 URL（REST 静态服务已有），非 MCP 工具缺口 |
| **compose-video 不可用**：Bash+Python CLI 无远程等价物 | drama 成片拼接 | 与「其余模式走剪映草稿导出」同口径处理：外部面明确不含 compose，或后续服务端化（超出本票范围） |

## 结论

- 内嵌工作流对裸文件的**写**依赖只剩 narration/drama step1 直改（#1669 已规划关闭）与隔离草稿工位（#1669 保留）；**读**依赖则遍布所有阶段且今日几乎零覆盖，#1669 的 workflow-state + 批量完成契约能消掉「状态检测」与「验证性重读」两大类，但**内容读取**（资产/剧本/step1/源文正文）与**隔离草稿访问**在 #1669 之后仍是裸文件面。
- 外部工具面清单决策（后续盘问票）应聚焦四件事：隔离草稿的远程访问方案、读侧内容工具（或只读文件工具兜底）的粒度、step1 事务接口与 #1669 的对齐、compose-video 的显式排除口径。
