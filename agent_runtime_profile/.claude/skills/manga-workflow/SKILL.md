---
name: manga-workflow
description: 完整的端到端工作流程，将小说转换为视频。使用场景：(1) 用户运行 /manga-workflow 命令，(2) 用户想开始新的视频项目，(3) 用户想继续现有项目。编排聚焦 subagent 完成各阶段任务，在每个阶段设置审核检查点。
---

# 视频工作流编排

你（主 agent）是编排中枢。你**不直接**处理小说原文或生成剧本，而是：
1. 检测项目状态 → 2. 决定下一阶段 → 3. dispatch 合适的 subagent → 4. 展示结果 → 5. 获取用户确认 → 6. 循环

**核心约束**：
- 小说原文**永远不加载到主 agent context**，由 subagent 自行读取
- 每次 dispatch 只传**文件路径和关键参数**，不传大块内容
- 每个 subagent 完成一个聚焦任务就返回，主 agent 负责阶段间衔接

---

## 内容模式

| 模式 | content_mode | 画面比例 | 默认时长 |
|------|-------------|---------|---------|
| **说书+画面（默认）** | `narration` | 9:16 竖屏 | 4 秒 |
| 剧集动画 | `drama` | 16:9 横屏 | 8 秒 |

---

## 阶段 0：项目设置

### 新项目

1. 询问项目名称
2. 创建 `projects/{名称}/` 及子目录（source/、scripts/、characters/、clues/、storyboards/、videos/、drafts/、output/）
3. 创建 `project.json` 初始文件
4. **询问内容模式**：`narration`（默认）或 `drama`
5. 请用户将小说文本放入 `source/`
6. **上传后自动生成项目概述**（synopsis、genre、theme、world_setting）

### 现有项目

1. 列出 `projects/` 中的项目
2. 显示项目状态摘要
3. 从上次未完成的阶段继续

---

## 状态检测

进入工作流后，首先检测项目当前状态。使用 Read 工具读取 `project.json`，使用 Glob 工具检查文件系统。

**检测清单**（按顺序检查，遇到第一个缺失项即确定当前阶段）：

```
1. characters 和 clues 是否为空？
   → 空：进入 [阶段 1: 全局角色/线索设计]

2. 目标集的 source/episode_{N}.txt 是否存在？
   → 不存在：进入 [阶段 2: 分集规划]

3. 目标集的 drafts/ 中间文件是否存在？
   → narration: 检查 drafts/episode_{N}/step1_segments.md
   → drama: 检查 drafts/episode_{N}/step1_normalized_script.md
   → 不存在：进入 [阶段 3: 单集预处理]

4. 目标集的 scripts/episode_{N}.json 是否存在？
   → 不存在：进入 [阶段 4: JSON 剧本生成]

5. characters/ 中是否有缺少 character_sheet 的角色？
   → 有：进入 [阶段 5: 人物设计]

6. clues/ 中是否有 importance=major 但缺少 clue_sheet 的线索？
   → 有：进入 [阶段 6: 线索设计]

7. storyboards/ 中是否有缺少分镜图的场景？
   → 有：进入 [阶段 7: 分镜图生成]

8. videos/ 中是否有缺少视频的场景？
   → 有：进入 [阶段 8: 视频生成]

9. 全部完成 → [阶段 9: 最终合成]
```

**确定目标集数**：如果用户未指定集数，检查 `project.json` 的 episodes 字段，找到最新的未完成集，或询问用户要处理哪一集。

---

## 阶段间确认协议

**每个 subagent 返回后**，主 agent 执行：

1. **展示摘要**：将 subagent 返回的摘要展示给用户
2. **获取确认**：使用 AskUserQuestion 提供以下选项：
   - **继续下一阶段**（推荐）
   - **重做此阶段**（附加修改要求后重新 dispatch 同一 subagent）
   - **跳过此阶段**
3. **根据用户选择行动**：
   - 继续 → 进入下一阶段
   - 重做 → 在 dispatch prompt 中附加用户反馈，重新 dispatch
   - 跳过 → 跳过，进入下一阶段

---

## 阶段 1：全局角色/线索设计

