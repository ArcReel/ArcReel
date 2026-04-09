# 宫格图生视频 — 设计文档

> 日期：2026-04-09
> 分支：feature/grid-image-to-video

## 1. 概述

在 ArcReel 中新增"宫格生成"模式，作为与现有"逐张生成"平级的分镜生成方式。宫格模式将同一 segment 组内的多个场景合并为一张宫格大图生成，切割后形成帧链式首尾帧结构，再送入视频生成管线。

**核心价值：**
- 一次生成保证画风和角色外观一致（无需依赖参考图传递）
- 帧链式首尾帧天然衔接场景过渡，视频起止画面可控

**不包含在本次变更中：**
- `multi_ref` 多角度参考模式（未来独立模式）

---

## 2. 帧链模型

宫格采用 `first_last` 帧链结构，**前一个场景的尾帧即后一个场景的首帧**，最后一个场景不生成尾帧。

N 个场景 → N 个格子：

```
Cell 0: S1 首帧
Cell 1: S1 尾帧 / S2 首帧
Cell 2: S2 尾帧 / S3 首帧
...
Cell N-1: S(N-1) 尾帧 / SN 首帧
```

视频生成时：
- Scene 1 ~ N-1：使用 first_last 模式（首帧 + 尾帧）
- Scene N（末尾）：使用 single 模式（仅首帧）

---

## 3. 数据模型

### 3.1 GridGeneration

```
GridGeneration:
  id: str                       # 唯一标识
  episode: int                  # 所属集数
  script_file: str              # 剧集脚本文件名
  scene_ids: list[str]          # 覆盖的场景 ID 列表（有序）
  grid_image_path: str          # 宫格大图路径
  rows: int                     # 网格行数
  cols: int                     # 网格列数
  cell_count: int               # 总格数（= len(scene_ids)，可能含空占位）
  frame_chain: list[FrameCell]  # 帧链详情
  status: str                   # pending / generating / splitting / completed / failed
  prompt: str                   # 实际发送的完整 prompt
  provider: str                 # 图片供应商
  model: str                    # 模型
  created_at: datetime
  error_message: str | None
```

### 3.2 FrameCell

```
FrameCell:
  index: int                # 格子索引（行优先，从 0 开始）
  row: int
  col: int
  frame_type: str           # "first" | "transition" | "placeholder"
  prev_scene_id: str | None # 作为哪个场景的尾帧
  next_scene_id: str | None # 作为哪个场景的首帧
  image_path: str | None    # 切割后的单帧图片路径
```

帧链示例（4 场景，grid_4）：

| Cell | frame_type | prev_scene | next_scene | 含义 |
|------|-----------|------------|------------|------|
| 0 | first | - | S1 | S1 首帧 |
| 1 | transition | S1 | S2 | S1 尾帧 / S2 首帧 |
| 2 | transition | S2 | S3 | S2 尾帧 / S3 首帧 |
| 3 | transition | S3 | S4 | S3 尾帧 / S4 首帧 |

### 3.3 generated_assets 扩展

```json
{
  "storyboard_image": "storyboards/scene_E1S01_first.png",
  "storyboard_last_image": "storyboards/scene_E1S01_last.png",
  "grid_id": "grid_abc123",
  "grid_cell_index": 1,
  "video_clip": "videos/scene_E1S01.mp4"
}
```

- `storyboard_image`：首帧图片路径
- `storyboard_last_image`：尾帧图片路径（末尾场景为 null）
- `grid_id` + `grid_cell_index`：追溯宫格来源

### 3.4 项目配置

`project.json` 新增：

```json
{
  "generation_mode": "single" | "grid"
}
```

项目创建和设置页均可切换。

---

## 4. 宫格布局与比例

### 4.1 自动选格策略

按 `segment_break` 分组，每组所有场景保证在同一张宫格中。根据组内场景数 N 自动选择最小适配的 grid_size：

| N | grid_size | 布局 | 空占位 |
|---|-----------|------|--------|
| < 4 | - | 退化为 single 模式 | - |
| 4 | grid_4 | 2×2 | 0 |
| 5 | grid_6 | 见 4.2 | 1 |
| 6 | grid_6 | 见 4.2 | 0 |
| 7 | grid_9 | 3×3 | 2 |
| 8 | grid_9 | 3×3 | 1 |
| 9 | grid_9 | 3×3 | 0 |
| > 9 | grid_9 + single | 前 9 走 grid_9，剩余 single | - |

### 4.2 布局与比例适配

关键约束：切割后每格比例必须与视频比例一致。

| grid_size | 横屏 16:9 | 竖屏 9:16 | 宫格图比例 | 切割后裁切量 |
|-----------|----------|----------|-----------|------------|
| grid_4 | 2×2 | 2×2 | 16:9 / 9:16 | 0% |
| grid_6 | 3行×2列 | 2行×3列 | 4:3 / 3:4 | ~11% 居中裁切 |
| grid_9 | 3×3 | 3×3 | 16:9 / 9:16 | 0% |

