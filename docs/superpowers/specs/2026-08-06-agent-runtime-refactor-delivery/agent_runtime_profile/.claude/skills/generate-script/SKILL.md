---
name: generate-script
description: 剧本生成：内部将当前模式的已审核内容生成正式 JSON 剧本。
user-invocable: false
---

# 生成正式剧本

## 前置

调 `get_workflow_status({"episode": N})`：

- narration / drama 的当前 step1 必须 current 且已审核；
- ad 不使用 step1；
- invalid reference draft 必须先修复；
- 项目路线和预期主结构必须一致。

## 调用

```text
mcp__arcreel__generate_episode_script({"episode": N})
mcp__arcreel__generate_episode_script({"episode": N, "dry_run": true})
```

`dry_run` 只预览，不写正式文件。

工具报告 reference invalid draft 时读取 `.claude/references/reference-draft-repair.md`。审核门阻塞时返回主 agent；不自行确认，不重复调用。

## 验证

生成后 workflow status 中 script 必须 current，且正式文件主结构非空：

- narration storyboard：`segments[]`；
- drama storyboard：`scenes[]`；
- ad：`shots[]`；
- narration / drama reference-video：`video_units[]`。

元数据以服务端写入值为准。剧本不携带项目路线字段，消费方读取 `project.json`。
