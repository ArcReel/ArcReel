## Context

### 现状

ArcReel 使用 Claude Agent SDK 运行一个主 agent 会话，通过 Agent 工具（原 Task 工具）dispatch subagent 处理具体任务。当前有两个大型 subagent（`novel-to-narration-script`、`novel-to-storyboard-script`）各自包含 3-4 个步骤和用户确认点，以及一个静态编排文档 `manga-workflow`。

### Claude Code Subagent 机制约束

基于 [Claude Code 官方文档](https://code.claude.com/docs/en/sub-agents.md)：

1. **Subagent 不能 spawn subagent**：嵌套委派不可行，多步工作流只能通过主 agent 链式 dispatch
2. **Skill 预加载**：subagent 的 `skills` frontmatter 字段可将 skill 内容注入 subagent context，subagent 不继承主 agent 的 skills
3. **Subagent 独立 context**：subagent 只接收自己的 system prompt + 基础环境信息，不接收主 agent 的完整 system prompt
4. **Background subagent**：可设置 `background: true` 让 subagent 后台运行，auto-deny 未预授权的权限
5. **Resume 机制**：subagent 可通过 agent ID 恢复继续，保留完整历史
6. **Auto-compaction**：subagent 支持自动压缩，约 95% 容量时触发

### 相关文件

| 文件 | 作用 |
|------|------|
| `agent_runtime_profile/.claude/agents/*.md` | Subagent 定义 |
| `agent_runtime_profile/.claude/skills/*/SKILL.md` | Skill 定义 |
| `agent_runtime_profile/.claude/settings.json` | 权限和工具配置 |
| `agent_runtime_profile/CLAUDE.md` | 主 agent 运行时指令 |
| `server/agent_runtime/session_manager.py` | 主 agent prompt 注入 |

## Goals / Non-Goals

**Goals:**

1. 将两个多步 subagent 拆解为多个聚焦的单任务 subagent，每个 subagent 做一件事并返回
2. 将角色/线索提取从 per-episode 流程中独立出来，成为全局操作
3. 将 manga-workflow 从静态文档升级为具备状态检测和 dispatch 逻辑的编排 skill
4. 建立清晰的 skill（脚本执行）vs subagent（推理分析）边界
5. 生成类 skill 的调用下沉到 subagent 中，保护主 agent 上下文空间

**Non-Goals:**

- 修改 generate-characters、generate-clues、generate-storyboard、generate-video、compose-video 等生成 skill 的实现
- 修改后端服务代码（server/agent_runtime/ 不变）
- 修改前端代码
- 修改 project.json 数据结构
- 实现全新的工作流引擎或状态机框架（用 skill prompt 实现即可）

## Decisions

### Decision 1：Subagent 拆解策略

**选择**：将两个大 agent 拆解为 3 个聚焦 subagent + 利用现有 skill

**替代方案**：
- A) 保留两个大 agent，只修改内部流程 → 不解决作用域错配问题
- B) 完全消除 subagent，全部用 skill 在主 agent 中执行 → 小说原文会污染主 agent context
- C) 每个原有步骤对应一个 subagent（5-6 个）→ 过度拆分，部分步骤太轻量不值得 subagent 开销

**理由**：3 个 subagent 对应 3 个真正需要推理的阶段（全局角色分析、单集预处理、JSON 生成时的验证和修正）。生成类操作（generate-characters 等）已有独立 skill/脚本，直接通过 subagent 调用即可。

**新 subagent 清单**：

| Subagent | 作用域 | 输入 | 输出 | 预加载 skills |
|----------|--------|------|------|--------------|
| `analyze-characters-clues` | 全局（整部小说） | 小说原文 + 已有角色/线索 | 角色表 + 线索表（写入 project.json） | — |
| `split-narration-segments` | 单集（narration 模式） | 本集小说文本 + 角色/线索列表 | `drafts/episode_{N}/step1_segments.md` | — |
| `normalize-drama-script` | 单集（drama 模式） | 本集小说文本 + 角色/线索列表 | `drafts/episode_{N}/step1_normalized_script.md` + `step2_shot_budget.md` | — |
| `create-episode-script` | 单集 | 集数 + content_mode | scripts/episode_N.json | `generate-script` |

**创建方式**：实施时由 Claude 提供每个 agent 的 description 文本，用户通过 `/agents` 命令使用 Claude 交互式创建。

### Decision 2：编排 skill 设计方式

