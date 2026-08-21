# ArcReel 开发流程

本文档是 ArcReel 开发工作的入口。开始 feature、bug、重构或文档改动前，先阅读本文件和同目录的 `AGENTS.md`。

## 1. 需求澄清与执行确认

对每个新的 feature、bug、重构或文档改动，在创建 worktree、修改文件或执行其他会改变状态的操作前，必须完成以下确认闸门：

1. 先做必要的只读调研，确认能够从代码库、文档或现有运行状态中直接查明的事实；不要把可以自行查明的问题交给用户。
2. 列出仍会实质影响需求范围、交互、数据模型、兼容性或验收结果的模糊点，并向用户澄清。没有阻塞性模糊点时，也要明确说明。
3. 提交具体执行方案，至少包括目标与范围、用户可见行为和验收标准、关键实现决策、预计影响区域、验证方式，以及已知风险或迁移事项。
4. 等待用户明确确认方案。未经确认，不得开始实现。

用户确认后如果出现会实质改变已确认范围、交互、数据模型或风险的新信息，应暂停实现、更新方案并再次确认；不改变已确认结果的实现细节可以在方案范围内自行处理。

纯代码库问答、解释和只读诊断不需要执行确认；一旦需要写文件或改变项目状态，就必须经过上述闸门。用户直接提出“实现”或“修改”不等于跳过闸门，除非用户已经明确确认了当前具体方案。

## 2. 使用独立 Feature Worktree

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

## 3. 开发与验证

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

### Web 与 Agent 共用操作链路

新增用户可执行的功能或操作时，Web 与 Agent 必须共用同一组业务操作，不能分别实现两套写入逻辑：

1. 先检查现有共享 Service / Domain Operation 是否已经能够完整表达该操作；能够表达时直接复用，不在 Web 路由或 Agent Tool 内复制业务规则。
2. 如果现有操作或 Agent Tool 无法表达新能力，应在同一次改动中新增或扩展共享操作，并新增或更新对应的 Agent MCP Tool。不能只实现 Web 入口，导致 Agent 只能提示用户改去 Web 端操作。
3. Web API 与 Agent MCP Tool 都应是薄边界：负责各自的参数解析、身份/权限与错误适配，然后调用同一个共享操作。校验、状态变更、事务和核心副作用必须收敛在共享层。
4. 新增或修改 Agent Tool 时，同步更新工具注册目录、迁移失败等访问策略、Agent Profile / Prompt 使用说明，以及前端工具名称的全部语言翻译。
5. 验证至少覆盖共享操作本身、Web 调用路径和 Agent 调用路径，并断言两条入口产生一致的持久化结果与副作用。

推荐链路：

```text
Web UI → Web API ┐
                 ├→ Shared Service / Domain Operation → Project / DB / Task Queue
Agent → MCP Tool ┘
```

## 4. Graphify 约定

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

## 5. Commit 与 Merge

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
