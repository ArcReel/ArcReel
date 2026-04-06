# 单集费用估算功能设计

## 概述

为 ArcReel 新增单集费用估算功能，在 Web UI 中展示每集的**预估费用**（基于当前模型配置）和**实际费用**（基于历史 API 调用累计），支持从项目总览到单个分镜卡片的三级费用展示。

## 需求

- **预估**：剧本变化时实时计算，基于 segment 数量 × 当前 image/video 模型定价
- **实际**：累计所有成功的 API 调用费用（含重新生成），按 segment 精确归属
- **费用项**：预估仅含分镜图 + 视频；实际在项目级额外包含角色/线索生成费用
- **货币**：所有费用均为 `Record<currency, amount>` 结构，支持 USD/CNY 混合，原始货币显示

## 数据模型变更

### ApiCall 表新增字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `segment_id` | `String(20), nullable, indexed` | 片段标识（如 `E1S001`），可解析出 episode 编号 |

- 新增 Alembic migration
- 入队时由 `generation_tasks.py` 传入 segment_id，贯穿 `UsageTracker.start_call()` → `UsageRepository`
- 角色/线索生成的 ApiCall 不设 segment_id（`null`），通过 `call_type` 区分
- 不做历史数据回溯

## 后端 API 设计

### `GET /api/v1/projects/{project_name}/cost-estimate`

一次返回整个项目所有集的预估 + 实际费用。

**请求参数**：无（从项目当前剧本和模型配置自动计算）

**响应结构**：

```json
{
  "project_name": "my-project",
  "models": {
    "image": { "provider": "gemini-aistudio", "model": "gemini-3.1-flash-image-preview" },
    "video": { "provider": "gemini-aistudio", "model": "veo-3.1-lite-generate-preview" }
  },
  "episodes": [
    {
      "episode": 1,
      "title": "开篇",
      "segments": [
        {
          "segment_id": "E1S001",
          "duration_seconds": 6,
          "estimate": {
            "image": { "USD": 0.04 },
            "video": { "USD": 0.35 }
          },
          "actual": {
            "image": { "USD": 0.08 },
            "video": { "USD": 0.35 }
          }
        }
      ],
      "totals": {
        "estimate": {
          "image": { "USD": 0.40 },
          "video": { "USD": 3.50 }
        },
        "actual": {
          "image": { "USD": 0.48 },
          "video": { "USD": 3.50 }
        }
      }
    }
  ],
  "project_totals": {
    "estimate": {
      "image": { "USD": 1.20 },
      "video": { "USD": 10.50 }
    },
    "actual": {
      "image": { "USD": 1.08, "CNY": 1.20 },
      "video": { "USD": 10.50 },
      "character_and_clue": { "USD": 0.45 }
    }
  }
}
```

### 费用类型 `CostBreakdown`

所有费用值统一为 `Record<currency, amount>` 映射：

```python
# 单一货币
{"USD": 0.04}
# 混合货币（重新生成用了不同 provider）
{"USD": 0.04, "CNY": 1.20}
```

### 计算逻辑

**预估**：
1. 读取每集剧本 → 遍历 segments
2. 通过 ConfigResolver 解析当前 image/video 模型 + 参数（resolution、audio、duration）
3. 调用 CostCalculator 计算单个 segment 的 image + video 费用

**实际**：
1. 从 UsageRepository 按 `project_name` + `segment_id` 查询所有成功的 ApiCall 记录
2. 按 segment_id + call_type + currency 分组累加费用（含重新生成的累计）
3. 项目级：额外查询 `segment_id IS NULL` 的 image 记录（角色+线索图片共用 `call_type=image`，合并为 `character_and_clue`）

### 新增服务层

`server/services/cost_estimation.py`：编排 ConfigResolver + CostCalculator + UsageRepository + ProjectManager

### 新增路由

`server/routers/cost_estimation.py`：挂载到 `/api/v1/projects/{project_name}/cost-estimate`

## 前端设计

### 数据层

