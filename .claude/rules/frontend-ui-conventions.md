---
paths:
  - "frontend/**"
---

# 前端 UI 绑定规范

## 占用感知型控件绑定

编辑/重生成/上传/入库/版本恢复等随资源占用态禁用的控件，新增或改动时通过三项检查：弹窗/面板打开时校验当前占用态；提交时刻用 `frontend/src/stores/tasks-store.ts` 导出的 `isResourceBusy(kind, projectName, resourceId)` 复核最新占用态（打开后状态可能已变化，仅在打开时刻校验会留下竞态窗口）；同一资源卡片上的兄弟控件同步绑定禁用态。占用态不止队列任务：组件本地 state 承载的在途写请求（保存中 / 上传中 / 改名中）同权参与上述校验与兄弟控件绑定——`isResourceBusy` 只覆盖队列任务，卡片自身发出的直接 API 调用须由本地 state 参与校验。

## 入队经动作层

生成类入队操作一律经 `frontend/src/actions/` 的动作函数（内部统一封装 API 调用、乐观占用打标与去重提示），组件不得直接调用入队类 API 方法；新增入队类 API 方法时同步登记 `frontend/eslint.config.js` 中 no-restricted-syntax 的方法名清单。