grid_6 详细计算：
- 横屏 3行×2列：每格原始比例 = 3/2 × 4/3 = 2:1，裁切到 16:9 需裁宽度 11.1%
- 竖屏 2行×3列：每格原始比例 = 2/3 × 3/4 = 1:2，裁切到 9:16 需裁高度 11.1%

---

## 5. 后端 API

### 5.1 新增端点

```
POST /api/v1/projects/{name}/generate/grid/{episode}
  Request: { script_file: str, scene_ids?: list[str] }
  Response: { success: bool, grid_ids: list[str], task_ids: list[str] }
  说明: 按 segment_break 分组，每组入队一个 grid 任务。
       可选 scene_ids 限制只生成指定场景所在的组。

GET /api/v1/projects/{name}/grids
  Response: list[GridGeneration]
  说明: 列出项目所有宫格记录

GET /api/v1/projects/{name}/grids/{grid_id}
  Response: GridGeneration（含 frame_chain 详情）
  说明: 查看单个宫格详情

POST /api/v1/projects/{name}/grids/{grid_id}/regenerate
  Response: { success: bool, task_id: str }
  说明: 重新生成指定宫格
```

### 5.2 新增路由文件

`server/routers/grids.py`：宫格相关 CRUD + 生成端点。

---

## 6. 生成管线

### 6.1 任务类型

新增 `task_type: "grid"`，`media_type: "image"`。

### 6.2 Worker 内完整流程

```
GenerationWorker 接到 grid 任务
  │
  ├─ 1. 计算布局
  │     根据场景数 N → grid_size → rows × cols
  │     根据视频比例选择布局方向（横/竖）
  │     计算宫格图精确像素尺寸
  │
  ├─ 2. 收集参考图（最多 6 张）
  │     按场景出现的角色/线索优先级排序
  │     角色 character_sheet > 线索 clue_sheet
  │
  ├─ 3. 组装 prompt（模板拼接）
  │     布局指令 + 帧链节奏 + 每格内容 + 风格约束 + 负面约束
  │     每格内容从 scene 的结构化 image_prompt/video_prompt 提取
  │
  ├─ 4. 调用 ImageBackend.generate()
  │     → 保存宫格大图到 grids/grid_{id}.png
  │     → status: generating → splitting
  │
  ├─ 5. 均匀切割 + 居中裁切
  │     cellW = gridW / cols, cellH = gridH / rows
  │     各边裁 2% 消除格线残留
  │     居中裁切到视频比例
  │     空占位格跳过（纯色检测）
  │     → 保存到 storyboards/scene_{id}_first.png / _last.png
  │
  └─ 6. 帧分配 + 元数据写入
        更新 frame_chain 中每个 cell 的 image_path
        更新各场景 generated_assets（storyboard_image / storyboard_last_image / grid_id）
        → status: splitting → completed
```

---

## 7. Prompt 模板

```
你是一位专业的分镜画师。请严格按照 {rows}×{cols} 宫格布局生成一张包含 {cell_count} 个等大画格的联合图。

【布局要求】
- {rows} 行 {cols} 列，阅读顺序：从左到右，从上到下
- 每格必须等大，格间无边框、无留白、无文字、无水印
- 所有格子保持一致的角色外观、光线和色彩风格

【帧链节奏】
本宫格采用首尾帧链式结构：
- 格0 是第一个场景的开场画面
- 格1~格{N-2} 是相邻场景的过渡帧（前一场景的结束 = 后一场景的开始）
- 格{N-1} 是最后一个场景的开场画面
- 相邻格之间应体现画面的自然过渡和动作延续

【参考图说明】
{reference_image_mapping}

【各格内容】
格0（row1 col1）— {S1}开场：
  {S1.image_prompt.scene}，{S1.image_prompt.composition}

格1（row1 col2）— {S1}→{S2}过渡：
  {S1.video_prompt.action 收束}，过渡到 {S2.image_prompt.scene}

格2（row2 col1）— {S2}→{S3}过渡：
  {S2.video_prompt.action 收束}，过渡到 {S3.image_prompt.scene}

...

格{N-1}（rowR colC）— {SN}开场：
  {SN.image_prompt.scene}，{SN.image_prompt.composition}

{placeholder_cells_if_any}

【风格要求】
{style_description}

【负面约束】
禁止出现：文字、水印、数字编号、边框、分隔线、拼贴感
```

空占位格（如有）追加：
```
格{M}（rowR colC）— 空占位：纯灰色背景，无任何内容
```

---

## 8. VideoBackend 扩展

### 8.1 能力声明

```python
@dataclass
class VideoCapabilities:
    first_frame: bool = True
    last_frame: bool = False
    reference_images: bool = False
    max_reference_images: int = 0
```

### 8.2 请求扩展

```python
@dataclass
class VideoGenerationRequest:
    prompt: str
    start_image: Path
    end_image: Path | None = None
    reference_images: list[Path] | None = None
    output_path: Path
    duration_seconds: int
    seed: int | None = None
```

