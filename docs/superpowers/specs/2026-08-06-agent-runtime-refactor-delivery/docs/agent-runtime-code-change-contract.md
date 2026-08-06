# ArcReel Agent Runtime v2 — 代码层修改契约

- **基线仓库**：`ArcReel/ArcReel`
- **基线提交**：`6883b9fd4cfc546c68c3f84c5c4f0c81edab677e`
- **配套 profile**：本交付物中的 `agent_runtime_profile/`
- **契约级别**：目标态；代码与 profile 应在同一版本发布

## 1. 目标

本契约把智能体运行层从“Agent 根据文件存在性重新实现状态机”改为：

1. 服务端计算工作流事实；
2. Agent 执行服务端返回的下一动作；
3. 所有正式结构化数据经事务式工具修改；
4. 所有派生产物按输入 provenance 判断 `current / stale / missing`；
5. 空结果、审核门、部分失败和确认等待都可持久、可恢复、可测试。

## 2. 非目标

本次不改变：

- `content_mode`、`generation_mode` 的业务含义；
- 已有生成供应商接口；
- 项目创建仍由 Web 端完成；
- ad 仍为恒单集、无 step1；
- reference-video 正式 step1 仍采用隔离草稿晋升通道；
- `compose_video.py` 的媒体算法与 CLI 参数。

---

# AR-RT-001：权威工作流状态服务

## 3. 新增领域服务

新增：

```text
lib/workflow_state.py
```

建议核心类型：

```python
class WorkflowState(str, Enum):
    PROJECT_INPUT = "PROJECT_INPUT"
    SELLING_POINTS = "SELLING_POINTS"
    ASSET_INVENTORY = "ASSET_INVENTORY"
    EPISODE_PLAN = "EPISODE_PLAN"
    STEP1_CONTENT = "STEP1_CONTENT"
    STEP1_REVIEW = "STEP1_REVIEW"
    FINAL_SCRIPT = "FINAL_SCRIPT"
    ASSET_SHEETS = "ASSET_SHEETS"
    PRODUCT_REVIEW = "PRODUCT_REVIEW"
    STORYBOARD = "STORYBOARD"
    VIDEO = "VIDEO"
    AUDIO = "AUDIO"
    EXPORT_READY = "EXPORT_READY"
```

`WorkflowStateService` 必须复用现有领域逻辑，而不是再次复制：

- `ProjectManager`；
- `StatusCalculator`；
- episode ledger / planning cursor；
- `lib.script_review`；
- generation route skeleton；
- 本契约新增的 provenance manifest。

所有 REST、MCP 与后续 UI workflow 展示共用此服务。

## 4. 新增 MCP 工具

新增：

```text
server/agent_runtime/sdk_tools/workflow_status.py
mcp__arcreel__get_workflow_status
```

注册到：

```text
server/agent_runtime/sdk_tools/__init__.py
```

### 4.1 输入

```json
{
  "episode": 2,
  "include_details": true
}
```

约束：

- `episode` 可省略；
- ad 省略或传入 1；传其他值返回参数错误；
- narration / drama 省略时按第 6 节选择目标集；
- 工具无副作用。

### 4.2 输出

输出为 JSON 文本，schema version 固定：

```json
{
  "schema_version": 1,
  "project_revision": "sha256:...",
  "source_revision": "sha256:...",
  "project": {
    "content_mode": "narration",
    "generation_mode": "storyboard",
    "grid_storyboard": false
  },
  "target": {
    "episode": 2,
    "script": "episode_2.json",
    "source": "source/episode_2.txt"
  },
  "state": "STORYBOARD",
  "blockers": [],
  "gates": {
    "step1_review": {
      "state": "confirmed",
      "revision": "sha256:..."
    },
    "product_sheet_review": {
      "state": "not_applicable",
      "pending_names": []
    }
  },
  "artifacts": {
    "asset_inventory": {
      "state": "current"
    },
    "asset_sheets": {
      "character": {"missing_ids": [], "stale_ids": [], "current_ids": ["张三"]},
      "scene": {"missing_ids": [], "stale_ids": [], "current_ids": ["客栈"]},
      "prop": {"missing_ids": [], "stale_ids": [], "current_ids": []}
    },
    "step1": {
      "state": "current",
      "path": "drafts/episode_2/step1_segments.json",
      "revision": "sha256:..."
    },
    "script": {
      "state": "current",
      "path": "scripts/episode_2.json",
      "revision": "sha256:..."
    },
    "storyboards": {
      "missing_ids": ["E2S03"],
      "stale_ids": ["E2S07"],
      "current_ids": ["E2S01", "E2S02"]
    },
    "videos": {
      "missing_ids": [],
      "stale_ids": ["E2S07"],
      "current_ids": ["E2S01", "E2S02"]
    },
    "audio": {
      "missing_ids": [],
      "stale_ids": [],
      "current_ids": []
    }
  },
  "next_action": {
    "type": "generate_storyboards",
    "args": {
      "script": "episode_2.json",
      "segment_ids": ["E2S03", "E2S07"]
    },
    "requested_ids": ["E2S03", "E2S07"],
    "requires_confirmation": false,
    "reason": "1 missing and 1 stale storyboard"
  }
}
```