**API 调用**：在 `frontend/src/api.ts` 新增 `getCostEstimate(projectName)` 方法。

**Store**：在 `projects-store.ts` 中新增 `costEstimate` 状态字段，随项目加载/剧本变更时刷新（debounce 500ms）。

### 三级 UI 展示

#### 1. 项目概览（OverviewCanvas）

在剧集列表区域上方新增项目总费用汇总栏：

- **预估**（黄色总价）：分镜 + 视频，按类型拆分
- **实际**（绿色总价）：分镜 + 视频 + 角色 + 线索，按类型拆分
- 混合货币同行显示：`分镜 $0.20 + ¥4.00`

剧集列表每行增加预估/实际费用列：
- 按类型拆分（分镜 / 视频）+ 总计
- 未生成的集显示灰色 "— 尚未生成 —"
- 总计标签颜色与分镜/视频标签一致（灰色），仅金额数字高亮

#### 2. 分镜板顶部（TimelineCanvas）

在 episode header 下方新增单行费用栏：
- 预估 | 实际 用竖线分隔
- 格式：`预估 分镜 $0.40 视频 $3.50 总计 $3.90 | 实际 分镜 $0.48 视频 $3.50 总计 $3.98`

#### 3. 分镜卡片（SegmentCard）

在 header 行的 segment_id 和时长后面内联显示：
- 用 `|` 与 segment_id/时长分隔
- 格式：`预估 分镜 $0.04 视频 $0.35 | 实际 分镜 $0.04 视频 $0.35`
- 未生成的项用 `—` 占位
- 不显示总计（单 segment 只有两项，无需汇总）

### 颜色语义

| 元素 | 颜色 |
|------|------|
| 标签（分镜/视频/总计/预估/实际） | `#71717a`（灰色） |
| 分项金额 | `#d4d4d8`（浅灰） |
| 预估总计金额 | `#fbbf24`（黄色） |
| 实际总计金额 | `#34d399`（绿色） |
| 未生成占位 | `#52525b`（深灰） |

### 实时更新

- 剧本变更（segment 增删、duration 修改）时 debounce 500ms 请求后端重新计算预估
- 生成任务完成后通过项目事件 SSE 触发费用数据刷新

## 影响范围

### 后端

| 文件 | 变更 |
|------|------|
| `lib/db/models/api_call.py` | 新增 `segment_id` 字段 |
| `lib/usage_tracker.py` | `start_call()` 新增 `segment_id` 参数 |
| `lib/db/repositories/usage_repo.py` | `start_call()` 传入 `segment_id`；新增按 segment 汇总查询方法 |
| `server/services/generation_tasks.py` | 入队时传入 `segment_id` |
| `server/services/cost_estimation.py` | **新增**：费用估算服务 |
| `server/routers/cost_estimation.py` | **新增**：API 路由 |
| `server/app.py` | 注册新路由 |
| Alembic migration | **新增**：添加 `segment_id` 字段 |

### 前端

| 文件 | 变更 |
|------|------|
| `frontend/src/api.ts` | 新增 `getCostEstimate()` |
| `frontend/src/types/cost.ts` | **新增**：费用估算类型定义 |
| `frontend/src/stores/projects-store.ts` | 新增 `costEstimate` 状态 |
| `frontend/src/components/canvas/OverviewCanvas.tsx` | 新增费用汇总栏 + 剧集列表费用列 |
| `frontend/src/components/canvas/timeline/TimelineCanvas.tsx` | 新增 episode 费用栏 |
| `frontend/src/components/canvas/timeline/SegmentCard.tsx` | header 内联费用显示 |

## 测试计划

- `test_cost_estimation_service.py`：预估计算逻辑（单货币、混合货币、无剧本、空 segment）
- `test_cost_estimation_router.py`：API 端点（正常响应、项目不存在、无剧本）
- `test_usage_repo.py`：新增按 segment_id 汇总查询的测试
- 现有 `test_usage_tracker.py`：更新 start_call 签名适配
