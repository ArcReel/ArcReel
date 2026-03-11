## Why

系统配置页面存在分类不清晰、交互不统一（仅部分字段有清除按钮）、可用性差（保存按钮位于页面底部需滚动才能看到）等问题，导致用户配置体验较差，需要全面重构以提升易用性和一致性。

## What Changes

- **顶栏多 Tab 结构**：将所有配置拆分为四个顶栏 Tab，与现有"API Keys"并列：**ArcReel 智能体配置**、**AI 生图/生视频配置**、**高级配置**、**API Keys**
- **区块级保存 + 未保存感知**：每个 Tab 有独立的保存按钮，Tab 内任意字段被修改时，保存按钮高亮并以 sticky 方式固定在屏幕底部，确保用户无需滚动即可察觉并完成保存
- **统一清除交互**：为所有可选配置字段（base_url、api key 等）统一添加清除按钮，消除现有交互不一致问题
- **必填配置缺失警告**：当 ArcReel 智能体 API Key（Anthropic）或 AI 生成后端（AI Studio / Vertex AI 二选一）未配置时，系统无法正常运行；需在项目大厅的设置入口和设置页本身给予明显提示
- **设计规范**：使用 `/frontend-design` 技能进行 UI 设计，使用 `/vercel-react-best-practices` 技能进行前端开发

## Capabilities

### New Capabilities

- `system-config-ui`：系统配置页面的 UI 交互规范，包含 Tab 导航结构、Tab 级保存机制、未保存变更感知设计、清除按钮统一规范、必填配置缺失警告

### Modified Capabilities

（无需修改已有 spec 文件，本次仅涉及前端 UI 层变更，不影响 API 契约或数据结构）

## Impact

- **主要文件**：`frontend/src/components/pages/SystemConfigPage.tsx`（需较大幅度重构）
- **关联文件**：`frontend/src/components/pages/ProjectsPage.tsx`（设置入口警告徽标）、`frontend/src/components/layout/GlobalHeader.tsx`（设置入口警告徽标）
- **不影响**：后端 API、数据类型定义（`types/system.ts`）、后端路由（`server/routers/system_config.py`）
- **依赖**：无新增外部依赖，使用现有 Tailwind CSS 样式系统