### 8.3 三级回退

视频生成时根据后端能力自动选择最优模式：

```
1. backend.capabilities().last_frame == True
   → first_last: start_image=首帧, end_image=尾帧

2. backend.capabilities().reference_images == True
   → reference: start_image=首帧, reference_images=[尾帧]

3. 都不支持
   → single: start_image=首帧, 忽略尾帧, log warning
```

### 8.4 各供应商能力

| 后端 | last_frame | reference_images | 实际模式 |
|------|-----------|-----------------|---------|
| Gemini Veo 3.1 preview | ✅ | ✅ (≤3) | first_last |
| Gemini Veo 3.1 Vertex | ✅ | ❌ | first_last |
| Ark Seedance 1.5 | ❌ | ❌ | single 回退 |
| Ark Seedance 2.0 | ✅ | ✅ (≤9) | first_last |
| Grok | ❌ | ✅ | reference 回退 |
| OpenAI Sora | ❌ | ✅ | reference 回退 |

---

## 9. 前端 UI

### 9.1 项目设置

在项目创建和设置页新增"分镜生成模式"选项：
- **逐张生成**（single）：每个场景独立生成分镜图
- **宫格生成**（grid）：按段落分组一次生成，首尾帧链式衔接

### 9.2 Timeline 视图

Grid 模式下 timeline 按 `segment_break` 分组展示：
- 每组头部显示：segment 标签、场景数、自动选定的 grid_size、"生成宫格"按钮
- 集级别顶部："一键生成全部宫格"按钮
- segment_break 之间有分隔标记

### 9.3 宫格预览面板

点击 segment 组可展开预览面板：
- 左侧：宫格原图（可放大查看）
- 右侧：帧链分配列表（Cell → Scene 首/尾帧映射）
- 操作：重新生成按钮、状态标签

### 9.4 SegmentCard 改动

Grid 模式下每个场景卡片的 STORYBOARD 列：
- **横屏**：首帧和尾帧**上下排布**，箭头 ↓ 连接
- 每帧标注 Cell 编号和共享关系（如 "= S1 尾帧"）
- 末尾场景仅显示首帧，标注"末尾场景 · 无尾帧"
- 视频生成按钮标记模式：`first_last` 或 `single`

---

## 10. Agent Skill 集成

### 10.1 新增 skill: generate-grid

位于 `agent_runtime_profile/.claude/skills/generate-grid/`。

脚本逻辑：
1. 确认 `generation_mode == "grid"`
2. 读取剧本，按 segment_break 分组
3. 对每组计算 grid_size（N < 4 标记为 single 回退）
4. 调用 `POST /api/v1/projects/{name}/generate/grid/{episode}`
5. `batch_enqueue_and_wait_sync()` 等待完成
6. 输出结果摘要

### 10.2 manga-workflow 编排器

根据 `generation_mode` 分支：
- `single` → 调用 `generate-storyboard`（现有）
- `grid` → 调用 `generate-grid`（新增）

视频阶段无需改动，`generate-video` 检测到首尾帧自动走 first_last。

### 10.3 兼容性

Grid 模式下 `generate-storyboard` 仍可用于单场景补生（退化为 single 模式）。

---

## 11. 切割算法

采用固定均匀网格切割（方案 D），纯坐标计算：

```python
def split_grid(grid_image, rows, cols, video_aspect_ratio):
    cell_w = grid_image.width // cols
    cell_h = grid_image.height // rows

    cells = []
    for r in range(rows):
        for c in range(cols):
            # 1. 均匀切割
            cell = grid_image.crop(
                c * cell_w, r * cell_h,
                (c+1) * cell_w, (r+1) * cell_h
            )
            # 2. 边缘裁剪（各边 2%，消除格线残留）
            cell = crop_edge_margin(cell, margin=0.02)
            # 3. 居中裁切到视频比例
            cell = center_crop_to_ratio(cell, video_aspect_ratio)
            cells.append(cell)

    return cells
```

空占位格检测：格子中心区域 RGB < 30 且均匀度 > 90% → 跳过。

---

## 12. 错误处理与边界情况

### 12.1 失败处理

| 失败点 | 策略 |
|-------|------|
| 宫格图 API 生成失败 | status=failed，记录 error_message，可重新生成 |
| 切割失败 | status=failed，保留宫格原图，可重试 |
| 视频生成失败 | 与现有逻辑一致，单场景可独立重试 |

### 12.2 边界情况

| 场景 | 处理 |
|------|------|
| segment 组 N < 4 | 退化为 single 模式 |
| segment 组 N > 9 | grid_9 装前 9 个，剩余 single |
| 项目 single ↔ grid 切换 | 已有数据保留，新生成按新模式 |
| 宫格模式下单场景重新生图 | 走 single，覆盖首帧，帧链共享关系断开 |
| video backend 不支持 first_last | 三级回退 |
| 空占位格 | prompt 标记 placeholder，切割跳过 |
