# AFK 硬启动探针

在制定批次计划前完成本页。探针只验证能力与权限，不修改真实 issue、PR、分支或 reviewer 状态；所有可能弹出的技术授权都在用户仍在线时解决。

## 1. Codex 工具面

逐项以当前工具目录和只读调用取证：

1. **GitHub connector**：确认存在读取仓库/issue/PR/合并配置/分支规则，以及创建 PR、转 draft、更新 issue/标签、评论、squash merge 所需的 connector 工具。先从传入 `repo_root` 的 `origin` 动态解析目标 `<owner>/<repo>`，再调用 `github_get_profile` 取得当前 login，并用 `github_get_repo_collaborator_permission` 核对该 login 对目标仓库的权限为 `write`、`maintain` 或 `admin`；随后只读确认同一目标仓库可访问、已启用 squash merge，并读取作用于默认分支与 `issue/*` 的 protection/ruleset。CI 等可由后续流程满足的要求记录即可；强制人工批准、actor 限制或其他当前 connector 身份无法满足/绕过的规则均视为失败。不得把仓库名写死，也不要用真实写操作做探针；任一配置无法读取时不能推定为通过。
2. **Collaboration**：确认 `spawn_agent`、`send_message`、`followup_task`、`wait_agent`、`list_agents`、`interrupt_agent` 可用，并由槽位信息算出 lead 之外的上限。
3. **本地审查 skill**：确认外部 `$code-review` 可由本地审查 agent 调用；它是审查流程的单一真相源。
4. **当前任务 heartbeat**：确认 `codex_app__automation_update` 支持当前 task 的 heartbeat 创建与删除。创建一个约 30 分钟后才会触发的临时 heartbeat，取得 id 后立即删除；两步都成功才算权限通过。

完成上述探针后，才能把 `--github-connector-ok` 与 `--heartbeat-ok` 传给脚本；这两个 flag 是 lead 对已完成工具探针的声明，不是绕过开关。

## 2. 本地与 Git 权限面

从主 checkout 的绝对路径运行：

```bash
bash <repo_root>/.agents/skills/afk-team-workflow-codex/scripts/preflight.sh \
  --repo <repo_root> \
  --github-connector-ok \
  --heartbeat-ok
```

脚本逐项验证：

- `gh` 只读仓库访问与 `jq`
- `git fetch --dry-run origin`
- 用兼容旧版 Git 的目录解析确认传入路径是主 checkout，而非 linked worktree
- 对 `issue/afk-preflight-probe-*` 下的唯一临时 ref 执行 `git push --dry-run`，验证 push 凭证而不写远端，并贴近真实 `issue/` 分支命名规则
- 在系统临时目录创建 detached probe worktree，验证 worktree 写权限；正常或异常退出都会清理 worktree 与全部 probe 临时目录
- GitHub connector 与 heartbeat 的 lead 声明已传入

成功时 stdout 只输出一份 JSON 结果；失败以 `AFK_PREFLIGHT_ERROR:` 写 stderr 并非零退出。

## 3. 判定

只有工具面与脚本面全部通过，AFK 门槛才是绿色。任何失败都保留现场、说明具体缺项并停止；不得把“稍后再授权”带入无人值守阶段，也不得用 `gh` 写操作替代缺失的 connector。