**选择**：将 `manga-workflow` 重写为带状态检测逻辑的编排 skill（纯 prompt 驱动，无代码框架）

**替代方案**：
- A) 编写一个 Python 状态机框架来编排 → 过度工程化，且与 Claude Agent SDK 的 prompt-based 模式不匹配
- B) 拆分为多个独立 skill，用户手动按顺序调用 → 缺乏自动化编排，用户体验差
- C) 在 session_manager.py 中硬编码编排逻辑 → 违反 agent_runtime_profile 与 server 的分离原则

**理由**：编排 skill 本质是一组决策规则——检查状态、决定下一步、dispatch 正确的 subagent。这适合用结构化的 prompt 来表达，不需要代码框架。主 agent 加载 manga-workflow skill 后，按 skill 中的决策树行动。

**manga-workflow 编排 skill 结构**：
```
1. 状态检测（读 project.json + 检查 drafts/scripts 文件系统）
2. 阶段决策树：
   ├─ 缺少角色/线索 → dispatch analyze-characters-clues
   ├─ 缺少 drafts → dispatch preprocess-episode
   ├─ 缺少 scripts → dispatch create-episode-script
   ├─ 缺少设计图 → dispatch subagent 调用 generate-characters/clues
   ├─ 缺少分镜 → dispatch subagent 调用 generate-storyboard
   └─ 缺少视频 → dispatch subagent 调用 generate-video
3. 每个 dispatch 返回后：展示摘要 → 用户确认 → 进入下一阶段
```

### Decision 3：Skill 调用方式——预加载 vs 运行时调用

**选择**：混合策略

- `create-episode-script` subagent 通过 `skills` 字段**预加载** `generate-script` skill（因为该 subagent 的核心任务就是调用这个 skill）
- 资产生成阶段（generate-characters/storyboard/video）由主 agent dispatch 一个通用 subagent，该 subagent 运行时通过 **Bash 工具直接调用** 对应的 Python 脚本（因为这些 skill 本质就是脚本包装）

**替代方案**：
- 所有 skill 都预加载 → 部分 subagent 会加载不需要的 skill 内容，浪费 context
- 所有 skill 都运行时调用 → 部分 skill 的指令对 subagent 的行为至关重要，需要预加载

**理由**：预加载适合"subagent 的行为完全由 skill 定义"的场景；运行时调用适合"subagent 只需执行一个脚本命令"的场景。

### Decision 4：全局角色/线索提取的触发时机

**选择**：作为编排流程的第一个显式阶段，且支持独立调用

**设计**：
- `manga-workflow` 编排时，如果 project.json 中 characters 为空或 clues 为空，自动进入全局提取阶段
- 用户也可以随时单独调用（如 "分析整部小说的角色" → 主 agent dispatch `analyze-characters-clues`）
- 支持增量模式：如果 project.json 已有角色，subagent 对比小说和现有列表，只追加新角色
- 用户可指定分析范围（整部小说 / 某几章 / 某一集对应的部分）

**替代方案**：
- 只在第一集创建时自动触发 → 回到原来的隐式副作用模式
- 每集都重新分析 → 浪费且可能产生不一致

### Decision 5：资产生成阶段是否使用 subagent

**选择**：资产生成 skill（generate-characters/storyboard/video）通过 subagent 调用

**理由**：
- 这些 skill 执行时会产生大量输出（生成 prompt、API 调用日志、进度信息）
- 下沉到 subagent 可保护主 agent context
- subagent 可以处理生成失败、重试、部分结果汇总等逻辑，只返回最终摘要
- 利用 subagent 的 `background: true` 选项，部分生成任务可后台运行

**实现方式**：为资产生成阶段创建一个通用的 `asset-generator` subagent 模板，通过参数指定调用哪个 skill 脚本。或者直接让主 agent dispatch general-purpose subagent 并在 prompt 中指定任务。

### Decision 6：角色/线索写入脚本化

**选择**：新建 `add_characters_clues.py` 脚本，封装 `ProjectManager.add_characters_batch()` + `add_clues_batch()` + `validate_project()`

**现状**：
- `ProjectManager` 已有 `add_characters_batch()` 和 `add_clues_batch()` 方法
- 但没有独立的 CLI 脚本——当前两个大 agent 通过内嵌 Python 代码块调用这些方法
- subagent 需要通过 Bash 工具调用脚本（而非内嵌代码），因此需要一个可执行脚本

