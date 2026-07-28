# Dynamic Agent Profile（按 content_mode 注入）

设计日期：2026-05-16
对应分支：`feat/dynamic-agent-profile`

## 1. 背景

当前 `agent_runtime_profile/.claude/` 在 server 启动和 `create_project` 时由 `lib/profile_manifest.sync_profile_to_project` 全量物化到每个项目目录。manifest-driven sync 用 sha256 区分内置升级 / 用户修改 / 用户主动删除，决策表 15 行 exhaustive 覆盖 `{P 存/缺}×{D 存/缺}×{M 无/active/tombstone}`。

物化结果对所有项目相同。`agent_runtime_profile/CLAUDE.md` 同时叙述 narration（说书+画面）和 drama（剧集动画）两种内容模式的规则；`agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md` 阶段 0 询问 content_mode、阶段 3 按 content_mode 分支 dispatch。这两个文件——尤其是 `CLAUDE.md`——是 agent **始终加载**或编排链路触发后**全量加载**的，无关分支占用注意力 + token。

## 2. 目标与非目标

**目标**：
- 让每个项目目录里的 `CLAUDE.md` 和 `manga-workflow/SKILL.md` **只包含当前项目 `content_mode` 相关内容**
- 仅按 `content_mode`（`narration` / `drama`）切分；`reference_video` 是 `generation_mode` 维度，不参与本次裁剪
- profile 端维护双变体源文件，sync 端按目标项目 mode 选择物化
- 兼容现有 manifest schema_version=1 数据，**不触发已部署项目的破坏性 reset**
- 顺手解耦 CLAUDE.md 中"画面比例 ⇄ content_mode"的错误对应（画面比例由 `aspect_ratio` 配置独立决定）

**非目标**：
- 不裁剪 `.claude/agents/*.md`（按需加载，ROI 低；split-narration-segments / normalize-drama-script / split-reference-video-units 三个 subagent 文件保持原样）
- 不裁剪 `.claude/references/generation-modes.md`（参考资料，按需 Read）
- 不引入模板引擎或 Jinja2 等条件渲染
- 不支持 content_mode 创建后修改（产品现状如此）；spec 仅给出"若未来开放可改"的兜底路径
- 不动 `_apply_decision` 15 行决策表的状态机本身

## 3. 设计概览

profile 端引入 mode 后缀变体（同层并列两份文件，IDE 中可双开对比）：

```
agent_runtime_profile/
├── CLAUDE.narration.md          # 新增（替代 CLAUDE.md）
├── CLAUDE.drama.md              # 新增
└── .claude/
    └── skills/
        └── manga-workflow/
            ├── SKILL.narration.md   # 新增（替代 SKILL.md）
            └── SKILL.drama.md       # 新增
```

sync 算法新增一层"按 mode 解析变体"的预处理，把双变体源文件投影成单一逻辑路径（`CLAUDE.narration.md` → 逻辑 `CLAUDE.md`），决策表完全不动地在逻辑路径上跑。manifest 新增可选字段 `content_mode`，缺失时按 project.json 推断后写回，不匹配时走现有的 reset 路径。

## 4. profile 端：命名约定与校验

### 4.1 命名约定

变体文件命名格式：`<stem>.<mode>.<ext>`，其中：
- `<mode>` ∈ `{narration, drama}`
- `<stem>.<ext>` 是物化后的"逻辑文件名"

变体投影示例：

| profile 端源文件 | 适用 mode | 项目端逻辑文件 |
|---|---|---|
| `CLAUDE.narration.md` | narration | `CLAUDE.md` |
| `CLAUDE.drama.md` | drama | `CLAUDE.md` |
| `.claude/skills/manga-workflow/SKILL.narration.md` | narration | `.claude/skills/manga-workflow/SKILL.md` |
| `.claude/skills/manga-workflow/SKILL.drama.md` | drama | `.claude/skills/manga-workflow/SKILL.md` |
| `.claude/agents/generate-assets.md` | 全部 | `.claude/agents/generate-assets.md` |

只识别 stem 中**最后一段** `.narration` / `.drama` 作为变体标记。`foo.narration.bar.md` 视为普通文件名（不去后缀），避免误伤。

### 4.2 校验规则（部署前置）

`resolve_profile_files_for_mode` 在解析时强制以下约束，违反即抛 `ProfileMisconfiguredError`（新建子类继承 `RuntimeError`），sync 拒绝运行：

