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

> 内容模式规格（画面比例、时长等）详见 `.claude/references/content-modes.md`。

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
5. 有角色缺少 character_sheet？ → **阶段 5**（与阶段 6 可并行）
6. 有 importance=major 线索缺少 clue_sheet？ → **阶段 6**（与阶段 5 可并行）
7. 有场景缺少分镜图？ → **阶段 7**
8. 有场景缺少视频？ → **阶段 8**
9. 全部完成 → 工作流结束，引导用户在 Web 端导出剪映草稿

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
   python .claude/skills/manage-project/scripts/peek_split_point.py --source {源文件} --target {目标字数}
   ```
4. 分析 nearby_breakpoints，建议自然断点
5. 用户确认后，先 dry run 验证：
   ```bash
   python .claude/skills/manage-project/scripts/split_episode.py --source {源文件} --episode {N} --target {目标字数} --anchor "{锚点文本}" --dry-run
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

## 阶段 5+6：角色设计 + 线索设计（可并行）

两个任务互不依赖，**同时 dispatch 两个 `generate-assets` subagent**（如果两者都需要）。

### subagent A — 角色设计

**触发**：有角色缺少 character_sheet

```
dispatch `generate-assets` subagent：
  任务类型：characters
  项目名称：{project_name}
  项目路径：projects/{project_name}/
  待生成项：{缺失角色名列表}
  脚本命令：
    python .claude/skills/generate-characters/scripts/generate_character.py --all
  验证方式：重新读取 project.json，检查对应角色的 character_sheet 字段
```

### subagent B — 线索设计

**触发**：有 importance=major 线索缺少 clue_sheet

```
dispatch `generate-assets` subagent：
  任务类型：clues
  项目名称：{project_name}
  项目路径：projects/{project_name}/
  待生成项：{缺失线索名列表}
  脚本命令：
    python .claude/skills/generate-clues/scripts/generate_clue.py --all
  验证方式：重新读取 project.json，检查对应线索的 clue_sheet 字段
```

如果只有其中一个需要执行，只 dispatch 对应的一个。
两个 subagent 全部返回后，合并摘要展示给用户，进入阶段间确认。

---

## 阶段 7：分镜图生成

**触发**：有场景缺少分镜图

检查 `project.json` 的 `generation_mode`：

- `"single"` 或未设置 → dispatch `generate-storyboard` subagent（现有逻辑）
- `"grid"` → dispatch `generate-grid` subagent（新增）
  - generate-grid 自动按 segment_break 分组
  - 每组 ≥ 4 场景 → 生成宫格图
  - 每组 < 4 场景 → 退化为逐张生成

### single 模式（默认）

**dispatch `generate-assets` subagent**：

```
dispatch `generate-assets` subagent：
  任务类型：storyboard
  项目名称：{project_name}
  项目路径：projects/{project_name}/
  脚本命令：
    python .claude/skills/generate-storyboard/scripts/generate_storyboard.py episode_{N}.json
  验证方式：重新读取 scripts/episode_{N}.json，检查各场景的 storyboard_image 字段
```

### grid 模式

**dispatch `generate-assets` subagent**：

```
dispatch `generate-assets` subagent：
  任务类型：storyboard
  项目名称：{project_name}
  项目路径：projects/{project_name}/
  脚本命令：
    python .claude/skills/generate-grid/scripts/generate_grid.py episode_{N}.json
  验证方式：重新读取 scripts/episode_{N}.json，检查各场景的 storyboard_image 字段
```

---

## 阶段 8：视频生成

**触发**：有场景缺少视频

**dispatch `generate-assets` subagent**：

```
dispatch `generate-assets` subagent：
  任务类型：video
  项目名称：{project_name}
  项目路径：projects/{project_name}/
  脚本命令：
    python .claude/skills/generate-video/scripts/generate_video.py episode_{N}.json --episode {N}
  验证方式：重新读取 scripts/episode_{N}.json，检查各场景的 video_clip 字段
```

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