**设计**：
```bash
# 用法
python .claude/skills/manage-project/scripts/add_characters_clues.py {project_name} \
  --characters '{"角色名": {"description": "...", "voice_style": "..."}}' \
  --clues '{"线索名": {"type": "prop", "description": "...", "importance": "major"}}'
```

- 输入：项目名 + JSON 格式的角色/线索数据（通过命令行参数或 stdin）
- 输出：写入 project.json + 调用 validate_project 验证 + 打印成功/失败摘要
- **settings.json 放行**：需在 `permissions.allow` 中添加 `Bash(python .claude/skills/manage-project/scripts/add_characters_clues.py *)`

### Decision 7：drama 模式预处理分两步——Gemini 生成 Markdown + script_generator 生成 JSON

**选择**：`normalize-drama-script` subagent 调用新脚本使用 `gemini-3.1-pro-preview` 生成 Markdown 格式的规范化剧本（step1），然后 `create-episode-script` subagent 使用已有的 `script_generator` 将 Markdown 转为 JSON（step2）

**两步流程**：

```
Step 1（normalize-drama-script subagent）
  ├─ 调用新脚本 normalize_drama_script.py
  ├─ 脚本使用 gemini-3.1-pro-preview 模型
  ├─ 输入：source/ 小说原文
  ├─ 输出：drafts/episode_{N}/step1_normalized_script.md
  │         drafts/episode_{N}/step2_shot_budget.md
  └─ 后续修改：subagent（Claude）直接编辑 Markdown 文件

Step 2（create-episode-script subagent — 已有实现）
  ├─ 调用已有的 generate_script.py
  ├─ script_generator 读取 step1_normalized_script.md
  ├─ 使用 gemini-3-flash-preview 生成 JSON
  └─ 输出：scripts/episode_{N}.json
```

**现状**：
- `ScriptGenerator` 已支持 drama 模式——`build_drama_prompt()` 会读取 `step1_normalized_script.md` 作为输入
- 规范化剧本（step1）当前由 agent（Claude）手动撰写——效率低且占用大量 context
- 新增的只是 step1 的 Gemini 自动化脚本，step2 完全复用已有实现

**新脚本设计**：
- 位置：`agent_runtime_profile/.claude/skills/generate-script/scripts/normalize_drama_script.py`（放在 generate-script skill 目录下，因为它是剧本生成流程的一部分）
- 模型：`gemini-3.1-pro-preview`（Pro 模型更擅长长文结构化改写）
- 输出格式：Markdown（与现有 `step1_normalized_script.md` 格式一致，确保 script_generator 可无缝消费）
- **settings.json 放行**：需添加 `Bash(python .claude/skills/generate-script/scripts/normalize_drama_script.py *)`

**替代方案**：
- 全部由 Claude 生成规范化剧本 → 速度慢、context 开销大、对长篇小说不友好
- 用 flash 模型 → Pro 模型在长文理解和结构化改写上质量更高

## Risks / Trade-offs

### [风险] Subagent 上下文传递开销
dispatch subagent 时需要在 prompt 中传入小说原文等大量内容，这会消耗 subagent 的 context 空间。

→ **缓解**：subagent 读取文件而非通过 prompt 接收内容。只在 prompt 中传递文件路径和关键参数，subagent 自行读取所需文件。

### [风险] 编排 skill 的复杂度
manga-workflow 编排 skill 需要检测多种状态、处理多种入口点，纯 prompt 可能变得过长。

→ **缓解**：使用清晰的决策树结构和 Markdown 格式化；如果 prompt 过长，可拆分为多个辅助 skill。

### [风险] 断点续传能力变化
原来的大 subagent 有连续的 context，可以自然恢复。拆分后，中断恢复依赖编排 skill 重新检测状态。

→ **缓解**：每个阶段完成后都会持久化到文件系统（project.json、drafts/、scripts/），编排 skill 通过文件系统状态恢复。这实际上比原来的 subagent 内部恢复更可靠——不依赖 subagent context 存活。

### [Trade-off] Subagent 数量增加 → API 调用增加
原来 1 个 subagent 完成全部工作，现在 3-4 个 subagent 依次执行，每次 dispatch 都有开销。

→ **接受**：额外的 API 调用开销可控，换来的是更好的架构隔离、灵活性和容错性。

### [Trade-off] 主 Agent 编排负担
主 agent 需要理解工作流阶段、dispatch 正确的 subagent、传递上下文。

→ **缓解**：manga-workflow 编排 skill 提供清晰的指令，主 agent 只需"按照 skill 指示行事"。