1. **变体配对**：若某个逻辑路径有任意变体存在，则 narration / drama 两份变体**必须都存在**。例：profile 端只有 `SKILL.narration.md` 没有 `SKILL.drama.md` → deploy 错误（避免 drama 项目静默丢 SKILL.md）。
2. **变体与通用互斥**：同一逻辑路径不允许同时存在通用文件和变体文件。例：profile 端同时存在 `CLAUDE.md` 和 `CLAUDE.narration.md` → 拒绝（语义二义）。
3. **mode 取值**：变体后缀只允许 `narration` / `drama`，其他后缀视为通用文件名一部分（不做变体识别）。

这三条都是**首次解析时一次性校验**，错误信息明确指向 profile 端文件，便于 dev 修复。

## 5. sync 算法变更

### 5.1 新增函数：`resolve_profile_files_for_mode`

替代 `enumerate_profile_files`，返回逻辑路径到源路径的映射：

```python
def resolve_profile_files_for_mode(
    profile_dir: Path,
    content_mode: str,
) -> dict[str, str]:
    """
    扫描 profile 树，返回 {logical_rel: source_rel}：
    - 通用文件：logical_rel == source_rel
    - 变体文件：仅保留匹配 content_mode 的一份，logical_rel 去掉 .<mode> 后缀

    Raises:
        ProfileMisconfiguredError: 违反 §4.2 校验规则
        ValueError: content_mode 非 narration/drama
    """
```

校验通过 `_VALID_CONTENT_MODES = {"narration", "drama"}` 集合。

### 5.2 `sync_profile_to_project` 签名变更

```python
def sync_profile_to_project(
    profile_dir: Path,
    project_dir: Path,
    content_mode: str,            # 新增必传参数
) -> dict:
```

内部把 `enumerate_profile_files(profile_dir)` 替换为 `resolve_profile_files_for_mode(profile_dir, content_mode)`，下游 `profile_files` 集合改用 `mapping.keys()`（逻辑路径集合）。`_apply_decision` 内 `profile_dir / rel` 改为 `profile_dir / mapping[rel]`（源路径），dest 端继续用逻辑路径 `project_dir / rel`。

manifest 的 entry key 始终是逻辑路径（如 `CLAUDE.md`），不暴露变体源路径。

### 5.3 `force_resync_profile` 同步调整

签名同样新增 `content_mode` 参数；`paths` 参数语义不变（外部传入的是逻辑路径，如 `"CLAUDE.md"`），内部用 mapping 查源路径。`content_mode` 由 `ProjectManager.force_resync_profile`（公开 API）从 `project_dir/project.json` 读取后透传，UI / API 调用方不需要感知 mode。

### 5.4 manifest 扩展：可选 `content_mode` 字段

```python
@dataclasses.dataclass
class Manifest:
    schema_version: int
    profile_id: str
    content_mode: str | None        # 新增；None 表示"待迁移"
    entries: dict[str, dict]
```

`MANIFEST_SCHEMA_VERSION` **保持 1**，不 bump。理由：bump 会触发现有所有项目 `_full_reset_from_profile`，**清空 dest 且不保留用户修改**——破坏性回归。

#### load 逻辑变更

```
读 manifest 后：
  if manifest.content_mode is None:
      # 老 manifest（pre-this-change）→ 视为合规，下次 save 时写入 mode
      mode_status = "needs_migration"
  elif manifest.content_mode == requested_mode:
      mode_status = "match"
  else:
      mode_status = "mismatch"
```

| mode_status | 处理 |
|---|---|
| match / needs_migration | 正常走 15 行决策表 |
| mismatch | 走 `_full_reset_from_profile`（与 schema/profile_id 不匹配同路径） |

sync 主体完成后，无论原状态如何，都把 manifest.content_mode 赋值为 requested_mode；`save_manifest` 序列化时自动落盘。

**mismatch reset 的代价**：清空 dest + 重物化。用户在原 mode 下修改过的 `CLAUDE.md` / `SKILL.md` 会丢失。这是符合"换 mode = 换语义源 = 等价于换 profile"的语义。文档需明确告知用户：手动改 project.json 的 content_mode 等同于显式触发 reset。当前产品创建后 mode 不可改，所以这条路径在正常使用下不触发。

#### save 逻辑变更

`Manifest.to_jsonable()` 把 `content_mode` 序列化（None 时省略键，保持 manifest 文件紧凑）。

#### 老 manifest 平滑迁移路径