### 4.3 Artifact state

单项状态只允许：

```text
missing | stale | current | blocked | not_applicable
```

集合必须互斥且穷尽当前正式结构中的全部资源 ID。容器形状损坏时：

- 不抛 500；
- 对应 artifact 标 `blocked`；
- blocker 含字段路径与原因；
- 不部分计数成 current。

## 5. `next_action.type` 枚举

允许值：

```text
collect_project_input
draft_selling_points
analyze_assets
plan_episodes
prepare_step1
confirm_step1
generate_script
generate_asset_sheets
confirm_product_sheet
generate_storyboards
generate_grid
generate_videos
generate_narration_audio
export
none
```

`prepare_step1` 必须额外返回：

```json
{
  "preprocessor": "split-narration-segments"
}
```

或：

```text
normalize-drama-script
split-reference-video-units
```

代码层不返回 profile 文件路径，也不负责 dispatch；只返回稳定的领域 action。

## 6. 目标集选择

### narration / drama

1. 用户传 episode：使用该集；不存在账本条目时 state 为 `EPISODE_PLAN`；
2. 未传：选择最小的 `ledger_status in {planned, stale}` 集；
3. 没有待制作集、源文仍未规划完：`EPISODE_PLAN`；
4. 全部已消费且所有产物 current：`EXPORT_READY`。

不得通过 `source/episode_N.txt` 或 `scripts/episode_N.json` 文件名推断下一集。

### ad

目标恒为 episode 1。

## 7. 状态优先级

状态机按以下顺序返回第一个未满足条件；blocker 高于普通动作。

### narration / drama

```text
PROJECT_INPUT
ASSET_INVENTORY
EPISODE_PLAN
STEP1_CONTENT
STEP1_REVIEW
FINAL_SCRIPT
ASSET_SHEETS
STORYBOARD        # 仅 storyboard
VIDEO
AUDIO             # 仅 narration + storyboard
EXPORT_READY
```

### ad

```text
PROJECT_INPUT
SELLING_POINTS    # 带货项目
ASSET_SHEETS      # 仅已有定义且缺/stale sheet
FINAL_SCRIPT
PRODUCT_REVIEW    # 有 product_sheet 时
STORYBOARD        # 仅 storyboard
VIDEO
EXPORT_READY
```

---

# AR-RT-002：资产清单完成状态

## 8. 问题契约

`characters / scenes / props` 为空不能表示“尚未分析”。空 bucket 是合法结果。

## 9. 项目持久化结构

在 `project.json` 增加可选顶层字段：

```json
{
  "workflow": {
    "asset_inventory": {
      "scope": {
        "kind": "all",
        "files": []
      },
      "source_revision": "sha256:...",
      "completed_at": "2026-08-06T00:00:00Z"
    },
    "product_sheet_reviews": {}
  }
}
```

旧项目缺字段时按未完成处理，但仅在 narration / drama 阻塞；ad 不要求资产清单分析。

## 10. Source revision

新增单一实现：

```text
lib/source_revision.py
```

- 对 scope 内源文件按规范化项目相对路径排序；
- hash 输入包含路径、文件字节 hash、source_kind、source_language；
- 算法标识写入前缀 `sha256-v1:`；
- 忽略派生的 `source/episode_N.txt` 时必须明确：全局 inventory 使用原始上传源，不把规划派生文件重复计入；
- 符号链接、项目外路径、不可读文件返回 blocker，不静默跳过。

## 11. 新增工具

```text
server/agent_runtime/sdk_tools/asset_inventory.py
mcp__arcreel__complete_asset_inventory
```

输入：

