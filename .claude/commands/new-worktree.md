---
name: "New Worktree"
description: 创建隔离的 git worktree，并自动同步本地配置文件（settings.local.json、.env、.vscode/）和链接 projects/ 目录
category: Workflow
tags: [git, worktree, setup]
---

创建隔离工作区，完成后将本地环境文件同步到新 worktree。

**Input**: 可选指定分支名（如 `/new-worktree feature/auth`）。未指定则从对话上下文推断。

**开始时宣告：** "使用 new-worktree 命令创建隔离工作区。"

---

## 步骤

### 1. 确定分支名和基准 ref

若用户提供了分支名则使用，否则从对话上下文推断（如正在讨论某功能）。
若用户指定了基准 ref（如远程分支 `origin/feature/xxx`），作为第二个参数传入。

### 2. 执行脚本

```bash
bash scripts/new-worktree.sh <branch-name> [base-ref]
```

脚本会自动完成：
- 创建 worktree 到 `.worktrees/<branch-name>`
- 同步 .claude/settings.local.json、.env、.vscode/
- 链接 projects/ 目录（符号链接，共享数据）
- 安装 Python 和前端依赖

### 3. 验证基线（可选）

脚本完成后，运行测试确认 worktree 起点干净：

```bash
cd <worktree路径> && uv run python -m pytest --tb=short -q
```

若测试失败：报告失败情况，询问是否继续或先排查。

### 4. 报告结果

```
Worktree 已就绪：<完整路径>

已同步文件：
  ✓ .claude/settings.local.json
  ✓ .env
  ✓ projects/ (符号链接，共享数据)
  ✓ .vscode/

测试基线：通过（N 个测试，0 个失败）
可以开始实现 <feature-name>
```

---

## 快速参考

| 情况 | 操作 |
|------|------|
| worktree 目录 | 固定 `.worktrees/` |
| 源文件/目录不存在 | 静默跳过，报告中标注 |
| 测试失败 | 报告失败 + 询问 |
