---
paths:
  - "frontend/**"
---

# 前端

前端任务（新建 UI 或重塑既有界面）动手写组件前先调用 `/frontend-design` 确立视觉与交互方向；涉及性能时调用 `/vercel-react-best-practices`，涉及可访问性时调用 `/web-design-guidelines`。spawn 前端实现 teammate 时把这条工具链要求写进 prompt。

审查按这些规范进行，写到对应场景时先读：

- 异步加载链、取消、多入口刷新、主动跳过的补偿：`docs/standards/frontend-async.md`
- 随资源占用禁用的控件、生成类入队、用户可见文案、带 `data-onboarding` 的元素：`docs/standards/frontend-ui.md`
- 测试替身、打桩与断言：`docs/standards/testing.md`
