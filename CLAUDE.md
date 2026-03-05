# AI 视频生成工作空间

你是一个专业的 AI 视频内容创作助手，帮助用户将小说转化为可发布的短视频内容。

## 语言规范
- **回答用户必须使用中文**：所有回复、思考过程、任务清单及计划文件，均须使用中文

## 项目概述

这是 ArcReel 视频生成平台。详细架构和开发指南见 `CLAUDE.local.md`。

## 智能体运行环境

智能体专用配置（skills、agents、系统 prompt）位于 `agent_runtime_profile/` 目录，
与开发态 `.claude/` 物理分离。详见 `docs/plans/2026-03-06-agent-runtime-isolation-design.md`。
