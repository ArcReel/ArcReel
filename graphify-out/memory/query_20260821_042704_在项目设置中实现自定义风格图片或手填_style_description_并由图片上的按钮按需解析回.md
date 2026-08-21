---
type: "implementation"
date: "2026-08-21T04:27:04.256503+00:00"
question: "在项目设置中实现自定义风格图片或手填 Style Description，并由图片上的按钮按需解析回填文本框"
contributor: "graphify"
outcome: "useful"
source_nodes: ["ProjectSettingsPage.tsx", "StylePicker.tsx", "ProjectsPage.tsx"]
---

# Q: 在项目设置中实现自定义风格图片或手填 Style Description，并由图片上的按钮按需解析回填文本框

## Answer

已实现：自定义风格页始终显示可编辑 style_description，支持无图片纯文本保存；图片预览右下角新增解析风格按钮，只有点击才调用视觉模型并回填；普通保存图片使用 analyze=false，不覆盖手填文本；已有图片可通过独立 analyze endpoint 重解析。模板与自定义文本保持互斥，不触发任何 Step2 或媒体重生成。项目列表也识别文本型自定义风格。相关前端构建、95项定向测试、ruff/eslint和后端路由测试均通过。

## Outcome

- Signal: useful

## Source Nodes

- ProjectSettingsPage.tsx
- StylePicker.tsx
- ProjectsPage.tsx