老项目 manifest（schema_version=1，无 content_mode 字段）首次 sync 后：
- mode_status = needs_migration → 跑正常 15 行决策表 → save 写入 content_mode → 后续 mode_status = match
- 由于内容真的变了（profile 端 `CLAUDE.md` 已删，改为 `CLAUDE.narration.md`），逻辑路径 `CLAUDE.md` 的三态计算：
  - `d_hash = sha256(老 CLAUDE.md)`，`m_hash = 老 manifest entry sha`，`p_hash = sha256(CLAUDE.narration.md)`
  - 用户**未改**过：`d_hash == m_hash != p_hash` → 决策 #4 升级覆盖 ✓
  - 用户**改**过：`d_hash != m_hash` 且 `!= p_hash` → 决策 #6 user_modified 保留 ✓
- 用户感受：未改的项目自动收敛到对应 mode 变体；改过的项目保留自定义

## 6. ProjectManager 集成

### 6.1 调用顺序问题

当前 `create_project` 在 project.json 写入**之前**就调 `sync_agent_profile`，此时 mode 还不知道。

**修法**：`create_project` 接收 `content_mode` 参数（默认 `"narration"` 兼容老 caller）；server 端 `create_project` 路由把请求体的 `req.content_mode` 透传过去。

```python
def create_project(self, name: str, content_mode: str = "narration") -> Path:
    ...
    self.sync_agent_profile(project_dir, content_mode=content_mode)
```

`server/routers/projects.py` 第 471 行：
```python
manager.create_project(project_name, content_mode=req.content_mode or "narration")
```

### 6.2 `sync_agent_profile` 签名变更

```python
def sync_agent_profile(
    self,
    project_dir: Path,
    *,
    content_mode: str | None = None,
) -> dict:
    """
    content_mode=None 时从 project_dir/project.json 读取；
    project.json 缺失或字段缺失 → 回退到 "narration"（与产品默认一致）+ logger.warning。
    project.json 字段非法 → 抛 ValueError（拒绝静默改变项目语义）。
    """
```

### 6.3 `sync_all_agent_profiles` 行为

对每个项目目录逐个调 `sync_agent_profile(project_dir)`（不传 mode，从 project.json 读）。新增失败模式：

| 失败 | 处理 |
|---|---|
| project.json 缺失 | log warning + 跳过；计 `failed_projects += 1` |
| content_mode 字段缺失 | 回退到 `narration` + log info（老项目向后兼容） |
| content_mode 字段非法值 | log warning + 跳过 |
| ProfileMisconfiguredError | 整体 abort（部署级错误，与 `ProfileMissingError` / `ProfileEmptyError` 同类） |

## 7. 变体文件内容裁剪范围

### 7.1 `CLAUDE.narration.md` / `CLAUDE.drama.md`

来自当前 `agent_runtime_profile/CLAUDE.md` 的裁剪：

**两份共有的清理**（顺手做）：
- "视频规格" 段去掉"narration → 9:16 / drama → 16:9"对应；改为统一句"视频比例由项目 `aspect_ratio` 配置决定"
- "项目结构"、"架构"、"快速开始"、"工作流程概览"等无 mode 分支段落两份保持一致

**narration 变体差异**：
- "## 内容模式" 段简化为"本项目为说书+画面模式（narration）"+ 该模式说明，删去 drama 描述
- 不删提 reference_video（generation_mode 维度仍可切换）

**drama 变体差异**：
- "## 内容模式" 段简化为剧集动画版
- 同上保留 reference_video 提及

### 7.2 `SKILL.narration.md` / `SKILL.drama.md`（manga-workflow）

来自当前 `agent_runtime_profile/.claude/skills/manga-workflow/SKILL.md`：

**两份共有的清理**：
- 状态检测、阶段 1 / 2 / 4 / 5 / 6 / 7 与 mode 无关，两份共用

**narration 变体差异**：
- 阶段 0 步骤 4：从"询问内容模式"改为说明项目已锁定为 `content_mode=narration`
- 状态检测第 3 条：列两行——`generation_mode ∈ {storyboard, grid}` → `step1_segments.md`；`generation_mode == reference_video` → `step1_reference_units.md`。删去 drama 的 `step1_normalized_script.md` 行
- 阶段 3：删去 content_mode 分支，简化为：`generation_mode == reference_video` → dispatch `split-reference-video-units`；否则 → dispatch `split-narration-segments`

**drama 变体差异**：对偶。状态检测保留 `step1_normalized_script.md` + reference_video 两行；阶段 3 简化为：`reference_video` → `split-reference-video-units`；否则 → `normalize-drama-script`。

> 注意 `generation_mode` 是项目级 + 集级可配置的，narration / drama 项目都可能用 reference_video 模式，所以变体不能砍掉 reference_video 分支。

文档化原则：变体文件顶部 frontmatter 紧邻新增一行注释 `<!-- mode: narration -->` / `<!-- mode: drama -->`，便于人眼快速识别，**不影响 sync 算法**（仅文档用途，Markdown 注释）。

