# 阶段交接文件

每个 issue 使用主 checkout 下的 `.afk/<batch-id>/handoff-<N>.md`。三个阶段各自在退役前追加一段，不改写前段；账本只由 lead 写。

只记录 diff、issue、PR 与远端状态无法重推的信息。可重推事实不写；空项写“无”。follow-up 只记候选，不自行立项。

## 段结构

### 实现

- 关键取舍与理由（含放弃方案）
- 环境备案：端口、数据目录、特殊运行方式
- 已知薄弱点与未覆盖场景
- follow-up 候选

### 本地审查

- 已修复发现各一句；跳过项及理由
- follow-up 候选
- rebase 处置

### 审查循环

- pushback 在案清单（内容与依据）
- 按共享 `pr-ai-review-loop/references/retrospective.md` 产出的过程总结与 ADR / CONTEXT.md / CLAUDE.md / follow-up 四类候选全文
- 故障记录

后续阶段、替补和崩溃恢复必须先读已有段；lead 在清尾时聚合全部 handoff。
