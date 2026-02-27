---
name: manga-workflow
description: 完整的端到端工作流程，将小说转换为视频。使用场景：(1) 用户运行 /manga-workflow 命令，(2) 用户想开始新的视频项目，(3) 用户想继续现有项目。按顺序编排所有其他 skill，并在每个阶段设置审核检查点。
---

# 视频工作流

完整的端到端工作流程，将小说转换为视频。

## 内容模式

系统支持两种内容模式，通过 `project.json` 中的 `content_mode` 字段切换：

| 模式 | content_mode | 画面比例 | 默认时长 | Agent |
|------|-------------|---------|---------|-------|
| **说书+画面（默认）** | `narration` | 9:16 竖屏 | 4 秒 | novel-to-narration-script |
| 剧集动画 | `drama` | 16:9 横屏 | 8 秒 | novel-to-storyboard-script |

## 工作流决策树

```
开始
  │
  ├─ 新项目？ ──────────────────┐
  │                             │
  │   1. 创建项目目录            │
  │   2. 将小说复制到 source/    │
  │   3. 创建 project.json       │
  │   4. 选择 content_mode       │
  │   └─────────────────────────┘
  │
  ├─ 检查项目状态 (project.json)
  │   │
  │   ├─ 没有剧本？
  │   │   ├─ narration 模式 → novel-to-narration-script agent（3 步）
  │   │   └─ drama 模式 → novel-to-storyboard-script agent（4 步）
  │   │   └─► 审核 ──┐
  │   │
  │   ├─ 没有确认线索？ ───► 确认 clues 列表 ──► 更新 project.json ──┐
  │   │
  │   ├─ 没有人物设计？ ────► /generate-characters ──► 审核 ──┐
  │   │
  │   ├─ 没有线索设计？ ────► /generate-clues ──► 审核 ──┐
  │   │
  │   ├─ 没有分镜图？ ──────► /generate-storyboard ──► 审核 ──┐
  │   │
  │   ├─ 没有视频？ ────────► /generate-video ──► 审核 ──┐
  │   │
  │   └─ 准备合成？ ───────► /compose-video ──► 完成！
```

> **项目概述**：上传源文件后，系统会自动分析小说内容并生成项目概述（故事梗概、题材类型、核心主题、世界观设定）。概述信息会保存到 `project.json` 的 `overview` 字段，供后续 Agent 参考。

## 阶段 1：项目设置

**新项目**：
1. 询问项目名称
2. 创建 `projects/{名称}/` 及子目录（含 `clues/`、`drafts/`）
3. 创建 `project.json` 初始文件
4. **询问内容模式**：`narration`（默认）或 `drama`
5. 请用户将小说文本放入 `source/`
6. **上传后自动生成项目概述**（synopsis、genre、theme、world_setting）

**现有项目**：
1. 列出 `projects/` 中的项目
2. 显示 `project.json` 中的项目状态
3. 从上次未完成的阶段继续

## 阶段 2：剧本创建

根据 `content_mode` 调用不同的 Agent（使用 Opus 模型）：

### 说书+画面模式（narration）

使用 Task 工具调用 `novel-to-narration-script` agent：
- **核心原则**：保留原文，不改编、不删减、不添加
- **三步流程**：
  1. **片段拆分**：按朗读节奏拆分为约 4 秒的片段，标记 `segment_break`
  2. **角色表/线索表**：生成详细描述，同步到 `project.json`
  3. **生成 JSON**：输出 `segments` 结构剧本
- 每一步都需要用户确认后才继续
- **视频 Prompt 仅包含角色对话**，不包含旁白（旁白由后期人工配音）

**输出**：
- `drafts/episode_{N}/step1_segments.md`
- `drafts/episode_{N}/step2_character_clue_tables.md`
- `scripts/episode_N.json`（使用 `segments` 数组）

### 剧集动画模式（drama）

使用 Task 工具调用 `novel-to-storyboard-script` agent：
- **核心原则**：将小说改编为剧本形式
- **四步流程**：
  1. **规范化剧本**：结构化整理
  2. **镜头预算**：场景复杂度分析，标记 `segment_break`
  3. **角色表/线索表**：生成详细描述，同步到 `project.json`
  4. **分镜表**：输出 `scenes` 结构剧本
