# AFK 硬启动探针

在制定批次计划前完成本页。探针只验证能力与权限，不修改真实 issue、PR、分支或 reviewer 状态；所有可能弹出的技术授权都在用户仍在线时解决。

## 1. Codex 工具面

逐项以当前工具目录和只读调用取证：

1. **GitHub connector**：确认存在读取仓库/issue/PR，以及创建 PR、转 draft、更新 issue/标签、评论、squash merge 所需的 connector 工具；先调用 `github_get_profile` 取得当前 login，再调用 `github_get_repo_collaborator_permission` 核对该 login 对 `ArcReel/ArcReel` 的权限为 `write`、`maintain` 或 `admin`，最后用一个只读 repository 搜索/读取工具确认仓库可访问。工具存在但权限低于 `write` 不算通过；不要用真实写操作做探针。
2. **Collaboration**：确认 `spawn_agent`、`send_message`、`followup_task`、`wait_agent`、`list_agents`、`interrupt_agent` 可用，并由槽位信息算出 lead 之外的上限。
3. **当前任务 heartbeat**：确认 `codex_app__automation_update` 支持当前 task 的 heartbeat 创建与删除。创建一个约 30 分钟后才会触发的临时 heartbeat，取得 id 后立即删除；两步都成功才算权限通过。

隔离的 forward-test 可以用场景提供的 connector/heartbeat 能力桩替代真实调用，但测试根必须有 `.afk-fixture` 标记，且所有 `gh`/Git remote 都指向隔离桩；此时不得回退访问 live connector 或 live automation。

完成上述探针后，才能把 `--github-connector-ok` 与 `--heartbeat-ok` 传给脚本；这两个 flag 是 lead 对已完成工具探针的声明，不是绕过开关。

## 2. 本地与 Git 权限面

从主 checkout 的绝对路径运行：

```bash
bash <repo_root>/.agents/skills/afk-team-workflow-codex/scripts/preflight.sh \
  --repo <repo_root> \
  --github-connector-ok \
  --heartbeat-ok \
  [--codex-bin <绝对路径>]
```

脚本逐项验证：

- `gh` 只读仓库访问与 `jq`
- `git fetch --dry-run origin`
- 对唯一临时 ref 的 `git push --dry-run`，验证 push 凭证而不写远端
- 在系统临时目录创建并清理 detached probe worktree，验证 worktree 写权限
- `codex review --help` 存在 `--base`
- 用临时空目录、ephemeral session 和 read-only sandbox 发起一次最小 `codex exec`，实际验证 Codex 鉴权与服务连通性；只看到 CLI help 不算通过
- GitHub connector 与 heartbeat 的 lead 声明已传入

`codex` 不在 PATH 时，脚本会尝试 macOS ChatGPT app 内置路径；也可显式传 `--codex-bin`。成功时 stdout 只输出一份 JSON 结果；失败以 `AFK_PREFLIGHT_ERROR:` 写 stderr 并非零退出。

## 3. 判定

只有工具面与脚本面全部通过，AFK 门槛才是绿色。任何失败都保留现场、说明具体缺项并停止；不得把“稍后再授权”带入无人值守阶段，也不得用 `gh` 写操作替代缺失的 connector。