```json
{
  "scope": {"kind": "all", "files": []},
  "expected_source_revision": "sha256-v1:..."
}
```

行为：

1. 重新计算 source revision；
2. 不一致则原子拒绝，返回最新 revision；
3. 持项目锁写入 inventory marker；
4. 不要求任一 bucket 非空；
5. 返回三个 bucket 数量与 revision。

修改源文后无需主动清 marker；status 比较 revision 后自然判 stale。

---

# AR-RT-003：正式 step1 的事务式编辑

## 12. 新增工具

```text
server/agent_runtime/sdk_tools/patch_step1.py
mcp__arcreel__patch_step1
```

适用：

- narration storyboard 的 `step1_segments.json`；
- drama storyboard 的 `step1_normalized_script.json`。

reference-video 明确排除，继续使用现有 open-draft / validate-promote 流程。

## 13. 输入契约

```json
{
  "episode": 1,
  "expected_revision": "sha256-v1:...",
  "operations": [
    {
      "op": "update",
      "id": "E1S03",
      "fields": {
        "duration_seconds": 8,
        "scene_description": "..."
      }
    },
    {
      "op": "insert_after",
      "after_id": "E1S03",
      "item": {
        "scene_id": "E1S04_1",
        "duration_seconds": 8
      }
    },
    {
      "op": "move_after",
      "id": "E1S06",
      "after_id": "E1S02"
    },
    {
      "op": "remove",
      "id": "E1S07"
    }
  ]
}
```

允许 op：

```text
update | insert_after | move_after | remove
```

`after_id: null` 表示放到首项。

## 14. 写入语义

- 取得与 Web 保存相同的项目/episode 文件锁；
- `expected_revision` 不匹配返回 conflict，不做部分写入；
- 所有 operation 先在内存副本执行；
- 按当前 Pydantic step1 schema 全量校验；
- 校验 ID 前缀、唯一性、字段白名单、时长能力、源文逐字约束与资产引用；
- 任一 operation 非法，全部不落盘；错误包含 operation index、ID、字段路径；
- 临时文件 + fsync + atomic replace；
- 成功后使 step1 review confirmation 失效；
- 不删除旧 script/media；由 provenance 自动判 stale；
- 返回 before/after revision 与受影响 ID。

## 15. 兼容迁移

运行时 sandbox / hook 应把 narration 与 drama 正式 step1 加入 denyWrite。仅 `*.invalid.json` 草稿允许 Edit。

---

# AR-RT-004：Artifact provenance 与陈旧传播

## 16. Manifest

新增项目内部文件：

```text
{project}/.arcreel_artifacts.json
{project}/.artifact_manifest.lock
```

实现：

```text
lib/artifact_manifest.py
lib/artifact_provenance.py
```

建议结构：

```json
{
  "schema_version": 1,
  "hash_algorithm": "sha256-v1",
  "entries": {
    "episode:1:step1": {
      "path": "drafts/episode_1/step1_segments.json",
      "input_hash": "sha256-v1:...",
      "output_hash": "sha256:...",
      "producer": "split_narration_segments",
      "created_at": "...",
      "legacy_backfill": false
    },
    "episode:1:storyboard:E1S03": {
      "path": "storyboards/scene_E1S03.png",
      "input_hash": "sha256-v1:...",
      "output_hash": "sha256:...",
      "producer": "generate_storyboards",
      "created_at": "...",
      "legacy_backfill": false
    }
  }
}
```

安全要求与 profile manifest 同级：

- `O_NOFOLLOW` / symlink 防护；
- project root containment；
- lock；
- deterministic JSON；
- atomic write；
- unchanged 不重写；
- manifest 自身不可由 agent 直接写。

## 17. 稳定 key

至少支持：

```text
asset:character:{name}:sheet
asset:scene:{name}:sheet
asset:prop:{name}:sheet
asset:product:{name}:sheet
episode:{N}:step1
episode:{N}:script
episode:{N}:grid:{group_id}
episode:{N}:storyboard:{resource_id}
episode:{N}:video:{resource_id_or_unit_id}
episode:{N}:audio:{segment_id}
```

名称进入 key 前使用可逆编码或结构化 key builder，不能手工字符串拼接后解析。

## 18. Hash canonicalization

统一函数：

```python
canonical_hash(kind: ArtifactKind, inputs: Mapping[str, Any]) -> str
```

