# 剧本生成中间产物 UI 展示与事件通知

## Background

当前说书/剧集动画两种模式在生成 JSON 剧本前各有一个前置步骤：

- **narration 模式**：`split-narration-segments` subagent 生成 `step1_segments.md`（片段拆分表）
- **drama 模式**：`normalize-drama-script` subagent 生成 `step1_normalized_script.md`（规范化剧本表）

这些中间产物对用户完全不可见——前端无 UI 展示、无事件通知、侧边栏也不显示仅有 step1 的剧集。用户无法查看拆分/规范化的结果，也无法感知这个过程。

## 目标

1. 用户能在 Web UI 中查看和编辑 step1 中间产物
2. step1 生成完成时自动通知用户并导航到对应内容
3. 仅有 step1（尚无最终 JSON 剧本）的剧集也能在侧边栏中可见

## 设计方案

### 一、后端变更

#### 1.1 StatusCalculator 修复

`_load_episode_script()` 当前仅检测 `step1_segments.md`，需根据 `content_mode` 同时支持 drama 模式：

- `content_mode === "narration"` → 检测 `step1_segments.md`
- `content_mode === "drama"` → 检测 `step1_normalized_script.md`

两种模式下检测到 step1 文件存在时，均返回 `"segmented"` 状态。

#### 1.2 新增 `draft` 事件类型

在 `ProjectEventService` 中新增两种事件：

| entity_type | action | 触发时机 |
|-------------|--------|---------|
| `draft` | `created` | step1 文件首次生成（PUT 端点检测文件不存在 → 创建） |
| `draft` | `updated` | step1 文件被编辑更新（PUT 端点检测文件已存在 → 更新） |

事件数据包含 `focus` 字段，用于驱动前端自动导航：

```python
focus = {
    "pane": "episode",
    "episode": episode_num,
    "tab": "preprocessing"  # 新增字段，指定激活的 Tab
}
```

事件的 `label` 字段根据 content_mode 区分：
- narration：`"第 N 集片段拆分"`
- drama：`"第 N 集规范化剧本"`

#### 1.3 清理 drafts API

移除 `server/routers/files.py` 中 step2/step3 的文件映射，只保留 step1：

```python
# narration 模式
STEP_FILES = {1: "step1_segments.md"}

# drama 模式
STEP_FILES = {1: "step1_normalized_script.md"}
```

API 端点 `GET/PUT/DELETE /drafts/{episode}/step1` 内部根据 `project.json` 的 `content_mode` 决定实际读写哪个文件。前端统一调用 step1，无需感知文件名差异。

#### 1.4 事件触发集成

drafts PUT 端点在保存成功后，调用 `ProjectEventService` 发射 `draft:created` 或 `draft:updated` 事件。subagent 通过现有 drafts API 保存文件时自然触发事件链。

### 二、前端变更

#### 2.1 侧边栏（AssetSidebar）

剧集列表渲染逻辑变更：

- 对 `status === "segmented"` 的剧集正常渲染（当前仅 `"generated"` 和有 script_file 的才渲染）
- 样式：灰色状态点（`text-gray-500`）+ 右侧「预处理」标签（indigo 小徽章：`text-indigo-400 bg-indigo-950`）
- 点击导航到 `/episodes/{N}`，与正常剧集行为一致

无 step1 且无 JSON 剧本的剧集不出现在列表中。

#### 2.2 TimelineCanvas Tab 改造

在标题区域下方新增 Tab 栏，两个 Tab：「预处理」和「剧本时间线」。

Tab 可见性与激活规则：

| 状态 | Tab 栏 | 默认激活 |
|------|--------|---------|
| 只有 step1，无剧本 | 显示，「剧本时间线」Tab 禁用 | 预处理 |
| step1 + 剧本都有 | 显示，两者都可点击 | 剧本时间线 |
| 只有剧本，无 step1 | 不显示 Tab 栏 | —（保持现有行为） |

Tab 样式：
- 激活态：`text-indigo-400`，底部 2px `border-indigo-500`
- 非激活态：`text-gray-500`，底部 2px `transparent`
- 禁用态：`text-gray-700`，`cursor-not-allowed`

#### 2.3 预处理 Tab 内容组件（新建）

新建 `PreprocessingView` 组件，参考 `SourceFileViewer` 的编辑/查看切换模式：

**查看模式（默认）**：
- 顶部状态栏：左侧显示完成状态 + 时间戳，右侧「编辑」按钮
- 主体区域：Markdown 渲染，将 step1 的 Markdown 表格渲染为 HTML 表格
- 状态标签根据 content_mode 显示不同文案：
  - narration：「片段拆分已完成」
  - drama：「规范化剧本已完成」

**编辑模式**：
- 点击「编辑」按钮进入
- textarea 文本编辑器（`font-mono`，参考 SourceFileViewer 样式）
- 顶部按钮变为「保存」+「取消」
- 保存调用 `PUT /api/v1/projects/{name}/drafts/{episode}/step1`
- 保存成功后自动退出编辑模式，后端发射 `draft:updated` 事件

#### 2.4 事件处理与自动导航

`useProjectEventsSSE` hook 中新增对 `draft` 事件的处理：

**Toast 通知**：
- `draft:created`：重要通知（`important: true`），弹出 Toast
  - narration：「第 N 集片段拆分完成 · XX 个片段 · 约 XXs」
  - drama：「第 N 集规范化剧本完成 · XX 个场景 · 约 XXs」
- `draft:updated`：非重要通知

**自动导航**：
- 收到 `draft:created` 事件后，根据 `focus` 字段：
  1. 导航到 `/episodes/{N}`（如果不在该页面）
  2. 激活「预处理」Tab
- 触发项目数据重新加载（刷新侧边栏剧集列表，使新剧集可见）

**事件优先级**：
- 在 `CHANGE_PRIORITY` 中添加 `"draft:created": 6`（在 episode 事件之后、storyboard_ready 之前）

## 涉及的文件

### 后端
- `lib/status_calculator.py` — 修复 drama 模式的 step1 检测
- `server/routers/files.py` — 清理 step2/step3 映射，集成事件发射
- `server/services/project_events.py` — 新增 draft 事件类型与 label 生成

### 前端
- `frontend/src/components/layout/AssetSidebar.tsx` — 侧边栏支持 segmented 状态
- `frontend/src/components/canvas/timeline/TimelineCanvas.tsx` — 新增 Tab 栏
- `frontend/src/components/canvas/timeline/PreprocessingView.tsx` — **新建**，预处理内容组件
- `frontend/src/hooks/useProjectEventsSSE.ts` — 新增 draft 事件处理
- `frontend/src/types/workspace.ts` — 新增 draft 事件类型定义
- `frontend/src/utils/project-changes.ts` — 新增 draft 事件的通知文案
- `frontend/src/api.ts` — 已有 draft API，无需修改

### 测试
- `tests/test_status_calculator.py` — 补充 drama 模式 step1 检测用例
- `tests/test_files_router.py` — 更新 drafts API 测试（移除 step2/step3）