- 每一步都需要用户确认后才继续
- **视频 Prompt 包含对话、旁白、音效**

**输出**：
- `drafts/episode_{N}/step1_normalized_script.md`
- `drafts/episode_{N}/step2_shot_budget.md`
- `drafts/episode_{N}/step3_character_clue_tables.md`
- `scripts/episode_N.json`（使用 `scenes` 数组）

**审核检查点**：Agent 完成后，展示剧本摘要和线索列表，等待确认。

**数据分层说明**：
- Agent 会将角色和线索的**完整定义**同步到 `project.json`
- 剧本只保留 `characters_in_segment/scene` 和 `clues_in_segment/scene` 名称列表
- 后续 skills 从 `project.json` 读取角色/线索信息

## 阶段 3：线索确认

在 agent 完成后：
1. 展示自动识别的潜在线索列表
2. 用户确认哪些需要固化
3. 设置 `importance` 级别（major/minor）
4. 更新 `project.json` 中的 `clues` 字段

**审核检查点**：确认线索列表完整。

## 阶段 4：人物设计

调用 `/generate-characters`：
- 根据描述生成人物设计图（**3:4 竖版**，三视图）
- 保存到 `characters/`
- 更新 `project.json`

**审核检查点**：展示每个人物，允许重新生成。

## 阶段 5：线索设计

调用 `/generate-clues`：
- 为 `importance='major'` 的线索生成设计图（**16:9 横屏**）
- 保存到 `clues/`
- 更新 `project.json`

**审核检查点**：展示每个线索设计图，允许重新生成。

## 阶段 6：分镜图生成

调用 `/generate-storyboard`：

| 模式 | 流程 | 画面比例 |
|------|------|---------|
| narration | 直接生成分镜图 | **9:16 竖屏** |
| drama | 直接生成分镜图 | 16:9 横屏 |

- **使用人物和线索参考图保持一致性**
- 保存到 `storyboards/`

**审核检查点**：展示每张分镜图，允许重新生成。

## 阶段 7：视频生成

调用 `/generate-video`：

| 模式 | 画面比例 | 默认时长 | Prompt 内容 |
|------|---------|---------|------------|
| narration | **9:16 竖屏** | 4 秒 | 仅对话，无旁白 |
| drama | 16:9 横屏 | 8 秒 | 对话 + 旁白 + 音效 |

- 每个片段/场景独立生成，使用分镜图作为起始帧
- 自动使用 ffmpeg 拼接成完整视频
- 保存到 `videos/` 和 `output/`
- 支持断点续传（`--resume`）

**审核检查点**：预览每个视频，允许重新生成。

## 阶段 8：最终合成

调用 `/compose-video`：
- 拼接所有视频片段
- 应用转场效果（使用 `segment_break` 标记）
- 添加背景音乐（可选）
- **narration 模式**：合并人工配音的旁白
- 输出到 `output/`

## 项目状态命令

随时检查项目状态：
```python
from lib.project_manager import ProjectManager
pm = ProjectManager()

# 加载项目元数据
project = pm.load_project("my_project")
print(project['status'])

# 同步并更新状态
pm.sync_project_status("my_project")
```

## 线索管理

### 在场景中使用线索

在剧本中添加 `clues_in_segment` 或 `clues_in_scene` 字段：
```json
{
  "segment_id": "E1S3",
  "characters_in_segment": ["姜月茴"],
  "clues_in_segment": ["玉佩", "老槐树"],
  "image_prompt": { ... },
  "video_prompt": { ... }
}
```

生成分镜时，线索参考图会自动加入 API 调用。

### 添加新线索

```python
pm.add_clue(
    "my_project",
    name="玉佩",
    clue_type="prop",  # 或 "location"
    description="翠绿色祖传玉佩...",
    importance="major"
)
```

## 中断后恢复

工作流可以中断后恢复：
- 所有进度保存到磁盘
- `project.json` 记录项目整体状态
- 剧本 JSON 记录每个片段/场景的生成资源
- 再次运行 `/manga-workflow` 即可继续
