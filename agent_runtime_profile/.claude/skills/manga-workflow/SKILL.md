---
name: manga-workflow
description: 将小说转换为短视频的端到端工作流编排器。当用户提到做视频、创建项目、继续项目、查看进度时必须使用此 skill。触发场景包括但不限于："帮我把小说做成视频"、"开个新项目"、"继续"、"下一步"、"看看项目进度"、"从头开始"、"拆集"、"自动跑完流程"等。即使用户只说了简短的"继续"或"下一步"，只要当前上下文涉及视频项目，就应该触发。不要用于单个资产生成（如只重画某张分镜图或只重新生成某个角色设计图——那些有专门的 skill）。
---

# 视频工作流编排

你（主 agent）是编排中枢。你**不直接**处理小说原文或生成剧本，而是：
1. 检测项目状态 → 2. 决定下一阶段 → 3. dispatch 合适的 subagent → 4. 展示结果 → 5. 获取用户确认 → 6. 循环

**核心约束**：
- 小说原文**永远不加载到主 agent context**，由 subagent 自行读取
- 每次 dispatch 只传**文件路径和关键参数**，不传大块内容
- 每个 subagent 完成一个聚焦任务就返回，主 agent 负责阶段间衔接

> 内容模式规格（画面比例、时长等）详见 `references/content-modes.md`。

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

进入工作流后，使用 Read 读取 `project.json`，使用 Glob 检查文件系统。按顺序检查，遇到第一个缺失项即确定当前阶段：

1. characters/clues 为空？ → **阶段 1**
2. 目标集 source/episode_{N}.txt 不存在？ → **阶段 2**
3. 目标集 drafts/ 中间文件不存在？ → **阶段 3**
   - narration: `drafts/episode_{N}/step1_segments.md`
   - drama: `drafts/episode_{N}/step1_normalized_script.md`
4. scripts/episode_{N}.json 不存在？ → **阶段 4**
5. 有角色缺少 character_sheet？ → **阶段 5**
6. 有 importance=major 线索缺少 clue_sheet？ → **阶段 6**
7. 有场景缺少分镜图？ → **阶段 7**
8. 有场景缺少视频？ → **阶段 8**
9. 全部完成 → **阶段 9**

**确定目标集数**：如果用户未指定，找到最新的未完成集，或询问用户。

---

## 阶段间确认协议

**每个 subagent 返回后**，主 agent 执行：

1. **展示摘要**：将 subagent 返回的摘要展示给用户
2. **获取确认**：使用 AskUserQuestion 提供选项：
   - **继续下一阶段**（推荐）
   - **重做此阶段**（附加修改要求后重新 dispatch）
   - **跳过此阶段**
3. **根据用户选择行动**

---

## 阶段 1：全局角色/线索设计

**触发**：project.json 中 characters 或 clues 为空

**dispatch `analyze-characters-clues` subagent**：

```
项目名称：{project_name}
项目路径：projects/{project_name}/
分析范围：{整部小说 / 用户指定的范围}
已有角色：{已有角色名列表，或"无"}
已有线索：{已有线索名列表，或"无"}

请分析小说原文，提取角色和线索信息，写入 project.json，返回摘要。
```

---

## 阶段 2：分集规划

**触发**：目标集的 `source/episode_{N}.txt` 不存在

每次只切分当前需要制作的那一集。**主 agent 直接执行**（不 dispatch subagent）：

1. 确定源文件：`source/_remaining.txt` 存在则使用，否则用原始小说文件
2. 询问用户目标字数（如 1000 字/集）
3. 调用 `peek_split_point.py` 展示切分点附近上下文：
   ```bash
   cd projects/{project_name} && python ../../.claude/skills/manage-project/scripts/peek_split_point.py \
     --source {源文件} --target {目标字数}
   ```
4. 分析 nearby_breakpoints，建议自然断点
5. 用户确认后，先 dry run 验证：
   ```bash
   cd projects/{project_name} && python ../../.claude/skills/manage-project/scripts/split_episode.py \
     --source {源文件} --episode {N} --target {目标字数} --anchor "{锚点文本}" --dry-run
   ```
6. 确认无误后实际执行（去掉 `--dry-run`）

---

## 阶段 3：单集预处理

**触发**：目标集的 drafts/ 中间文件不存在

根据 content_mode 选择 subagent：

- **narration** → dispatch `split-narration-segments`
- **drama** → dispatch `normalize-drama-script`

dispatch prompt 包含：项目名称、项目路径、集数、本集小说文件路径、角色/线索名称列表。

---

## 阶段 4：JSON 剧本生成

**触发**：scripts/episode_{N}.json 不存在

**dispatch `create-episode-script` subagent**：传入项目名称、项目路径、集数。

---

## 阶段 5-9：资产生成与合成

阶段 5-9 共享统一的 dispatch 模式：**dispatch general-purpose subagent**，传入项目名和对应的脚本命令。

| 阶段 | 触发条件 | 脚本命令 |
|------|---------|---------|
| 5. 人物设计 | 有角色缺少 character_sheet | 对每个缺失角色执行：`cd projects/{name} && python ../../.claude/skills/generate-characters/scripts/generate_character.py --character "{角色名}"` |
| 6. 线索设计 | 有 major 线索缺少 clue_sheet | 对每个缺失线索执行：`cd projects/{name} && python ../../.claude/skills/generate-clues/scripts/generate_clue.py --clue "{线索名}"` |
| 7. 分镜图 | 有场景缺少分镜图 | `cd projects/{name} && python ../../.claude/skills/generate-storyboard/scripts/generate_storyboard.py --episode {N}` |
| 8. 视频 | 有场景缺少视频 | `cd projects/{name} && python ../../.claude/skills/generate-video/scripts/generate_video.py --episode {N}` |
| 9. 合成 | 所有视频已生成 | `cd projects/{name} && python ../../.claude/skills/compose-video/scripts/compose_video.py --episode {N}` |

**dispatch prompt 模板**（以阶段 5 为例）：
```
任务：为项目 {project_name} 生成缺失的人物设计图。

步骤：
1. 使用 Read 工具读取 projects/{project_name}/project.json
2. 找到所有缺少 character_sheet 的角色
3. 对每个缺失的角色，调用上表中对应的脚本命令
4. 返回生成结果摘要（成功/失败/跳过）
```

阶段 6-9 同理，替换触发条件和脚本命令即可。

---

## 灵活入口

工作流**不强制从头开始**。根据状态检测结果，自动从正确的阶段开始：

- "分析小说角色" → 只执行阶段 1
- "创建第2集剧本" → 从阶段 2 开始（如果角色已有）
- "继续" → 状态检测找到第一个缺失项
- 指定具体阶段（如"生成分镜图"）→ 直接跳到该阶段

---

## 数据分层

- 角色/线索完整定义**只存 project.json**，剧本中仅引用名称
- 统计字段（scenes_count、status、progress）**读时计算**，不存储
- 剧集元数据在剧本保存时**写时同步**