- JSON `sort_keys=True`；
- 紧凑 separators；
- UTF-8；
- 非有限数字拒绝；
- 路径使用项目相对 POSIX 形式；
- 输入 schema 带 `kind_version`，builder 语义修改时递增。

## 19. 各产物 input hash

### 19.1 Asset sheet

包含：

- asset type / name / description；
- project style / style_description；
- 产品原图 output hash（product）；
- provider、model、图像设置；
- prompt builder kind version。

### 19.2 Step1

包含：

- source revision 或 episode source bytes hash；
- content_mode / generation_mode；
- source_kind / source_language；
- relevant project preferences；
- text model/provider；
- prompt builder kind version。

### 19.3 Final script

包含：

- current step1 output hash；ad 则为 brief、products、target_duration；
- overview、style、资产定义 revision；
- content/generation mode；
- text model/provider；
- script prompt builder kind version。

### 19.4 Storyboard / grid

包含：

- item image_prompt / visual fields；
- referenced sheet output hashes；
- style、aspect_ratio、grid_storyboard；
- previous-frame dependency output hash；
- image provider/model/settings；
- builder kind version。

### 19.5 Video

包含：

- item/unit video prompt；
- duration；
- storyboard frame output hash，或 reference image output hashes；
- route、aspect_ratio、resolution；
- video provider/model/settings；
- builder kind version。

### 19.6 Narration audio

包含：

- exact `novel_text`；
- effective voice / speed；
- audio provider/model/settings；
- builder kind version。

## 20. 状态判定

```text
path missing                                      -> missing
path exists, manifest entry missing               -> legacy
path exists, entry exists, expected == input_hash -> current
path exists, entry exists, expected != input_hash -> stale
path escapes project / not regular / unreadable   -> blocked
```

`legacy` 不暴露为最终 workflow state。迁移策略见第 22 节。

## 21. 陈旧传播例

- 修改 step1 → script stale → storyboard/grid stale → video stale；
- 修改 `image_prompt` → storyboard stale → video stale；
- 重生 sheet → 引用该 sheet 的 storyboard/reference video stale；
- 切换 `grid_storyboard` → 全部 storyboard 与依赖视频 stale；
- 修改 `narration_voice/speed` → audio stale；
- 修改模型或分辨率 → 对应媒体 stale。

传播通过重新计算 expected hash 完成，不需要级联清空字段或删除文件。

## 22. 旧项目迁移

不得把所有存量媒体直接判 stale，避免意外大规模重生成与费用。

实现一次性/懒迁移：

1. 对存在且位于项目内的旧 artifact 路径计算当前 expected input hash；
2. 写 manifest entry，`legacy_backfill=true`；
3. 把当下状态视为 current；
4. 后续任何输入变化正常转 stale；
5. 损坏、逃逸或不存在路径不 backfill。

迁移在项目锁下幂等执行。提供显式管理函数与单元测试，不在每次 status 无界扫描全盘。

---

# AR-RT-005：生成工具默认处理 missing + stale

## 23. 修改范围

至少修改：

```text
server/agent_runtime/sdk_tools/enqueue_assets.py
server/agent_runtime/sdk_tools/enqueue_storyboards.py
server/agent_runtime/sdk_tools/enqueue_grid.py
server/agent_runtime/sdk_tools/enqueue_videos.py
server/agent_runtime/sdk_tools/enqueue_narration_audio.py
lib/status_calculator.py
生成 worker 成功回写点
```

## 24. 选择语义

- 未传 ID/names：选择 `missing ∪ stale`；
- 显式传 ID：选择所有有效命中 ID，即使 current，也表示强制重生；
- 显式空列表：参数错误；
- 未命中 ID：错误列出，不静默忽略；
- 全部 current 且未显式选择：返回 no-op summary，不入队；
- 工具结果区分 `generated_missing` 与 `regenerated_stale`。

## 25. 成功回写

只有生成完成且正式文件写入成功后更新 artifact manifest。入队、开始执行、checkpoint 都不能把 artifact 标 current。

任务失败保留旧文件与旧 manifest entry；status 仍为 stale 或 missing，不被失败结果覆盖成 current。

## 26. `StatusCalculator`

现有按非空路径计数的逻辑改为调用 provenance resolver。API 可继续保留 `completed` 计数，但含义改为 current 数；新增：

```json
{
  "storyboards": {"total": 10, "completed": 7, "stale": 2, "missing": 1},
  "videos": {"total": 10, "completed": 6, "stale": 3, "missing": 1}
}
```

