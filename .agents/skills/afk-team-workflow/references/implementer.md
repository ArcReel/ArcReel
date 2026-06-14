# 实现契约（第一阶段）

你是批次中某个 issue 的实现者。交付一个**质量门通过、基于最新 main、改动已全部 commit、未建 PR** 的 worktree，由下一阶段（本地审查）接手——它要在此基础上 rebase 与 push，未 commit 的改动接不了手。不建 PR、不 push：PR 由本地审查阶段在独立审查完成后创建。

输入变量（来自 spawn prompt）：issue 号、lead 名。

## 步骤

1. **读 issue**：`gh issue view <N> --comments` 通读正文与评论。验收标准即工作边界——按字面完成，不扩展范围；验收标准与代码现实冲突、或遇到拿不准的取舍时 SendMessage 请示 lead，不自行选边
2. **建 worktree**：fetch 后从最新 `origin/main` 创建，放在 `.worktrees/` 下（仓库惯例，开发服务的文件监视已排除该目录），分支名 `issue/<N>`
3. **环境隔离**：需要启动 server 或写数据库验证时，端口与数据目录与其他 teammate 错开——批次中多个 worktree 同时运行，共用默认端口或 dev 数据库会互相污染
4. **实现**：用 /tdd（红-绿-重构，垂直切片）。tdd 流程中"与用户确认计划/接口/测试范围"的环节在本流程没有用户：issue 的验收标准就是已批准的计划，照此自行决策；只有超出 issue 范围的重大接口取舍才请示 lead。遵守仓库 CLAUDE.md 全部规范：i18n 三语补全、依赖用 `uv add` / `pnpm add`、代码注释不写 issue/PR 编号等
5. **质量门**：运行项目质量门（测试、lint、类型检查，改动涉及前端则含前端检查），全部通过后交付。质量门可能改写文件（如 formatter），改完补 commit，交付前确认 `git status` 干净

## 交付与退役

SendMessage 向 lead 汇报：worktree 路径、分支名、改动概要、测试结果、备案的环境失败（如有）。lead 确认后退役。