**触发条件**：project.json 中 characters 为空或 clues 为空

**dispatch `analyze-characters-clues` subagent**：

```
使用 Agent 工具 dispatch `analyze-characters-clues` subagent。

prompt 内容：
---
项目名称：{project_name}
项目路径：projects/{project_name}/
分析范围：{整部小说 / 用户指定的范围}
已有角色：{已有角色名列表，或"无"}
已有线索：{已有线索名列表，或"无"}

请分析小说原文，提取角色和线索信息，写入 project.json，返回摘要。
---
```

**返回后**：展示角色/线索摘要 → 用户确认 → 进入阶段 2

---

## 阶段 2：分集规划

**触发条件**：目标集的 `source/episode_{N}.txt` 不存在

每次只切分当前需要制作的那一集，不要求一次性规划全部集数。

**主 agent 直接执行**（不 dispatch subagent）：

```
1. 确定源文件：
   source/_remaining.txt 存在？
     ├─ 是 → 使用 _remaining.txt（从上次剩余内容继续）
     └─ 否 → 使用 source/ 下的原始小说文件

2. 询问用户目标字数（如 1000 字/集，或使用上次设定）

3. 调用 peek_split_point.py 展示切分点附近上下文：
   cd projects/{project_name} && python ../../.claude/skills/manage-project/scripts/peek_split_point.py \
     --source {源文件} --target {目标字数}

4. 分析 peek 输出的 context_before/context_after 和 nearby_breakpoints，
   建议自然断点（优先选择句号、段落边界等）

5. 用户确认切分点后，先 dry run 验证：
   cd projects/{project_name} && python ../../.claude/skills/manage-project/scripts/split_episode.py \
     --source {源文件} --episode {N} --target {目标字数} --anchor "{确认的锚点文本}" --dry-run

6. 确认无误后实际执行：
   cd projects/{project_name} && python ../../.claude/skills/manage-project/scripts/split_episode.py \
     --source {源文件} --episode {N} --target {目标字数} --anchor "{确认的锚点文本}"
```

**完成后**：展示切分结果（episode_{N}.txt 字数、_remaining.txt 字数） → 用户确认 → 进入阶段 3

---

## 阶段 3：单集预处理

**触发条件**：目标集的 drafts/ 中间文件不存在

根据 content_mode 选择不同 subagent：

### narration 模式 → dispatch `split-narration-segments`

```
prompt 内容：
---
项目名称：{project_name}
项目路径：projects/{project_name}/
集数：{N}
本集小说文件：source/episode_{N}.txt
角色列表：{project.json 中的角色名列表}
线索列表：{project.json 中的线索名列表}

请拆分本集小说为说书片段，保存到 drafts/episode_{N}/step1_segments.md，返回摘要。
---
```

### drama 模式 → dispatch `normalize-drama-script`

```
prompt 内容：
---
项目名称：{project_name}
项目路径：projects/{project_name}/
集数：{N}
本集小说文件：source/episode_{N}.txt
操作类型：首次生成
角色列表：{project.json 中的角色名列表}
线索列表：{project.json 中的线索名列表}

请生成本集规范化剧本，保存到 drafts/episode_{N}/step1_normalized_script.md，返回摘要。
---
```

**返回后**：展示片段/场景统计摘要 → 用户确认 → 进入阶段 4

---

## 阶段 4：JSON 剧本生成

**触发条件**：目标集的 scripts/episode_{N}.json 不存在

**dispatch `create-episode-script` subagent**：

```
prompt 内容：
---
项目名称：{project_name}
项目路径：projects/{project_name}/
集数：{N}

请调用 generate_script.py 生成 JSON 剧本，验证输出，返回摘要。
---
```

**返回后**：展示剧本生成摘要 → 用户确认 → 进入阶段 5

---

## 阶段 5：人物设计

**触发条件**：project.json 中有角色缺少 character_sheet

**dispatch 资产生成 subagent**：