项目只有在所有必需 artifact current 时为 completed。

---

# AR-RT-006：审核门

## 27. Step1 review

复用现有 `lib/script_review.py`，但审核确认必须绑定 step1 output hash：

```json
{
  "reviewed_output_hash": "sha256:...",
  "confirmed_at": "..."
}
```

step1 修改或重新生成后 hash 改变，审核自然失效。`get_workflow_status` 暴露 `pending / confirmed / not_applicable`。

## 28. Product sheet review

新增工具：

```text
server/agent_runtime/sdk_tools/product_review.py
mcp__arcreel__confirm_product_sheet_review
```

输入：

```json
{
  "names": ["产品A"],
  "expected_sheet_hashes": {
    "产品A": "sha256:..."
  }
}
```

写入：

```json
project.workflow.product_sheet_reviews[product_name] = {
  "approved_output_hash": "sha256:...",
  "approved_at": "..."
}
```

规则：

- 产品无 sheet：not applicable，不阻塞；
- sheet 存在且当前 output hash 未确认：`PRODUCT_REVIEW`；
- 重生/替换 sheet 后旧确认自动失效；
- expected hash 不匹配原子拒绝，避免用户看到旧图却批准新图；
- ad 的首次 storyboard 或 reference-video 入队前强制检查；
- 全自主授权不能替代产品视觉保真确认。

---

# AR-RT-007：Profile frontmatter 与静态校验

## 29. YAML 解析

现有 `AssistantService._load_skill_metadata` 使用逐行 `split(':', 1)`，无法正确处理合法 YAML 的引号、冒号、多行值，也无法发现非法 frontmatter。

修改：

```text
server/agent_runtime/service.py
```

要求：

- 使用 `yaml.safe_load` 或统一 frontmatter parser；
- frontmatter 顶层必须是 object；
- `name` 非空字符串；
- `description` 非空字符串；
- `user-invocable` 为 bool，默认 true；
- YAML 无效时 warning + 跳过，不以破损 description 暴露 skill；
- variant skill 的 name 与 user-invocable 必须一致；description 可按 mode 不同，但本 profile 不再需要 workflow variants。

## 30. Profile lint

新增：

```text
scripts/lint_agent_runtime_profile.py
```

CI 必须检查：

1. 所有 skill / agent frontmatter 是合法 YAML；
2. 所有三模式 variant 配对完整；
3. 对 narration、drama、ad 分别执行 `resolve_profile_files_for_mode`；
4. 投影后所有 Markdown pointer 目标存在；
5. 不出现已废弃字符串：
   - `--scene-ids`
   - `--resume` 作为 Python CLI
   - `--music-volume`
   - `step1_normalized_script.md` 作为有效输入
   - 直接 Edit 正式 narration/drama step1
6. 所有 MCP 示例工具名已注册；
7. JSON eval 文件可解析，ID 唯一。

---

# AR-RT-008：Profile 同步与迁移

## 31. 结构变化

目标 profile 删除：

```text
CLAUDE.ad.md
CLAUDE.drama.md
CLAUDE.narration.md
.claude/references/generation-modes.md
.claude/agents/generate-assets.md
.claude/skills/manga-workflow/SKILL.ad.md
.claude/skills/manga-workflow/SKILL.drama.md
.claude/skills/manga-workflow/SKILL.narration.md
.claude/skills/generate-video/references/veo_prompts.md
```

新增/替换：

```text
CLAUDE.md
.claude/references/workflow-mode.<mode>.md
.claude/references/generation-routing.md
.claude/references/completion-contract.md
.claude/references/edit-or-regenerate.md
.claude/references/reference-draft-repair.md
.claude/references/video-duration-confirmation.md
.claude/agents/run-generation-task.md
.claude/skills/manga-workflow/SKILL.md
```

现有 `profile_manifest` 已支持任意 `.mode.md` 投影；保持该语义。

## 32. 同步验收

为 `tests/test_profile_manifest.py` 和 `tests/test_project_manager_symlink.py` 增加：

- 三个旧顶层 CLAUDE variant → 新 common CLAUDE 的升级；
- mode reference 在三个项目中分别物化为 `.claude/references/workflow-mode.md`；
- 未修改的旧 workflow variant 与旧 agent 被移除；
- 用户修改过的旧文件按现有三方合并语义保留，不被静默覆盖；
- 新 common skill 在技能列表只出现一次；
- profile 为空/缺失仍 fail closed。

