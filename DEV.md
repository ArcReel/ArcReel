# ArcReel 开发流程

本文档是 ArcReel 开发工作的入口。开始 feature、bug、重构或文档改动前，先阅读本文件和同目录的 `AGENTS.md`。

## 1. 使用独立 Feature Worktree

不要直接在 `main` 工作树中开发。Worktree 统一放在 `.worktrees/`，该目录已加入 Git ignore。

分支命名格式为 `<type>-<kebab-case>`：

- `feat-<name>`：新功能
- `bug-<name>`：缺陷修复
- `refactor-<name>`：行为不变的重构
- `chore-<name>`：依赖、配置或构建改动
- `docs-<name>`：文档改动

从 `main` 创建 worktree：

```bash
git worktree add .worktrees/<name> -b <type>-<name> main
cd .worktrees/<name>
```

## 2. 开发与验证

在 feature worktree 内开发，并按改动范围执行验证：

```bash
# 后端常用验证
uv run pytest
uv run ruff check .
uv run basedpyright
uv run lint-imports

# 前端常用验证（先进入 frontend/）
pnpm lint
pnpm check
```

## 3. Graphify 约定

ArcReel 的 graph 位于 `graphify-out/`。Graphify 是代码库导航和关系查询工具，不能替代本文件中的开发流程约束。

当前仓库在 `.githooks/` 中跟踪 Graphify 的 Git hook。新 clone 安装
`graphifyy` 后只需启用仓库 hooks 路径：

```bash
git config core.hooksPath .githooks
git config merge.graphify.name "Graphify graph merge driver"
git config merge.graphify.driver "graphify merge-driver %O %A %B"
graphify hook status
```

`post-commit` 会在 commit 完成后后台更新代码 graph；`post-checkout` 会在切换分支后更新 graph；`post-merge` 会在主工作树完成 merge（包括 fast-forward merge）后执行 `graphify update .`。三个 hook 都跳过 linked Worktree，只允许主工作树更新根目录的 `graphify-out/`。

团队共享的 Graphify 输出应提交到 `graphify-out/`，包括 `graph.json`、`graph.html`、`GRAPH_REPORT.md`、`manifest.json` 以及标签、健康检查和工作记忆。机器专属的解释器/扫描根记录、成本账本、cache 和日期备份由 `.gitignore` 排除。hook 完成后共享产物可能出现新的 Git 变更，这些变更可以在后续 commit 中统一提交。

`post-commit`/`post-checkout` 来自 `graphify hook install`，但仓库版本会将安装器写入的 `_PINNED` 解释器路径清空，使 hook 可跨机器使用。升级 Graphify 并重新生成 hook 后，提交前必须再次清空 `_PINNED`；Graphify 不会生成 `post-merge`，该文件由仓库维护。

如果 hook 未启用、`graphify` 不在 PATH，或 hook 执行失败，在主工作树中补跑：

```bash
graphify update .
```

通常不需要每次编辑后手动运行更新。除上述 merge 兜底外，以下情况也需要手动执行：

- hook 没有安装或执行失败；
- 需要在提交前让 graph 包含当前未提交代码；
- 发生大规模重构或删除后，需要强制更新：`graphify update . --force`。

新 Task 遇到代码库、架构或文件关系问题时，应优先查询已有 graph：

```bash
graphify query "<question>"
graphify path "<A>" "<B>"
graphify explain "<concept>"
```

## 4. Commit 与 Merge

验证通过后，在 feature worktree 内提交，commit message 遵循 Conventional Commits。Graphify hook 可能在 commit 完成后异步生成新的 `graphify-out/` 变更；不要把这理解为 hook 失败，也不要求把它塞回刚刚完成的 commit。

在 merge 之前，先向用户确认；未经确认不要 merge 回 `main`。

用户确认后，在主工作树中完成 merge，然后清理 worktree 和本地分支：

```bash
git merge <type>-<name>
# post-merge hook 会自动执行；若 hook 未安装或失败，手动补跑：
graphify update .
git worktree remove .worktrees/<name>
git branch -d <type>-<name>
```

如果是新的 clone，或当前 clone 的 hook 状态显示未安装，执行：

```bash
git config core.hooksPath .githooks
git config merge.graphify.name "Graphify graph merge driver"
git config merge.graphify.driver "graphify merge-driver %O %A %B"
graphify hook status
```

`.git/hooks/` 是本地 Git 元数据，不会随仓库提交；仓库共享 hook 位于 `.githooks/`。`.gitattributes` 中的 Graphify merge driver 规则也应保留并提交，以便团队共享 `graphify-out/graph.json` 的合并策略。
