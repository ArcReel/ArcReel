# Toast 与持久通知拆分（Issue #351）

## 背景

`frontend/src/stores/app-store.ts:116-127` 的 `pushToast` 在每次调用时强制写入 `workspaceNotifications`，导致瞬时 toast 与 drawer 持久通知的分工失效。全仓 60+ 非测试调用点都走这同一条 API，drawer 被保存成功、模式切换这类日常即时反馈塞爆。

这是「剧本生成阶段弹出耗时提示通知」feature 的前置依赖。

## 目标与非目标

**目标**
1. 在 store 层把瞬时（toast）与持久（drawer）通知解耦，提供三个语义清晰的入口
2. 按既定规则迁移全部现有调用点，无功能回归
3. 沉淀分类规则，避免后续新加调用点再次混用

**非目标**
- 不调整 drawer UI / 通知项样式
- 不调整 toast 显示时长、堆叠、动画
- 不改动 `WorkspaceNotification` / `WorkspaceNotificationInput` 数据结构
- 不重写 SSE 层的通知生成逻辑

## 架构

### Store API（`frontend/src/stores/app-store.ts`）

```ts
// 入口一：仅瞬时 toast（不再触达 workspaceNotifications）
pushToast: (text: string, tone?: Toast["tone"]) => void

// 入口二：仅持久（已存在，保留，不改）
pushWorkspaceNotification: (input: WorkspaceNotificationInput) => void

// 入口三：组合便利——同时 toast + 写入 drawer（新增）
pushNotification: (
  text: string,
  tone?: Toast["tone"],
  options?: { target?: WorkspaceNotificationTarget | null },
) => void
```

实现关系：`pushNotification` 内部调用 `pushToast` 与 `pushWorkspaceNotification`，不是第三条独立数据路径。

**签名收窄**：`pushToast` 原签名的第三个参数 `options: { target }` 被移除；`target` 只对持久化有意义，随 `pushNotification` / `pushWorkspaceNotification` 传递。

**类型清理**：删除 `ToastOptions` interface（仅有 target 字段，语义已并入 `pushNotification`）。

### 分类规则

写入 `frontend/src/stores/app-store.ts` 顶部注释（紧邻三个 API 定义），作为长期指南：

| API | 用于 | 典型场景 |
|---|---|---|
| `pushToast` | 用户主动操作的即时反馈 | 表单保存/校验（成功 + 失败）、导入/删除/切换/上传、scroll target 未找到、后台任务提交成功回执、轻量错误 |
| `pushWorkspaceNotification` | 后台异步事件留痕（用户可能不在当前页） | SSE 单条事件（如 `agent_update_scene`） |
| `pushNotification` | 用户需要后续回看的重要结果 | 后台任务**失败**、SSE grouped_notification |

**判断口诀**
1. 用户现在不在场 → 需要持久
2. 后台任务"失败"需要留痕排查 → 需要持久
3. 其余 → 仅 toast

**不提供逃生门**：`pushToast` 不接受 `{ persist: true }` 之类选项，强制调用点三选一，意图显式。

## 迁移映射

### → `pushNotification`（toast + 持久）

**`frontend/src/components/layout/GlobalHeader.tsx`**
- L194 剪映导出失败
- L223 ZIP 导出失败
- L192/213/218 的**成功**保持 `pushToast`

**`frontend/src/components/canvas/OverviewCanvas.tsx`**
- L122 regenerate 失败
- L80/L118 保持 `pushToast`

**`frontend/src/components/canvas/StudioCanvasRouter.tsx`** — 所有 `_failed` 失败分支
- L159 `update_prompt_failed`
- L176 `generate_storyboard_failed`
- L194 `generate_video_failed`
- L230 `update_character_failed`
- L246 `submit_failed`（character）
- L271 `add_failed`（character）
- L283 `update_scene_failed`
- L293 `submit_failed`（scene）
- L304 `add_failed`（scene）
- L316 `update_prop_failed`
- L326 `submit_failed`（prop）
- L337 `add_failed`（prop）
- L348 `grid_generation_failed`
- task_submitted（L174/192/244/291/324）和 grid 成功（L346）保持 `pushToast`

**`frontend/src/components/canvas/reference/ReferenceVideoCanvas.tsx`**
- L56 统一 `showError` → `pushNotification`
- L134 / L186 需逐条判断：失败分支用 `pushNotification`，成功分支用 `pushToast`（迁移时读取上下文决定）