## 8. 边界情况

| 情况 | 行为 |
|---|---|
| project.json 的 content_mode 是 `"reference_video"` 或其它非 narration/drama | 抛 `ValueError`，sync 拒绝该项目；sync_all 跳过 + 计错误。**不静默回退**，避免把非法 mode 当成 narration 重写 .claude |
| 同时手动改 project.json 的 mode + 重启 server | sync 检测 mode 不匹配 → 触发 reset → 用户自定义内容丢失。文档明示这是预期行为，未来若开放 mode 编辑要通过 UI 显式确认而非直接改 JSON |
| profile 端只新增 `CLAUDE.narration.md` 忘了 `CLAUDE.drama.md` | resolve 抛 `ProfileMisconfiguredError`；server 启动时 `sync_all_agent_profiles` 全 abort（`aborted=True`），与 `ProfileMissingError` 同等保护级 |
| profile 端 `CLAUDE.md` + `CLAUDE.narration.md` 并存 | 同上拒绝（变体与通用互斥违规） |
| 老项目 project.json 缺 content_mode 字段 | 回退 `narration` + log info；老 manifest needs_migration 路径平滑收敛 |
| 项目 dir 不存在 project.json 但 .claude/ 已物化（异常态） | log warning 跳过 |

## 9. 测试矩阵

`tests/test_profile_manifest.py` 已存在，新增分组：

### 9.1 单元测试：`resolve_profile_files_for_mode`

- 单变体配对完整 + 通用文件混合：narration 项目得到 narration 变体逻辑路径 ✓
- drama 项目得到 drama 变体逻辑路径 ✓
- 只有 narration 变体没有 drama 变体 → `ProfileMisconfiguredError`
- `CLAUDE.md` + `CLAUDE.narration.md` 并存 → `ProfileMisconfiguredError`
- 非法 mode 参数 → `ValueError`
- `foo.narration.bar.md` 不被识别为变体（保持完整文件名）

### 9.2 manifest 兼容性

- load 老 manifest（无 content_mode 字段）：mode_status = needs_migration，sync 不触发 reset
- load 新 manifest 且 mode 匹配：正常 15 行决策表
- load 新 manifest 且 mode 不匹配：走 `_full_reset_from_profile`
- save 后 manifest 包含 content_mode 字段

### 9.3 sync 端到端

- 全新 narration 项目 sync：dest 包含 `CLAUDE.md`（内容=narration 变体）、`SKILL.md`（=narration 变体），不包含变体源文件名
- 全新 drama 项目 sync：对偶
- 老项目带用户改过的 `CLAUDE.md`（不匹配任何变体）：决策 #6 保留用户内容
- 老项目带未改过的 `CLAUDE.md`（旧 mode-mixed 内容）：决策 #4 升级到 narration 变体
- 用户改 project.json content_mode 后再 sync：mode mismatch → reset → dest 切换到新 mode 变体

### 9.4 ProjectManager 集成

- `create_project(name, content_mode="drama")`：dest 物化 drama 变体
- `sync_all_agent_profiles` 项目缺 project.json：跳过 + 失败计数
- `sync_all_agent_profiles` 项目 content_mode 非法：跳过 + log warning
- `force_resync_profile(paths=["CLAUDE.md"])`：传逻辑路径，重新物化当前 mode 变体

## 10. 兼容性与发布

- manifest schema_version 不动 → 现存项目零侵入，首次 sync 自然迁移
- API 兼容：`create_project(name)` 老 caller 默认 `narration`；`server/routers/projects.py` 显式传 `req.content_mode`，已有路由保持 200 响应
- 测试：现有 `test_profile_manifest.py` 所有 case 需要补 `content_mode="narration"` 参数；新增 cases 见 §9
- 文档：`agent_runtime_profile/.claude/references/generation-modes.md` 不动；这次设计文档（本文）作为变更说明

## 11. 未来扩展

- **若未来 content_mode 开放编辑**：UI 改 mode 时显式调 `force_resync_profile`（或新增 `change_project_mode` 端点），明示用户"自定义 SKILL.md 内容会丢失"。spec 现状预留 reset 路径已经够用。
- **若加第三种 content_mode**：profile 端加 `*.<new_mode>.md` 三份变体；`resolve_profile_files_for_mode` 的 `_VALID_CONTENT_MODES` 加一条；校验规则 §4.2.1 自动扩展"三份必须全部存在"。
- **若想给 `references/generation-modes.md` 也做变体**：同样的变体后缀机制直接复用，不需要再设计。
