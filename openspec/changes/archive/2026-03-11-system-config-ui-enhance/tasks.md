## 1. 设计阶段（使用 /frontend-design）

- [x] 1.1 使用 `/frontend-design` 技能设计顶栏四 Tab 的视觉样式（含圆点徽标、Tab 激活态）
- [x] 1.2 使用 `/frontend-design` 技能设计 `TabSaveFooter` 组件的两种状态：正常嵌入态（禁用）和 sticky 高亮态（有未保存变更）

## 2. 可复用组件：TabSaveFooter

- [x] 2.1 创建 `TabSaveFooter` 组件，接收 `isDirty`、`saving`、`error`、`onSave`、`onReset` props
- [x] 2.2 实现 sticky 逻辑：`isDirty` 为 true 时添加 `sticky bottom-0 z-10 shadow` 样式，保存按钮切换为 primary 高亮色
- [x] 2.3 添加"撤销"按钮，`isDirty` 为 true 时显示，点击触发 `onReset`
- [x] 2.4 `saving` 为 true 时显示加载态并禁用按钮；`error` 非空时在按钮旁显示错误提示

## 3. 配置 Tab 组件

- [x] 3.1 创建 `AgentConfigTab` 组件（ArcReel 智能体配置），内部维护 Anthropic 相关字段的草稿状态，底部嵌入 `TabSaveFooter`
- [x] 3.2 创建 `MediaConfigTab` 组件（AI 生图/生视频配置），内部维护 Gemini/Vertex 相关字段的草稿状态，底部嵌入 `TabSaveFooter`
- [x] 3.3 创建 `AdvancedConfigTab` 组件（高级配置），内部维护限速/并发字段的草稿状态，底部嵌入 `TabSaveFooter`
- [x] 3.4 每个配置 Tab 组件实现 `isDirty` 检测（`deepEqual` 比较草稿与已保存值的 `useRef`）

## 4. 顶栏 Tab 导航与徽标

- [x] 4.1 将顶栏 Tab 从 `[config, api-keys]` 改为 `[agent, media, advanced, api-keys]`，保留 `ApiKeysTab` 组件不变
- [x] 4.2 实现 Tab 圆点徽标：当某配置 Tab 存在未保存变更时，其 Tab 标签旁显示圆点（●），切换 Tab 后徽标仍持续显示

## 5. 统一清除按钮

- [x] 5.1 为所有可选字段（base_url、api key 等非必填项）统一添加清除（×）按钮，有值时显示，空时隐藏
- [x] 5.2 点击清除后字段值置空，触发对应 Tab 的 `isDirty` 更新

## 6. SystemConfigPage 整合与清理

- [x] 6.1 将 `SystemConfigPage` 重构为编排层，组合四个 Tab 组件，移除原有全局草稿状态和底部全局保存按钮
- [x] 6.2 保留连接测试（Connection Test）功能，确保其仍可正常工作（归入对应 Tab）
- [x] 6.3 为页面底部添加足够的 `padding-bottom`，防止 sticky 页脚遮挡最后一行内容

## 7. 必填配置缺失检测与警告

- [x] 7.1 实现 `getConfigIssues(config)` 工具函数：分别检查 `anthropic_api_key.is_set`、image backend 对应凭证（`gemini_api_key.is_set` 或 `vertex_credentials.is_set`）、video backend 对应凭证，输出 `ConfigIssue[]`，并对 image/video 指向同一提供商且原因相同的条目做去重合并；在 `useConfigStatus` hook 中封装请求与缓存，暴露 `issues: ConfigIssue[]` 和 `isComplete: boolean`
- [x] 7.2 在 `ProjectsPage.tsx` 设置按钮（第 358 行附近）添加红色圆点徽标，当 `isConfigComplete === false` 时显示
- [x] 7.3 在 `GlobalHeader.tsx` 设置按钮（第 323 行附近）添加红色圆点徽标，当 `isConfigComplete === false` 时显示
- [x] 7.4 在 `SystemConfigPage` Tab 导航上方添加警告横幅组件，列出缺失的必填项并提供点击跳转到对应 Tab 的链接
- [x] 7.5 配置 Tab 保存成功后触发 `useConfigStatus` 重新检测，确保徽标和横幅实时更新

## 8. 质量保障（使用 /vercel-react-best-practices）

- [x] 8.1 使用 `/vercel-react-best-practices` 技能检查组件实现，确保符合 React 最佳实践（useRef 追踪 savedValues 避免不必要重渲染、deepEqual 缓存等）
- [x] 8.2 运行 `pnpm typecheck` 确保 TypeScript 无类型错误
- [x] 8.3 手动验证：修改字段后 sticky 页脚出现 + Tab 徽标显示；保存后 sticky 解除；切换 Tab 后徽标保留；撤销后恢复原值；清除按钮正常
- [x] 8.4 手动验证配置缺失警告：未配置时设置按钮出现红点、设置页出现横幅；补全配置保存后徽标和横幅实时消失