**`frontend/src/hooks/useProjectEventsSSE.ts`**
- L174 同步项目变更失败 → `pushNotification`
- L250 grouped_notification → `pushNotification`（取代当前「pushToast + 强制持久」的副作用链）
- L280 已是 `pushWorkspaceNotification`，不动

### → `pushToast`（仅瞬时）

其余全部调用点一律迁移为 `pushToast`。包括但不限于：
- `EpisodeModeSwitcher.tsx`、`VersionTimeMachine.tsx`
- `PreprocessingView.tsx`、`ProjectSettingsPage.tsx`、`AgentConfigTab.tsx`
- `settings/MediaModelSection.tsx`、`settings/CustomProviderForm.tsx`、`settings/CustomProviderDetail.tsx`
- `ApiKeysTab.tsx`
- `lorebook/CharacterCard.tsx` / `SceneCard.tsx` / `PropCard.tsx`
- `lorebook/CharactersPage.tsx` / `ScenesPage.tsx` / `PropsPage.tsx`
- `AddToLibraryButton.tsx`、`AssetLibraryPage.tsx`、`AssetSidebar.tsx`
- `CreateProjectModal.tsx`、`ProjectsPage.tsx`（含 L262 删除失败——前台操作即时反馈归 toast）
- `useScrollTarget.ts`

### 测试文件同步更新

- `frontend/src/stores/stores.test.ts`
- `frontend/src/components/layout/GlobalHeader.test.tsx`
- `frontend/src/components/canvas/EpisodeModeSwitcher.test.tsx`

任何 spy `pushToast` 并验证「同时写入 workspaceNotifications」的断言需要移除——那是当前 bug 行为，不是规范。

## 数据流

```
pushToast(text, tone)
   └─→ set({ toast: {...} })
                           （workspaceNotifications 不动）

pushWorkspaceNotification(input)
   └─→ set({ workspaceNotifications: [...] })
                           （toast 不动）

pushNotification(text, tone, options)
   ├─→ pushToast(text, tone)
   └─→ pushWorkspaceNotification({ text, tone, target })
```

## 错误处理

此重构不涉及运行时错误处理。迁移若出错（漏改、类型不匹配）在 typecheck 阶段暴露：`pushToast` 签名收窄后，旧的 `pushToast(text, tone, { target })` 调用会编译失败，迫使调用点显式选择新 API。

## 测试策略

### 单元测试（`stores.test.ts`）新增三个关键断言

1. `pushToast` 只写 `toast`，**不**写 `workspaceNotifications` — 直接覆盖 issue 根因
2. `pushWorkspaceNotification` 只写 `workspaceNotifications`，**不**写 `toast`
3. `pushNotification` 两者都写；tone 与 target 正确传递

### 已有测试修正

- `EpisodeModeSwitcher.test.tsx` — spy 调用点语义不变，但若存在对 workspaceNotifications 的隐式断言需移除
- `GlobalHeader.test.tsx` — L113 用 `pushWorkspaceNotification`，不受影响

### 手工验证清单（迁移完成后过一遍）

- 编辑剧本保存 → 只弹 toast，drawer 不新增
- 导出剪映失败 → toast + drawer 留痕
- 切换剧集模式 → 只弹 toast
- 项目 regenerate 失败 → toast + drawer
- SSE grouped_notification → toast + drawer（回归保持）
- drawer 通知数量不再被日常操作塞爆

### 不做的测试

- 不为每个调用点写专属单测（YAGNI）
- 不做 e2e——手工验证覆盖足够，改动集中在 store 层 + 调用点简单替换

## 实施顺序（给 writing-plans 参考）

1. 改 `app-store.ts`：收窄 `pushToast` 签名、新增 `pushNotification`、删除 `ToastOptions`、写规则注释
2. 补 `stores.test.ts` 三个关键断言
3. 批量迁移调用点（按上文清单分文件替换）
4. 修复已有测试文件
5. `pnpm check` + `pnpm build` 过 typecheck / test
6. 手工验证清单过一遍

## 验收清单（Issue DoD 对齐）

- [x] 瞬时（toast）与持久（drawer）通知 API 解耦，可独立调用 — §Store API
- [x] 明确按通知类型的分工规则 — §分类规则
- [x] 现有调用点按规则迁移，无功能回归 — §迁移映射 + §测试策略
