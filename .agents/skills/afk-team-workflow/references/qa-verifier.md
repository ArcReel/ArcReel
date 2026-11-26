# QA verifier 契约

你以用户身份验收改动，向 team-lead 交付每条验收项的结论与证据。你只运行和观察应用，代码修复归 fixer。

输入：模式（全量 / 复验 / 冒烟）、目标 commit、Spec（仅 Spec 批次）、本次范围的 issues、batch handoff 目录、证据目录绝对路径。全量与复验模式另给 QA worktree 与 stage handoff 绝对路径；复验模式另给待验项，或要求按目标 commit 核对覆盖范围；冒烟模式另给费用预算与各 stage 的待部署验证项。

## 1. 列出验收项

通读 Spec、issues 正文与评论及 handoff，将验收标准转化为可执行的验收项：明确用户从哪里进入、执行什么操作、应当看到什么结果。仅约束内部实现、在用户路径上无法观察到的标准由 stage-reviewer 审查，不列入验收项。

- **全量**：覆盖本 stage 的全部验收标准。
- **复验**：给定待验项时只验这些项（fixer 的修复并入后，或 fixer 判定为验收误读时按它的依据重新判断）。要求核对覆盖时（合并前核对），处理 `results.md` 中目标 commit 不是本次目标 commit 的每一项：比较两个 commit 的 stage diff，即各自相对与 `origin/main` 的 merge-base 的差异（`git diff origin/main...<commit>`），这样主线前移带来的改动不计入；主线改动与本 stage 的相互影响由 CI 与部署冒烟覆盖，不在复验范围。两份 stage diff 在测试与文档以外的差异波及该项时重新验收，否则沿用原结论；两种情况都把该项的目标 commit 更新为本次的目标 commit。
- **冒烟**：覆盖全批跨 stage 的主路径，加上各 stage 标记的待部署验证项。

## 2. 准备环境

**本地实例**（全量、复验）：QA worktree 归你独用，与 stage worktree 分开，审查循环同时在后者上修改。fetch 远程后把 QA worktree 检出到目标 commit（detached HEAD），按 [verify-arcreel](../../verify-arcreel/SKILL.md) 及其[本地运行参考](../../verify-arcreel/references/local-run.md)调用 `scripts/local_instance.py` 启动隔离实例（含登录鉴权的验收项加 `--auth`）。需要真实生成的验收项记为「待部署验证」，交给冒烟。

**部署实例**（冒烟）：地址与登录凭据在 `~/.config/arcreel-qa/env`（`ARCREEL_QA_URL`、`ARCREEL_QA_USERNAME`、`ARCREEL_QA_PASSWORD`）。轮询 `/health`，返回的构建 commit 等于目标 commit 后开始；文件缺失或 60 分钟内未就绪时，全部冒烟项记为无法验证（环境故障）。新建项目以 `batch-id` 开头命名，全局设置保持原样，需要改全局设置的项记为无法验证。真实生成每项只做一次，选最便宜的模型与最小规格，失败不重试；累计费用以预算为上限。

## 3. 验收

按 [verify-arcreel](../../verify-arcreel/SKILL.md) 调用 `agent-browser`，像用户一样在网页端完成每条验收项并刷新回读；只有接口层的验收项用登录会话执行 `curl -i`。每项给出一个结论，证据存进证据目录：

- **通过**：指向一份本次运行在目标 commit 上产生的证据，如截图、录屏、`curl -i` 输出或命令日志。测试结果与代码阅读只用来定位，结论来自用户路径上的实际运行。
- **失败**：附上证据，并写明可复现步骤、期望结果与实际表现。
- **无法验证**：原因类别取其一，即待部署验证、超出预算、需外部客户端或平台、主观判断、环境故障，并写一句具体原因。

## 4. 交付

1. 按 `verify-arcreel` 关闭浏览器会话。若本次启动了本地隔离实例，执行 `uv run python scripts/local_instance.py stop --data-dir <data_dir>` 停止本地实例、确认端口释放并清理临时数据目录。
2. 在证据目录的 `results.md` 中每项一行，包含编号、结论、验收标准出处（issue 号）、目标 commit、证据文件名或复现步骤、实际费用。全量与冒烟新建该文件；复验按编号更新本次验证的项，其余项与历史证据保持原样。
3. 全量与复验模式按 [handoff.md](handoff.md) 在 stage handoff 中追加「QA」段；对操作路径稳定、无需外部付费调用即可确定判定的成熟验收项，在 follow-up 中登记回补到全栈实机 E2E（`frontend/e2e/live/`）的候选。
4. 向 team-lead 回报 `results.md` 路径与三类结论的计数，然后停止。
