---
name: release
description: 发版：合并 Release PR，起草版本亮点，确认后写入 Changelog 与 GitHub Release。
disable-model-invocation: true
---

# 发版

参数为空时从「合并 Release PR」开始；参数带版本号时该版本已经发布，从「亮点草案」开始。

`scripts/release_notes.py` 位于本 skill 目录，用 `python3` 在仓库内运行。

## 合并 Release PR

调用本 skill 即授权在闸门通过时合并。

1. 找到 Release PR：`gh pr list --state open --head release-please--branches--main--components--arcreel`。没有 Release PR 时告知用户并结束。
2. 过闸门：PR 的 head 包含 `origin/main` 的最新提交，全部检查通过。检查还在运行时在后台等待。
3. 闸门不过时先查明原因。Release 分支由 release-please 随 `main` 的每次推送重建，缺陷修在 `main` 上；是否带着问题发版按 `docs/agents/escalation.md` 找用户。
4. 合并：`gh pr merge <编号> --squash --match-head-commit <head sha>`。
5. 等 release-please 创建 `v<版本>` tag 与 GitHub Release。tag 触发的 Docker Release 放到后台，结束后把结果告诉用户。

完成判据：`gh release view v<版本>` 返回已发布的 Release。随即进入亮点草案。

## 亮点草案

版本亮点是 Changelog 明细之上的**导读**，写给**创作者**（`PRODUCT.md`「Users」）：他们据此判断这一版值不值得升级、升级后先试什么。

1. 运行 `release_notes.py themes [版本]`，得到按 Spec 归并的 Changelog 条目和最近一版亮点。
2. 按 `docs/standards/copy.md`「通用」一节与 `tech-doc-style-chinese` 起草，把握下面四点：
   - **主题来自 Spec**：先读每个 Spec 的 Problem Statement 与 Solution，再定主题，写创作者原来的问题和现在能做的事。无 Spec 归属的条目按同一标准取舍，事实读关联的 issue 或 PR。Changelog 条目用来核对事实。
   - **导读要取舍**：只留创作者会为之升级或主动去试的主题，篇幅与分量相称，其余留在明细。最近一版亮点是篇幅与语气的参照。
   - **界面词**：功能、模型和供应商都用创作者在界面上看到的叫法（`frontend/src/i18n/zh/`、模型目录的 `display_name`），实现写成结果。
   - **升级提示**：升级后会改变创作者现有数据、设置或使用前提的变化，写在对应条目下；所属主题未入选时单列一条。
3. 向用户展示完整草案和一行未入选清单，然后结束当前回合。格式：

   ```markdown
   ### 🌟 版本亮点

   * **名称：** 创作者得到的结果。
     * **升级提示：** 只在有这类变化的条目下出现。
   ```

Changelog 与 GitHub Release 在用户确认前保持只读。

完成判据：每个入选主题都写出了创作者原来的问题和现在能做的事，每句话都有 Changelog、Spec 或 issue 原文支持；全部 Changelog 条目都核对过是否需要升级提示；用户已明确确认完整草案。

## 发布亮点

草案完成判据满足后：

1. 把确认稿原样写入临时文件，第一行是 `### 🌟 版本亮点`。
2. 运行 `release_notes.py apply <版本> <文件>`。脚本提交并推送 `main` 上的 `CHANGELOG.md`、写入 GitHub Release 并校验两处，不切换当前分支；已有亮点时原位替换。
3. 向用户报告提交与 Release 链接。

完成判据：脚本输出两行校验通过。
