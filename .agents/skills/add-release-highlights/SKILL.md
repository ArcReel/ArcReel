---
name: add-release-highlights
description: 起草指定版本的版本亮点；确认后同步到 Changelog 与 GitHub Release，并推送 main。
disable-model-invocation: true
---

# 添加版本亮点

## 草案

1. 以目标版本的完整 Changelog 为事实边界；条目含义不足时再追溯关联 issue、commit 或版本比较。把用户可见改动归并为少量结果导向的主题，内部改动仅在产生用户可感知结果时纳入。
2. 按以下格式写出完整草案：

   ```markdown
   ### 🌟 版本亮点

   * **主题：** 用户可感知的结果。
   ```

   正文语言跟随该版本的现有语言。
3. 向用户展示草案并请求确认，保持 Changelog 与 GitHub Release 只读并结束当前回合。

完成判据：每个重要的用户可见主题均已覆盖，每项表述有版本变更支持，且用户已明确确认完整草案。

## 执行

草案完成判据满足后：

1. 确认工作树位于 `main`，在 Changelog 的版本标题与分类明细之间插入确认稿；已有时原位更新，保留原有明细。
2. 将同一区块同步到该版本的 GitHub Release，保持逐字一致。
3. 只暂存本次 Changelog 变更，以 `chore(changelog): add <version> highlights` 提交，然后执行 `git push origin main`。

完成判据：Changelog 与 GitHub Release 各有一个 `### 🌟 版本亮点`，内容与确认稿一致；提交已位于 `main` 与 `origin/main`。