```
使用 Agent 工具 dispatch general-purpose subagent。

prompt 内容：
---
任务：为项目 {project_name} 生成缺失的人物设计图。

步骤：
1. 使用 Read 工具读取 projects/{project_name}/project.json
2. 找到所有缺少 character_sheet 的角色
3. 对每个缺失的角色，调用：
   cd projects/{project_name} && python ../../.claude/skills/generate-characters/scripts/generate_character.py --character "{角色名}"
4. 返回生成结果摘要（成功/失败/跳过）
---
```

**返回后**：展示人物设计摘要 → 用户确认 → 进入阶段 6

---

## 阶段 6：线索设计

**触发条件**：project.json 中有 importance=major 的线索缺少 clue_sheet

**dispatch 资产生成 subagent**：

```
使用 Agent 工具 dispatch general-purpose subagent。

prompt 内容：
---
任务：为项目 {project_name} 生成缺失的线索设计图。

步骤：
1. 使用 Read 工具读取 projects/{project_name}/project.json
2. 找到所有 importance="major" 且缺少 clue_sheet 的线索
3. 对每个缺失的线索，调用：
   cd projects/{project_name} && python ../../.claude/skills/generate-clues/scripts/generate_clue.py --clue "{线索名}"
4. 返回生成结果摘要
---
```

**返回后**：展示线索设计摘要 → 用户确认 → 进入阶段 7

---

## 阶段 7：分镜图生成

**触发条件**：scripts/episode_{N}.json 中有场景缺少分镜图

**dispatch 资产生成 subagent**：

```
使用 Agent 工具 dispatch general-purpose subagent。

prompt 内容：
---
任务：为项目 {project_name} 第 {N} 集生成分镜图。

步骤：
1. 在项目目录下调用：
   cd projects/{project_name} && python ../../.claude/skills/generate-storyboard/scripts/generate_storyboard.py --episode {N}
2. 返回生成结果摘要（成功数/失败数/跳过数）
---
```

**返回后**：展示分镜图生成摘要 → 用户确认 → 进入阶段 8

---

## 阶段 8：视频生成

**触发条件**：scripts/episode_{N}.json 中有场景缺少视频

**dispatch 资产生成 subagent**：

```
使用 Agent 工具 dispatch general-purpose subagent。

prompt 内容：
---
任务：为项目 {project_name} 第 {N} 集生成视频。

步骤：
1. 在项目目录下调用：
   cd projects/{project_name} && python ../../.claude/skills/generate-video/scripts/generate_video.py --episode {N}
2. 返回生成结果摘要（成功数/失败数/跳过数）
---
```

**返回后**：展示视频生成摘要 → 用户确认 → 进入阶段 9

---

## 阶段 9：最终合成

**触发条件**：所有视频已生成，准备合成

**dispatch 资产生成 subagent**：

```
使用 Agent 工具 dispatch general-purpose subagent。

prompt 内容：
---
任务：为项目 {project_name} 第 {N} 集合成最终视频。

步骤：
1. 在项目目录下调用：
   cd projects/{project_name} && python ../../.claude/skills/compose-video/scripts/compose_video.py --episode {N}
2. 返回合成结果摘要
---
```

**返回后**：展示合成结果 → 完成！

---

## 灵活入口

工作流**不强制从头开始**。根据状态检测结果，自动从正确的阶段开始：

- 用户说"分析小说角色" → 只执行阶段 1，完成后不自动继续
- 用户说"创建第2集剧本" → 从阶段 2（分集规划）开始（如果角色已有）
- 用户说"继续" → 状态检测找到第一个缺失项，从该阶段继续
- 用户指定具体阶段（如"生成分镜图"）→ 直接跳到该阶段

---

## 中断后恢复

所有进度持久化到文件系统：
- `project.json`：角色、线索、剧集元数据
- `drafts/`：中间文件
- `scripts/`：JSON 剧本
- `characters/`、`clues/`、`storyboards/`、`videos/`：生成的资产

再次运行 `/manga-workflow` 时，状态检测会自动定位到上次中断的阶段。

---

## 数据分层说明

- 角色/线索的完整定义**只存储在 project.json**，剧本中仅引用名称
- `scenes_count`、`status`、`progress` 等统计字段由 StatusCalculator **读时计算**，不存储
- 剧集元数据（episode/title/script_file）在剧本保存时**写时同步**