如果现有 manifest state machine 不能安全处理“variants → common”源变化，应提升 manifest schema version 并走明确 reset/migration，不能靠路径巧合。

---

# AR-RT-009：测试与 Evals

## 33. 单元测试

新增测试至少覆盖：

### Workflow state

- narration/drama/ad 每个 state；
- 空 props 但 inventory current；
- ledger stale 优先回 STEP1_CONTENT；
- reference route 跳过 storyboard/audio；
- ad generic 跳过 product states；
- ad product sheet current revision gate；
- malformed script 返回 blocker 而非 500；
- target episode 选择只读 ledger。

### Provenance

- canonical hash deterministic；
- prompt 修改导致 storyboard + video stale；
- sheet 重生只影响引用它的产物；
- grid switch 导致 storyboard/video stale；
- voice/speed 导致 audio stale；
- provider/model 变化导致对应媒体 stale；
- legacy backfill 幂等；
- symlink/path traversal 拒绝；
- failed generation 不更新 manifest。

### Step1 patch

- expected revision conflict；
- update/insert/move/remove；
- 任一 operation 失败则全批回滚；
- schema 与时长校验；
- source fidelity；
- review invalidation；
- script/media 不删除但 status stale；
- concurrent Web save 串行化。

### Review

- step1 hash 绑定；
- product sheet hash 绑定；
- 旧 revision 确认不复用。

### Frontmatter

- description 含冒号、引号、多行；
- 非法 YAML 被跳过；
- variant metadata drift 被拒；
- `user-invocable: false` 不显示在用户技能列表。

## 34. 行为 eval

以交付 profile 中：

```text
agent_runtime_profile/skill-optimization-workspace/evals/evals.json
agent_runtime_profile/skill-optimization-workspace/manga-workflow-trigger-eval.json
```

为新基线。旧 eval 中的 `--scene-ids`、Python `--resume`、`--music-volume` 断言全部删除。

---

# 35. API 与 UI 兼容

## REST

建议新增只读 endpoint，复用同一服务：

```text
GET /api/v1/projects/{project}/workflow-status?episode=N
```

用于调试与 Web 流程展示。MCP 与 REST 输出 schema 共用 Pydantic model。

## UI

- 展示 missing / stale / current，不再把“文件存在”统一显示完成；
- product sheet 显示当前 revision 的审核状态；
- stale 产物提供重生入口，不自动删除旧文件；
- 旧项目 provenance backfill 不应弹出大规模重生成提示。

## 数据兼容

- `project.json.workflow` 为可选字段；
- `.arcreel_artifacts.json` 缺失走迁移；
- 旧 API 字段保留，新增 stale/missing 计数；
- manifest / workflow 文件不进入用户导出的剧本内容。

---

# 36. 实施顺序

建议拆为以下可独立评审的 PR：

1. **Profile lint + frontmatter YAML parser**：先阻止文档继续漂移。
2. **Artifact manifest 基础设施 + legacy backfill**：尚不改变 UI 完成语义。
3. **各生成工具写 provenance + missing/stale 选择**。
4. **WorkflowStateService + MCP/REST status**。
5. **资产 inventory marker + complete tool**。
6. **patch_step1 事务工具 + denyWrite 收口**。
7. **product sheet revision gate**。
8. **替换 agent_runtime_profile + 新 evals**。
9. **UI stale/current 展示**。

第 8 步必须与第 4、5、6 步同一发布窗口完成；否则新 profile 会调用尚不存在的工具。

---

# 37. 最终验收标准

全部满足才算完成：

1. 在 narration、drama、ad 新项目中，仅用 `/manga-workflow` 可从当前状态推进到 `EXPORT_READY`；
2. 服务重启、新会话与上下文压缩后，下一状态不依赖会话记忆；
3. 合法空资产 bucket 不产生无限循环；
4. 修改上游输入后，旧文件仍保留但 status 精确标 stale；
5. 省略 ID 的生成调用补齐 missing 与 stale，不重生 current；
6. 正式 project/script/step1 不再通过文件工具直接修改；
7. reference invalid draft 修复不重复付费生成；
8. 所有批量任务满足 requested = current ∪ failed ∪ blocked；
9. profile lint、单元测试和新 behavior eval 全部通过；
10. 旧项目首次升级不触发无意的大规模媒体重生成。
